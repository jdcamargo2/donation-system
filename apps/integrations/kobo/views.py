import base64
import binascii
import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.storage import default_storage
from django.core.exceptions import PermissionDenied, RequestDataTooBig, ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from apps.integrations.kobo.client import build_kobo_api_client
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.forms import (
    KoboAssetConfigurationForm,
    KoboReviewForm,
    KoboRejectionForm,
    get_compatible_asset_configuration,
)
from apps.integrations.kobo.mappings.ficha_10 import (
    FICHA_10_FORM_ID,
    FICHA_10_VERSION,
)
from apps.integrations.kobo.mappings.ficha_11 import (
    FICHA_11_FORM_ID,
    FICHA_11_VERSION,
)
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboAttachment,
    KoboDiscoveredAsset,
    KoboSubmission,
)
from apps.integrations.kobo.processors import (
    process_submission,
    process_submission_attachments,
)
from apps.integrations.kobo.services import (
    REJECTION_REASON_LABELS,
    activate_kobo_asset,
    configure_discovered_asset,
    converge_webhook_submission,
    get_project_pending_submissions,
    get_project_submission_history,
    import_kobo_submission,
    review_submission,
    reject_kobo_submission,
    receive_webhook_submission,
    restore_kobo_submission_to_review,
    route_normalized_submission,
)
from apps.integrations.kobo.hub import (
    conflict_detail,
    conflict_list,
    configure_mapping,
    dashboard_status,
    deactivate_mapping,
    hub_dashboard,
    identity_detail,
    identity_list,
    identity_status,
    mapping_list,
    mapping_modal,
    pending_submission_list,
    reconcile_identity,
    resolve_conflict,
    retry_submission_import,
    submission_incident_context,
    sync_all,
    sync_asset,
    sync_history,
)
from apps.integrations.kobo.submission_presentation import (
    attachment_status_label,
    form_identity,
    present_contact_fields,
    present_processing_events,
    present_submission_fields,
    should_show_retry_attachments,
    should_show_retry_normalization,
    submission_status_label,
    territorial_summary_rows,
)


WEBHOOK_BASIC_REALM = "SIGEDON Kobo Webhook"


def _has_valid_webhook_credentials(request) -> bool:
    """
    PRE: request is an HTTP request directed to the Kobo webhook.
    POST: returns True only for non-empty configured Basic credentials or the
    legacy secret header, without logging or raising for malformed input.
    """
    configured_username = settings.KOBO_WEBHOOK_USERNAME
    configured_secret = settings.KOBO_WEBHOOK_SECRET
    if not configured_secret:
        return False

    supplied_secret = request.headers.get("X-Kobo-Webhook-Secret", "")
    if supplied_secret and secrets.compare_digest(supplied_secret, configured_secret):
        return True

    authorization = request.headers.get("Authorization", "")
    try:
        scheme, encoded_credentials = authorization.split(None, 1)
        if scheme.casefold() != "basic":
            return False
        decoded_credentials = base64.b64decode(
            encoded_credentials, validate=True
        ).decode("utf-8")
        supplied_username, supplied_password = decoded_credentials.split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False

    return bool(
        configured_username
        and supplied_username
        and supplied_password
        and secrets.compare_digest(supplied_username, configured_username)
        and secrets.compare_digest(supplied_password, configured_secret)
    )


def _webhook_unauthorized_response():
    # PRE: webhook authentication did not validate.
    # POST: returns the Basic challenge without creating or processing staging data.
    return JsonResponse(
        {"ok": False, "error": "unauthorized"},
        status=401,
        headers={"WWW-Authenticate": f'Basic realm="{WEBHOOK_BASIC_REALM}"'},
    )


def _webhook_payload_too_large_response():
    # PRE: request payload exceeded the configured webhook boundary.
    # POST: returns a generic 413 response without staging request data.
    return JsonResponse({"ok": False, "error": "payload_too_large"}, status=413)


