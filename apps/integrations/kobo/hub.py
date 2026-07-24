"""Territorial Kobo operations hub. HTTP handlers delegate every mutation to services."""

from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.integrations.kobo.contracts import (
    PastoralZone,
    TerritorialAdministrationStatus,
    TerritorialConflictDecision,
)
from apps.integrations.kobo.forms import (
    PastoralZoneProjectMappingForm,
    TerritorialConflictResolutionForm,
    TerritorialReasonForm,
)
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboImportRecord,
    KoboPastoralZoneProjectMapping,
    KoboSubmission,
    KoboSyncRun,
    KoboTerritorialAdministrationEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.client import build_kobo_api_client
from apps.integrations.kobo.presentation import (
    PASTORAL_ZONE_TOTAL,
    pastoral_zone_label,
    spanish_join,
    sync_status_label,
)
from apps.integrations.kobo.services import (
    activate_observed_territorial_identity,
    configure_pastoral_zone_project_mapping,
    deactivate_pastoral_zone_project_mapping,
    deactivate_territorial_identity,
    observe_territorial_identity,
    reconcile_territorial_identity_submissions,
    resolve_territorial_identity_conflict,
    sync_asset_submissions,
)
from apps.integrations.kobo.services.automation import (
    INCIDENT_ACTIONS,
    INCIDENT_LABELS,
    AutoImportOutcome,
    IncidentKind,
    classify_incident,
    incident_queryset,
    retry_auto_import,
    retry_incidents_for_pastoral_zone,
)
from apps.integrations.kobo.services.orchestration import (
    SUPPORTED_FORM_ROLES,
    sync_supported_assets,
)
from apps.operations.models import Project


HUB_READ_PERMISSION = "kobo.view_territorial_administration"
PAGE_SIZE = 25
REASON_MESSAGES = {
    "zone_mapping_in_use": "La zona ya tiene núcleos registrados en uso.",
    "territorial_identity_already_used": (
        "El núcleo tiene historial importado o resuelto y no puede reasignarse."
    ),
    "already_resolved": "El caso ya fue resuelto.",
    "invalid_identity_transition": "La transición de estado no está permitida.",
    "permission_denied": "No posee el permiso requerido para esta operación.",
    "reason_required": "Debe indicar un motivo.",
    "project_not_available": "El proyecto no está disponible para la asignación.",
    "proposed_mapping_not_available": "No existe una asignación activa para la propuesta.",
}
VALID_ZONE_VALUES = {zone.value for zone in PastoralZone}
RETRYABLE_INCIDENT_KINDS = frozenset(
    {
        IncidentKind.ZONE_WITHOUT_PROJECT,
        IncidentKind.NUCLEUS_NOT_FOUND,
        IncidentKind.NORMALIZATION_ERROR,
        IncidentKind.MATERIALIZATION_ERROR,
        IncidentKind.TECHNICAL_ERROR,
        IncidentKind.ROUTING_ERROR,
    }
)
# Deprecated alias: human review queue replaced by automatic-import incidents.
pending_review_queryset = incident_queryset


