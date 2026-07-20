from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.integrations.kobo.contracts import (
    PastoralZone,
    TerritorialAdministrationReasonCode as ReasonCode,
    TerritorialAdministrationResult,
    TerritorialAdministrationStatus as ResultStatus,
    TerritorialConflictDecision,
    TerritorialReconciliationResult,
    TerritorialRoutingReasonCode,
)
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
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
from apps.operations.models import AuditLog, Project
from apps.operations.services import log_action


MAX_RECONCILIATION_BATCH = 100
MAX_REASON_LENGTH = 500
AVAILABLE_PROJECT_STATUSES = frozenset(
    {Project.Status.PLANNED, Project.Status.ACTIVE, Project.Status.SUSPENDED}
)

MANAGE_MAPPINGS_PERMISSION = "kobo.manage_pastoral_zone_mappings"
RESOLVE_CONFLICTS_PERMISSION = "kobo.resolve_territorial_conflicts"
CHANGE_IDENTITY_STATUS_PERMISSION = "kobo.change_territorial_identity_status"
RUN_RECONCILIATION_PERMISSION = "kobo.run_territorial_reconciliation"


def _administration_result(status, *, reason_code=None, entity_id=None, warnings=()):
    return TerritorialAdministrationResult(
        status=status,
        reason_code=reason_code,
        entity_id=entity_id,
        warnings=tuple(warnings),
    )


# Concise local factory name; the owning function remains uniquely named across
# the service package for architecture checks and diagnostic tracebacks.
_result = _administration_result


def _authorized(actor, permission):
    # PRE: actor is the proposed principal and permission is an explicit Kobo codename.
    # POST: returns a typed blocker unless an authenticated authorized actor may proceed.
    if not getattr(actor, "is_authenticated", False):
        return _administration_result(ResultStatus.BLOCKED, reason_code=ReasonCode.ACTOR_REQUIRED)
    if not actor.has_perm(permission):
        return _administration_result(ResultStatus.BLOCKED, reason_code=ReasonCode.PERMISSION_DENIED)
    return None


def _required_reason(reason):
    # PRE: reason is untrusted administrative input.
    # POST: returns a bounded non-empty audit reason, or None when the input is invalid.
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip()[:MAX_REASON_LENGTH]


def _record_administration_event(
    *, actor, action, instance, previous_state, new_state, reason=""
):
    # PRE: the domain mutation and actor are valid inside the current transaction.
    # POST: appends one payload-safe territorial event and one append-only AuditLog.
    event = KoboTerritorialAdministrationEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=instance._meta.label,
        entity_id=instance.pk,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason,
    )
    log_action(
        actor,
        AuditLog.Action.UPDATED,
        instance,
        (
            f"Acción territorial Kobo {action}; estado anterior={previous_state}; "
            f"estado posterior={new_state}; motivo={reason or 'no aplica'}."
        ),
    )
    return event


def _mapping_state(mapping):
    return {
        "pastoral_zone": mapping.pastoral_zone,
        "project_id": mapping.project_id,
        "is_active": mapping.is_active,
    }