def _declared_webhook_payload_exceeds_limit(request) -> bool:
    # PRE: request is an authenticated webhook request and the limit is positive.
    # POST: returns False for absent or malformed Content-Length without raising.
    try:
        content_length = int(request.META.get("CONTENT_LENGTH", ""))
    except (TypeError, ValueError):
        return False
    return content_length > settings.KOBO_WEBHOOK_MAX_BYTES


@csrf_exempt
@require_POST
def webhook_submission(request):
    # PRE: Kobo POSTs JSON with configured Basic credentials or legacy secret.
    # POST: safely stages and processes one configured asset submission idempotently.
    if not settings.KOBO_ENABLED:
        raise Http404
    if not _has_valid_webhook_credentials(request):
        return _webhook_unauthorized_response()
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    if _declared_webhook_payload_exceeds_limit(request):
        return _webhook_payload_too_large_response()
    try:
        body = request.body
    except RequestDataTooBig:
        return _webhook_payload_too_large_response()
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    if len(body) > settings.KOBO_WEBHOOK_MAX_BYTES:
        return _webhook_payload_too_large_response()
    try:
        raw_payload = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    if not isinstance(raw_payload, dict):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    asset_uid = raw_payload.get("_xform_id_string")
    if not isinstance(asset_uid, str) or not asset_uid:
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    try:
        asset = KoboAsset.objects.select_related("form_definition").get(asset_uid=asset_uid)
        submission, created = receive_webhook_submission(asset=asset, raw_payload=raw_payload)
    except (KoboAsset.DoesNotExist, KoboPayloadError):
        return JsonResponse({"ok": False, "error": "invalid_submission"}, status=400)
    try:
        convergence = converge_webhook_submission(
            submission.pk,
            default_timezone=timezone.get_current_timezone(),
        )
    except Exception:
        return JsonResponse({"ok": False, "error": "internal_error"}, status=500)
    durable_statuses = {
        KoboSubmission.Status.READY_FOR_REVIEW,
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
        KoboSubmission.Status.IMPORTED,
        KoboSubmission.Status.VALIDATION_FAILED,
        KoboSubmission.Status.PROCESSING_FAILED,
        KoboSubmission.Status.REJECTED,
        KoboSubmission.Status.DUPLICATE,
        KoboSubmission.Status.PARTIALLY_IMPORTED,
    }
    if convergence.completed or convergence.final_status in durable_statuses:
        return JsonResponse(
            {
                "ok": True,
                "created": created,
                "submission_id": convergence.submission_id,
                "status": convergence.final_status,
            },
            status=201 if created else 200,
        )
    # Still incomplete (e.g. RECEIVED/NORMALIZED): ask Kobo to retry.
    return JsonResponse(
        {
            "ok": False,
            "error": "processing_incomplete",
            "submission_id": convergence.submission_id,
            "status": convergence.final_status,
        },
        status=422,
    )


def _require_kobo_enabled() -> None:
    # PRE: a browser request targets an internal Kobo surface.
    # POST: returns only while the feature is enabled; otherwise hides the surface.
    if not settings.KOBO_ENABLED:
        raise Http404


def _local_asset_state(asset: KoboAsset | None) -> str:
    # PRE: asset is the optional configured counterpart of a discovered asset.
    # POST: returns one explicit local lifecycle state without database mutation.
    if asset is None:
        return "unconfigured"
    return "active" if asset.is_active else "configured_inactive"


def _asset_configuration_context(asset):
    # PRE: asset is loaded with its supported form definition.
    # POST: returns asset-only configuration context without exposing legacy bindings.
    return {"asset": asset}


def _submission_queryset():
    # PRE: Kobo staging models are migrated.
    # POST: returns review data with relations prefetched and no state changes.
    return KoboSubmission.objects.select_related(
        "form_definition",
        "asset",
        "project",
    ).prefetch_related(
        "attachments",
        "processing_events",
    )


