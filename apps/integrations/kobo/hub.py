"""Territorial Kobo operations hub. HTTP handlers delegate every mutation to services."""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
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
    KoboImportRecord,
    KoboPastoralZoneProjectMapping,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboSubmission,
    KoboTerritorialAdministrationEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
)
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.services import (
    activate_observed_territorial_identity,
    configure_pastoral_zone_project_mapping,
    deactivate_pastoral_zone_project_mapping,
    deactivate_territorial_identity,
    observe_territorial_identity,
    reconcile_territorial_identity_submissions,
    resolve_territorial_identity_conflict,
)
from apps.operations.models import Project


HUB_READ_PERMISSION = "kobo.view_territorial_administration"
PAGE_SIZE = 25
REASON_MESSAGES = {
    "zone_mapping_in_use": "La zona ya tiene identidades territoriales en uso.",
    "territorial_identity_already_used": "La identidad tiene historial importado o resuelto y no puede reasignarse.",
    "already_resolved": "El conflicto ya fue resuelto.",
    "invalid_identity_transition": "La transición de estado no está permitida.",
    "permission_denied": "No posee el permiso requerido para esta operación.",
    "reason_required": "Debe indicar un motivo.",
    "project_not_available": "El proyecto no está disponible para el mapping.",
    "proposed_mapping_not_available": "No existe un mapping activo para la propuesta.",
}


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


@territorial_hub_access
def hub_dashboard(request):
    # PRE: Kobo is enabled and the caller may read territorial administration.
    # POST: renders aggregate operational state without loading submissions into Python.
    submissions = _project_filter(request, KoboSubmission.objects.all())
    routing_counts = submissions.values("routing_status").annotate(total=Count("pk"))
    by_routing = {row["routing_status"]: row["total"] for row in routing_counts}
    form_counts = submissions.values("form_definition__form_id").annotate(total=Count("pk"))
    by_form = {row["form_definition__form_id"]: row["total"] for row in form_counts}
    zone_counts = KoboTerritorialIdentity.objects.values("pastoral_zone").annotate(total=Count("pk"))
    identity_zones = {row["pastoral_zone"]: row["total"] for row in zone_counts}
    mappings = KoboPastoralZoneProjectMapping.objects.filter(is_active=True)
    mapped_zones = set(mappings.values_list("pastoral_zone", flat=True))
    context = {
        "mapping_count": len(mapped_zones),
        "zone_rows": [
            {"code": zone.value, "label": zone.value.replace("_", " ").title(), "identities": identity_zones.get(zone.value, 0), "mapped": zone.value in mapped_zones}
            for zone in PastoralZone
        ],
        "identity_count": KoboTerritorialIdentity.objects.count(),
        "open_conflicts": KoboTerritorialIdentityConflict.objects.filter(status="open").count(),
        "pending_identity": by_routing.get(KoboSubmission.RoutingStatus.PENDING_IDENTITY, 0),
        "routing_errors": by_routing.get(KoboSubmission.RoutingStatus.ERROR, 0),
        "pending_review": submissions.filter(status=KoboSubmission.Status.READY_FOR_REVIEW).count(),
        "imported": submissions.filter(status=KoboSubmission.Status.IMPORTED).count(),
        "form_counts": {
            "ficha_01": by_form.get(FICHA_01_FORM_ID, 0),
            "ficha_10": by_form.get(FICHA_10_FORM_ID, 0),
            "ficha_11": by_form.get(FICHA_11_FORM_ID, 0),
        },
        "missing_mappings": [zone for zone in PastoralZone if zone.value not in mapped_zones],
        "kobo_configuration_complete": bool(settings.KOBO_BASE_URL and settings.KOBO_API_TOKEN),
        "projects": Project.objects.order_by("code", "pk"),
    }
    return render(request, "kobo/hub/dashboard.html", context)


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
    return render(request, "kobo/hub/mapping_list.html", {"rows": rows, "form": PastoralZoneProjectMappingForm(), "can_manage": request.user.has_perm("kobo.manage_pastoral_zone_mappings")})


