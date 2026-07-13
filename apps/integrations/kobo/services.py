from dataclasses import dataclass, replace

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.integrations.kobo.client import KoboApiClient, KoboRemoteAsset
from apps.integrations.kobo.errors import (
    KoboConfigurationError,
    KoboPayloadError,
)
from apps.integrations.kobo.form_registry import get_registered_form, list_registered_forms
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboProjectBinding,
    KoboSubmission,
)
from apps.integrations.kobo.processors import (
    PROCESSABLE_STATUSES,
    process_submission,
)


FORM_DEFINITION_ROLES = {
    (FICHA_01_FORM_ID, FICHA_01_VERSION): KoboAsset.FormRole.TERRITORIAL_PROFILE,
    (FICHA_10_FORM_ID, FICHA_10_VERSION): KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
    (FICHA_11_FORM_ID, FICHA_11_VERSION): KoboAsset.FormRole.PRIORITIZATION_MATRIX,
}


@dataclass(frozen=True)
class SyncResult:
    fetched_count: int
    created_count: int
    existing_count: int
    failed_count: int


@dataclass(frozen=True)
class ProcessingBatchResult:
    selected_count: int
    processed_count: int
    ready_count: int
    validation_failed_count: int
    processing_failed_count: int
    skipped_count: int


@dataclass(frozen=True)
class ReviewResult:
    submission_id: int
    previous_status: str
    final_status: str
    reviewed_by_id: int


@dataclass(frozen=True)
class ProjectAssociationResult:
    submission_id: int
    asset_id: int | None
    project_id: int | None
    previous_status: str
    final_status: str
    associated: bool


@dataclass(frozen=True)
class RoutingResolution:
    binding_id: int
    asset_id: int
    project_id: int
    routing_type: str
    source_field: str
    source_value: str


@dataclass(frozen=True)
class AssetDiscoveryResult:
    fetched_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    unavailable_count: int
    failed_count: int


@dataclass(frozen=True)
class AssetReadiness:
    ready: bool
    code: str
    message: str
    routing_type: str | None
    active_binding_count: int


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
    submission, created = KoboSubmission.objects.get_or_create(
        form_definition=form_definition,
        external_id=external_id,
        defaults={
            "asset": asset,
            "raw_payload": raw_payload,
            "status": KoboSubmission.Status.RECEIVED,
        },
    )
    if created:
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="webhook",
            level=KoboProcessingEvent.Level.INFO,
            code="webhook_received",
            message="Kobo webhook submission received.",
        )
    return submission, created


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

    raw_submissions = client.get_submissions(configured_asset_uid, limit=limit)
    created_count = 0
    existing_count = 0
    failed_count = 0

    for raw_payload in raw_submissions:
        try:
            external_id = _validate_api_submission(form_definition, raw_payload)
            if dry_run:
                exists = KoboSubmission.objects.filter(
                    form_definition=form_definition,
                    external_id=external_id,
                ).exists()
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

    return SyncResult(
        fetched_count=len(raw_submissions),
        created_count=created_count,
        existing_count=existing_count,
        failed_count=failed_count,
    )


def process_pending_submissions(
    *,
    limit: int = 100,
    default_timezone,
) -> ProcessingBatchResult:
    """
    PRE: limit is positive and default_timezone is supplied by the caller.
    POST: processes oldest retryable submissions independently up to limit and
    returns aggregate, non-sensitive counts.
    """
    if limit <= 0:
        raise KoboConfigurationError("Kobo processing limit must be positive.")

    submissions = list(
        KoboSubmission.objects.filter(status__in=PROCESSABLE_STATUSES)
        .order_by("received_at", "pk")[:limit]
    )
    processed_count = 0
    ready_count = 0
    validation_failed_count = 0
    processing_failed_count = 0
    skipped_count = 0

    for submission in submissions:
        try:
            outcome = process_submission(
                submission,
                default_timezone=default_timezone,
            )
        except Exception:
            processing_failed_count += 1
            continue
        processed_count += int(outcome.processed)
        skipped_count += int(not outcome.processed)
        ready_count += int(
            outcome.final_status == KoboSubmission.Status.READY_FOR_REVIEW
        )
        validation_failed_count += int(
            outcome.final_status == KoboSubmission.Status.VALIDATION_FAILED
        )
        processing_failed_count += int(
            outcome.final_status == KoboSubmission.Status.PROCESSING_FAILED
        )

    return ProcessingBatchResult(
        selected_count=len(submissions),
        processed_count=processed_count,
        ready_count=ready_count,
        validation_failed_count=validation_failed_count,
        processing_failed_count=processing_failed_count,
        skipped_count=skipped_count,
    )