def configure_pastoral_zone_project_mapping(*, pastoral_zone, project, actor):
    """
    PRE: actor has the explicit mapping permission and project is a persisted available Project.
    POST: creates or activates the zone mapping atomically, unless any identity already uses a
    different mapping; imported or routed history is never reassigned.
    """
    blocked = _authorized(actor, MANAGE_MAPPINGS_PERMISSION)
    if blocked:
        return blocked
    try:
        zone = PastoralZone(pastoral_zone).value
    except (TypeError, ValueError):
        return _result(ResultStatus.INVALID_STATE, reason_code=ReasonCode.INVALID_PASTORAL_ZONE)
    project_id = getattr(project, "pk", None)
    if project_id is None:
        return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.PROJECT_NOT_AVAILABLE)

    try:
        with transaction.atomic():
            try:
                locked_project = Project.objects.select_for_update().get(pk=project_id)
            except Project.DoesNotExist:
                return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.PROJECT_NOT_AVAILABLE)
            if locked_project.status not in AVAILABLE_PROJECT_STATUSES:
                return _result(ResultStatus.BLOCKED, reason_code=ReasonCode.PROJECT_NOT_AVAILABLE)

            mappings = list(
                KoboPastoralZoneProjectMapping.objects.select_for_update()
                .filter(pastoral_zone=zone)
                .order_by("pk")
            )
            active = next((mapping for mapping in mappings if mapping.is_active), None)
            if active and active.project_id == locked_project.pk:
                return _result(ResultStatus.ALREADY_APPLIED, entity_id=active.pk)
            if active and KoboTerritorialIdentity.objects.select_for_update().filter(
                pastoral_zone=zone
            ).exists():
                return _result(
                    ResultStatus.BLOCKED,
                    reason_code=ReasonCode.ZONE_MAPPING_IN_USE,
                    entity_id=active.pk,
                )

            previous_state = _mapping_state(active) if active else {}
            now = timezone.now()
            if active:
                active.is_active = False
                active.deactivated_by = actor
                active.deactivated_at = now
                active.deactivation_reason = "Replaced by explicit territorial configuration."
                active.save(
                    update_fields=(
                        "is_active",
                        "deactivated_by",
                        "deactivated_at",
                        "deactivation_reason",
                        "updated_at",
                    )
                )

            target = next(
                (mapping for mapping in mappings if mapping.project_id == locked_project.pk),
                None,
            )
            if target:
                target.is_active = True
                target.deactivated_by = None
                target.deactivated_at = None
                target.deactivation_reason = ""
                target.save(
                    update_fields=(
                        "is_active",
                        "deactivated_by",
                        "deactivated_at",
                        "deactivation_reason",
                        "updated_at",
                    )
                )
            else:
                target = KoboPastoralZoneProjectMapping.objects.create(
                    pastoral_zone=zone,
                    project=locked_project,
                )
            _record_administration_event(
                actor=actor,
                action="pastoral_zone_mapping_configured",
                instance=target,
                previous_state=previous_state,
                new_state=_mapping_state(target),
            )
            return _result(ResultStatus.SUCCESS, entity_id=target.pk)
    except IntegrityError:
        current = KoboPastoralZoneProjectMapping.objects.filter(
            pastoral_zone=zone, is_active=True
        ).first()
        if current and current.project_id == project_id:
            return _result(ResultStatus.ALREADY_APPLIED, entity_id=current.pk)
        return _result(ResultStatus.BLOCKED, reason_code=ReasonCode.CONCURRENT_UPDATE)


def deactivate_pastoral_zone_project_mapping(*, pastoral_zone, actor, reason):
    """
    PRE: actor may manage mappings and reason explains the administrative decision.
    POST: deactivates the locked mapping only when no territorial identity uses its zone.
    """
    blocked = _authorized(actor, MANAGE_MAPPINGS_PERMISSION)
    if blocked:
        return blocked
    safe_reason = _required_reason(reason)
    if safe_reason is None:
        return _result(ResultStatus.INVALID_STATE, reason_code=ReasonCode.REASON_REQUIRED)
    try:
        zone = PastoralZone(pastoral_zone).value
    except (TypeError, ValueError):
        return _result(ResultStatus.INVALID_STATE, reason_code=ReasonCode.INVALID_PASTORAL_ZONE)

    with transaction.atomic():
        mapping = (
            KoboPastoralZoneProjectMapping.objects.select_for_update()
            .filter(pastoral_zone=zone, is_active=True)
            .first()
        )
        if mapping is None:
            return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.MAPPING_NOT_FOUND)
        if KoboTerritorialIdentity.objects.select_for_update().filter(pastoral_zone=zone).exists():
            return _result(
                ResultStatus.BLOCKED,
                reason_code=ReasonCode.ZONE_MAPPING_IN_USE,
                entity_id=mapping.pk,
            )
        previous_state = _mapping_state(mapping)
        mapping.is_active = False
        mapping.deactivated_by = actor
        mapping.deactivated_at = timezone.now()
        mapping.deactivation_reason = safe_reason
        mapping.save(
            update_fields=(
                "is_active",
                "deactivated_by",
                "deactivated_at",
                "deactivation_reason",
                "updated_at",
            )
        )
        _record_administration_event(
            actor=actor,
            action="pastoral_zone_mapping_deactivated",
            instance=mapping,
            previous_state=previous_state,
            new_state=_mapping_state(mapping),
            reason=safe_reason,
        )
        return _result(ResultStatus.SUCCESS, entity_id=mapping.pk)


def _conflict_resolution_status(decision):
    return {
        KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING:
            KoboTerritorialIdentityConflict.Status.RESOLVED_KEEP_EXISTING,
        KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED:
            KoboTerritorialIdentityConflict.Status.RESOLVED_ACCEPT_PROPOSED,
        KoboTerritorialIdentityConflict.Resolution.DISMISSED:
            KoboTerritorialIdentityConflict.Status.DISMISSED,
    }.get(decision)


