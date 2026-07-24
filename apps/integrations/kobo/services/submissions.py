from datetime import tzinfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.db.models import Q

from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    WebhookConvergenceResult,
)
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.form_registry import KoboFormType, list_registered_forms
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.processors import PROCESSABLE_STATUSES, process_submission
from apps.integrations.kobo.services.territorial_routing import route_normalized_submission


def sync_registered_forms() -> int:
    """
    PRE: the registry is defined and KoboFormDefinition has been migrated.
    POST: activates exact registered definitions, deactivates all other history,
    removes none, and returns the registered-definition count.
    """
    registered_forms = list_registered_forms()
    for registered_form in registered_forms:
        KoboFormDefinition.objects.update_or_create(
            form_id=registered_form.form_id,
            version=registered_form.version,
            defaults={
                "title": registered_form.title,
                "schema_snapshot": {},
                "field_mapping": {},
                "is_active": True,
            },
        )
    supported_definitions = Q()
    for registered_form in registered_forms:
        supported_definitions |= Q(
            form_id=registered_form.form_id,
            version=registered_form.version,
        )
    KoboFormDefinition.objects.exclude(supported_definitions).update(is_active=False)

    return len(registered_forms)


def receive_webhook_submission(*, asset: KoboAsset, raw_payload: dict) -> tuple[KoboSubmission, bool]:
    """
    PRE: asset is active with an exact registered form/role and raw_payload is JSON.
    POST: stages one immutable webhook submission idempotently without operations effects.
    """
    if not isinstance(raw_payload, dict):
        raise KoboPayloadError("Kobo webhook payload must be an object.")
    if asset is None or asset.pk is None or not asset.is_active:
        raise KoboPayloadError("Kobo webhook asset is unavailable.")
    form_definition = asset.form_definition
    if not form_definition.is_active:
        raise KoboPayloadError("Kobo webhook form definition is inactive.")
    expected_role = FORM_DEFINITION_ROLES.get(
        (form_definition.form_id, form_definition.version)
    )
    if asset.form_role != expected_role:
        raise KoboPayloadError("Kobo webhook asset role is incompatible.")
    external_id = raw_payload.get("_uuid")
    if not isinstance(external_id, str) or not external_id.strip():
        raise KoboPayloadError("Kobo submission _uuid must be a non-empty string.")
    if raw_payload.get("_xform_id_string") != asset.asset_uid:
        raise KoboPayloadError("Kobo submission asset UID does not match configuration.")
    discovered_asset = KoboDiscoveredAsset.objects.filter(
        asset_uid=asset.asset_uid
    ).only("metadata_snapshot").first()
    if discovered_asset is None:
        raise KoboPayloadError("Kobo asset metadata is unavailable.")
    metadata = discovered_asset.metadata_snapshot
    if (
        not isinstance(metadata, dict)
        or metadata.get("id_string") != form_definition.form_id
        or (
            metadata.get("version") is not None
            and metadata["version"] != form_definition.version
        )
    ):
        raise KoboPayloadError("Kobo asset metadata is incompatible with configuration.")
    instance_id = raw_payload.get("meta/instanceID")
    if instance_id is not None and instance_id != f"uuid:{external_id}":
        raise KoboPayloadError("Kobo submission instanceID is inconsistent.")
    try:
        submission, created = KoboSubmission.objects.get_or_create(
            form_definition=form_definition,
            external_id=external_id,
            defaults={
                "asset": asset,
                "raw_payload": raw_payload,
                "status": KoboSubmission.Status.RECEIVED,
            },
        )
    except IntegrityError:
        submission = KoboSubmission.objects.get(
            form_definition=form_definition,
            external_id=external_id,
        )
        created = False
    if created:
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="webhook",
            level=KoboProcessingEvent.Level.INFO,
            code="webhook_received",
            message="Kobo webhook submission received.",
        )
    return submission, created


def converge_webhook_submission(
    submission_id: int,
    *,
    default_timezone: tzinfo,
) -> WebhookConvergenceResult:
    """
    PRE: submission_id identifies a staged webhook submission and no remote work is required.
    POST: normalizes, routes, and auto-imports when eligible. Durable incident states count
    as completed so Kobo does not retry forever; valid forms are never left for human approval.
    """
    from apps.integrations.kobo.services.automation import auto_import_if_eligible

    with transaction.atomic():
        submission = KoboSubmission.objects.select_for_update().get(pk=submission_id)
        if submission.status in PROCESSABLE_STATUSES:
            process_submission(submission, default_timezone=default_timezone)
            submission.refresh_from_db()

        if submission.status == KoboSubmission.Status.READY_FOR_REVIEW:
            route_normalized_submission(submission)

    submission = KoboSubmission.objects.get(pk=submission_id)
    if submission.status in {
        KoboSubmission.Status.READY_FOR_REVIEW,
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
        KoboSubmission.Status.VALIDATION_FAILED,
        KoboSubmission.Status.PROCESSING_FAILED,
    }:
        auto_import_if_eligible(submission)
        submission.refresh_from_db()

    return WebhookConvergenceResult(
        submission_id=submission.pk,
        final_status=submission.status,
        completed=submission.status not in PROCESSABLE_STATUSES,
    )