def _can_view_raw_payload(user) -> bool:
    # PRE: user is an authenticated request user.
    # POST: grants raw access only through existing elevated permission/superuser.
    return user.is_superuser or user.has_perm("kobo.change_kobosubmission")


def _can_view_sensitive_kobo_data(user) -> bool:
    # PRE: user is an authenticated request user.
    # POST: returns elevated Kobo review authorization without side effects.
    return user.is_superuser or user.has_perm("kobo.change_kobosubmission")


MICROPROJECT_CHOICE_LABELS = {
    "component": {
        "infrastructure": "Infraestructura",
        "health_psychosocial": "Salud y atención psicosocial",
        "training": "Formación",
        "livelihoods": "Medios de vida",
        "communication": "Comunicación",
        "mixed": "Mixto",
    },
    "beneficiary_group": {
        "youth": "Jóvenes",
        "women": "Mujeres",
        "adults": "Adultos",
        "unemployed": "Personas desempleadas",
        "entrepreneurs": "Emprendedores",
        "parish_volunteers": "Voluntariado parroquial",
        "mixed": "Mixto",
        "other": "Otro",
    },
    "estimated_cost_range": {
        "under_1000": "Menos de USD 1.000",
        "1000_5000": "USD 1.000 a 5.000",
        "5000_15000": "USD 5.000 a 15.000",
        "15000_50000": "USD 15.000 a 50.000",
        "over_50000": "Más de USD 50.000",
        "unknown": "Por determinar",
    },
    "implementation_urgency": {
        "immediate": "Inmediata",
        "short_term": "Corto plazo",
        "medium_term": "Mediano plazo",
        "follow_up": "Seguimiento",
        "unknown": "Por determinar",
    },
    "technical_viability": {
        "high": "Alta",
        "medium": "Media",
        "low": "Baja",
        "requires_design": "Requiere diseño",
        "not_viable": "No viable",
    },
}


def _project_submission_detail_rows(submission):
    # PRE: submission is an imported Kobo record with normalized payload data.
    # POST: returns labelled Ficha 10/11 fields only; territorial forms return none.
    payload = submission.normalized_payload or {}
    if (
        submission.form_definition.form_id == FICHA_11_FORM_ID
        and submission.form_definition.version == FICHA_11_VERSION
    ):
        return (
            ("Código del Núcleo Vital / comunidad", payload.get("nucleo_code")),
            ("Nivel de daño físico", payload.get("physical_damage_score")),
            ("Familias afectadas", payload.get("affected_families_score")),
            ("Vulnerabilidad social", payload.get("social_vulnerability_score")),
            ("Interrupción de servicios básicos", payload.get("services_interruption_score")),
            ("Pérdida de medios de vida", payload.get("livelihood_loss_score")),
            ("Capacidad parroquial disponible", payload.get("parish_capacity_score")),
            ("Accesibilidad territorial", payload.get("territorial_accessibility_score")),
            ("Existencia de aliados", payload.get("allies_availability_score")),
            ("Potencial de impacto rápido", payload.get("rapid_impact_score")),
            ("Viabilidad financiera", payload.get("financial_viability_score")),
            ("Puntaje total", payload.get("priority_total")),
            ("Semáforo sugerido", payload.get("suggested_semaphore")),
            ("Semáforo final validado", payload.get("final_semaphore")),
            ("Prioridad final de intervención", payload.get("final_priority")),
            ("Síntesis de decisión", payload.get("priority_summary")),
            ("Microproyectos vinculados", payload.get("linked_microprojects")),
        )
    if (
        submission.form_definition.form_id != FICHA_10_FORM_ID
        or submission.form_definition.version != FICHA_10_VERSION
    ):
        return ()
    beneficiary_groups = payload.get("beneficiary_group", ())
    beneficiary_labels = ", ".join(
        MICROPROJECT_CHOICE_LABELS["beneficiary_group"].get(value, value)
        for value in beneficiary_groups
    )
    return (
        ("Código del Núcleo Vital", payload.get("nucleo_code")),
        ("Nombre del microproyecto", payload.get("microproject_name")),
        (
            "Componente principal",
            MICROPROJECT_CHOICE_LABELS["component"].get(
                payload.get("component"), payload.get("component")
            ),
        ),
        ("Problema que atiende", payload.get("problem_summary")),
        ("Objetivo específico", payload.get("specific_objective")),
        ("Población beneficiaria principal", beneficiary_labels),
        ("Actividades principales", payload.get("main_activities")),
        (
            "Rango de costo estimado",
            MICROPROJECT_CHOICE_LABELS["estimated_cost_range"].get(
                payload.get("estimated_cost_range"),
                payload.get("estimated_cost_range"),
            ),
        ),
        (
            "Urgencia de implementación",
            MICROPROJECT_CHOICE_LABELS["implementation_urgency"].get(
                payload.get("implementation_urgency"),
                payload.get("implementation_urgency"),
            ),
        ),
        (
            "Viabilidad técnica inicial",
            MICROPROJECT_CHOICE_LABELS["technical_viability"].get(
                payload.get("technical_viability"), payload.get("technical_viability")
            ),
        ),
        ("Resultado esperado verificable", payload.get("expected_result")),
    )


