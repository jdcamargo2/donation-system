from typing import Mapping

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from apps.integrations.kobo.errors import (
    KoboConfigurationError,
    KoboPayloadError,
    KoboUnsupportedFormError,
)
from apps.integrations.kobo.form_registry import KoboFormType, resolve_form_type
from apps.integrations.kobo.import_contracts import (
    ImportOutcome,
    ImportWarning,
    KoboImportBlocked,
    KoboImportHandler,
    KoboImportResult,
    KoboMaterializationResult,
)
from apps.integrations.kobo.import_handlers import KOBO_IMPORT_HANDLERS
from apps.integrations.kobo.models import (
    KoboImportRecord,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    KoboRejectionResult,
    KoboRestoreResult,
    REJECTION_REASON_LABELS,
)


def _operational_import_failure(
    submission: KoboSubmission,
    *,
    error_code: str,
    error_message: str,
    warnings: tuple[ImportWarning, ...] = (),
) -> KoboImportResult:
    """
    PRE: submission is locked in an import transaction and remains reviewable.
    POST: records a non-sensitive import failure as a retryable processing incident.
    """
    # APPROVED_FOR_IMPORT is a transitional automatic state, never a durable incident.
    submission.status = KoboSubmission.Status.PROCESSING_FAILED
    submission.error_code = error_code
    submission.error_message = error_message
    submission.save(update_fields=("status", "error_code", "error_message"))
    event_exists = submission.processing_events.filter(
        stage="operational_import",
        code=error_code,
        message=error_message,
    ).exists()
    if not event_exists:
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="operational_import",
            level=KoboProcessingEvent.Level.WARNING,
            code=error_code,
            message=error_message,
        )
    return KoboImportResult(
        outcome=ImportOutcome.BLOCKED,
        submission_id=submission.pk,
        materialization_type=None,
        materialization_id=None,
        created=False,
        warnings=warnings,
        reason_code=error_code,
    )


def _lock_submission_for_operational_import(submission_id: int) -> KoboSubmission:
    """
    PRE: submission_id identifies a persisted KoboSubmission inside a transaction.
    POST: locks only the KoboSubmission row, without joining nullable relations.
    """
    return KoboSubmission.objects.select_for_update().get(pk=submission_id)


def _validate_project_operator(actor, submission: KoboSubmission) -> None:
    """
    PRE: actor and submission are persisted candidates for a project decision.
    POST: returns only for an authenticated project operator and assigned project.
    """
    if not getattr(actor, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated project operator is required.")
    if not actor.has_perm("operations.change_project"):
        raise KoboConfigurationError("Project change permission is required.")
    if submission.project_id is None:
        raise KoboPayloadError("Submission has no assigned project.")


def reject_kobo_submission(
    submission: KoboSubmission,
    *,
    actor,
    reason: str,
    comment: str = "",
) -> KoboRejectionResult:
    """
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    PRE: submission is persisted, ready for review, and reason is a supported code.
    POST: atomically records one auditable rejection without changing payloads or attachments.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")
    if reason not in REJECTION_REASON_LABELS:
        raise KoboPayloadError("Rejection reason is invalid.")
    cleaned_comment = strip_tags(comment).strip()
    if reason == "other" and not cleaned_comment:
        raise KoboPayloadError("A comment is required for the other rejection reason.")

    with transaction.atomic():
        locked_submission = _lock_submission_for_operational_import(submission.pk)
        _validate_project_operator(actor, locked_submission)
        if locked_submission.status == KoboSubmission.Status.REJECTED:
            return KoboRejectionResult(
                submission_id=locked_submission.pk,
                rejected=False,
                already_rejected=True,
            )
        if locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW:
            raise KoboPayloadError("Only submissions ready for review can be rejected.")
        if locked_submission.imported_at is not None:
            raise KoboPayloadError("Imported submissions cannot be rejected.")

        from apps.operations.models import AuditLog
        from apps.operations.services import log_action

        locked_submission.status = KoboSubmission.Status.REJECTED
        locked_submission.save(update_fields=("status",))
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="review",
            level=KoboProcessingEvent.Level.INFO,
            code=reason,
            message=cleaned_comment or REJECTION_REASON_LABELS[reason],
        )
        log_action(
            actor,
            AuditLog.Action.REJECTED,
            locked_submission,
            "Ficha Kobo rechazada.",
        )

    submission.status = KoboSubmission.Status.REJECTED
    return KoboRejectionResult(
        submission_id=submission.pk,
        rejected=True,
        already_rejected=False,
    )


def restore_kobo_submission_to_review(
    submission: KoboSubmission,
    *,
    actor,
) -> KoboRestoreResult:
    """
    # Legacy manual-review workflow. Not used by the automated Kobo pipeline.
    PRE: submission is persisted and actor is a project operator.
    POST: atomically restores only a rejected submission to ready-for-review.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")

    with transaction.atomic():
        locked_submission = _lock_submission_for_operational_import(submission.pk)
        _validate_project_operator(actor, locked_submission)
        if locked_submission.status == KoboSubmission.Status.READY_FOR_REVIEW:
            return KoboRestoreResult(
                submission_id=locked_submission.pk,
                restored=False,
                already_ready=True,
            )
        if locked_submission.status != KoboSubmission.Status.REJECTED:
            raise KoboPayloadError("Only rejected submissions can be restored.")

        from apps.operations.models import AuditLog
        from apps.operations.services import log_action

        locked_submission.status = KoboSubmission.Status.READY_FOR_REVIEW
        locked_submission.save(update_fields=("status",))
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="review",
            level=KoboProcessingEvent.Level.INFO,
            code="restored",
            message="Kobo submission restored to review.",
        )
        log_action(
            actor,
            AuditLog.Action.UPDATED,
            locked_submission,
            "Ficha Kobo restaurada a revisión.",
        )

    submission.status = KoboSubmission.Status.READY_FOR_REVIEW
    return KoboRestoreResult(
        submission_id=submission.pk,
        restored=True,
        already_ready=False,
    )