def territorial_hub_access(view):
    """
    PRE: view is an internal Kobo territorial HTTP handler.
    POST: hides the hub when disabled and otherwise requires its explicit read permission.
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.KOBO_ENABLED:
            raise Http404
        if not request.user.is_authenticated or not request.user.has_perm(HUB_READ_PERMISSION):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def _is_htmx_request(request):
    return request.headers.get("HX-Request") == "true"


def _message_for_result(result, success_message):
    # PRE: result is a typed territorial service result.
    # POST: returns one safe operator-facing message without exposing exceptions or identifiers.
    if result.status == TerritorialAdministrationStatus.SUCCESS:
        return success_message, messages.SUCCESS
    if result.status == TerritorialAdministrationStatus.ALREADY_APPLIED:
        return "La operación ya estaba aplicada.", messages.INFO
    reason = getattr(result, "reason_code", None)
    return REASON_MESSAGES.get(str(reason), "La operación no pudo completarse."), messages.ERROR


def _paginate(request, queryset):
    # PRE: queryset has a stable ordering suitable for the current list.
    # POST: returns one bounded database page without evaluating all rows in Python.
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))


def _pagination_context(request, queryset):
    # PRE: queryset is stably ordered and request contains optional page selection.
    # POST: supplies the existing pagination partial with its required state.
    page_obj = _paginate(request, queryset)
    query = request.GET.copy()
    query.pop("page", None)
    return {
        "page_obj": page_obj,
        "is_paginated": page_obj.paginator.num_pages > 1,
        "pagination_query": query.urlencode(),
    }


def _project_filter(request, queryset, field="project_id"):
    project = request.GET.get("project")
    return queryset.filter(**{field: project}) if project and project.isdigit() else queryset


def _build_next_actions(*, missing_mappings, incident_count):
    # PRE: counts and missing zones come from dashboard aggregations.
    # POST: returns ordered actionable next steps for zones and incidents only.
    items = []
    if missing_mappings:
        zone_names = spanish_join([pastoral_zone_label(zone) for zone in missing_mappings])
        first_zone = missing_mappings[0].value
        items.append(
            {
                "title": "Zonas sin configurar",
                "description": f"{zone_names} todavía no tienen proyecto asociado.",
                "count": len(missing_mappings),
                "url": reverse("kobo:mapping_list") + f"?zone={first_zone}",
            }
        )
    if incident_count:
        items.append(
            {
                "title": "Incidencias por resolver",
                "description": "Hay formularios que no pudieron procesarse automáticamente.",
                "count": incident_count,
                "url": reverse("kobo:conflict_list"),
            }
        )
    return items


def _parse_selected_zone(raw_zone):
    # PRE: raw_zone comes from an optional GET parameter.
    # POST: returns a PastoralZone when valid; otherwise None without mutating state.
    if not raw_zone or raw_zone not in VALID_ZONE_VALUES:
        return None
    return PastoralZone(raw_zone)


def _processing_status():
    # PRE: sync leases and runs are persisted for supported assets.
    # POST: returns idle or syncing without exposing lease owners or errors.
    now = timezone.now()
    if KoboAsset.objects.filter(
        is_active=True,
        form_role__in=SUPPORTED_FORM_ROLES,
        sync_lease_expires_at__gt=now,
        sync_lease_run_id__isnull=False,
    ).exists():
        return "syncing"
    if KoboSyncRun.objects.filter(
        kind=KoboSyncRun.Kind.SUBMISSIONS,
        status=KoboSyncRun.Status.RUNNING,
    ).exists():
        return "syncing"
    return "idle"


def _latest_supported_sync_run():
    return (
        KoboSyncRun.objects.filter(
            kind=KoboSyncRun.Kind.SUBMISSIONS,
            asset__is_active=True,
            asset__form_role__in=SUPPORTED_FORM_ROLES,
        )
        .select_related("asset")
        .order_by("-started_at", "-pk")
        .first()
    )


def _dashboard_metrics(request):
    # PRE: caller may read the territorial hub.
    # POST: returns light aggregates used by the dashboard and status fragment.
    submissions = _project_filter(request, KoboSubmission.objects.all())
    mappings = {
        item.pastoral_zone: item
        for item in KoboPastoralZoneProjectMapping.objects.filter(is_active=True).select_related(
            "project"
        )
    }
    mapped_zones = set(mappings)
    missing_mappings = [zone for zone in PastoralZone if zone.value not in mapped_zones]
    incident_count = incident_queryset(submissions).count()
    return {
        "imported_count": submissions.filter(status=KoboSubmission.Status.IMPORTED).count(),
        "incident_count": incident_count,
        "mapping_count": len(mapped_zones),
        "zone_total": PASTORAL_ZONE_TOTAL,
        "missing_mappings": missing_mappings,
        "missing_mappings_label": spanish_join(
            [pastoral_zone_label(zone) for zone in missing_mappings]
        ),
        "next_actions": _build_next_actions(
            missing_mappings=missing_mappings,
            incident_count=incident_count,
        ),
        "last_sync": _latest_supported_sync_run(),
        "processing_status": _processing_status(),
        "latest_received_at": submissions.aggregate(value=Max("received_at"))["value"],
        "kobo_configuration_complete": bool(settings.KOBO_BASE_URL and settings.KOBO_API_TOKEN),
        "can_sync": request.user.has_perm("kobo.change_koboasset"),
    }


def _dashboard_status_metrics(request):
    """
    PRE: caller passed territorial_hub_access and only needs polling data.
    POST: returns compact aggregates without mappings, payloads, or full form rows.
    """
    submissions = _project_filter(request, KoboSubmission.objects.all())
    aggregates = submissions.aggregate(
        imported_count=Count("pk", filter=Q(status=KoboSubmission.Status.IMPORTED)),
        latest_received_at=Max("received_at"),
    )
    return {
        "imported_count": aggregates["imported_count"],
        "incident_count": incident_queryset(submissions).count(),
        "mapping_count": KoboPastoralZoneProjectMapping.objects.filter(is_active=True).count(),
        "zone_total": PASTORAL_ZONE_TOTAL,
        "last_sync": _latest_supported_sync_run(),
        "processing_status": _processing_status(),
        "latest_received_at": aggregates["latest_received_at"],
    }


def _review_categories():
    # PRE: hub readers may inspect incident queues.
    # POST: returns category cards for automatic-import incidents without human review.
    submissions = KoboSubmission.objects.all()
    return [
        {
            "key": "pending_identity",
            "title": "Formularios sin núcleo registrado",
            "description": "Requieren recibir o resolver el registro territorial (Ficha 1).",
            "empty_meaning": "Todos los formularios tienen núcleo identificado o no aplica.",
            "empty_action": "Revise Ficha 1 o registre el núcleo correspondiente.",
            "count": submissions.filter(
                routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY
            ).count(),
            "url": reverse("kobo:pending_submission_list") + "?routing_status=pending_identity",
        },
        {
            "key": "conflicts",
            "title": "Conflictos de asignación",
            "description": "La zona o el proyecto del formulario no coincide con el núcleo registrado.",
            "empty_meaning": "No hay conflictos de asignación abiertos.",
            "empty_action": "Cuando aparezca un conflicto, ábralo desde esta misma pantalla.",
            "count": KoboTerritorialIdentityConflict.objects.filter(status="open").count(),
            "url": None,
        },
        {
            "key": "processing_errors",
            "title": "Errores de procesamiento",
            "description": "Formularios que no completaron su procesamiento automático.",
            "empty_meaning": "No hay errores de procesamiento pendientes.",
            "empty_action": "Si un formulario falla al procesarse, aparecerá aquí.",
            "count": submissions.filter(
                status=KoboSubmission.Status.PROCESSING_FAILED
            ).count(),
            "url": reverse("kobo:pending_submission_list")
            + f"?status={KoboSubmission.Status.PROCESSING_FAILED}",
        },
        {
            "key": "remote_updates",
            "title": "Actualizaciones remotas pendientes",
            "description": "Cambios recibidos desde KoboToolbox que requieren atención.",
            "empty_meaning": "No hay actualizaciones remotas pendientes.",
            "empty_action": "Tras una sincronización con cambios, revise esta categoría.",
            "count": submissions.filter(remote_update_pending=True).count(),
            "url": reverse("kobo:pending_submission_list") + "?remote_update_pending=1",
        },
        {
            "key": "invalid_data",
            "title": "Datos inválidos",
            "description": "Formularios con datos que no pasaron la validación automática.",
            "empty_meaning": "No hay formularios con datos inválidos.",
            "empty_action": "Corrija los datos en KoboToolbox y sincronice de nuevo.",
            "count": submissions.filter(
                status=KoboSubmission.Status.VALIDATION_FAILED
            ).count(),
            "url": reverse("kobo:pending_submission_list")
            + f"?status={KoboSubmission.Status.VALIDATION_FAILED}",
        },
        {
            "key": "routing_errors",
            "title": "Errores de asignación territorial",
            "description": "Formularios que no pudieron asociarse a zona o proyecto.",
            "empty_meaning": "No hay errores de asignación pendientes.",
            "empty_action": "Revise la configuración de zonas o el código del núcleo.",
            "count": submissions.filter(
                routing_status=KoboSubmission.RoutingStatus.ERROR
            ).count(),
            "url": reverse("kobo:pending_submission_list") + "?routing_status=error",
        },
    ]


def _mapping_row_for_zone(zone):
    # PRE: zone is a PastoralZone value object.
    # POST: returns one table-row dict matching mapping_list rows.
    mapping = (
        KoboPastoralZoneProjectMapping.objects.filter(
            pastoral_zone=zone.value,
            is_active=True,
        )
        .select_related("project")
        .first()
    )
    identity_count = KoboTerritorialIdentity.objects.filter(
        pastoral_zone=zone.value
    ).count()
    return {
        "zone": zone,
        "mapping": mapping,
        "identity_count": identity_count,
    }


@territorial_hub_access
def hub_dashboard(request):
    # PRE: Kobo is enabled and the caller may read territorial administration.
    # POST: renders the automatic-reception resumen without per-ficha sync controls.
    context = _dashboard_metrics(request)
    context["hub_nav"] = "summary"
    return render(request, "kobo/hub/dashboard.html", context)


@territorial_hub_access
@require_GET
def dashboard_status(request):
    # PRE: caller may read the territorial hub and KOBO_ENABLED is True.
    # POST: returns light aggregates for polling without payloads or secrets.
    context = _dashboard_status_metrics(request)
    return render(request, "kobo/hub/_dashboard_status.html", context)


@territorial_hub_access
def sync_history(request):
    # PRE: caller may read the territorial hub.
    # POST: lists submission sync runs outside the reduced dashboard.
    queryset = (
        KoboSyncRun.objects.filter(kind=KoboSyncRun.Kind.SUBMISSIONS)
        .select_related("asset")
        .order_by("-started_at", "-pk")
    )
    context = _pagination_context(request, queryset)
    context["hub_nav"] = "summary"
    return render(request, "kobo/hub/sync_history.html", context)


@territorial_hub_access
@require_POST
def sync_asset(request, pk, mode):
    """PRE: caller may manage Kobo assets. POST: runs one safe synchronous sync."""
    if not request.user.has_perm("kobo.change_koboasset"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    asset = get_object_or_404(KoboAsset, pk=pk, is_active=True)
    client = build_kobo_api_client()
    result = sync_asset_submissions(asset=asset, client=client, actor=request.user, full=mode == "full")
    level = messages.SUCCESS if result.status == KoboSyncRun.Status.SUCCEEDED else (
        messages.WARNING if result.status in (KoboSyncRun.Status.PARTIAL, "SYNC_ALREADY_RUNNING") else messages.ERROR
    )
    status_text = sync_status_label(result.status).lower()
    if result.status == "SYNC_ALREADY_RUNNING":
        status_text = "ya en curso"
    messages.add_message(request, level, f"Sincronización {status_text}: {asset.name}.")
    return redirect("kobo:hub")


@territorial_hub_access
@require_POST
def sync_all(request):
    """
    PRE: caller may change Kobo assets.
    POST: runs incremental sync for all supported assets and returns HTMX fragments or redirect.
    """
    if not request.user.has_perm("kobo.change_koboasset"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    client = build_kobo_api_client()
    result = sync_supported_assets(client=client, actor=request.user, full=False)
    if _is_htmx_request(request):
        context = _dashboard_metrics(request)
        context["sync_result"] = result
        context["status_oob"] = True
        context["next_actions_oob"] = True
        return render(request, "kobo/hub/_sync_result.html", context)
    if result.status == "SUCCEEDED":
        level = messages.SUCCESS
    elif result.status == "PARTIAL":
        level = messages.WARNING
    else:
        level = messages.ERROR
    messages.add_message(
        request,
        level,
        (
            f"Sincronización completada: {result.imported} importados, "
            f"{result.incidents} incidencias, {result.errors} con error."
        ),
    )
    return redirect("kobo:hub")


@territorial_hub_access
def mapping_list(request):
    selected_zone = _parse_selected_zone(request.GET.get("zone"))
    change_zone = _parse_selected_zone(request.GET.get("change"))
    return render(
        request,
        "kobo/hub/mapping_list.html",
        _mapping_list_context(
            request,
            selected_zone=selected_zone,
            change_zone=change_zone,
        ),
    )


def _mapping_list_context(
    request,
    *,
    form=None,
    selected_zone=None,
    change_zone=None,
):
    # PRE: optional form/zones come from GET preselection or POST validation recovery.
    # POST: returns the assignment screen context without mutating mappings.
    mappings = {
        item.pastoral_zone: item
        for item in KoboPastoralZoneProjectMapping.objects.filter(is_active=True).select_related(
            "project"
        )
    }
    identity_counts = dict(
        KoboTerritorialIdentity.objects.values("pastoral_zone")
        .annotate(total=Count("pk"))
        .values_list("pastoral_zone", "total")
    )
    rows = [
        {
            "zone": zone,
            "mapping": mappings.get(zone.value),
            "identity_count": identity_counts.get(zone.value, 0),
        }
        for zone in PastoralZone
    ]
    change_mapping = None
    if change_zone is not None:
        change_mapping = mappings.get(change_zone.value)
        if change_mapping is None:
            change_zone = None
    focus_project = selected_zone is not None or (
        form is not None and form.is_bound and not form.is_valid()
    )
    if form is None:
        form_kwargs = {"focus_project": focus_project}
        if selected_zone is not None:
            form_kwargs["initial"] = {"pastoral_zone": selected_zone.value}
        form = PastoralZoneProjectMappingForm(**form_kwargs)
    change_form = None
    if change_zone is not None and change_mapping is not None:
        change_form = PastoralZoneProjectMappingForm(
            initial={
                "pastoral_zone": change_zone.value,
                "project": change_mapping.project_id,
            },
            focus_project=True,
        )
    return {
        "rows": rows,
        "form": form,
        "selected_zone": selected_zone,
        "selected_zone_label": pastoral_zone_label(selected_zone) if selected_zone else "",
        "change_zone": change_zone,
        "change_zone_label": pastoral_zone_label(change_zone) if change_zone else "",
        "change_mapping": change_mapping,
        "change_form": change_form,
        "change_explanation": (
            (
                f"La zona {pastoral_zone_label(change_zone)} dejará de asociar nuevos "
                f"formularios al proyecto {change_mapping.project.name}. "
                "Los formularios ya importados no serán modificados."
            )
            if change_zone is not None and change_mapping is not None
            else ""
        ),
        "reason_form": TerritorialReasonForm(),
        "can_manage": request.user.has_perm("kobo.manage_pastoral_zone_mappings"),
        "hub_nav": "mappings",
        "missing_count": sum(1 for row in rows if row["mapping"] is None),
    }


def _mapping_modal_context(request, *, zone, mode, form=None, reason_form=None):
    # PRE: zone is a valid PastoralZone and mode is configure or change.
    # POST: returns modal fragment context without mutating mappings.
    mapping = (
        KoboPastoralZoneProjectMapping.objects.filter(
            pastoral_zone=zone.value,
            is_active=True,
        )
        .select_related("project")
        .first()
    )
    if mode == "change" and mapping is None:
        mode = "configure"
    if form is None:
        initial = {"pastoral_zone": zone.value}
        if mapping is not None:
            initial["project"] = mapping.project_id
        form = PastoralZoneProjectMappingForm(initial=initial, focus_project=True)
        form.fields["pastoral_zone"].widget = forms.HiddenInput()
    else:
        form.fields["pastoral_zone"].widget = forms.HiddenInput()
    return {
        "zone": zone,
        "zone_label": pastoral_zone_label(zone),
        "mode": mode,
        "form": form,
        "mapping": mapping,
        "reason_form": reason_form or TerritorialReasonForm(),
        "can_manage": request.user.has_perm("kobo.manage_pastoral_zone_mappings"),
        "change_explanation": (
            (
                f"La zona {pastoral_zone_label(zone)} dejará de asociar nuevos "
                f"formularios al proyecto {mapping.project.name}. "
                "Los formularios ya importados no serán modificados."
            )
            if mapping is not None
            else ""
        ),
    }


@territorial_hub_access
@require_GET
def mapping_modal(request):
    # PRE: caller may read the hub; optional zone/change query selects a pastoral zone.
    # POST: returns the mapping modal fragment for HTMX without mutating state.
    zone = _parse_selected_zone(request.GET.get("zone") or request.GET.get("change"))
    if zone is None:
        raise Http404
    mode = "change" if request.GET.get("change") else "configure"
    return render(
        request,
        "kobo/hub/_mapping_modal.html",
        _mapping_modal_context(request, zone=zone, mode=mode),
    )


def _htmx_mapping_success(request, *, zone, message, level):
    # PRE: mapping mutation succeeded and zone identifies the affected row.
    # POST: returns row OOB + closed modal + toast for HTMX clients.
    toast_class = "alert-success" if level == messages.SUCCESS else (
        "alert-info" if level == messages.INFO else "alert-danger"
    )
    row = _mapping_row_for_zone(zone)
    row_html = render_to_string(
        "kobo/hub/_mapping_row.html",
        {
            "row": row,
            "can_manage": request.user.has_perm("kobo.manage_pastoral_zone_mappings"),
            "row_oob": True,
        },
        request=request,
    )
    toast_html = (
        f'<div id="kobo-toast-root" hx-swap-oob="innerHTML">'
        f'<div class="alert {toast_class} kobo-hub-toast" role="status">{message}</div>'
        f"</div>"
    )
    return HttpResponse(
        row_html
        + '<div id="kobo-modal-root" hx-swap-oob="innerHTML"></div>'
        + toast_html
    )


@territorial_hub_access
@require_POST
def configure_mapping(request):
    if not request.user.has_perm("kobo.manage_pastoral_zone_mappings"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    form = PastoralZoneProjectMappingForm(request.POST, focus_project=True)
    if form.is_valid():
        result = configure_pastoral_zone_project_mapping(actor=request.user, **form.cleaned_data)
        message, level = _message_for_result(result, "Asignación guardada.")
        if result.status in {
            TerritorialAdministrationStatus.SUCCESS,
            TerritorialAdministrationStatus.ALREADY_APPLIED,
        }:
            retry_incidents_for_pastoral_zone(
                pastoral_zone=form.cleaned_data["pastoral_zone"]
            )
        if _is_htmx_request(request):
            zone = PastoralZone(form.cleaned_data["pastoral_zone"])
            return _htmx_mapping_success(request, zone=zone, message=message, level=level)
        messages.add_message(request, level, message)
        return redirect("kobo:mapping_list")
    selected_zone = _parse_selected_zone(request.POST.get("pastoral_zone"))
    if _is_htmx_request(request) and selected_zone is not None:
        mode = "change" if request.POST.get("mapping_mode") == "change" else "configure"
        return render(
            request,
            "kobo/hub/_mapping_modal.html",
            _mapping_modal_context(request, zone=selected_zone, mode=mode, form=form),
            status=400,
        )
    messages.error(request, "Revise los datos de la asignación.")
    return render(
        request,
        "kobo/hub/mapping_list.html",
        _mapping_list_context(request, form=form, selected_zone=selected_zone),
        status=400,
    )


@territorial_hub_access
@require_POST
def deactivate_mapping(request, zone):
    if not request.user.has_perm("kobo.manage_pastoral_zone_mappings"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    form = TerritorialReasonForm(request.POST)
    parsed_zone = _parse_selected_zone(zone)
    if form.is_valid() and parsed_zone is not None:
        result = deactivate_pastoral_zone_project_mapping(
            pastoral_zone=zone,
            actor=request.user,
            reason=form.cleaned_data["reason"],
        )
        message, level = _message_for_result(result, "Asignación quitada.")
        if _is_htmx_request(request):
            return _htmx_mapping_success(
                request, zone=parsed_zone, message=message, level=level
            )
        messages.add_message(request, level, message)
    elif _is_htmx_request(request) and parsed_zone is not None:
        return render(
            request,
            "kobo/hub/_mapping_modal.html",
            _mapping_modal_context(
                request,
                zone=parsed_zone,
                mode="change",
                reason_form=form if form.is_bound else TerritorialReasonForm(),
            ),
            status=400,
        )
    else:
        messages.error(request, "Debe indicar el motivo para quitar la asignación.")
    return redirect("kobo:mapping_list")


@territorial_hub_access
def identity_list(request):
    queryset = KoboTerritorialIdentity.objects.select_related("project", "source_submission").annotate(
        profile_count=Count("territorial_profiles", distinct=True),
        microproject_count=Count("prioritized_microprojects", distinct=True),
        assessment_count=Count("prioritization_assessments", distinct=True),
        pending_count=Count("project__kobo_submissions", filter=Q(project__kobo_submissions__routing_status="pending_identity"), distinct=True),
    )
    for field, lookup in (("zone", "pastoral_zone"), ("status", "status"), ("project", "project_id")):
        value = request.GET.get(field)
        if value:
            queryset = queryset.filter(**{lookup: value})
    if request.GET.get("q"):
        queryset = queryset.filter(nucleo_code_normalized__icontains=request.GET["q"].strip())
    if request.GET.get("conflicts") == "open":
        queryset = queryset.filter(conflicts__status="open")
    if request.GET.get("pending") == "1":
        queryset = queryset.filter(project__kobo_submissions__routing_status="pending_identity")
    context = _pagination_context(request, queryset.distinct().order_by("nucleo_code_normalized", "pk"))
    context.update(
        {
            "projects": Project.objects.order_by("code"),
            "zones": tuple(PastoralZone),
            "statuses": KoboTerritorialIdentity.Status,
            "hub_nav": "identities",
        }
    )
    return render(request, "kobo/hub/identity_list.html", context)


@territorial_hub_access
def identity_detail(request, pk):
    identity = get_object_or_404(
        KoboTerritorialIdentity.objects.select_related("project", "source_submission").prefetch_related(
            "territorial_profiles",
            "prioritized_microprojects",
            "prioritization_assessments",
            "conflicts__incoming_submission",
        ),
        pk=pk,
    )
    linked_submissions = KoboSubmission.objects.filter(
        nucleo_code_normalized=identity.nucleo_code_normalized
    ).select_related("project", "form_definition").order_by("-received_at", "-pk")
    events = KoboTerritorialAdministrationEvent.objects.filter(
        entity_id=identity.pk, entity_type=identity._meta.label
    ).select_related("actor")[:10]
    return render(
        request,
        "kobo/hub/identity_detail.html",
        {
            "identity": identity,
            "submissions": linked_submissions,
            "events": events,
            "pending_count": linked_submissions.filter(routing_status="pending_identity").count(),
            "error_count": linked_submissions.filter(routing_status="error").count(),
            "can_change": request.user.has_perm("kobo.change_territorial_identity_status"),
            "can_reconcile": request.user.has_perm("kobo.run_territorial_reconciliation"),
            "hub_nav": "identities",
        },
    )


@territorial_hub_access
@require_POST
def identity_status(request, pk, action):
    identity = get_object_or_404(KoboTerritorialIdentity, pk=pk)
    form = TerritorialReasonForm(request.POST)
    handlers = {
        "observe": observe_territorial_identity,
        "activate": activate_observed_territorial_identity,
        "deactivate": deactivate_territorial_identity,
    }
    if not request.user.has_perm("kobo.change_territorial_identity_status"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if action not in handlers or not form.is_valid():
        messages.error(request, "Debe indicar un motivo válido.")
    else:
        result = handlers[action](identity=identity, actor=request.user, reason=form.cleaned_data["reason"])
        message, level = _message_for_result(result, "Estado del núcleo actualizado.")
        messages.add_message(request, level, message)
    return redirect("kobo:identity_detail", pk=pk)


@territorial_hub_access
@require_POST
def reconcile_identity(request, pk):
    if not request.user.has_perm("kobo.run_territorial_reconciliation"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    identity = get_object_or_404(KoboTerritorialIdentity, pk=pk)
    result = reconcile_territorial_identity_submissions(identity=identity, actor=request.user)
    messages.info(
        request,
        (
            f"Reconciliación: resueltas {result.resolved}, pendientes {result.still_pending}, "
            f"casos {result.conflicts}, errores {result.errors}, omitidas {result.skipped}."
            f"{' Hay más por procesar.' if result.has_more else ''}"
        ),
    )
    return redirect("kobo:identity_detail", pk=pk)


@territorial_hub_access
def conflict_list(request):
    queryset = KoboTerritorialIdentityConflict.objects.select_related(
        "identity__project",
        "incoming_submission",
        "existing_project",
        "proposed_project",
    )
    status = request.GET.get("status")
    if status:
        queryset = queryset.filter(status=status)
    for field in ("existing_pastoral_zone", "proposed_pastoral_zone"):
        value = request.GET.get(field)
        if value:
            queryset = queryset.filter(**{field: value})
    if request.GET.get("project"):
        queryset = queryset.filter(
            Q(existing_project_id=request.GET["project"]) | Q(proposed_project_id=request.GET["project"])
        )
    review_categories = _review_categories()
    open_conflicts_count = next(
        (category["count"] for category in review_categories if category["key"] == "conflicts"),
        0,
    )
    context = _pagination_context(request, queryset.order_by("status", "-created_at", "-pk"))
    context.update(
        {
            "zones": tuple(PastoralZone),
            "projects": Project.objects.order_by("code"),
            "statuses": KoboTerritorialIdentityConflict.Status,
            "hub_nav": "cases",
            "review_categories": review_categories,
            "show_conflict_table": open_conflicts_count > 0 or bool(status),
        }
    )
    return render(request, "kobo/hub/conflict_list.html", context)


@territorial_hub_access
def conflict_detail(request, pk):
    conflict = get_object_or_404(
        KoboTerritorialIdentityConflict.objects.select_related(
            "identity__project",
            "incoming_submission",
            "existing_project",
            "proposed_project",
            "resolved_by",
        ),
        pk=pk,
    )
    return render(
        request,
        "kobo/hub/conflict_detail.html",
        {
            "conflict": conflict,
            "form": TerritorialConflictResolutionForm(),
            "can_resolve": request.user.has_perm("kobo.resolve_territorial_conflicts"),
            "can_accept": request.user.has_perm("kobo.run_territorial_reconciliation"),
            "hub_nav": "cases",
        },
    )


@territorial_hub_access
@require_POST
def resolve_conflict(request, pk):
    if not request.user.has_perm("kobo.resolve_territorial_conflicts"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    conflict = get_object_or_404(KoboTerritorialIdentityConflict, pk=pk)
    form = TerritorialConflictResolutionForm(request.POST)
    if form.is_valid():
        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=TerritorialConflictDecision(form.cleaned_data["decision"]),
            actor=request.user,
            reason=form.cleaned_data["reason"],
        )
        message, level = _message_for_result(result, "Caso resuelto.")
        messages.add_message(request, level, message)
    else:
        messages.error(request, "Revise la decisión y el motivo.")
    return redirect("kobo:conflict_detail", pk=pk)


@territorial_hub_access
def pending_submission_list(request):
    routing_status = request.GET.get("routing_status")
    status_filter = request.GET.get("status")
    remote_update_pending = request.GET.get("remote_update_pending")

    if routing_status in {
        KoboSubmission.RoutingStatus.PENDING_IDENTITY,
        KoboSubmission.RoutingStatus.CONFLICT,
        KoboSubmission.RoutingStatus.ERROR,
    }:
        queryset = KoboSubmission.objects.filter(routing_status=routing_status)
        list_mode = "routing"
        page_title = "Incidencias"
        page_intro = (
            "Formularios sin núcleo registrado, con conflicto de asignación o con errores."
        )
    elif status_filter == KoboSubmission.Status.PROCESSING_FAILED:
        queryset = KoboSubmission.objects.filter(
            status=KoboSubmission.Status.PROCESSING_FAILED
        )
        list_mode = "processing_errors"
        page_title = "Errores de procesamiento"
        page_intro = "Formularios que no completaron su procesamiento automático."
    elif status_filter == KoboSubmission.Status.VALIDATION_FAILED:
        queryset = KoboSubmission.objects.filter(
            status=KoboSubmission.Status.VALIDATION_FAILED
        )
        list_mode = "invalid_data"
        page_title = "Datos inválidos"
        page_intro = "Formularios con datos que no pasaron la validación automática."
    elif remote_update_pending == "1":
        queryset = KoboSubmission.objects.filter(remote_update_pending=True)
        list_mode = "remote_updates"
        page_title = "Actualizaciones remotas pendientes"
        page_intro = "Cambios recibidos desde KoboToolbox que requieren atención."
    else:
        queryset = incident_queryset()
        list_mode = "incidents"
        page_title = "Incidencias"
        page_intro = "Formularios que no pudieron procesarse automáticamente."

    if request.GET.get("reason_code"):
        queryset = queryset.filter(routing_reason_code=request.GET["reason_code"])
    if request.GET.get("ficha"):
        queryset = queryset.filter(form_definition__form_id=request.GET["ficha"])
    if request.GET.get("nucleo_code"):
        queryset = queryset.filter(
            nucleo_code_normalized__icontains=request.GET["nucleo_code"].strip()
        )
    if request.GET.get("date"):
        queryset = queryset.filter(received_at__date=request.GET["date"])

    queryset = queryset.select_related("form_definition", "project").order_by(
        "-received_at", "-pk"
    )
    identities = dict(KoboTerritorialIdentity.objects.values_list("nucleo_code_normalized", "pk"))
    conflicts = dict(
        KoboTerritorialIdentityConflict.objects.filter(status="open").values_list(
            "incoming_submission_id", "pk"
        )
    )
    context = _pagination_context(request, queryset)
    context.update(
        {
            "identities": identities,
            "conflicts": conflicts,
            "hub_nav": "cases",
            "list_mode": list_mode,
            "page_title": page_title,
            "page_intro": page_intro,
        }
    )
    return render(request, "kobo/hub/pending_submission_list.html", context)


@territorial_hub_access
@require_POST
def retry_submission_import(request, pk):
    """
    PRE: caller may read the hub and change submissions; pk identifies an incident.
    POST: retries automatic import and redirects to the submission detail.
    """
    if not request.user.has_perm("kobo.change_kobosubmission"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    submission = get_object_or_404(KoboSubmission, pk=pk)
    result = retry_auto_import(submission)
    if result.outcome == AutoImportOutcome.IMPORTED:
        messages.success(request, "Formulario importado automáticamente.")
    elif result.outcome == AutoImportOutcome.ALREADY_IMPORTED:
        messages.info(request, "El formulario ya estaba importado.")
    elif result.outcome == AutoImportOutcome.INCIDENT:
        label = INCIDENT_LABELS.get(result.incident_kind, "Incidencia")
        messages.warning(request, f"Sigue como incidencia: {label}.")
    else:
        messages.info(request, "No se pudo reintentar la importación automática.")
    return redirect("kobo:submission_detail", pk=pk)


def submission_incident_context(submission):
    """
    PRE: submission is a staged Kobo row loaded for detail presentation.
    POST: returns incident presentation fields for the operator detail screen.
    """
    if submission.status == KoboSubmission.Status.IMPORTED:
        import_record = None
        try:
            import_record = submission.import_record
        except KoboImportRecord.DoesNotExist:
            import_record = None
        return {
            "is_incident": False,
            "is_imported": True,
            "import_record": import_record,
            "incident_kind": None,
            "incident_label": "",
            "incident_action": "",
            "can_retry_import": False,
        }
    is_incident = incident_queryset(KoboSubmission.objects.filter(pk=submission.pk)).exists()
    if not is_incident:
        return {
            "is_incident": False,
            "is_imported": False,
            "import_record": None,
            "incident_kind": None,
            "incident_label": "",
            "incident_action": "",
            "can_retry_import": False,
        }
    kind = classify_incident(submission)
    return {
        "is_incident": True,
        "is_imported": False,
        "import_record": None,
        "incident_kind": kind,
        "incident_label": INCIDENT_LABELS.get(kind, "Incidencia"),
        "incident_action": INCIDENT_ACTIONS.get(kind, ""),
        "can_retry_import": kind in RETRYABLE_INCIDENT_KINDS,
    }
