"""Territorial Kobo operations hub. HTTP handlers delegate every mutation to services."""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

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
    KoboPastoralZoneProjectMapping,
    KoboSubmission,
    KoboSyncRun,
    KoboTerritorialAdministrationEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.client import build_kobo_api_client
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.presentation import (
    PASTORAL_ZONE_TOTAL,
    form_role_title,
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
FICHA_FORM_IDS = {
    "ficha_01": FICHA_01_FORM_ID,
    "ficha_10": FICHA_10_FORM_ID,
    "ficha_11": FICHA_11_FORM_ID,
}
FICHA_DISPLAY = {
    "ficha_01": ("Ficha 1", "Registro territorial"),
    "ficha_10": ("Ficha 10", "Microproyectos priorizados"),
    "ficha_11": ("Ficha 11", "Evaluación de prioridad"),
}
VALID_ZONE_VALUES = {zone.value for zone in PastoralZone}


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


def pending_review_queryset(base_queryset=None):
    """
    PRE: base_queryset is None or a KoboSubmission queryset.
    POST: returns the single shared criterion for forms pending human review.
    """
    queryset = base_queryset if base_queryset is not None else KoboSubmission.objects.all()
    return queryset.filter(status=KoboSubmission.Status.READY_FOR_REVIEW)


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


def _build_next_actions(
    *,
    missing_mappings,
    pending_review,
    open_conflicts,
    routing_errors,
    processing_errors,
    pending_identity,
    remote_updates_pending,
):
    # PRE: counts and missing zones come from dashboard aggregations.
    # POST: returns ordered actionable next steps without repeating dashboard metrics.
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
    if pending_review:
        items.append(
            {
                "title": "Formularios pendientes de revisión",
                "description": "Comprueba la información antes de importarla al sistema.",
                "count": pending_review,
                "url": reverse("kobo:pending_submission_list"),
            }
        )
    errors_or_conflicts = open_conflicts + routing_errors + processing_errors + pending_identity
    if errors_or_conflicts:
        items.append(
            {
                "title": "Errores o conflictos",
                "description": "Hay incidencias de asignación o procesamiento que requieren decisión.",
                "count": errors_or_conflicts,
                "url": reverse("kobo:conflict_list"),
            }
        )
    if remote_updates_pending:
        items.append(
            {
                "title": "Actualización remota pendiente",
                "description": "Hay cambios recibidos desde KoboToolbox pendientes de revisión.",
                "count": remote_updates_pending,
                "url": reverse("kobo:pending_submission_list") + "?remote_update_pending=1",
            }
        )
    return items


def _form_receipt_rows(by_form, form_status_rows):
    # PRE: by_form and form_status_rows come from grouped submission aggregations.
    # POST: returns one stable row per supported ficha with operator-facing totals.
    status_index = {}
    for row in form_status_rows:
        form_id = row["form_definition__form_id"]
        bucket = status_index.setdefault(
            form_id, {"pending": 0, "imported": 0, "errors": 0}
        )
        status = row["status"]
        total = row["total"]
        if status == KoboSubmission.Status.READY_FOR_REVIEW:
            bucket["pending"] += total
        elif status == KoboSubmission.Status.IMPORTED:
            bucket["imported"] += total
        elif status in (
            KoboSubmission.Status.PROCESSING_FAILED,
            KoboSubmission.Status.VALIDATION_FAILED,
        ):
            bucket["errors"] += total
    rows = []
    for key, form_id in FICHA_FORM_IDS.items():
        title, subtitle = FICHA_DISPLAY[key]
        detail = status_index.get(form_id, {"pending": 0, "imported": 0, "errors": 0})
        rows.append(
            {
                "key": key,
                "title": title,
                "subtitle": subtitle,
                "total": by_form.get(form_id, 0),
                "pending": detail["pending"],
                "imported": detail["imported"],
                "errors": detail["errors"],
            }
        )
    return rows


def _latest_sync_by_ficha(sync_assets):
    # PRE: sync_assets lists active hub assets in display order.
    # POST: returns at most one latest submissions sync per ficha without duplicates.
    if not sync_assets:
        return []
    asset_ids = [row["asset"].pk for row in sync_assets]
    runs = (
        KoboSyncRun.objects.filter(
            kind=KoboSyncRun.Kind.SUBMISSIONS,
            asset_id__in=asset_ids,
        )
        .select_related("asset")
        .order_by("asset_id", "-started_at", "-pk")
    )
    latest_by_asset = {}
    for run in runs:
        if run.asset_id not in latest_by_asset:
            latest_by_asset[run.asset_id] = run
    rows = []
    for row in sync_assets:
        run = latest_by_asset.get(row["asset"].pk)
        if run is None:
            continue
        rows.append(
            {
                "title": row["title"],
                "subtitle": row["subtitle"],
                "run": run,
            }
        )
    return rows


def _parse_selected_zone(raw_zone):
    # PRE: raw_zone comes from an optional GET parameter.
    # POST: returns a PastoralZone when valid; otherwise None without mutating state.
    if not raw_zone or raw_zone not in VALID_ZONE_VALUES:
        return None
    return PastoralZone(raw_zone)


def _review_categories():
    # PRE: hub readers may inspect review queues.
    # POST: returns category cards using shared count criteria and functional list links.
    submissions = KoboSubmission.objects.all()
    pending_review = pending_review_queryset(submissions).count()
    return [
        {
            "key": "pending_review",
            "title": "Formularios pendientes de revisión",
            "description": "Comprueba la información antes de importarla al sistema.",
            "empty_meaning": "No hay formularios listos para revisión humana.",
            "empty_action": "Sincronice formularios o espere nuevos envíos desde KoboToolbox.",
            "count": pending_review,
            "url": reverse("kobo:pending_submission_list"),
        },
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
            "empty_action": "Si un formulario falla al procesarse, aparecerá aquí para revisión.",
            "count": submissions.filter(
                status=KoboSubmission.Status.PROCESSING_FAILED
            ).count(),
            "url": reverse("kobo:pending_submission_list")
            + f"?status={KoboSubmission.Status.PROCESSING_FAILED}",
        },
        {
            "key": "remote_updates",
            "title": "Actualizaciones remotas pendientes",
            "description": "Cambios recibidos desde KoboToolbox que requieren revisión humana.",
            "empty_meaning": "No hay actualizaciones remotas pendientes.",
            "empty_action": "Tras una sincronización con cambios, revise esta categoría.",
            "count": submissions.filter(remote_update_pending=True).count(),
            "url": reverse("kobo:pending_submission_list") + "?remote_update_pending=1",
        },
    ]