def review_submission(
    submission: KoboSubmission,
    *,
    decision: str,
    reason: str,
    reviewed_by,
) -> ReviewResult:
    """
    PRE: submission is ready, decision is valid, reviewer is authenticated, and
    rejection includes a reason.
    POST: atomically records the terminal review state and event without payload,
    operations, or publication changes, and returns an explicit result.
    """
    valid_decisions = {
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
        KoboSubmission.Status.REJECTED,
    }
    if decision not in valid_decisions:
        raise KoboPayloadError("Review decision is invalid.")
    if not getattr(reviewed_by, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated reviewer is required.")
    cleaned_reason = reason.strip()
    if decision == KoboSubmission.Status.REJECTED and not cleaned_reason:
        raise KoboPayloadError("A rejection reason is required.")

    event_message = cleaned_reason or "Submission approved for import."
    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        if locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW:
            raise KoboPayloadError("Submission is not ready for review.")
        previous_status = locked_submission.status
        locked_submission.status = decision
        locked_submission.save(update_fields=("status",))
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="review",
            level=KoboProcessingEvent.Level.INFO,
            code=decision,
            message=event_message,
        )
    submission.status = decision
    return ReviewResult(
        submission_id=submission.pk,
        previous_status=previous_status,
        final_status=submission.status,
        reviewed_by_id=reviewed_by.pk,
    )


def _association_failure(
    submission: KoboSubmission,
    *,
    previous_status: str,
    error_code: str,
    error_message: str,
) -> ProjectAssociationResult:
    # PRE: submission is locked inside an atomic association attempt.
    # POST: preserves review status, records a safe warning, and returns failure.
    submission.error_code = error_code
    submission.error_message = error_message
    submission.save(update_fields=("error_code", "error_message"))
    KoboProcessingEvent.objects.create(
        submission=submission,
        stage="project_association",
        level=KoboProcessingEvent.Level.WARNING,
        code=error_code,
        message=error_message,
    )
    return ProjectAssociationResult(
        submission_id=submission.pk,
        asset_id=None,
        project_id=None,
        previous_status=previous_status,
        final_status=submission.status,
        associated=False,
    )


def resolve_routing_field(
    submission: KoboSubmission,
    source_field: str,
) -> str:
    """
    PRE: source_field starts with submission. or payload.
    POST: returns a non-empty textual whitelisted/model or normalized payload
    value without raw payload access, arbitrary getattr, paths, indices, or calls.
    """
    try:
        prefix, field_name = validate_routing_source_field(source_field)
    except ValidationError as exc:
        raise KoboPayloadError(str(exc.message)) from exc

    if prefix == "submission":
        submission_values = {
            "pastoral_zone": submission.pastoral_zone,
            "parish": submission.parish,
            "primary_community": submission.primary_community,
            "external_id": submission.external_id,
        }
        if field_name not in submission_values:
            raise KoboPayloadError("Routing submission field is not allowed.")
        value = submission_values[field_name]
    elif prefix == "payload":
        if not isinstance(submission.normalized_payload, dict):
            raise KoboPayloadError("Normalized routing payload is unavailable.")
        if field_name not in submission.normalized_payload:
            raise KoboPayloadError("Normalized routing field is missing.")
        value = submission.normalized_payload[field_name]
    else:
        raise KoboPayloadError("Routing source field prefix is invalid.")

    if not isinstance(value, str) or not value.strip():
        raise KoboPayloadError("Routing source value must be non-empty text.")
    return value


