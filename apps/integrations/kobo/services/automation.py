"""Automatic Kobo import after normalization and territorial routing."""

from dataclasses import dataclass
from enum import StrEnum

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.import_contracts import ImportOutcome
from apps.integrations.kobo.models import KoboProcessingEvent, KoboSubmission
from apps.integrations.kobo.services.importers import import_kobo_submission
from apps.integrations.kobo.services.territorial_routing import route_normalized_submission


KOBO_SYSTEM_USERNAME = "kobo.system"
KOBO_SYSTEM_EMAIL = "kobo.system@localhost"


class AutoImportOutcome(StrEnum):
    IMPORTED = "imported"
    ALREADY_IMPORTED = "already_imported"
    INCIDENT = "incident"
    SKIPPED = "skipped"
    FAILED = "failed"


class IncidentKind(StrEnum):
    ZONE_WITHOUT_PROJECT = "zone_without_project"
    NUCLEUS_NOT_FOUND = "nucleus_not_found"
    TERRITORIAL_CONFLICT = "territorial_conflict"
    INVALID_DATA = "invalid_data"
    NORMALIZATION_ERROR = "normalization_error"
    MATERIALIZATION_ERROR = "materialization_error"
    REMOTE_UPDATE_PENDING = "remote_update_pending"
    TECHNICAL_ERROR = "technical_error"
    ROUTING_ERROR = "routing_error"


INCIDENT_LABELS = {
    IncidentKind.ZONE_WITHOUT_PROJECT: "Zona sin proyecto",
    IncidentKind.NUCLEUS_NOT_FOUND: "Núcleo no encontrado",
    IncidentKind.TERRITORIAL_CONFLICT: "Conflicto territorial",
    IncidentKind.INVALID_DATA: "Datos inválidos",
    IncidentKind.NORMALIZATION_ERROR: "Error de normalización",
    IncidentKind.MATERIALIZATION_ERROR: "Error de materialización",
    IncidentKind.REMOTE_UPDATE_PENDING: "Cambio remoto pendiente",
    IncidentKind.TECHNICAL_ERROR: "Error técnico",
    IncidentKind.ROUTING_ERROR: "Error de asignación territorial",
}

INCIDENT_ACTIONS = {
    IncidentKind.ZONE_WITHOUT_PROJECT: "Configure la zona pastoral y reintente el procesamiento.",
    IncidentKind.NUCLEUS_NOT_FOUND: (
        "Espere o importe primero la Ficha 1 del núcleo, luego reintente."
    ),
    IncidentKind.TERRITORIAL_CONFLICT: "Resuelva el conflicto de asignación territorial.",
    IncidentKind.INVALID_DATA: "Corrija los datos en KoboToolbox y sincronice de nuevo.",
    IncidentKind.NORMALIZATION_ERROR: "Revise el formulario y use reintentar normalización.",
    IncidentKind.MATERIALIZATION_ERROR: "Corrija la configuración interna y reintente la importación.",
    IncidentKind.REMOTE_UPDATE_PENDING: (
        "Revise el cambio remoto recibido desde KoboToolbox."
    ),
    IncidentKind.TECHNICAL_ERROR: "Reintente el procesamiento tras revisar la configuración.",
    IncidentKind.ROUTING_ERROR: "Corrija la asignación territorial y reintente.",
}


@dataclass(frozen=True)
class AutoImportResult:
    submission_id: int
    outcome: AutoImportOutcome
    incident_kind: IncidentKind | None = None
    reason_code: str = ""
    import_outcome: str = ""