@territorial_hub_access
@require_POST
def configure_mapping(request):
    if not request.user.has_perm("kobo.manage_pastoral_zone_mappings"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    form = PastoralZoneProjectMappingForm(request.POST)
    if form.is_valid():
        result = configure_pastoral_zone_project_mapping(actor=request.user, **form.cleaned_data)
        message, level = _message_for_result(result, "Mapping configurado.")
        messages.add_message(request, level, message)
    else:
        messages.error(request, "Revise los datos del mapping.")
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
        message, level = _message_for_result(result, "Mapping desactivado.")
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
    context.update({"projects": Project.objects.order_by("code"), "zones": PastoralZone, "statuses": KoboTerritorialIdentity.Status})
    return render(request, "kobo/hub/identity_list.html", context)


@territorial_hub_access
def identity_detail(request, pk):
    identity = get_object_or_404(KoboTerritorialIdentity.objects.select_related("project", "source_submission").prefetch_related("territorial_profiles", "prioritized_microprojects", "prioritization_assessments", "conflicts__incoming_submission"), pk=pk)
    linked_submissions = KoboSubmission.objects.filter(nucleo_code_normalized=identity.nucleo_code_normalized).select_related("project", "form_definition").order_by("-received_at", "-pk")
    events = KoboTerritorialAdministrationEvent.objects.filter(entity_id=identity.pk, entity_type=identity._meta.label).select_related("actor")[:10]
    return render(request, "kobo/hub/identity_detail.html", {"identity": identity, "submissions": linked_submissions, "events": events, "pending_count": linked_submissions.filter(routing_status="pending_identity").count(), "error_count": linked_submissions.filter(routing_status="error").count(), "can_change": request.user.has_perm("kobo.change_territorial_identity_status"), "can_reconcile": request.user.has_perm("kobo.run_territorial_reconciliation")})


@territorial_hub_access
@require_POST
def identity_status(request, pk, action):
    identity = get_object_or_404(KoboTerritorialIdentity, pk=pk)
    form = TerritorialReasonForm(request.POST)
    handlers = {"observe": observe_territorial_identity, "activate": activate_observed_territorial_identity, "deactivate": deactivate_territorial_identity}
    if not request.user.has_perm("kobo.change_territorial_identity_status"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if action not in handlers or not form.is_valid():
        messages.error(request, "Debe indicar un motivo válido.")
    else:
        result = handlers[action](identity=identity, actor=request.user, reason=form.cleaned_data["reason"])
        message, level = _message_for_result(result, "Estado de identidad actualizado.")
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
    messages.info(request, f"Reconciliación: resueltas {result.resolved}, pendientes {result.still_pending}, conflictos {result.conflicts}, errores {result.errors}, omitidas {result.skipped}.{' Hay más por procesar.' if result.has_more else ''}")
    return redirect("kobo:identity_detail", pk=pk)


@territorial_hub_access
def conflict_list(request):
    queryset = KoboTerritorialIdentityConflict.objects.select_related("identity__project", "incoming_submission", "existing_project", "proposed_project")
    status = request.GET.get("status")
    if status:
        queryset = queryset.filter(status=status)
    for field in ("existing_pastoral_zone", "proposed_pastoral_zone"):
        value = request.GET.get(field)
        if value:
            queryset = queryset.filter(**{field: value})
    if request.GET.get("project"):
        queryset = queryset.filter(Q(existing_project_id=request.GET["project"]) | Q(proposed_project_id=request.GET["project"]))
    context = _pagination_context(request, queryset.order_by("status", "-created_at", "-pk"))
    context.update({"zones": PastoralZone, "projects": Project.objects.order_by("code"), "statuses": KoboTerritorialIdentityConflict.Status})
    return render(request, "kobo/hub/conflict_list.html", context)


@territorial_hub_access
def conflict_detail(request, pk):
    conflict = get_object_or_404(KoboTerritorialIdentityConflict.objects.select_related("identity__project", "incoming_submission", "existing_project", "proposed_project", "resolved_by"), pk=pk)
    return render(request, "kobo/hub/conflict_detail.html", {"conflict": conflict, "form": TerritorialConflictResolutionForm(), "can_resolve": request.user.has_perm("kobo.resolve_territorial_conflicts"), "can_accept": request.user.has_perm("kobo.run_territorial_reconciliation")})


@territorial_hub_access
@require_POST
def resolve_conflict(request, pk):
    if not request.user.has_perm("kobo.resolve_territorial_conflicts"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    conflict = get_object_or_404(KoboTerritorialIdentityConflict, pk=pk)
    form = TerritorialConflictResolutionForm(request.POST)
    if form.is_valid():
        result = resolve_territorial_identity_conflict(conflict=conflict, decision=TerritorialConflictDecision(form.cleaned_data["decision"]), actor=request.user, reason=form.cleaned_data["reason"])
        message, level = _message_for_result(result, "Conflicto resuelto.")
        messages.add_message(request, level, message)
    else:
        messages.error(request, "Revise la decisión y el motivo.")
    return redirect("kobo:conflict_detail", pk=pk)


@territorial_hub_access
def pending_submission_list(request):
    queryset = KoboSubmission.objects.filter(routing_status__in=("pending_identity", "conflict", "error")).select_related("form_definition", "project").order_by("-received_at", "-pk")
    for field, lookup in (("routing_status", "routing_status"), ("reason_code", "routing_reason_code")):
        value = request.GET.get(field)
        if value:
            queryset = queryset.filter(**{lookup: value})
    if request.GET.get("ficha"):
        queryset = queryset.filter(form_definition__form_id=request.GET["ficha"])
    if request.GET.get("nucleo_code"):
        queryset = queryset.filter(nucleo_code_normalized__icontains=request.GET["nucleo_code"].strip())
    if request.GET.get("date"):
        queryset = queryset.filter(received_at__date=request.GET["date"])
    identities = dict(KoboTerritorialIdentity.objects.values_list("nucleo_code_normalized", "pk"))
    conflicts = dict(KoboTerritorialIdentityConflict.objects.filter(status="open").values_list("incoming_submission_id", "pk"))
    context = _pagination_context(request, queryset)
    context.update({"identities": identities, "conflicts": conflicts})
    return render(request, "kobo/hub/pending_submission_list.html", context)
