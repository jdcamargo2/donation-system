from datetime import tzinfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.db.models import Q

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    SyncResult,
    WebhookConvergenceResult,
)
from apps.integrations.kobo.errors import KoboConfigurationError, KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.form_registry import KoboFormType, list_registered_forms
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboSubmission,
    KoboSyncRun,
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


def _configured_ficha_01_asset_uid() -> str:
    # PRE: Django settings are loaded.
    # POST: returns the configured Ficha 1 asset UID or raises a safe error.
    asset_uid = settings.KOBO_FICHA_01_ASSET_UID
    if not asset_uid or not asset_uid.strip():
        raise KoboConfigurationError("KOBO_FICHA_01_ASSET_UID is required.")
    return asset_uid


def _validate_api_submission(
    form_definition: KoboFormDefinition,
    raw_payload: dict,
) -> str:
    # PRE: form_definition and raw_payload are candidates for Ficha 1 staging.
    # POST: returns the exact _uuid or raises KoboPayloadError without mutation.
    if (
        form_definition.form_id != FICHA_01_FORM_ID
        or form_definition.version != FICHA_01_VERSION
    ):
        raise KoboPayloadError("Submission does not belong to registered Ficha 1.")
    if not isinstance(raw_payload, dict):
        raise KoboPayloadError("Kobo submission payload must be an object.")

    external_id = raw_payload.get("_uuid")
    if not isinstance(external_id, str) or not external_id.strip():
        raise KoboPayloadError("Kobo submission _uuid must be a non-empty string.")

    configured_asset_uid = _configured_ficha_01_asset_uid()
    if raw_payload.get("_xform_id_string") != configured_asset_uid:
        raise KoboPayloadError("Kobo submission asset UID does not match Ficha 1.")

    instance_id = raw_payload.get("meta/instanceID")
    if instance_id is not None and instance_id != f"uuid:{external_id}":
        raise KoboPayloadError("Kobo submission instanceID is inconsistent.")
    return external_id