def get_kobo_system_actor():
    """
    PRE: auth tables are migrated.
    POST: returns the durable technical actor used for automatic imports.
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=KOBO_SYSTEM_USERNAME,
        defaults={
            "email": KOBO_SYSTEM_EMAIL,
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        },
    )
    update_fields = []
    if created or user.has_usable_password():
        user.set_unusable_password()
        update_fields.append("password")
    if not user.is_active:
        user.is_active = True
        update_fields.append("is_active")
    if user.email != KOBO_SYSTEM_EMAIL:
        user.email = KOBO_SYSTEM_EMAIL
        update_fields.append("email")
    if update_fields:
        user.save(update_fields=update_fields)

    if not user.has_perm("operations.change_project"):
        content_type = ContentType.objects.get(app_label="operations", model="project")
        permission = Permission.objects.get(
            content_type=content_type,
            codename="change_project",
        )
        user.user_permissions.add(permission)
        if hasattr(user, "_perm_cache"):
            delattr(user, "_perm_cache")
        if hasattr(user, "_user_perm_cache"):
            delattr(user, "_user_perm_cache")
    return user


def classify_incident(submission: KoboSubmission) -> IncidentKind:
    """
    PRE: submission is a staged row that could not be imported automatically.
    POST: returns one operator-facing incident category without exposing payloads.
    """
    if submission.remote_update_pending:
        return IncidentKind.REMOTE_UPDATE_PENDING
    if submission.status == KoboSubmission.Status.VALIDATION_FAILED:
        return IncidentKind.INVALID_DATA
    if submission.status == KoboSubmission.Status.PROCESSING_FAILED:
        return IncidentKind.NORMALIZATION_ERROR
    if submission.error_code in {"MATERIALIZATION_FAILED"}:
        return IncidentKind.MATERIALIZATION_ERROR
    if submission.error_code and submission.error_code.startswith("IMPORT_"):
        if submission.error_code in {
            "IMPORT_ROUTING_PENDING",
            "IMPORT_PROJECT_MISSING",
        }:
            return IncidentKind.ZONE_WITHOUT_PROJECT
        if submission.error_code == "IMPORT_ROUTING_CONFLICT":
            return IncidentKind.TERRITORIAL_CONFLICT
        if submission.error_code == "REMOTE_UPDATE_REVIEW_REQUIRED":
            return IncidentKind.REMOTE_UPDATE_PENDING
        return IncidentKind.MATERIALIZATION_ERROR

    reason = submission.routing_reason_code
    if reason == TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING:
        return IncidentKind.ZONE_WITHOUT_PROJECT
    if reason == TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY:
        return IncidentKind.NUCLEUS_NOT_FOUND
    if reason in {
        TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT,
        TerritorialRoutingReasonCode.TERRITORIAL_CONFLICT_REJECTED,
    }:
        return IncidentKind.TERRITORIAL_CONFLICT
    if reason in {
        TerritorialRoutingReasonCode.MISSING_NUCLEO_CODE,
        TerritorialRoutingReasonCode.INVALID_NUCLEO_CODE,
        TerritorialRoutingReasonCode.MISSING_PASTORAL_ZONE,
        TerritorialRoutingReasonCode.INVALID_PASTORAL_ZONE,
        TerritorialRoutingReasonCode.UNSUPPORTED_FORM,
    }:
        return IncidentKind.INVALID_DATA
    if submission.routing_status == KoboSubmission.RoutingStatus.PENDING_IDENTITY:
        return IncidentKind.NUCLEUS_NOT_FOUND
    if submission.routing_status == KoboSubmission.RoutingStatus.CONFLICT:
        return IncidentKind.TERRITORIAL_CONFLICT
    if submission.routing_status == KoboSubmission.RoutingStatus.ERROR:
        return IncidentKind.ROUTING_ERROR
    return IncidentKind.TECHNICAL_ERROR


def incident_queryset(base_queryset=None):
    """
    PRE: base_queryset is None or a KoboSubmission queryset.
    POST: returns submissions that require operator attention, never valid pending imports.
    """
    queryset = base_queryset if base_queryset is not None else KoboSubmission.objects.all()
    return queryset.filter(
        Q(remote_update_pending=True)
        | Q(
            status__in=(
                KoboSubmission.Status.VALIDATION_FAILED,
                KoboSubmission.Status.PROCESSING_FAILED,
            )
        )
        | Q(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status__in=(
                KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                KoboSubmission.RoutingStatus.CONFLICT,
                KoboSubmission.RoutingStatus.ERROR,
                KoboSubmission.RoutingStatus.UNRESOLVED,
            ),
        )
        | Q(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            project__isnull=True,
        )
        | Q(
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            imported_at__isnull=True,
        )
        | Q(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            error_code__gt="",
        )
    ).exclude(status=KoboSubmission.Status.IMPORTED)


def _auto_approve_for_import(submission: KoboSubmission) -> bool:
    """
    PRE: submission is eligible for automatic import and remains READY_FOR_REVIEW.
    POST: records system approval without attributing a human reviewer.
    """
    with transaction.atomic():
        locked = KoboSubmission.objects.select_for_update().get(pk=submission.pk)
        if locked.status == KoboSubmission.Status.APPROVED_FOR_IMPORT:
            return True
        if locked.status != KoboSubmission.Status.READY_FOR_REVIEW:
            return False
        if locked.remote_update_pending:
            return False
        if (
            locked.routing_status != KoboSubmission.RoutingStatus.RESOLVED
            or locked.project_id is None
        ):
            return False
        locked.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
        locked.save(update_fields=("status",))
        KoboProcessingEvent.objects.create(
            submission=locked,
            stage="auto_import",
            level=KoboProcessingEvent.Level.INFO,
            code="auto_approved",
            message="Submission approved automatically by the Kobo system actor.",
        )
    submission.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
    return True


def auto_import_if_eligible(submission: KoboSubmission) -> AutoImportResult:
    """
    PRE: submission has completed normalize/route (or failed into a durable state).
    POST: imports when every import contract passes using the system actor; otherwise
    leaves the row as an incident without human rejection or correction.
    """
    if submission is None or submission.pk is None:
        return AutoImportResult(
            submission_id=0,
            outcome=AutoImportOutcome.SKIPPED,
            reason_code="SUBMISSION_MISSING",
        )

    submission.refresh_from_db()
    if submission.status == KoboSubmission.Status.IMPORTED:
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.ALREADY_IMPORTED,
            import_outcome=ImportOutcome.ALREADY_IMPORTED.value,
        )

    if submission.status in {
        KoboSubmission.Status.REJECTED,
        KoboSubmission.Status.DUPLICATE,
        KoboSubmission.Status.PARTIALLY_IMPORTED,
        KoboSubmission.Status.RECEIVED,
        KoboSubmission.Status.NORMALIZED,
    }:
        if submission.status in {
            KoboSubmission.Status.RECEIVED,
            KoboSubmission.Status.NORMALIZED,
        }:
            return AutoImportResult(
                submission_id=submission.pk,
                outcome=AutoImportOutcome.SKIPPED,
                reason_code=submission.status,
            )
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.SKIPPED,
            reason_code=submission.status,
        )

    if submission.status in {
        KoboSubmission.Status.VALIDATION_FAILED,
        KoboSubmission.Status.PROCESSING_FAILED,
    } or submission.remote_update_pending:
        kind = classify_incident(submission)
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.INCIDENT,
            incident_kind=kind,
            reason_code=submission.error_code or submission.routing_reason_code,
        )

    if submission.status not in {
        KoboSubmission.Status.READY_FOR_REVIEW,
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
    }:
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.SKIPPED,
            reason_code=submission.status,
        )

    if (
        submission.routing_status != KoboSubmission.RoutingStatus.RESOLVED
        or submission.project_id is None
    ):
        kind = classify_incident(submission)
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.INCIDENT,
            incident_kind=kind,
            reason_code=submission.routing_reason_code or submission.error_code,
        )

    if submission.status == KoboSubmission.Status.READY_FOR_REVIEW:
        if not _auto_approve_for_import(submission):
            submission.refresh_from_db()
            kind = classify_incident(submission)
            return AutoImportResult(
                submission_id=submission.pk,
                outcome=AutoImportOutcome.INCIDENT,
                incident_kind=kind,
                reason_code=submission.routing_reason_code or submission.error_code,
            )
        submission.refresh_from_db()

    actor = get_kobo_system_actor()
    import_result = import_kobo_submission(submission, actor=actor)
    submission.refresh_from_db()

    if import_result.outcome == ImportOutcome.IMPORTED:
        KoboProcessingEvent.objects.create(
            submission_id=submission.pk,
            stage="auto_import",
            level=KoboProcessingEvent.Level.INFO,
            code="auto_imported",
            message="Submission imported automatically by the Kobo system actor.",
        )
        if submission.nucleo_code_normalized:
            try:
                retry_pending_identity_for_code(
                    nucleo_code_normalized=submission.nucleo_code_normalized
                )
            except Exception:
                pass
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.IMPORTED,
            import_outcome=import_result.outcome.value,
        )
    if import_result.outcome == ImportOutcome.ALREADY_IMPORTED:
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.ALREADY_IMPORTED,
            import_outcome=import_result.outcome.value,
        )

    kind = classify_incident(submission)
    return AutoImportResult(
        submission_id=submission.pk,
        outcome=(
            AutoImportOutcome.FAILED
            if import_result.outcome == ImportOutcome.FAILED
            else AutoImportOutcome.INCIDENT
        ),
        incident_kind=kind,
        reason_code=import_result.reason_code or submission.error_code,
        import_outcome=import_result.outcome.value,
    )


def retry_auto_import(submission: KoboSubmission) -> AutoImportResult:
    """
    PRE: submission is an unresolved incident that may now be importable.
    POST: re-routes when still reviewable, then attempts automatic import safely.
    """
    if submission is None or submission.pk is None:
        return AutoImportResult(
            submission_id=0,
            outcome=AutoImportOutcome.SKIPPED,
            reason_code="SUBMISSION_MISSING",
        )
    submission.refresh_from_db()
    if submission.status == KoboSubmission.Status.IMPORTED:
        return AutoImportResult(
            submission_id=submission.pk,
            outcome=AutoImportOutcome.ALREADY_IMPORTED,
            import_outcome=ImportOutcome.ALREADY_IMPORTED.value,
        )
    if submission.status == KoboSubmission.Status.READY_FOR_REVIEW:
        route_normalized_submission(submission)
        submission.refresh_from_db()
    return auto_import_if_eligible(submission)


def retry_incidents_for_pastoral_zone(*, pastoral_zone: str, limit: int = 100) -> int:
    """
    PRE: pastoral_zone is a canonical zone value and a mapping may now exist.
    POST: re-routes and auto-imports up to limit zone-related incidents.
    """
    candidates = list(
        KoboSubmission.objects.filter(
            pastoral_zone=pastoral_zone,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        .filter(
            Q(routing_reason_code=TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING)
            | Q(project__isnull=True)
            | Q(routing_status=KoboSubmission.RoutingStatus.ERROR)
        )
        .order_by("received_at", "pk")[:limit]
    )
    imported = 0
    for submission in candidates:
        result = retry_auto_import(submission)
        imported += int(result.outcome == AutoImportOutcome.IMPORTED)
    return imported


def retry_pending_identity_for_code(*, nucleo_code_normalized: str, limit: int = 100) -> int:
    """
    PRE: nucleo_code_normalized identifies a territorial identity that may now exist.
    POST: re-routes pending Ficha 10/11 rows and auto-imports eligible ones.
    """
    candidates = list(
        KoboSubmission.objects.filter(
            nucleo_code_normalized=nucleo_code_normalized,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        ).order_by("received_at", "pk")[:limit]
    )
    imported = 0
    for submission in candidates:
        result = retry_auto_import(submission)
        imported += int(result.outcome == AutoImportOutcome.IMPORTED)
    return imported