def _project_submission_detail_title(submission) -> str:
    # PRE: submission has its form definition loaded.
    # POST: returns a form-specific internal detail title without exposing metadata.
    if (
        submission.form_definition.form_id == FICHA_11_FORM_ID
        and submission.form_definition.version == FICHA_11_VERSION
    ):
        return "Matriz de priorización y semáforo"
    if (
        submission.form_definition.form_id == FICHA_10_FORM_ID
        and submission.form_definition.version == FICHA_10_VERSION
    ):
        return "Microproyecto priorizado"
    return "Proyecto y territorio"


def _project_pending_submission_summary(submission) -> str:
    """
    PRE: submission is normalized and belongs to a supported Kobo form role.
    POST: returns a concise non-sensitive summary for an internal review list.
    """
    payload = submission.normalized_payload or {}
    if submission.asset.form_role == KoboAsset.FormRole.PRIORITIZED_MICROPROJECT:
        return payload.get("microproject_name") or "Microproyecto sin nombre"
    if submission.asset.form_role == KoboAsset.FormRole.PRIORITIZATION_MATRIX:
        priority = payload.get("final_priority") or "sin prioridad final"
        return f"Prioridad {priority}"
    return (
        payload.get("communities_covered")
        or submission.primary_community
        or "Sin resumen territorial"
    )


def _project_pending_submission_rows(project):
    """
    PRE: project is persisted and Kobo is enabled for its internal detail.
    POST: returns display rows for exactly that project's pending submissions.
    """
    return tuple(
        {
            "submission": submission,
            "summary": _project_pending_submission_summary(submission),
        }
        for submission in get_project_pending_submissions(project)
    )


def _project_submission_history_rows(project):
    """
    PRE: project is persisted and the internal history is being displayed.
    POST: returns historical decision rows with actor and rejection event data.
    """
    from django.utils.text import capfirst

    from apps.operations.models import AuditLog

    submissions = tuple(get_project_submission_history(project))
    audit_logs = AuditLog.objects.filter(
        model_name=capfirst(KoboSubmission._meta.verbose_name),
        entity_id__in=[str(submission.pk) for submission in submissions],
        summary__in=("Ficha Kobo importada al proyecto.", "Ficha Kobo rechazada."),
    ).select_related("user")
    audit_by_submission = {
        audit.entity_id: audit for audit in audit_logs.order_by("-created_at", "-pk")
    }
    rows = []
    for submission in submissions:
        rejection_event = next(
            (
                event
                for event in reversed(submission.processing_events.all())
                if event.stage == "review" and event.code in REJECTION_REASON_LABELS
            ),
            None,
        )
        audit = audit_by_submission.get(str(submission.pk))
        rows.append(
            {
                "submission": submission,
                "decision_at": (
                    rejection_event.created_at
                    if rejection_event is not None
                    else submission.imported_at
                ),
                "actor": audit.user if audit else None,
                "rejection_reason": rejection_event.code if rejection_event else "",
                "rejection_comment": rejection_event.message if rejection_event else "",
            }
        )
    return tuple(rows)