def receive_api_submission(
    form_definition: KoboFormDefinition,
    raw_payload: dict,
) -> tuple[KoboSubmission, bool]:
    """
    PRE: form_definition is the active Ficha 1 definition and raw_payload contains
    a valid non-empty _uuid.
    POST: returns the existing submission or creates it as received, preserving
    raw_payload without normalization, attachments, or operations changes.
    """
    external_id = _validate_api_submission(form_definition, raw_payload)
    return KoboSubmission.objects.get_or_create(
        form_definition=form_definition,
        external_id=external_id,
        defaults={
            "raw_payload": raw_payload,
            "status": KoboSubmission.Status.RECEIVED,
        },
    )


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
    POST: serializes processing and explicit per-form routing until the persisted
    row is normalized with a durable routing outcome.
    """
    with transaction.atomic():
        submission = KoboSubmission.objects.select_for_update().get(pk=submission_id)
        if submission.status in PROCESSABLE_STATUSES:
            process_submission(submission, default_timezone=default_timezone)
            submission.refresh_from_db()

        if submission.status == KoboSubmission.Status.READY_FOR_REVIEW:
            routing = route_normalized_submission(submission)
            submission.refresh_from_db()
            return WebhookConvergenceResult(
                submission_id=submission.pk,
                final_status=submission.status,
                completed=(
                    routing.status.value
                    != KoboSubmission.RoutingStatus.UNRESOLVED
                    and (
                        routing.form_type != KoboFormType.FICHA_1
                        or submission.project_id is not None
                    )
                ),
            )

        return WebhookConvergenceResult(
            submission_id=submission.pk,
            final_status=submission.status,
            completed=submission.status not in PROCESSABLE_STATUSES,
        )


def _record_invalid_payload_event(
    form_definition: KoboFormDefinition,
    raw_payload: object,
) -> None:
    # PRE: raw_payload failed validation and must not be stored or logged.
    # POST: creates a safe event only when its _uuid identifies existing staging.
    if not isinstance(raw_payload, dict):
        return
    external_id = raw_payload.get("_uuid")
    if not isinstance(external_id, str) or not external_id.strip():
        return
    submission = KoboSubmission.objects.filter(
        form_definition=form_definition,
        external_id=external_id,
    ).first()
    if submission is None:
        return
    KoboProcessingEvent.objects.create(
        submission=submission,
        stage="receive",
        level=KoboProcessingEvent.Level.ERROR,
        code="invalid_payload",
        message="Kobo submission failed staging validation.",
    )


def sync_ficha_01_submissions(
    client: KoboApiClient,
    asset_uid: str,
    limit: int = 100,
    dry_run: bool = False,
) -> SyncResult:
    """
    PRE: asset_uid is non-empty, limit is positive, and Ficha 1 is registered.
    POST: fetches only configured Ficha 1 results, processes each independently,
    persists nothing in dry-run, and returns explicit outcome counts.
    """
    configured_asset_uid = _configured_ficha_01_asset_uid()
    if not asset_uid or asset_uid != configured_asset_uid:
        raise KoboConfigurationError("Ficha 1 asset UID does not match configuration.")
    if limit <= 0:
        raise KoboConfigurationError("Kobo submission limit must be positive.")

    try:
        form_definition = KoboFormDefinition.objects.get(
            form_id=FICHA_01_FORM_ID,
            version=FICHA_01_VERSION,
        )
    except KoboFormDefinition.DoesNotExist as exc:
        raise KoboConfigurationError(
            "Registered Ficha 1 definition must be synchronized first."
        ) from exc

    asset = KoboAsset.objects.filter(asset_uid=configured_asset_uid).first()
    sync_run = None if dry_run else KoboSyncRun.objects.create(
        asset=asset,
        kind=KoboSyncRun.Kind.SUBMISSIONS,
        status=KoboSyncRun.Status.RUNNING,
    )
    created_count = 0
    existing_count = 0
    failed_count = 0
    fetched_count = 0
    partial = False
    try:
        raw_submissions = client.iter_submissions(configured_asset_uid, limit=limit) if hasattr(client, "iter_submissions") else iter(client.get_submissions(configured_asset_uid, limit=limit))
        for raw_payload in raw_submissions:
            fetched_count += 1
            try:
                external_id = _validate_api_submission(form_definition, raw_payload)
                if dry_run:
                    exists = KoboSubmission.objects.filter(form_definition=form_definition, external_id=external_id).exists()
                    existing_count += int(exists)
                    created_count += int(not exists)
                    continue
                _, created = receive_api_submission(form_definition, raw_payload)
                created_count += int(created)
                existing_count += int(not created)
            except KoboPayloadError:
                failed_count += 1
                if not dry_run:
                    _record_invalid_payload_event(form_definition, raw_payload)
    except KoboIntegrationError:
        partial = fetched_count > 0
        if not partial:
            if sync_run:
                sync_run.status = KoboSyncRun.Status.FAILED
                sync_run.error_code = "remote_error"
                sync_run.safe_error_message = "Kobo remote synchronization failed."
                sync_run.finished_at = timezone.now()
                sync_run.save()
            raise
    if sync_run:
        sync_run.status = KoboSyncRun.Status.PARTIAL if partial else KoboSyncRun.Status.SUCCEEDED
        sync_run.partial = partial
        sync_run.finished_at = timezone.now()
        sync_run.items_seen = fetched_count
        sync_run.items_created = created_count
        sync_run.items_updated = existing_count
        sync_run.items_failed = failed_count
        sync_run.save()

    return SyncResult(
        fetched_count=fetched_count,
        created_count=created_count,
        existing_count=existing_count,
        failed_count=failed_count,
        pages_fetched=0 if not fetched_count else 1,
        partial=partial,
    )