def _identity_has_incompatible_history(identity, submissions):
    # PRE: identity and its matching submissions are locked for an ACCEPT_PROPOSED decision.
    # POST: returns True when changing project would invalidate imported or resolved history.
    if KoboTerritorialProfile.objects.filter(territorial_identity=identity).exists():
        return True
    if KoboPrioritizedMicroproject.objects.filter(territorial_identity=identity).exists():
        return True
    if KoboPrioritizationAssessment.objects.filter(territorial_identity=identity).exists():
        return True
    submission_ids = [submission.pk for submission in submissions]
    if KoboImportRecord.objects.filter(submission_id__in=submission_ids).exists():
        return True
    unsafe_statuses = {
        KoboSubmission.Status.IMPORTED,
        KoboSubmission.Status.PARTIALLY_IMPORTED,
    }
    if any(submission.status in unsafe_statuses for submission in submissions):
        return True
    exempt_ids = {identity.source_submission_id}
    return any(
        submission.pk not in exempt_ids
        and submission.routing_status == KoboSubmission.RoutingStatus.RESOLVED
        for submission in submissions
    )


def resolve_territorial_identity_conflict(
    *, conflict, decision: TerritorialConflictDecision, actor, reason
):
    """
    PRE: actor has conflict permission, decision is a stable model choice, and reason is non-empty.
    POST: resolves one locked conflict idempotently without reassigning imported submissions or
    rewriting any import record; ACCEPT_PROPOSED also invokes the common reconciliation service.
    """
    blocked = _authorized(actor, RESOLVE_CONFLICTS_PERMISSION)
    if blocked:
        return blocked
    safe_reason = _required_reason(reason)
    if safe_reason is None:
        return _result(ResultStatus.INVALID_STATE, reason_code=ReasonCode.REASON_REQUIRED)
    target_status = _conflict_resolution_status(decision)
    if target_status is None:
        return _result(
            ResultStatus.INVALID_STATE,
            reason_code=ReasonCode.INVALID_CONFLICT_DECISION,
        )
    if decision == KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED:
        blocked = _authorized(actor, RUN_RECONCILIATION_PERMISSION)
        if blocked:
            return blocked
    conflict_id = getattr(conflict, "pk", None)
    if conflict_id is None:
        return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.CONFLICT_NOT_FOUND)

    with transaction.atomic():
        try:
            locked_conflict = KoboTerritorialIdentityConflict.objects.select_for_update().get(
                pk=conflict_id
            )
        except KoboTerritorialIdentityConflict.DoesNotExist:
            return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.CONFLICT_NOT_FOUND)
        if locked_conflict.status != KoboTerritorialIdentityConflict.Status.OPEN:
            if locked_conflict.resolution == decision:
                return _result(
                    ResultStatus.ALREADY_APPLIED,
                    reason_code=ReasonCode.ALREADY_RESOLVED,
                    entity_id=locked_conflict.pk,
                )
            return _result(
                ResultStatus.BLOCKED,
                reason_code=ReasonCode.CONFLICT_DECISION_MISMATCH,
                entity_id=locked_conflict.pk,
            )

        identity = KoboTerritorialIdentity.objects.select_for_update().get(
            pk=locked_conflict.identity_id
        )
        incoming = KoboSubmission.objects.select_for_update().get(
            pk=locked_conflict.incoming_submission_id
        )
        previous_state = {
            "status": locked_conflict.status,
            "resolution": locked_conflict.resolution,
            "identity_zone": identity.pastoral_zone,
            "identity_project_id": identity.project_id,
            "submission_routing_status": incoming.routing_status,
        }

        if decision == KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING:
            if (
                incoming.status
                in {KoboSubmission.Status.IMPORTED, KoboSubmission.Status.PARTIALLY_IMPORTED}
                or KoboImportRecord.objects.filter(submission=incoming).exists()
            ):
                return _result(
                    ResultStatus.BLOCKED,
                    reason_code=ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED,
                    entity_id=incoming.pk,
                )
            incoming.project = None
            incoming.routing_status = KoboSubmission.RoutingStatus.ERROR
            incoming.routing_reason_code = TerritorialRoutingReasonCode.TERRITORIAL_CONFLICT_REJECTED
            incoming.routing_resolved_at = None
            incoming.save(
                update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
            )
        elif decision == KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED:
            mapping = (
                KoboPastoralZoneProjectMapping.objects.select_for_update()
                .filter(
                    pastoral_zone=locked_conflict.proposed_pastoral_zone,
                    project_id=locked_conflict.proposed_project_id,
                    is_active=True,
                )
                .first()
            )
            if mapping is None or locked_conflict.proposed_project_id is None:
                return _result(
                    ResultStatus.BLOCKED,
                    reason_code=ReasonCode.PROPOSED_MAPPING_NOT_AVAILABLE,
                    entity_id=locked_conflict.pk,
                )
            submissions = list(
                KoboSubmission.objects.select_for_update()
                .filter(nucleo_code_normalized=identity.nucleo_code_normalized)
                .order_by("pk")
            )
            if _identity_has_incompatible_history(identity, submissions):
                return _result(
                    ResultStatus.BLOCKED,
                    reason_code=ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED,
                    entity_id=identity.pk,
                )
            identity.nucleo_code_original = incoming.nucleo_code_original
            identity.pastoral_zone = locked_conflict.proposed_pastoral_zone
            identity.project_id = locked_conflict.proposed_project_id
            identity.source_submission = incoming
            identity.save(
                update_fields=(
                    "nucleo_code_original",
                    "pastoral_zone",
                    "project",
                    "source_submission",
                    "updated_at",
                )
            )
            incoming.project_id = identity.project_id
            incoming.routing_status = KoboSubmission.RoutingStatus.RESOLVED
            incoming.routing_reason_code = ""
            incoming.routing_resolved_at = timezone.now()
            incoming.save(
                update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
            )

        locked_conflict.status = target_status
        locked_conflict.resolution = decision
        locked_conflict.resolved_by = actor
        locked_conflict.resolved_at = timezone.now()
        locked_conflict.resolution_reason = safe_reason
        locked_conflict.save(
            update_fields=("status", "resolution", "resolved_by", "resolved_at", "resolution_reason")
        )
        new_state = {
            "status": locked_conflict.status,
            "resolution": locked_conflict.resolution,
            "identity_zone": identity.pastoral_zone,
            "identity_project_id": identity.project_id,
            "submission_routing_status": incoming.routing_status,
        }
        _record_administration_event(
            actor=actor,
            action="territorial_conflict_resolved",
            instance=locked_conflict,
            previous_state=previous_state,
            new_state=new_state,
            reason=safe_reason,
        )
        if decision == KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED:
            reconcile_territorial_identity_submissions(identity=identity, actor=actor)
        return _result(ResultStatus.SUCCESS, entity_id=locked_conflict.pk)