def validate_routing_source_field(source_field: str) -> tuple[str, str]:
    """
    PRE: source_field is candidate direct model or normalized-payload routing data.
    POST: returns its safe prefix/key or raises ValidationError without data access.
    """
    if not isinstance(source_field, str) or "." not in source_field:
        raise ValidationError("Routing source field prefix is invalid.")
    if any(marker in source_field for marker in ("/", "[", "]", "(", ")", " ")):
        raise ValidationError("Routing source field is not allowed.")
    prefix, field_name = source_field.split(".", 1)
    if not field_name or field_name.startswith("_") or "." in field_name:
        raise ValidationError("Routing source field is not allowed.")
    allowed_submission_fields = {
        "pastoral_zone",
        "parish",
        "primary_community",
        "external_id",
    }
    if prefix == "submission" and field_name not in allowed_submission_fields:
        raise ValidationError("Routing submission field is not allowed.")
    if prefix not in {"submission", "payload"}:
        raise ValidationError("Routing source field prefix is invalid.")
    return prefix, field_name


def _routing_resolution(binding: KoboProjectBinding) -> RoutingResolution:
    # PRE: binding is the single exact active routing match.
    # POST: returns immutable identifiers and route metadata without modification.
    return RoutingResolution(
        binding_id=binding.pk,
        asset_id=binding.asset_id,
        project_id=binding.project_id,
        routing_type=binding.routing_type,
        source_field=binding.source_field,
        source_value=binding.source_value,
    )


def resolve_project_binding(
    submission: KoboSubmission,
    asset: KoboAsset,
) -> RoutingResolution:
    """
    PRE: submission is normalized, asset is active, and any assigned asset agrees.
    POST: returns the sole exact active direct/field-value binding, ignores inactive
    bindings, performs no project-name lookup, and never modifies submission.
    """
    if not asset.is_active:
        raise KoboConfigurationError("routing_asset_inactive")
    if submission.asset_id is not None and submission.asset_id != asset.pk:
        raise KoboConfigurationError("routing_asset_mismatch")
    if not isinstance(submission.normalized_payload, dict):
        raise KoboPayloadError("Normalized routing payload is unavailable.")

    active_bindings = KoboProjectBinding.objects.filter(
        asset=asset,
        is_active=True,
    ).select_related("project")
    direct_bindings = list(
        active_bindings.filter(routing_type=KoboProjectBinding.RoutingType.DIRECT)
    )
    if len(direct_bindings) > 1:
        raise KoboConfigurationError("routing_ambiguous")
    if direct_bindings:
        from apps.operations.models import Project

        if direct_bindings[0].project.status != Project.Status.ACTIVE:
            raise KoboConfigurationError("routing_project_inactive")
        return _routing_resolution(direct_bindings[0])

    matches = []
    for binding in active_bindings.filter(
        routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE
    ):
        resolved_value = resolve_routing_field(submission, binding.source_field)
        if resolved_value == binding.source_value:
            matches.append(binding)
    if not matches:
        raise KoboConfigurationError("routing_not_found")
    if len(matches) > 1:
        raise KoboConfigurationError("routing_ambiguous")
    from apps.operations.models import Project

    if matches[0].project.status != Project.Status.ACTIVE:
        raise KoboConfigurationError("routing_project_inactive")
    return _routing_resolution(matches[0])