def _detail_context(submission, user, *, review_form=None):
    # PRE: submission is loaded and user passed general view authorization.
    # POST: returns detail context for imported rows or automatic-import incidents.
    attachments = list(submission.attachments.all())
    processing_events = list(submission.processing_events.all())
    can_view_raw_payload = _can_view_raw_payload(user)
    can_change_submission = user.has_perm("kobo.change_kobosubmission")
    can_view_technical = can_view_raw_payload
    ficha_title, ficha_subtitle = form_identity(submission)
    incident = submission_incident_context(submission)
    return {
        "submission": submission,
        "ficha_title": ficha_title,
        "ficha_subtitle": ficha_subtitle,
        "status_label": submission_status_label(submission.status),
        "territorial_rows": territorial_summary_rows(submission),
        "presented_fields": present_submission_fields(submission),
        "contact_fields": present_contact_fields(submission),
        "presented_events": present_processing_events(processing_events),
        "attachments": attachments,
        "attachment_status_label": attachment_status_label,
        "has_project": submission.project_id is not None,
        "review_form": review_form or KoboReviewForm(submission=submission),
        "can_change_submission": can_change_submission,
        "can_view_technical": can_view_technical,
        "can_view_raw_payload": can_view_raw_payload,
        "show_retry_normalization": can_change_submission
        and should_show_retry_normalization(submission),
        "show_retry_attachments": can_change_submission
        and should_show_retry_attachments(submission, attachments),
        "show_retry_import": can_change_submission and incident["can_retry_import"],
        "is_incident": incident["is_incident"],
        "is_imported": incident["is_imported"],
        "import_record": incident["import_record"],
        "incident_label": incident["incident_label"],
        "incident_action": incident["incident_action"],
        "page_title": (
            "Detalle de formulario"
            if incident["is_imported"]
            else ("Incidencia" if incident["is_incident"] else "Detalle de formulario")
        ),
        "kobo_enabled": settings.KOBO_ENABLED,
        "normalized_payload_json": (
            json.dumps(submission.normalized_payload or {}, indent=2, ensure_ascii=False)
            if can_view_technical
            else None
        ),
        "raw_payload_json": (
            json.dumps(submission.raw_payload, indent=2, ensure_ascii=False)
            if can_view_raw_payload
            else None
        ),
        "device_id": submission.raw_payload.get("deviceid") if can_view_technical else None,
        "submitted_by": (
            submission.raw_payload.get("_submitted_by") if can_view_technical else None
        ),
    }


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def submission_list(request):
    submissions = KoboSubmission.objects.select_related("form_definition").annotate(
        attachment_count=Count("attachments", distinct=True),
        downloaded_attachment_count=Count(
            "attachments",
            filter=Q(attachments__status=KoboAttachment.Status.DOWNLOADED),
            distinct=True,
        ),
    )
    filters = {
        "status": request.GET.get("status", "").strip(),
        "form_id": request.GET.get("form_id", "").strip(),
        "pastoral_zone": request.GET.get("pastoral_zone", "").strip(),
        "parish": request.GET.get("parish", "").strip(),
    }
    if filters["status"]:
        submissions = submissions.filter(status=filters["status"])
    if filters["form_id"]:
        submissions = submissions.filter(form_definition__form_id=filters["form_id"])
    if filters["pastoral_zone"]:
        submissions = submissions.filter(pastoral_zone=filters["pastoral_zone"])
    if filters["parish"]:
        submissions = submissions.filter(parish=filters["parish"])
    return render(
        request,
        "kobo/submission_list.html",
        {
            "submissions": submissions,
            "filters": filters,
            "status_choices": KoboSubmission.Status.choices,
            "form_ids": KoboSubmission.objects.values_list(
                "form_definition__form_id", flat=True
            ).distinct(),
        },
    )


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def submission_detail(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    return render(
        request,
        "kobo/submission_detail.html",
        _detail_context(submission, request.user),
    )


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def review_submission_action(request, pk):
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    raise Http404


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def retry_normalization_action(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    outcome = process_submission(
        submission,
        default_timezone=timezone.get_current_timezone(),
    )
    if outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW:
        route_normalized_submission(submission)
        from apps.integrations.kobo.services import auto_import_if_eligible

        auto_import_if_eligible(submission)
        submission.refresh_from_db()
    messages.info(
        request,
        f"Procesamiento finalizado: {submission_status_label(submission.status)}.",
    )
    return redirect("kobo:submission_detail", pk=submission.pk)


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def retry_attachments_action(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    client = build_kobo_api_client()
    result = process_submission_attachments(
        submission,
        client=client,
        storage=default_storage,
        max_bytes=settings.KOBO_MAX_ATTACHMENT_BYTES,
    )
    messages.info(request, f"Adjuntos procesados: {result.selected}.")
    return redirect("kobo:submission_detail", pk=submission.pk)


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def project_submission_detail(request, pk):
    if not settings.KOBO_ENABLED:
        raise Http404
    submission = get_object_or_404(
        KoboSubmission.objects.select_related(
            "project",
            "asset",
            "form_definition",
        ),
        pk=pk,
        status=KoboSubmission.Status.IMPORTED,
        project__isnull=False,
        asset__is_active=True,
    )
    normalized_payload = submission.normalized_payload or {}
    can_view_sensitive = _can_view_sensitive_kobo_data(request.user)
    evidences = submission.attachments.filter(
        status=KoboAttachment.Status.DOWNLOADED
    ).order_by("pk")
    if not can_view_sensitive:
        evidences = evidences.exclude(
            privacy_level=KoboAttachment.PrivacyLevel.PRIVATE
        )
    return render(
        request,
        "kobo/project_submission_detail.html",
        {
            "submission": submission,
            "normalized_payload": normalized_payload,
            "evidences": evidences,
            "can_view_sensitive": can_view_sensitive,
            "project_submission_detail_rows": _project_submission_detail_rows(
                submission
            ),
            "project_submission_detail_title": _project_submission_detail_title(
                submission
            ),
            "sensitive_data": {
                "parish_delegate": normalized_payload.get("parish_delegate"),
                "contact_phone": normalized_payload.get("contact_phone"),
                "main_informant_role": normalized_payload.get("main_informant_role"),
                "submitted_by": submission.raw_payload.get("_submitted_by"),
                "device_id": submission.raw_payload.get("deviceid"),
            },
        },
    )


@login_required
@permission_required("operations.view_project", raise_exception=True)
def project_pending_submission_list(request, project_pk):
    # PRE: request user can view operations projects and Kobo is enabled.
    # POST: renders only the selected project's pending Kobo submissions.
    _require_kobo_enabled()
    from apps.operations.models import Project

    project = get_object_or_404(Project, pk=project_pk)
    return render(
        request,
        "kobo/project_pending_submission_list.html",
        {
            "project": project,
            "pending_submission_rows": _project_pending_submission_rows(project),
            "can_import_kobo_submissions": request.user.has_perm(
                "operations.change_project"
            ),
        },
    )


@login_required
@permission_required(
    ("operations.view_project", "operations.change_project"),
    raise_exception=True,
)
def project_pending_submission_review(request, project_pk, pk):
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    raise Http404


@require_POST
@login_required
@permission_required(
    ("operations.view_project", "operations.change_project"),
    raise_exception=True,
)
def project_pending_submission_import(request, project_pk, pk):
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    raise Http404


@require_POST
@login_required
@permission_required(
    ("operations.view_project", "operations.change_project"),
    raise_exception=True,
)
def project_pending_submission_reject(request, project_pk, pk):
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    raise Http404


@login_required
@permission_required("operations.view_project", raise_exception=True)
def project_submission_history(request, project_pk):
    # PRE: request user may view the selected operations project.
    # POST: renders imported and rejected Kobo submissions without raw payloads.
    _require_kobo_enabled()
    from apps.operations.models import Project

    project = get_object_or_404(Project, pk=project_pk)
    return render(
        request,
        "kobo/project_submission_history.html",
        {
            "project": project,
            "history_rows": _project_submission_history_rows(project),
            "can_restore_kobo_submissions": request.user.has_perm(
                "operations.change_project"
            ),
        },
    )


@login_required
@permission_required("operations.view_project", raise_exception=True)
def project_submission_history_detail(request, project_pk, pk):
    # PRE: request user may view the selected project's Kobo history.
    # POST: renders safe read-only normalized data for one historical submission.
    _require_kobo_enabled()
    from apps.operations.models import Project

    project = get_object_or_404(Project, pk=project_pk)
    submission = get_object_or_404(
        KoboSubmission.objects.select_related("project", "asset", "form_definition")
        .prefetch_related("attachments"),
        pk=pk,
        project=project,
        status__in=(KoboSubmission.Status.IMPORTED, KoboSubmission.Status.REJECTED),
    )
    return render(
        request,
        "kobo/project_pending_submission_review.html",
        {
            "project": project,
            "submission": submission,
            "normalized_payload": submission.normalized_payload or {},
            "attachments": submission.attachments.all(),
            "project_submission_detail_rows": _project_submission_detail_rows(submission),
            "project_submission_detail_title": _project_submission_detail_title(submission),
            "read_only": True,
            "back_to_history": True,
        },
    )


@require_POST
@login_required
@permission_required(
    ("operations.view_project", "operations.change_project"),
    raise_exception=True,
)
def project_rejected_submission_restore(request, project_pk, pk):
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    raise Http404


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def project_submission_evidence(request, pk, attachment_pk):
    if not settings.KOBO_ENABLED:
        raise Http404
    attachment = get_object_or_404(
        KoboAttachment.objects.select_related("submission"),
        pk=attachment_pk,
        submission_id=pk,
        submission__status=KoboSubmission.Status.IMPORTED,
        submission__project__isnull=False,
        submission__asset__is_active=True,
        status=KoboAttachment.Status.DOWNLOADED,
    )
    if (
        attachment.privacy_level == KoboAttachment.PrivacyLevel.PRIVATE
        and not _can_view_sensitive_kobo_data(request.user)
    ):
        raise Http404
    if not attachment.file:
        raise Http404
    safe_filename = attachment.file.name.rsplit("/", 1)[-1]
    try:
        stored_file = attachment.file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404 from exc
    return FileResponse(
        stored_file,
        content_type=attachment.content_type or "application/octet-stream",
        filename=safe_filename,
    )


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def discovered_asset_list(request):
    # PRE: request user may view Kobo assets.
    # POST: renders safe discovery summaries without remote secrets or mutation.
    _require_kobo_enabled()
    discovered_assets = list(KoboDiscoveredAsset.objects.order_by("name", "pk"))
    configured_by_uid = {
        asset.asset_uid: asset
        for asset in KoboAsset.objects.filter(
            asset_uid__in=[item.asset_uid for item in discovered_assets]
        )
    }
    rows = [
        {
            "discovered": item,
            "local_asset": configured_by_uid.get(item.asset_uid),
            "local_state": _local_asset_state(configured_by_uid.get(item.asset_uid)),
            "short_uid": item.asset_uid[:12],
        }
        for item in discovered_assets
    ]
    return render(request, "kobo/discovered_asset_list.html", {"rows": rows})


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def discovered_asset_detail(request, pk):
    # PRE: request user may view Kobo assets and pk identifies a discovery candidate.
    # POST: renders safe discovery/configuration details without mutation.
    _require_kobo_enabled()
    discovered = get_object_or_404(KoboDiscoveredAsset, pk=pk)
    asset = KoboAsset.objects.filter(asset_uid=discovered.asset_uid).select_related(
        "form_definition"
    ).first()
    compatible_configuration = (
        get_compatible_asset_configuration(discovered) if asset is None else None
    )
    return render(
        request,
        "kobo/discovered_asset_detail.html",
        {
            "discovered": discovered,
            "asset": asset,
            "local_state": _local_asset_state(asset),
            "compatible_configuration": compatible_configuration,
            "configuration_form": (
                KoboAssetConfigurationForm(discovered_asset=discovered)
                if compatible_configuration is not None
                else None
            ),
        },
    )


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def configure_discovered_asset_action(request, pk):
    # PRE: authorized POST supplies configuration fields for one discovered asset.
    # POST: creates at most one inactive local asset and redirects to its detail.
    _require_kobo_enabled()
    discovered = get_object_or_404(KoboDiscoveredAsset, pk=pk)
    existing = KoboAsset.objects.filter(asset_uid=discovered.asset_uid).first()
    if existing is not None:
        messages.error(request, "El activo descubierto ya está configurado.")
        return redirect("kobo:asset_configuration", pk=existing.pk)
    compatible_configuration = get_compatible_asset_configuration(discovered)
    if compatible_configuration is None:
        return render(
            request,
            "kobo/discovered_asset_detail.html",
            {
                "discovered": discovered,
                "asset": None,
                "bindings": (),
                "local_state": "unconfigured",
                "compatible_configuration": None,
                "configuration_form": None,
            },
            status=400,
        )
    form = KoboAssetConfigurationForm(request.POST, discovered_asset=discovered)
    if form.is_valid():
        try:
            asset = configure_discovered_asset(
                discovered,
                name=form.cleaned_data["name"],
                form_definition=form.cleaned_data["form_definition"],
                form_role=form.cleaned_data["form_role"],
                configured_by=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Activo configurado; permanece inactivo.")
            return redirect("kobo:asset_configuration", pk=asset.pk)
    return render(
        request,
        "kobo/discovered_asset_detail.html",
        {
            "discovered": discovered,
            "asset": None,
            "bindings": (),
            "local_state": "unconfigured",
            "compatible_configuration": compatible_configuration,
            "configuration_form": form,
        },
        status=400,
    )


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def asset_configuration_detail(request, pk):
    # PRE: authorized request identifies a configured Kobo asset.
    # POST: renders territorial-routing asset status without state changes.
    _require_kobo_enabled()
    asset = get_object_or_404(
        KoboAsset.objects.select_related("form_definition"), pk=pk
    )
    return render(
        request,
        "kobo/asset_configuration_detail.html",
        _asset_configuration_context(asset),
    )


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def activate_kobo_asset_action(request, pk):
    # PRE: authorized POST identifies an inactive configured asset.
    # POST: activates only a ready asset; failures preserve current state.
    _require_kobo_enabled()
    asset = get_object_or_404(KoboAsset, pk=pk)
    try:
        activate_kobo_asset(asset, activated_by=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc.message))
    else:
        messages.success(request, "Integración activada.")
    return redirect("kobo:asset_configuration", pk=asset.pk)


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def deactivate_kobo_asset_action(request, pk):
    # PRE: authorized POST identifies a configured asset.
    # POST: deactivates it while preserving historical data and submissions.
    _require_kobo_enabled()
    asset = get_object_or_404(KoboAsset, pk=pk)
    deactivate_kobo_asset(asset, deactivated_by=request.user)
    messages.success(request, "Integración desactivada.")
    return redirect("kobo:asset_configuration", pk=asset.pk)