def _change_identity_status(*, identity, actor, reason, allowed_from, target_status, action):
    # PRE: caller specifies one explicit identity transition and its required permission.
    # POST: applies only the named locked transition, preserving zone, project, code, and evidence.
    blocked = _authorized(actor, CHANGE_IDENTITY_STATUS_PERMISSION)
    if blocked:
        return blocked
    safe_reason = _required_reason(reason)
    if safe_reason is None:
        return _result(ResultStatus.INVALID_STATE, reason_code=ReasonCode.REASON_REQUIRED)
    identity_id = getattr(identity, "pk", None)
    if identity_id is None:
        return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.IDENTITY_NOT_FOUND)
    with transaction.atomic():
        try:
            locked_identity = KoboTerritorialIdentity.objects.select_for_update().get(pk=identity_id)
        except KoboTerritorialIdentity.DoesNotExist:
            return _result(ResultStatus.NOT_FOUND, reason_code=ReasonCode.IDENTITY_NOT_FOUND)
        if locked_identity.status == target_status:
            return _result(ResultStatus.ALREADY_APPLIED, entity_id=locked_identity.pk)
        if locked_identity.status not in allowed_from:
            return _result(
                ResultStatus.INVALID_STATE,
                reason_code=ReasonCode.INVALID_IDENTITY_TRANSITION,
                entity_id=locked_identity.pk,
            )
        previous_state = {"status": locked_identity.status}
        locked_identity.status = target_status
        locked_identity.save(update_fields=("status", "updated_at"))
        _record_administration_event(
            actor=actor,
            action=action,
            instance=locked_identity,
            previous_state=previous_state,
            new_state={"status": locked_identity.status},
            reason=safe_reason,
        )
        return _result(ResultStatus.SUCCESS, entity_id=locked_identity.pk)


def observe_territorial_identity(*, identity, actor, reason):
    """
    PRE: identity is PENDING_REVIEW or ACTIVE and actor may change identity status.
    POST: marks only the identity OBSERVED and records the motivated decision atomically.
    """
    return _change_identity_status(
        identity=identity,
        actor=actor,
        reason=reason,
        allowed_from={KoboTerritorialIdentity.Status.PENDING_REVIEW, KoboTerritorialIdentity.Status.ACTIVE},
        target_status=KoboTerritorialIdentity.Status.OBSERVED,
        action="territorial_identity_observed",
    )