def assign_normalized_submission_to_direct_project(
    submission: KoboSubmission,
) -> bool:
    """
    PRE: submission is persisted, normalized, and ready for review.
    POST: assigns the sole active direct-binding project and processed timestamp,
    or records a safe routing failure without changing the review status.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")

    def fail() -> bool:
        with transaction.atomic():
            locked_submission = KoboSubmission.objects.select_for_update().get(
                pk=submission.pk
            )
            locked_submission.error_code = "routing_configuration_error"
            locked_submission.error_message = "Kobo project routing could not be resolved."
            locked_submission.save(update_fields=("error_code", "error_message"))
            KoboProcessingEvent.objects.create(
                submission=locked_submission,
                stage="project_routing",
                level=KoboProcessingEvent.Level.ERROR,
                code="routing_configuration_error",
                message="Kobo project routing could not be resolved.",
            )
        submission.error_code = "routing_configuration_error"
        submission.error_message = "Kobo project routing could not be resolved."
        return False

    try:
        locked_submission = KoboSubmission.objects.select_related(
            "asset__form_definition"
        ).get(pk=submission.pk)
        asset = locked_submission.asset
        if (
            locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW
            or asset is None
            or not asset.is_active
            or not asset.form_definition.is_active
            or asset.form_role
            != FORM_DEFINITION_ROLES.get(
                (asset.form_definition.form_id, asset.form_definition.version)
            )
        ):
            return fail()
        bindings = list(
            asset.project_bindings.filter(is_active=True).select_related("project")
        )
        if (
            len(bindings) != 1
            or bindings[0].routing_type != KoboProjectBinding.RoutingType.DIRECT
        ):
            return fail()
        from apps.operations.models import Project

        if bindings[0].project.status != Project.Status.ACTIVE:
            return fail()
    except (KoboAsset.DoesNotExist, KoboSubmission.DoesNotExist):
        return fail()

    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        if (
            locked_submission.project_id == bindings[0].project_id
            and locked_submission.processed_at is not None
        ):
            return True
        locked_submission.project_id = bindings[0].project_id
        locked_submission.processed_at = timezone.now()
        locked_submission.error_code = ""
        locked_submission.error_message = ""
        locked_submission.save(
            update_fields=(
                "project",
                "processed_at",
                "error_code",
                "error_message",
            )
        )
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="project_routing",
            level=KoboProcessingEvent.Level.INFO,
            code="project_assigned",
            message="Kobo submission assigned to its direct project.",
        )
    submission.project_id = bindings[0].project_id
    submission.processed_at = locked_submission.processed_at
    submission.error_code = ""
    submission.error_message = ""
    return True


def associate_submission_with_project(
    submission: KoboSubmission,
    *,
    reviewed_by,
) -> ProjectAssociationResult:
    """
    PRE: feature enablement was checked at the view boundary; submission is
    approved with dict payload/zone and reviewed_by is authenticated.
    POST: atomically resolves exact active asset/binding, imports the association,
    or records a safe expected warning without modifying either payload.
    """
    if not getattr(reviewed_by, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated reviewer is required.")

    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        previous_status = locked_submission.status
        if previous_status == KoboSubmission.Status.IMPORTED:
            return ProjectAssociationResult(
                submission_id=locked_submission.pk,
                asset_id=locked_submission.asset_id,
                project_id=locked_submission.project_id,
                previous_status=previous_status,
                final_status=previous_status,
                associated=False,
            )
        if previous_status != KoboSubmission.Status.APPROVED_FOR_IMPORT:
            raise KoboPayloadError("Submission is not approved for project association.")

        if not isinstance(locked_submission.raw_payload, dict):
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="invalid_raw_payload",
                error_message="Submission payload is unavailable for association.",
            )
        asset_uid = locked_submission.raw_payload.get("_xform_id_string")
        if not isinstance(asset_uid, str) or not asset_uid.strip():
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_uid_missing",
                error_message="Kobo asset identifier is missing.",
            )
        try:
            asset = KoboAsset.objects.get(asset_uid=asset_uid)
        except KoboAsset.DoesNotExist:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_not_found",
                error_message="Configured Kobo asset was not found.",
            )
        if not asset.is_active:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_inactive",
                error_message="Configured Kobo asset is inactive.",
            )
        expected_role = FORM_DEFINITION_ROLES.get(
            (asset.form_definition.form_id, asset.form_definition.version)
        )
        if asset.form_role != expected_role:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_role_incompatible",
                error_message="Kobo asset role is incompatible with this submission.",
            )

        try:
            routing = resolve_project_binding(locked_submission, asset)
        except KoboConfigurationError as exc:
            error_code = str(exc)
            if error_code not in {"routing_not_found", "routing_ambiguous"}:
                error_code = "routing_configuration_error"
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code=error_code,
                error_message="No unique active project route matches this submission.",
            )
        except KoboPayloadError:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="routing_value_invalid",
                error_message="Submission routing data is invalid or unavailable.",
            )

        associated_at = timezone.now()
        locked_submission.asset = asset
        locked_submission.project_id = routing.project_id
        locked_submission.imported_at = associated_at
        if locked_submission.processed_at is None:
            locked_submission.processed_at = associated_at
        locked_submission.status = KoboSubmission.Status.IMPORTED
        locked_submission.error_code = ""
        locked_submission.error_message = ""
        locked_submission.save(
            update_fields=(
                "asset",
                "project",
                "imported_at",
                "processed_at",
                "status",
                "error_code",
                "error_message",
            )
        )
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="project_association",
            level=KoboProcessingEvent.Level.INFO,
            code="project_associated",
            message="Submission associated with its configured project.",
        )

    submission.asset_id = asset.pk
    submission.project_id = routing.project_id
    submission.imported_at = associated_at
    submission.processed_at = locked_submission.processed_at
    submission.status = locked_submission.status
    submission.error_code = ""
    submission.error_message = ""
    return ProjectAssociationResult(
        submission_id=submission.pk,
        asset_id=asset.pk,
        project_id=routing.project_id,
        previous_status=previous_status,
        final_status=submission.status,
        associated=True,
    )


def get_project_imported_submissions(
    project,
    *,
    form_role=None,
):
    """
    PRE: project exists.
    POST: returns imported submissions for the exact project and active assets,
    optionally filtered by role, ordered by assessment and receipt descending,
    with related review data loaded and without modifying state.
    """
    downloaded_attachments = KoboAttachment.objects.filter(
        status=KoboAttachment.Status.DOWNLOADED
    ).order_by("pk")
    queryset = (
        KoboSubmission.objects.filter(
            project=project,
            status=KoboSubmission.Status.IMPORTED,
            asset__is_active=True,
        )
        .select_related("form_definition", "asset", "project")
        .prefetch_related(
            Prefetch(
                "attachments",
                queryset=downloaded_attachments,
                to_attr="downloaded_attachments",
            ),
            "processing_events",
        )
        .annotate(
            attachment_count=Count("attachments", distinct=True),
            downloaded_attachment_count=Count(
                "attachments",
                filter=Q(attachments__status=KoboAttachment.Status.DOWNLOADED),
                distinct=True,
            ),
        )
        .order_by("-assessment_date", "-received_at")
    )
    if form_role is not None:
        queryset = queryset.filter(asset__form_role=form_role)
    return queryset


def _remote_asset_values(remote_asset: KoboRemoteAsset) -> dict:
    # PRE: remote_asset is a validated safe client projection.
    # POST: returns only fields permitted in discovery staging.
    return {
        "name": remote_asset.name,
        "asset_type": remote_asset.asset_type,
        "deployment_status": remote_asset.deployment_status,
        "owner_username": remote_asset.owner_username,
        "remote_created_at": remote_asset.created_at,
        "remote_modified_at": remote_asset.modified_at,
        "metadata_snapshot": remote_asset.safe_metadata,
        "is_available": True,
    }


def discover_assets(
    client: KoboApiClient,
    *,
    limit: int = 100,
    dry_run: bool = False,
) -> AssetDiscoveryResult:
    """
    PRE: feature enablement was checked at the command boundary and client is
    configured.
    POST: after a complete successful fetch, creates/updates discovery staging,
    marks unseen history unavailable unless dry-run, and creates no integrations.
    """
    listed_assets = client.list_assets(limit=limit)
    remote_assets = []
    detail_failures = 0
    for remote_asset in listed_assets:
        try:
            detail = client.get_asset_detail(remote_asset.asset_uid)
        except (KoboIntegrationError, KoboPayloadError):
            detail_failures += 1
            remote_assets.append(remote_asset)
            continue
        metadata = {
            **remote_asset.safe_metadata,
            **{key: value for key, value in detail.items() if value is not None},
        }
        remote_assets.append(replace(remote_asset, safe_metadata=metadata))
    remote_by_uid = {asset.asset_uid: asset for asset in remote_assets}
    existing_by_uid = {
        asset.asset_uid: asset
        for asset in KoboDiscoveredAsset.objects.filter(
            asset_uid__in=remote_by_uid
        )
    }
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    for asset_uid, remote_asset in remote_by_uid.items():
        existing = existing_by_uid.get(asset_uid)
        if existing is None:
            created_count += 1
            continue
        expected_values = _remote_asset_values(remote_asset)
        has_changes = any(
            getattr(existing, field_name) != value
            for field_name, value in expected_values.items()
        )
        updated_count += int(has_changes)
        unchanged_count += int(not has_changes)

    unavailable_queryset = KoboDiscoveredAsset.objects.filter(
        is_available=True
    ).exclude(asset_uid__in=remote_by_uid)
    unavailable_count = unavailable_queryset.count()
    if not dry_run:
        seen_at = timezone.now()
        with transaction.atomic():
            for asset_uid, remote_asset in remote_by_uid.items():
                values = _remote_asset_values(remote_asset)
                values["last_seen_at"] = seen_at
                KoboDiscoveredAsset.objects.update_or_create(
                    asset_uid=asset_uid,
                    defaults=values,
                )
            unavailable_queryset.update(is_available=False)

    return AssetDiscoveryResult(
        fetched_count=len(remote_assets),
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        unavailable_count=0 if dry_run else unavailable_count,
        failed_count=detail_failures,
    )


def _require_authenticated_actor(actor, *, action: str) -> None:
    # PRE: actor is the caller supplied for an auditable configuration action.
    # POST: returns only for an authenticated user; otherwise raises safely.
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ValidationError(f"An authenticated user is required to {action}.")


def _require_registered_active_definition(
    form_definition: KoboFormDefinition,
) -> None:
    # PRE: form_definition is a persisted candidate configuration definition.
    # POST: accepts only an active exact entry in the local supported-form registry.
    if form_definition is None or form_definition.pk is None:
        raise ValidationError("Form definition must exist.")
    if not form_definition.is_active:
        raise ValidationError("Form definition must be active.")
    try:
        get_registered_form(form_definition.form_id, form_definition.version)
    except KoboPayloadError as exc:
        raise ValidationError("Form definition is not registered locally.") from exc


def configure_discovered_asset(
    discovered_asset: KoboDiscoveredAsset,
    *,
    name: str,
    form_definition: KoboFormDefinition,
    form_role: str,
    configured_by,
) -> KoboAsset:
    """
    PRE: discovered asset is available and unconfigured; definition, role and
    authenticated actor are valid.
    POST: creates one inactive KoboAsset only, using the exact discovered UID.
    """
    _require_authenticated_actor(configured_by, action="configure a Kobo asset")
    if discovered_asset is None or discovered_asset.pk is None:
        raise ValidationError("Discovered asset must exist.")
    if not discovered_asset.is_available:
        raise ValidationError("Unavailable discovered assets cannot be configured.")
    _require_registered_active_definition(form_definition)
    valid_roles = {value for value, _label in KoboAsset.FormRole.choices}
    if form_role not in valid_roles:
        raise ValidationError("Kobo asset form role is invalid.")
    expected_role = FORM_DEFINITION_ROLES.get(
        (form_definition.form_id, form_definition.version)
    )
    if form_role != expected_role:
        raise ValidationError("Kobo asset role is incompatible with its form definition.")
    clean_name = name.strip() if isinstance(name, str) else ""
    if not clean_name:
        raise ValidationError("Kobo asset name is required.")

    with transaction.atomic():
        if KoboAsset.objects.filter(asset_uid=discovered_asset.asset_uid).exists():
            raise ValidationError("This discovered asset is already configured.")
        asset = KoboAsset(
            asset_uid=discovered_asset.asset_uid,
            name=clean_name,
            form_definition=form_definition,
            form_role=form_role,
            is_active=False,
        )
        asset.full_clean()
        asset.save()
    return asset


def create_project_binding(
    asset: KoboAsset,
    *,
    routing_type: str,
    project,
    source_field: str,
    source_value: str,
    is_active: bool,
    configured_by,
) -> KoboProjectBinding:
    """
    PRE: asset/project exist, actor is authenticated, and routing is coherent.
    POST: creates one valid binding without activating the asset or other effects.
    """
    _require_authenticated_actor(configured_by, action="create a Kobo binding")
    if asset is None or asset.pk is None:
        raise ValidationError("Kobo asset must exist.")
    if project is None or project.pk is None:
        raise ValidationError("Project must exist.")
    if routing_type not in KoboProjectBinding.RoutingType.values:
        raise ValidationError("Kobo binding routing type is invalid.")
    source_field = source_field.strip() if isinstance(source_field, str) else ""
    source_value = source_value.strip() if isinstance(source_value, str) else ""
    if routing_type == KoboProjectBinding.RoutingType.DIRECT:
        if source_field or source_value:
            raise ValidationError("Direct routing requires empty source fields.")
    else:
        if not source_field or not source_value:
            raise ValidationError("Field-value routing requires field and value.")
        validate_routing_source_field(source_field)

    if is_active:
        active_routes = set(
            asset.project_bindings.filter(is_active=True).values_list(
                "routing_type", flat=True
            )
        )
        if active_routes and routing_type not in active_routes:
            raise ValidationError("Active Kobo routing strategies cannot be mixed.")

    binding = KoboProjectBinding(
        asset=asset,
        project=project,
        routing_type=routing_type,
        source_field=source_field,
        source_value=source_value,
        is_active=bool(is_active),
    )
    binding.full_clean()
    binding.save()
    return binding


def link_asset_to_project(
    asset: KoboAsset,
    *,
    project,
    linked_by,
) -> KoboProjectBinding:
    """
    PRE: asset and project are persisted, the actor is authenticated, and the
    project is active.
    POST: preserves historical bindings as inactive, keeps exactly one active
    direct binding, and activates the asset.
    """
    _require_authenticated_actor(linked_by, action="link a Kobo asset")
    if asset is None or asset.pk is None:
        raise ValidationError("La ficha Kobo no existe.")
    if project is None or project.pk is None:
        raise ValidationError("Debe seleccionar un proyecto.")

    from apps.operations.models import Project

    with transaction.atomic():
        try:
            locked_project = Project.objects.select_for_update().get(pk=project.pk)
        except Project.DoesNotExist as exc:
            raise ValidationError("El proyecto seleccionado no existe.") from exc
        if locked_project.status != Project.Status.ACTIVE:
            raise ValidationError("Solo se pueden enlazar proyectos activos.")
        locked_asset = KoboAsset.objects.select_for_update().select_related(
            "form_definition"
        ).get(pk=asset.pk)
        try:
            _require_registered_active_definition(locked_asset.form_definition)
        except ValidationError as exc:
            raise ValidationError(
                "La definición de la ficha no está activa o no es compatible."
            ) from exc
        expected_role = FORM_DEFINITION_ROLES.get(
            (
                locked_asset.form_definition.form_id,
                locked_asset.form_definition.version,
            )
        )
        if locked_asset.form_role != expected_role:
            raise ValidationError("La ficha Kobo no es compatible con su definición.")

        locked_asset.project_bindings.select_for_update().filter(
            is_active=True
        ).update(is_active=False)
        binding = locked_asset.project_bindings.filter(
            routing_type=KoboProjectBinding.RoutingType.DIRECT
        ).first()
        if binding is None:
            binding = KoboProjectBinding(
                asset=locked_asset,
                project=locked_project,
                routing_type=KoboProjectBinding.RoutingType.DIRECT,
                source_field="",
                source_value="",
                is_active=True,
            )
        else:
            binding.project = locked_project
            binding.source_field = ""
            binding.source_value = ""
            binding.is_active = True
        binding.full_clean()
        binding.save()
        locked_asset.is_active = True
        locked_asset.save(update_fields=("is_active",))
    return binding


def unlink_asset_from_project(asset: KoboAsset, *, unlinked_by) -> KoboAsset:
    """
    PRE: asset is persisted and the actor is authenticated.
    POST: deactivates current bindings and the asset without deleting historical
    bindings or submissions.
    """
    _require_authenticated_actor(unlinked_by, action="unlink a Kobo asset")
    if asset is None or asset.pk is None:
        raise ValidationError("La ficha Kobo no existe.")

    with transaction.atomic():
        locked_asset = KoboAsset.objects.select_for_update().get(pk=asset.pk)
        locked_asset.project_bindings.select_for_update().filter(
            is_active=True
        ).update(is_active=False)
        locked_asset.is_active = False
        locked_asset.save(update_fields=("is_active",))
    return locked_asset


def get_asset_readiness(asset: KoboAsset) -> AssetReadiness:
    """
    PRE: asset is a persisted KoboAsset.
    POST: returns immutable readiness diagnostics without modifying any data.
    """
    if asset is None or asset.pk is None:
        raise ValidationError("Kobo asset must exist.")
    try:
        _require_registered_active_definition(asset.form_definition)
    except ValidationError:
        return AssetReadiness(
            False, "missing_form_definition", "Definición no disponible.", None, 0
        )
    routes = tuple(
        asset.project_bindings.filter(is_active=True).values_list(
            "routing_type", flat=True
        )
    )
    route_types = set(routes)
    if asset.is_active and routes and len(route_types) == 1:
        return AssetReadiness(
            True, "active", "Integración activa.", next(iter(route_types)), len(routes)
        )
    if not routes:
        return AssetReadiness(False, "no_active_bindings", "Falta routing activo.", None, 0)
    if len(route_types) != 1:
        return AssetReadiness(False, "mixed_routing", "Routing activo mezclado.", None, len(routes))
    routing_type = next(iter(route_types))
    ready = (
        routing_type == KoboProjectBinding.RoutingType.FIELD_VALUE
        or len(routes) == 1
    )
    if not ready:
        return AssetReadiness(False, "mixed_routing", "Routing directo inválido.", routing_type, len(routes))
    return AssetReadiness(
        True,
        "ready_to_activate",
        "Configuración lista para activar.",
        routing_type,
        len(routes),
    )


def activate_kobo_asset(asset: KoboAsset, *, activated_by) -> KoboAsset:
    """
    PRE: asset is inactive, actor authenticated, and readiness is valid.
    POST: changes only is_active to True and returns the asset.
    """
    _require_authenticated_actor(activated_by, action="activate a Kobo asset")
    if asset.is_active:
        raise ValidationError("Kobo asset is already active.")
    readiness = get_asset_readiness(asset)
    if not readiness.ready:
        raise ValidationError(f"Kobo asset is not ready: {readiness.code}.")
    asset.is_active = True
    asset.save(update_fields=("is_active",))
    return asset


def deactivate_kobo_asset(asset: KoboAsset, *, deactivated_by) -> KoboAsset:
    """
    PRE: asset exists and actor is authenticated.
    POST: changes only is_active to False, preserving bindings and submissions.
    """
    _require_authenticated_actor(deactivated_by, action="deactivate a Kobo asset")
    if asset is None or asset.pk is None:
        raise ValidationError("Kobo asset must exist.")
    asset.is_active = False
    asset.save(update_fields=("is_active",))
    return asset
