from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.integrations.kobo.contracts import (
    PastoralZone,
    TerritorialRoutingReasonCode,
    TerritorialRoutingResult,
    TerritorialRoutingStatus,
)
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.form_registry import KoboFormType, resolve_form_type
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.models import (
    KoboPastoralZoneProjectMapping,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)


PENDING_RECONCILIATION_LIMIT = 100
TERMINAL_ROUTING_STATUSES = frozenset(
    {
        KoboSubmission.Status.REJECTED,
        KoboSubmission.Status.IMPORTED,
        KoboSubmission.Status.PARTIALLY_IMPORTED,
        KoboSubmission.Status.DUPLICATE,
    }
)


def _result(submission, *, reason_code=None, message=None):
    # PRE: submission represents the current persisted territorial routing state.
    # POST: returns a typed, payload-safe snapshot without further database writes.
    return TerritorialRoutingResult(
        status=TerritorialRoutingStatus(submission.routing_status),
        form_type=KoboFormType.FICHA_1,
        nucleo_code_original=submission.nucleo_code_original or None,
        nucleo_code_normalized=submission.nucleo_code_normalized or None,
        pastoral_zone=(
            PastoralZone(submission.pastoral_zone)
            if submission.pastoral_zone in {zone.value for zone in PastoralZone}
            else None
        ),
        project_id=submission.project_id,
        reason_code=reason_code,
        message=message,
    )


def _record_event(submission, *, code, level, message):
    # PRE: submission is locked inside the routing transaction and message is safe metadata.
    # POST: appends one Kobo processing event without recording raw payload data.
    KoboProcessingEvent.objects.create(
        submission=submission,
        stage="territorial_routing",
        level=level,
        code=code,
        message=message,
    )


def _mark_routing_failure(submission, *, reason_code, message):
    # PRE: submission is locked and a routing precondition or configuration failed.
    # POST: leaves project unassigned, persists a safe routing error, and records it once.
    already_recorded = (
        submission.routing_status == KoboSubmission.RoutingStatus.ERROR
        and submission.routing_reason_code == reason_code
    )
    submission.project = None
    submission.routing_status = KoboSubmission.RoutingStatus.ERROR
    submission.routing_reason_code = reason_code
    submission.routing_resolved_at = None
    submission.save(
        update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
    )
    if not already_recorded:
        _record_event(
            submission,
            code="territorial_routing_failed",
            level=KoboProcessingEvent.Level.ERROR,
            message=message,
        )


def _resolve_zone_mapping(pastoral_zone):
    # PRE: pastoral_zone is a canonical PastoralZone value.
    # POST: returns its sole active mapping, None, or raises KoboPayloadError on corruption.
    mappings = list(
        KoboPastoralZoneProjectMapping.objects.filter(
            pastoral_zone=pastoral_zone,
            is_active=True,
        ).select_related("project")
    )
    if len(mappings) > 1:
        raise KoboPayloadError("More than one active pastoral-zone project mapping exists.")
    return mappings[0] if mappings else None


def _mark_resolved(submission, *, project, event_code, event_message):
    # PRE: submission is locked and project is the identity's resolved project.
    # POST: records territorial association only; it never changes review or import status.
    changed = (
        submission.project_id != project.pk
        or submission.routing_status != KoboSubmission.RoutingStatus.RESOLVED
        or bool(submission.routing_reason_code)
        or submission.routing_resolved_at is None
    )
    submission.project = project
    submission.routing_status = KoboSubmission.RoutingStatus.RESOLVED
    submission.routing_reason_code = ""
    submission.routing_resolved_at = timezone.now()
    submission.save(
        update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
    )
    if changed:
        _record_event(
            submission,
            code=event_code,
            level=KoboProcessingEvent.Level.INFO,
            message=event_message,
        )


def _reconcile_pending_submissions(identity):
    # PRE: identity is locked and has a stable project association.
    # POST: resolves at most PENDING_RECONCILIATION_LIMIT matching Ficha 10/11 rows,
    # without importing or changing review status.
    candidates = list(
        KoboSubmission.objects.filter(
            form_definition__form_id__in=(
                FICHA_10_FORM_ID,
                FICHA_11_FORM_ID,
            ),
            nucleo_code_normalized=identity.nucleo_code_normalized,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            project__isnull=True,
        )
        .order_by("received_at", "pk")[:PENDING_RECONCILIATION_LIMIT]
    )
    reconciled = 0
    for candidate in candidates:
        locked_candidate = KoboSubmission.objects.select_for_update().get(pk=candidate.pk)
        if (
            locked_candidate.routing_status
            != KoboSubmission.RoutingStatus.PENDING_IDENTITY
            or locked_candidate.project_id is not None
            or locked_candidate.nucleo_code_normalized != identity.nucleo_code_normalized
        ):
            continue
        _mark_resolved(
            locked_candidate,
            project=identity.project,
            event_code="territorial_pending_submission_reconciled",
            event_message="Pending territorial submission resolved from its confirmed identity.",
        )
        reconciled += 1
    return reconciled