@territorial_hub_access
def hub_dashboard(request):
    # PRE: Kobo is enabled and the caller may read territorial administration.
    # POST: renders a reduced operational resumen without redundant tables.
    submissions = _project_filter(request, KoboSubmission.objects.all())
    routing_counts = submissions.values("routing_status").annotate(total=Count("pk"))
    by_routing = {row["routing_status"]: row["total"] for row in routing_counts}
    form_counts = submissions.values("form_definition__form_id").annotate(total=Count("pk"))
    by_form = {row["form_definition__form_id"]: row["total"] for row in form_counts}
    form_status_rows = list(
        submissions.values("form_definition__form_id", "status").annotate(total=Count("pk"))
    )
    mappings = {
        item.pastoral_zone: item
        for item in KoboPastoralZoneProjectMapping.objects.filter(is_active=True).select_related(
            "project"
        )
    }
    mapped_zones = set(mappings)
    missing_mappings = [zone for zone in PastoralZone if zone.value not in mapped_zones]
    pending_review = pending_review_queryset(submissions).count()
    pending_identity = by_routing.get(KoboSubmission.RoutingStatus.PENDING_IDENTITY, 0)
    routing_errors = by_routing.get(KoboSubmission.RoutingStatus.ERROR, 0)
    open_conflicts = KoboTerritorialIdentityConflict.objects.filter(status="open").count()
    processing_errors = submissions.filter(
        status=KoboSubmission.Status.PROCESSING_FAILED
    ).count()
    remote_updates_pending = submissions.filter(remote_update_pending=True).count()
    identity_count = KoboTerritorialIdentity.objects.count()
    sync_assets = []
    for asset in KoboAsset.objects.filter(is_active=True).order_by("name", "asset_uid"):
        title, subtitle = form_role_title(asset.form_role)
        sync_assets.append(
            {
                "asset": asset,
                "title": title,
                "subtitle": subtitle,
            }
        )
    context = {
        "mapping_count": len(mapped_zones),
        "zone_total": PASTORAL_ZONE_TOTAL,
        "identity_count": identity_count,
        "pending_review": pending_review,
        "form_receipt_rows": _form_receipt_rows(by_form, form_status_rows),
        "missing_mappings": missing_mappings,
        "missing_mappings_label": spanish_join(
            [pastoral_zone_label(zone) for zone in missing_mappings]
        ),
        "next_actions": _build_next_actions(
            missing_mappings=missing_mappings,
            pending_review=pending_review,
            open_conflicts=open_conflicts,
            routing_errors=routing_errors,
            processing_errors=processing_errors,
            pending_identity=pending_identity,
            remote_updates_pending=remote_updates_pending,
        ),
        "kobo_configuration_complete": bool(settings.KOBO_BASE_URL and settings.KOBO_API_TOKEN),
        "latest_sync_rows": _latest_sync_by_ficha(sync_assets),
        "sync_assets": sync_assets,
        "can_sync": request.user.has_perm("kobo.change_koboasset"),
        "hub_nav": "summary",
    }
    return render(request, "kobo/hub/dashboard.html", context)


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
def mapping_list(request):
    mappings = {
        item.pastoral_zone: item
        for item in KoboPastoralZoneProjectMapping.objects.filter(is_active=True).select_related("project")
    }
    identity_counts = dict(
        KoboTerritorialIdentity.objects.values("pastoral_zone").annotate(total=Count("pk")).values_list("pastoral_zone", "total")
    )
    rows = [
        {"zone": zone, "mapping": mappings.get(zone.value), "identity_count": identity_counts.get(zone.value, 0)}
        for zone in PastoralZone
    ]
    selected_zone = _parse_selected_zone(request.GET.get("zone"))
    form_kwargs = {}
    if selected_zone is not None:
        form_kwargs["initial"] = {"pastoral_zone": selected_zone.value}
    return render(
        request,
        "kobo/hub/mapping_list.html",
        {
            "rows": rows,
            "form": PastoralZoneProjectMappingForm(**form_kwargs),
            "selected_zone": selected_zone,
            "selected_zone_label": pastoral_zone_label(selected_zone) if selected_zone else "",
            "can_manage": request.user.has_perm("kobo.manage_pastoral_zone_mappings"),
            "hub_nav": "mappings",
            "missing_count": sum(1 for row in rows if row["mapping"] is None),
        },
    )