def _validate_common_import_preconditions(
    submission: KoboSubmission,
    *,
    actor,
    handler_registry: Mapping[KoboFormType, KoboImportHandler],
) -> KoboFormType:
    """
    PRE: submission is row-locked inside the common import transaction.
    POST: returns its supported form type only when every shared import rule passes.
    """
    if not actor.has_perm("operations.change_project"):
        raise KoboImportBlocked("IMPORT_PERMISSION_DENIED")
    if submission.status != KoboSubmission.Status.APPROVED_FOR_IMPORT:
        raise KoboImportBlocked("IMPORT_REVIEW_NOT_APPROVED")
    if submission.imported_at is not None:
        raise KoboImportBlocked("IMPORT_TIMESTAMP_INVALID")
    try:
        form_type = resolve_form_type(
            submission.form_definition.form_id,
            submission.form_definition.version,
        )
    except KoboUnsupportedFormError as exc:
        raise KoboImportBlocked("UNSUPPORTED_FORM") from exc
    handler = handler_registry.get(form_type)
    if handler is None or handler.form_type != form_type:
        raise KoboImportBlocked("UNSUPPORTED_FORM")
    if submission.routing_status != KoboSubmission.RoutingStatus.RESOLVED:
        routing_reason_codes = {
            KoboSubmission.RoutingStatus.PENDING_IDENTITY: "IMPORT_ROUTING_PENDING",
            KoboSubmission.RoutingStatus.CONFLICT: "IMPORT_ROUTING_CONFLICT",
            KoboSubmission.RoutingStatus.ERROR: "IMPORT_ROUTING_ERROR",
        }
        raise KoboImportBlocked(
            routing_reason_codes.get(
                submission.routing_status,
                "IMPORT_ROUTING_UNRESOLVED",
            )
        )
    if submission.project_id is None:
        raise KoboImportBlocked("IMPORT_PROJECT_MISSING")
    if not isinstance(submission.raw_payload, dict) or not submission.raw_payload:
        raise KoboImportBlocked("IMPORT_ORIGINAL_PAYLOAD_MISSING")
    if (
        not isinstance(submission.normalized_payload, dict)
        or not submission.normalized_payload
        or submission.normalized_at is None
    ):
        raise KoboImportBlocked("IMPORT_NORMALIZATION_INVALID")

    from apps.operations.models import Project

    asset = submission.asset
    expected_role = FORM_DEFINITION_ROLES.get(
        (submission.form_definition.form_id, submission.form_definition.version)
    )
    if (
        asset is None
        or not asset.is_active
        or not submission.form_definition.is_active
        or asset.form_definition_id != submission.form_definition_id
        or asset.form_role != expected_role
    ):
        raise KoboImportBlocked("IMPORT_ASSET_INVALID")
    if submission.project.status != Project.Status.ACTIVE:
        raise KoboImportBlocked("IMPORT_PROJECT_INACTIVE")
    return form_type