def route_ficha_1_submission(submission: KoboSubmission) -> TerritorialRoutingResult:
    """
    PRE: submission is persisted and already normalized from a supported Ficha 1.
    POST: atomically creates/confirms one territorial identity or records a safe
    routing failure/conflict; it never imports or approves a submission.
    """
    if submission is None or submission.pk is None:
        raise KoboPayloadError("A persisted Kobo submission is required for territorial routing.")

    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().select_related(
            "form_definition"
        ).get(pk=submission.pk)
        try:
            form_type = resolve_form_type(
                locked_submission.form_definition.form_id,
                locked_submission.form_definition.version,
            )
        except KoboPayloadError:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.UNSUPPORTED_FORM,
                message="Territorial routing only supports registered Ficha 1 submissions.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.UNSUPPORTED_FORM)
        if form_type != KoboFormType.FICHA_1:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.UNSUPPORTED_FORM,
                message="Territorial routing only supports registered Ficha 1 submissions.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.UNSUPPORTED_FORM)
        if locked_submission.status in TERMINAL_ROUTING_STATUSES:
            raise KoboPayloadError("Terminal Kobo submissions cannot be territorially routed.")
        if not locked_submission.nucleo_code_original:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.MISSING_NUCLEO_CODE,
                message="Territorial routing requires the original nucleus code.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.MISSING_NUCLEO_CODE)
        if not locked_submission.nucleo_code_normalized:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.INVALID_NUCLEO_CODE,
                message="Territorial routing requires a normalized nucleus code.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.INVALID_NUCLEO_CODE)
        try:
            pastoral_zone = PastoralZone(locked_submission.pastoral_zone)
        except ValueError:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.INVALID_PASTORAL_ZONE,
                message="Territorial routing requires a canonical pastoral zone.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.INVALID_PASTORAL_ZONE)

        try:
            mapping = _resolve_zone_mapping(pastoral_zone.value)
        except KoboPayloadError:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING,
                message="Territorial routing configuration is ambiguous for the pastoral zone.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING)
        if mapping is None:
            _mark_routing_failure(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING,
                message="No active project mapping exists for the pastoral zone.",
            )
            return _result(locked_submission, reason_code=TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING)

        try:
            identity = KoboTerritorialIdentity.objects.select_for_update().get(
                nucleo_code_normalized=locked_submission.nucleo_code_normalized
            )
            created = False
        except KoboTerritorialIdentity.DoesNotExist:
            try:
                with transaction.atomic():
                    identity = KoboTerritorialIdentity.objects.create(
                        nucleo_code_original=locked_submission.nucleo_code_original,
                        nucleo_code_normalized=locked_submission.nucleo_code_normalized,
                        pastoral_zone=pastoral_zone.value,
                        project=mapping.project,
                        source_submission=locked_submission,
                        status=KoboTerritorialIdentity.Status.PENDING_REVIEW,
                    )
                    created = True
            except IntegrityError:
                identity = KoboTerritorialIdentity.objects.select_for_update().get(
                    nucleo_code_normalized=locked_submission.nucleo_code_normalized
                )
                created = False

        if (
            identity.pastoral_zone != pastoral_zone.value
            or identity.project_id != mapping.project_id
        ):
            _, conflict_created = KoboTerritorialIdentityConflict.objects.get_or_create(
                identity=identity,
                incoming_submission=locked_submission,
                proposed_pastoral_zone=pastoral_zone.value,
                status=KoboTerritorialIdentityConflict.Status.OPEN,
                defaults={
                    "existing_pastoral_zone": identity.pastoral_zone,
                    "existing_project": identity.project,
                    "proposed_project": mapping.project,
                },
            )
            already_conflicted = (
                locked_submission.routing_status == KoboSubmission.RoutingStatus.CONFLICT
                and locked_submission.routing_reason_code
                == TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT
            )
            locked_submission.project = None
            locked_submission.routing_status = KoboSubmission.RoutingStatus.CONFLICT
            locked_submission.routing_reason_code = TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT
            locked_submission.routing_resolved_at = None
            locked_submission.save(
                update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
            )
            if conflict_created or not already_conflicted:
                _record_event(
                    locked_submission,
                    code="territorial_identity_conflict",
                    level=KoboProcessingEvent.Level.WARNING,
                    message="Incoming territorial identity conflicts with the established identity.",
                )
            return _result(
                locked_submission,
                reason_code=TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT,
            )

        _mark_resolved(
            locked_submission,
            project=identity.project,
            event_code=("territorial_identity_created" if created else "territorial_identity_confirmed"),
            event_message=(
                "Territorial identity created and routing resolved."
                if created
                else "Territorial identity confirmed and routing resolved."
            ),
        )
        reconciled = _reconcile_pending_submissions(identity)
        if reconciled:
            _record_event(
                locked_submission,
                code="territorial_pending_submissions_reconciled",
                level=KoboProcessingEvent.Level.INFO,
                message="Pending territorial submissions were reconciled.",
            )
        return _result(locked_submission)