def activate_observed_territorial_identity(*, identity, actor, reason):
    """
    PRE: identity is OBSERVED and actor may change identity status.
    POST: marks only the identity ACTIVE and records the motivated decision atomically.
    """
    return _change_identity_status(
        identity=identity,
        actor=actor,
        reason=reason,
        allowed_from={KoboTerritorialIdentity.Status.OBSERVED},
        target_status=KoboTerritorialIdentity.Status.ACTIVE,
        action="territorial_identity_activated",
    )


def deactivate_territorial_identity(*, identity, actor, reason):
    """
    PRE: identity is not INACTIVE and actor may change identity status.
    POST: marks only the identity INACTIVE while preserving code, routing, and materializations.
    """
    return _change_identity_status(
        identity=identity,
        actor=actor,
        reason=reason,
        allowed_from={
            KoboTerritorialIdentity.Status.PENDING_REVIEW,
            KoboTerritorialIdentity.Status.ACTIVE,
            KoboTerritorialIdentity.Status.OBSERVED,
        },
        target_status=KoboTerritorialIdentity.Status.INACTIVE,
        action="territorial_identity_deactivated",
    )


def reconcile_territorial_identity_submissions(*, identity, actor, limit=MAX_RECONCILIATION_BATCH):
    """
    PRE: actor may reconcile, identity is persisted, and limit is between 1 and 100.
    POST: resolves one locked batch of pending Ficha 10/11 submissions only; review,
    approval, import status, imported rows, bindings, and import records remain untouched.
    """
    blocked = _authorized(actor, RUN_RECONCILIATION_PERMISSION)
    if blocked:
        return TerritorialReconciliationResult(
            status=blocked.status,
            reason_code=blocked.reason_code,
            identity_id=getattr(identity, "pk", None),
        )
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_RECONCILIATION_BATCH:
        return TerritorialReconciliationResult(
            status=ResultStatus.INVALID_STATE,
            reason_code=ReasonCode.INVALID_RECONCILIATION_LIMIT,
            identity_id=getattr(identity, "pk", None),
        )
    identity_id = getattr(identity, "pk", None)
    if identity_id is None:
        return TerritorialReconciliationResult(
            status=ResultStatus.NOT_FOUND,
            reason_code=ReasonCode.IDENTITY_NOT_FOUND,
        )

    with transaction.atomic():
        try:
            locked_identity = KoboTerritorialIdentity.objects.select_for_update().get(pk=identity_id)
        except KoboTerritorialIdentity.DoesNotExist:
            return TerritorialReconciliationResult(
                status=ResultStatus.NOT_FOUND,
                reason_code=ReasonCode.IDENTITY_NOT_FOUND,
                identity_id=identity_id,
            )
        candidates = list(
            KoboSubmission.objects.select_for_update()
            .filter(
                form_definition__form_id__in=(FICHA_10_FORM_ID, FICHA_11_FORM_ID),
                nucleo_code_normalized=locked_identity.nucleo_code_normalized,
                routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                project__isnull=True,
            )
            .order_by("received_at", "pk")[:limit]
        )
        resolved = 0
        errors = 0
        identity_valid = bool(locked_identity.project_id) and locked_identity.pastoral_zone in {
            zone.value for zone in PastoralZone
        }
        for submission in candidates:
            if not identity_valid:
                errors += 1
                continue
            submission.project_id = locked_identity.project_id
            submission.routing_status = KoboSubmission.RoutingStatus.RESOLVED
            submission.routing_reason_code = ""
            submission.routing_resolved_at = timezone.now()
            submission.save(
                update_fields=("project", "routing_status", "routing_reason_code", "routing_resolved_at")
            )
            resolved += 1
        still_pending = KoboSubmission.objects.filter(
            form_definition__form_id__in=(FICHA_10_FORM_ID, FICHA_11_FORM_ID),
            nucleo_code_normalized=locked_identity.nucleo_code_normalized,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            project__isnull=True,
        ).count()
        result = TerritorialReconciliationResult(
            status=ResultStatus.SUCCESS,
            identity_id=locked_identity.pk,
            resolved=resolved,
            still_pending=still_pending,
            errors=errors,
            has_more=still_pending > 0,
        )
        if candidates:
            _record_administration_event(
                actor=actor,
                action="territorial_submissions_reconciled",
                instance=locked_identity,
                previous_state={"pending": len(candidates) + still_pending},
                new_state={
                    "resolved": resolved,
                    "still_pending": still_pending,
                    "conflicts": 0,
                    "errors": errors,
                    "skipped": 0,
                    "has_more": result.has_more,
                },
            )
        return result