@territorial_hub_access
@require_POST
def configure_mapping(request):
    if not request.user.has_perm("kobo.manage_pastoral_zone_mappings"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    form = PastoralZoneProjectMappingForm(request.POST)
    if form.is_valid():
        result = configure_pastoral_zone_project_mapping(actor=request.user, **form.cleaned_data)
        message, level = _message_for_result(result, "Asignación guardada.")
        messages.add_message(request, level, message)
    else:
        messages.error(request, "Revise los datos de la asignación.")
    return redirect("kobo:mapping_list")


@territorial_hub_access
@require_POST
def deactivate_mapping(request, zone):
    if not request.user.has_perm("kobo.manage_pastoral_zone_mappings"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    form = TerritorialReasonForm(request.POST)
    if form.is_valid():
        result = deactivate_pastoral_zone_project_mapping(pastoral_zone=zone, actor=request.user, reason=form.cleaned_data["reason"])
        message, level = _message_for_result(result, "Asignación desactivada.")
        messages.add_message(request, level, message)
    else:
        messages.error(request, "Debe indicar el motivo de desactivación.")
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
        page_title = "Formularios por revisar"
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
    elif remote_update_pending == "1":
        queryset = KoboSubmission.objects.filter(remote_update_pending=True)
        list_mode = "remote_updates"
        page_title = "Actualizaciones remotas pendientes"
        page_intro = "Cambios recibidos desde KoboToolbox que requieren revisión humana."
    else:
        queryset = pending_review_queryset()
        list_mode = "pending_review"
        page_title = "Formularios pendientes de revisión"
        page_intro = "Formularios listos para comprobación humana antes de importarlos."

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
