from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    KoboRejectionResult,
    KoboRestoreResult,
    OperationalImportResult,
    REJECTION_REASON_LABELS,
)
from apps.integrations.kobo.errors import KoboConfigurationError, KoboPayloadError
from apps.integrations.kobo.models import KoboProcessingEvent, KoboSubmission


def _operational_import_failure(
    submission: KoboSubmission,
    *,
    error_code: str,
    error_message: str,
) -> OperationalImportResult:
    """
    PRE: submission is locked in an import transaction and remains reviewable.
    POST: records a non-sensitive import failure without changing its lifecycle.
    """
    submission.error_code = error_code
    submission.error_message = error_message
    submission.save(update_fields=("error_code", "error_message"))
    KoboProcessingEvent.objects.create(
        submission=submission,
        stage="operational_import",
        level=KoboProcessingEvent.Level.ERROR,
        code=error_code,
        message=error_message,
    )
    return OperationalImportResult(
        submission_id=submission.pk,
        project_id=submission.project_id,
        imported=False,
        already_imported=False,
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


def import_kobo_submission(
    submission: KoboSubmission,
    *,
    actor,
) -> OperationalImportResult:
    """
    PRE: submission is persisted and actor is an authenticated project operator.
    POST: atomically marks exactly one ready submission as imported, records a
    processing event and audit entry, or leaves it reviewable on failure.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")
    if not getattr(actor, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated importer is required.")

    with transaction.atomic():
        locked_submission = _lock_submission_for_operational_import(submission.pk)
        if locked_submission.status == KoboSubmission.Status.IMPORTED:
            return OperationalImportResult(
                submission_id=locked_submission.pk,
                project_id=locked_submission.project_id,
                imported=False,
                already_imported=True,
            )
        if locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW:
            return _operational_import_failure(
                locked_submission,
                error_code="import_state_invalid",
                error_message="Submission is not ready for operational import.",
            )
        if locked_submission.imported_at is not None:
            return _operational_import_failure(
                locked_submission,
                error_code="import_timestamp_invalid",
                error_message="Submission already has an import timestamp.",
            )
        if locked_submission.project is None:
            return _operational_import_failure(
                locked_submission,
                error_code="import_project_missing",
                error_message="Submission has no project assigned for import.",
            )

        from apps.operations.models import AuditLog, Project
        from apps.operations.services import log_action

        asset = locked_submission.asset
        expected_role = FORM_DEFINITION_ROLES.get(
            (
                locked_submission.form_definition.form_id,
                locked_submission.form_definition.version,
            )
        )
        if (
            asset is None
            or not asset.is_active
            or not locked_submission.form_definition.is_active
            or asset.form_definition_id != locked_submission.form_definition_id
            or asset.form_role != expected_role
        ):
            return _operational_import_failure(
                locked_submission,
                error_code="import_asset_invalid",
                error_message="Submission asset configuration is not valid for import.",
            )
        if locked_submission.project.status != Project.Status.ACTIVE:
            return _operational_import_failure(
                locked_submission,
                error_code="import_project_inactive",
                error_message="Submission project is not active for import.",
            )
        if (
            not isinstance(locked_submission.normalized_payload, dict)
            or not locked_submission.normalized_payload
        ):
            return _operational_import_failure(
                locked_submission,
                error_code="import_normalized_payload_missing",
                error_message="Submission has no normalized data for import.",
            )

        imported_at = timezone.now()
        locked_submission.status = KoboSubmission.Status.IMPORTED
        locked_submission.imported_at = imported_at
        locked_submission.error_code = ""
        locked_submission.error_message = ""
        locked_submission.save(
            update_fields=(
                "status",
                "imported_at",
                "error_code",
                "error_message",
            )
        )
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="operational_import",
            level=KoboProcessingEvent.Level.INFO,
            code="imported",
            message="Kobo submission imported into its assigned project.",
        )
        log_action(
            actor,
            AuditLog.Action.CREATED,
            locked_submission,
            "Ficha Kobo importada al proyecto.",
        )

    submission.status = KoboSubmission.Status.IMPORTED
    submission.imported_at = imported_at
    submission.error_code = ""
    submission.error_message = ""
    return OperationalImportResult(
        submission_id=submission.pk,
        project_id=locked_submission.project_id,
        imported=True,
        already_imported=False,
    )