def _validate_materialization_result(result: KoboMaterializationResult) -> None:
    """
    PRE: a handler returned a purported successful materialization result.
    POST: rejects empty, malformed, or non-persisted-looking target references.
    """
    if (
        not result.materialization_type
        or not result.target_app_label.isidentifier()
        or not result.target_model.isidentifier()
        or result.target_object_id <= 0
    ):
        raise RuntimeError("Kobo handler returned an invalid materialization reference.")


def _merge_warnings(
    *warning_groups: tuple[ImportWarning, ...],
) -> tuple[ImportWarning, ...]:
    """
    PRE: warning groups contain pure, safe handler warnings.
    POST: returns stable warnings without duplicate code-message pairs.
    """
    merged = []
    seen = set()
    for warning_group in warning_groups:
        for warning in warning_group:
            key = (warning.code, warning.message)
            if key not in seen:
                seen.add(key)
                merged.append(warning)
    return tuple(merged)


def _warnings_from_record(record: KoboImportRecord) -> tuple[ImportWarning, ...]:
    """
    PRE: record is the persisted result for an imported submission.
    POST: reconstructs only well-formed safe warnings from result metadata.
    """
    metadata = record.result_metadata if isinstance(record.result_metadata, dict) else {}
    raw_warnings = metadata.get("warnings", ())
    if not isinstance(raw_warnings, list):
        return ()
    warnings = []
    for raw_warning in raw_warnings:
        if not isinstance(raw_warning, dict):
            continue
        code = raw_warning.get("code")
        message = raw_warning.get("message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            warnings.append(ImportWarning(code=code, message=message))
    return tuple(warnings)


def _already_imported_result(
    submission: KoboSubmission,
    record: KoboImportRecord | None,
) -> KoboImportResult:
    """
    PRE: submission is locked and already has IMPORTED status.
    POST: returns its original traceable result without creating any side effect.
    """
    if record is None:
        return KoboImportResult(
            outcome=ImportOutcome.ALREADY_IMPORTED,
            submission_id=submission.pk,
            materialization_type=None,
            materialization_id=None,
            created=False,
            warnings=(),
            reason_code="LEGACY_IMPORT_RECORD_MISSING",
        )
    metadata = record.result_metadata if isinstance(record.result_metadata, dict) else {}
    return KoboImportResult(
        outcome=ImportOutcome.ALREADY_IMPORTED,
        submission_id=submission.pk,
        materialization_type=metadata.get("materialization_type"),
        materialization_id=record.target_object_id,
        created=bool(metadata.get("created", False)),
        warnings=_warnings_from_record(record),
    )


def _record_blocked_import(
    submission_id: int,
    *,
    reason_code: str,
    warnings: tuple[ImportWarning, ...] = (),
) -> KoboImportResult:
    """
    PRE: a controlled blocker aborted or prevented materialization.
    POST: records one safe retryable warning without changing lifecycle or timestamp.
    """
    with transaction.atomic():
        submission = _lock_submission_for_operational_import(submission_id)
        return _operational_import_failure(
            submission,
            error_code=reason_code,
            error_message="Kobo import is blocked by an unmet contract.",
            warnings=warnings,
        )


def _record_failed_import(submission_id: int) -> KoboImportResult:
    """
    PRE: the materialization transaction rolled back after an unexpected exception.
    POST: best-effort records a safe retryable technical processing incident.
    """
    with transaction.atomic():
        submission = _lock_submission_for_operational_import(submission_id)
        submission.status = KoboSubmission.Status.PROCESSING_FAILED
        submission.error_code = "MATERIALIZATION_FAILED"
        submission.error_message = "Kobo materialization failed safely."
        submission.save(update_fields=("status", "error_code", "error_message"))
    try:
        if not KoboProcessingEvent.objects.filter(
            submission_id=submission_id,
            stage="operational_import",
            code="MATERIALIZATION_FAILED",
        ).exists():
            KoboProcessingEvent.objects.create(
                submission_id=submission_id,
                stage="operational_import",
                level=KoboProcessingEvent.Level.ERROR,
                code="MATERIALIZATION_FAILED",
                message="Kobo materialization failed safely.",
            )
    except Exception:
        pass
    return KoboImportResult(
        outcome=ImportOutcome.FAILED,
        submission_id=submission_id,
        materialization_type=None,
        materialization_id=None,
        created=False,
        warnings=(),
        reason_code="MATERIALIZATION_FAILED",
    )


def _import_kobo_submission_with_handlers(
    submission: KoboSubmission,
    *,
    actor,
    handler_registry: Mapping[KoboFormType, KoboImportHandler],
) -> KoboImportResult:
    """
    PRE: submission is persisted; actor may import projects; handlers perform only
    transactional DB work and return traceable persisted target identifiers.
    POST: creates one import record before marking APPROVED_FOR_IMPORT as IMPORTED,
    returns an idempotent prior result, or records a retryable processing incident.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")
    if not getattr(actor, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated importer is required.")

    try:
        with transaction.atomic():
            locked_submission = _lock_submission_for_operational_import(submission.pk)
            existing_record = KoboImportRecord.objects.filter(
                submission=locked_submission
            ).first()
            if (
                existing_record is None
                and KoboPrioritizedMicroproject.objects.filter(
                    source_submission=locked_submission
                ).exists()
            ):
                raise KoboImportBlocked("FICHA_10_MICROPROJECT_STATE_CONFLICT")
            if (
                existing_record is None
                and KoboPrioritizationAssessment.objects.filter(
                    source_submission=locked_submission
                ).exists()
            ):
                raise KoboImportBlocked("FICHA_11_ASSESSMENT_STATE_CONFLICT")
            if locked_submission.status == KoboSubmission.Status.IMPORTED:
                return _already_imported_result(locked_submission, existing_record)
            if locked_submission.remote_update_pending:
                raise KoboImportBlocked("REMOTE_UPDATE_REVIEW_REQUIRED")
            if existing_record is not None:
                raise KoboImportBlocked("IMPORT_RECORD_STATE_CONFLICT")

            form_type = _validate_common_import_preconditions(
                locked_submission,
                actor=actor,
                handler_registry=handler_registry,
            )
            handler = handler_registry[form_type]
            validation_warnings = handler.validate_for_import(
                submission=locked_submission
            )
            materialization = handler.materialize(
                submission=locked_submission,
                actor=actor,
            )
            _validate_materialization_result(materialization)
            warnings = _merge_warnings(
                validation_warnings,
                materialization.warnings,
            )
            KoboImportRecord.objects.create(
                submission=locked_submission,
                handler_type=form_type.value,
                target_app_label=materialization.target_app_label,
                target_model=materialization.target_model,
                target_object_id=materialization.target_object_id,
                created_by=actor,
                result_metadata={
                    "materialization_type": materialization.materialization_type,
                    "created": materialization.created,
                    "warnings": [
                        {"code": warning.code, "message": warning.message}
                        for warning in warnings
                    ],
                },
            )

            from apps.operations.models import AuditLog
            from apps.operations.services import log_action

            imported_at = timezone.now()
            locked_submission.status = KoboSubmission.Status.IMPORTED
            locked_submission.imported_at = imported_at
            if locked_submission.processed_at is None:
                locked_submission.processed_at = imported_at
            locked_submission.error_code = ""
            locked_submission.error_message = ""
            locked_submission.save(
                update_fields=(
                    "status",
                    "imported_at",
                    "processed_at",
                    "error_code",
                    "error_message",
                )
            )
            KoboProcessingEvent.objects.create(
                submission=locked_submission,
                stage="operational_import",
                level=KoboProcessingEvent.Level.INFO,
                code="imported",
                message="Kobo submission materialized and imported.",
            )
            log_action(
                actor,
                AuditLog.Action.CREATED,
                locked_submission,
                "Ficha Kobo materializada e importada.",
            )
    except KoboImportBlocked as exc:
        return _record_blocked_import(
            submission.pk,
            reason_code=exc.reason_code,
            warnings=exc.warnings,
        )
    except Exception:
        return _record_failed_import(submission.pk)

    submission.status = KoboSubmission.Status.IMPORTED
    submission.imported_at = imported_at
    if submission.processed_at is None:
        submission.processed_at = imported_at
    submission.error_code = ""
    submission.error_message = ""
    return KoboImportResult(
        outcome=ImportOutcome.IMPORTED,
        submission_id=submission.pk,
        materialization_type=materialization.materialization_type,
        materialization_id=materialization.target_object_id,
        created=materialization.created,
        warnings=warnings,
    )


def import_kobo_submission(
    submission: KoboSubmission,
    *,
    actor,
) -> KoboImportResult:
    """
    PRE: submission is persisted and actor is the authorized import decision maker.
    POST: applies the closed production handler registry through the common service.
    """
    return _import_kobo_submission_with_handlers(
        submission,
        actor=actor,
        handler_registry=KOBO_IMPORT_HANDLERS,
    )
