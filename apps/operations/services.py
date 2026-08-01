from contextlib import contextmanager, nullcontext

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .milestones import get_milestone_progress
from .models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Project,
    ProjectMilestone,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ProjectUpdateImmutableError,
    ProjectUpdateReview,
    ProjectUpdateReviewDecision,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
    ProjectUpdateRemediationError,
    SupportingDocument,
    ZERO_MONEY,
)
from .project_update_responsibles import validate_project_update_reporter
from .public_portal_cache import invalidate_public_portal_cache


class ExpenseFinalizedError(ValidationError):
    """Raised when ordinary mutation targets an annulled expense."""


class SupportingDocumentError(ValidationError):
    """Raised when a supporting-document mutation violates an expense invariant."""


EXPENSE_FINAL_STATUSES = frozenset({Expense.Status.ANNULLED})
PROJECT_UPDATE_FINAL_STATUSES = frozenset({ProjectUpdate.Status.PUBLISHED})


def _store_upload_for_field(instance, field_name, uploaded_file):
    """
    PRE: uploaded_file is validated and instance supplies the target FileField metadata.
    POST: stores the bytes outside any service transaction and returns its storage and name.
    """
    field = instance._meta.get_field(field_name)
    storage = field.storage
    generated_name = field.generate_filename(instance, uploaded_file.name)
    return storage, storage.save(generated_name, uploaded_file)


def _compensate_stored_upload(storage, stored_name):
    """
    PRE: stored_name was created by this request and its relational confirmation failed.
    POST: attempts only its compensating delete and never masks the original exception.
    """
    try:
        storage.delete(stored_name)
    except Exception:
        pass


@contextmanager
def _stored_upload(instance, field_name, uploaded_file):
    """PRE: uploaded_file may be absent or valid. POST: compensates its new file if the caller raises."""
    if not uploaded_file:
        yield None
        return
    storage, stored_name = _store_upload_for_field(instance, field_name, uploaded_file)
    try:
        yield stored_name
    except Exception:
        _compensate_stored_upload(storage, stored_name)
        raise


class ProjectUpdateReviewError(ValidationError):
    """Raised when a documentary review cannot be registered safely."""


class ProjectUpdateReviewDecisionError(ValidationError):
    """Raised when an institutional review outcome cannot be registered safely."""


class InvalidStateTransitionError(ValidationError):
    """Raised when an explicit domain state action is not allowed."""


class OperationalEntityFinalizedError(ValidationError):
    """Raised when ordinary mutation targets a closed or annulled entity."""


TERMINAL_REASON_MIN_LENGTH = 10
TERMINAL_REASON_MAX_LENGTH = 500
PROJECT_TERMINAL_STATUSES = frozenset({Project.Status.CLOSED})
DONATION_TERMINAL_STATUSES = frozenset({Donation.Status.ANNULLED})
ALLOCATION_TERMINAL_STATUSES = frozenset({FundAllocation.Status.FINISHED, FundAllocation.Status.ANNULLED})


DONATION_STATUS_TRANSITIONS = {
    Donation.Status.REGISTERED: frozenset({Donation.Status.RECEIVED, Donation.Status.ANNULLED}),
    Donation.Status.RECEIVED: frozenset({Donation.Status.ANNULLED}),
    Donation.Status.ANNULLED: frozenset(),
}
PROJECT_STATUS_TRANSITIONS = {
    Project.Status.ACTIVE: frozenset({Project.Status.CLOSED}),
    Project.Status.CLOSED: frozenset(),
}
FUND_ALLOCATION_STATUS_TRANSITIONS = {
    FundAllocation.Status.ACTIVE: frozenset({FundAllocation.Status.FINISHED, FundAllocation.Status.ANNULLED}),
    FundAllocation.Status.FINISHED: frozenset(),
    FundAllocation.Status.ANNULLED: frozenset(),
}


def validate_state_transition(*, current_status, target_status, allowed_transitions):
    """
    PRE: current/target are proposed model states and allowed_transitions is explicit.
    POST: returns None only for a valid non-idempotent transition; otherwise raises.
    """
    if current_status not in allowed_transitions:
        raise InvalidStateTransitionError({'status': _('El estado actual no pertenece al flujo configurado.')})
    if target_status == current_status:
        raise InvalidStateTransitionError({'status': _('Repetir el estado actual no es una transición válida.')})
    if target_status not in allowed_transitions[current_status]:
        raise InvalidStateTransitionError({'status': _('La transición de estado solicitada no está permitida.')})


def _require_transition_actor(actor):
    """
    PRE: actor is the user proposed for an explicit state transition.
    POST: returns only for authenticated actors; otherwise raises safely.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise InvalidStateTransitionError({'actor': _('La transición exige un usuario autenticado.')})


def _log_status_transition(actor, instance, previous_status, target_status):
    """
    PRE: instance is locked and already persisted in target_status.
    POST: creates and returns one safe audit event naming old and new states.
    """
    return log_action(
        actor,
        AuditLog.Action.UPDATED,
        instance,
        _('Estado cambiado de %(previous)s a %(target)s.')
        % {'previous': previous_status, 'target': target_status},
    )


def transition_donation_status(donation_id: int, *, actor, target_status: str) -> Donation:
    """
    PRE: donation_id exists, actor is authenticated, and target_status is requested explicitly.
    POST: atomically locks, validates and audits exactly one permitted status transition.
    """
    _require_transition_actor(actor)
    if target_status in DONATION_TERMINAL_STATUSES:
        raise InvalidStateTransitionError(
            {'status': _('Las acciones terminales requieren su confirmación específica.')}
        )
    with transaction.atomic():
        donation = Donation.objects.select_for_update().get(pk=donation_id)
        previous_status = donation.status
        validate_state_transition(current_status=previous_status, target_status=target_status, allowed_transitions=DONATION_STATUS_TRANSITIONS)
        if donation.amount <= ZERO_MONEY:
            raise InvalidStateTransitionError({'amount': _('Una donación operativa debe tener monto positivo.')})
        if target_status == Donation.Status.RECEIVED and donation.received_date is None:
            raise InvalidStateTransitionError({'received_date': _('La fecha de recepción es obligatoria para marcar la donación como recibida.')})
        donation.status = target_status
        donation.full_clean()
        donation.save(update_fields=('status', 'updated_at'))
        _log_status_transition(actor, donation, previous_status, target_status)
        return donation


def transition_fund_allocation_status(allocation_id: int, *, actor, target_status: str) -> FundAllocation:
    """
    PRE: allocation_id exists, actor is authenticated, and target_status is requested explicitly.
    POST: atomically locks, validates and audits exactly one permitted status transition.
    """
    _require_transition_actor(actor)
    if target_status in ALLOCATION_TERMINAL_STATUSES:
        raise InvalidStateTransitionError(
            {'status': _('Las acciones terminales requieren su confirmación específica.')}
        )
    with transaction.atomic():
        allocation = FundAllocation.objects.select_for_update().select_related('donation', 'project').get(pk=allocation_id)
        previous_status = allocation.status
        validate_state_transition(current_status=previous_status, target_status=target_status, allowed_transitions=FUND_ALLOCATION_STATUS_TRANSITIONS)
        _validate_allocation_balance(
            allocation.donation,
            allocation.amount,
            exclude_pk=allocation.pk,
        )
        allocation.full_clean()
        allocation.status = target_status
        allocation.full_clean()
        allocation.save(update_fields=('status', 'updated_at'))
        _log_status_transition(actor, allocation, previous_status, target_status)
        return allocation


# PRE: reason is the proposed historical justification for an annulment.
# POST: returns trimmed valid text or raises an explicit domain validation error.
def validate_terminal_reason(reason):
    clean_reason = reason.strip() if isinstance(reason, str) else ''
    if len(clean_reason) < TERMINAL_REASON_MIN_LENGTH:
        raise InvalidStateTransitionError(
            {'reason': _('El motivo debe contener al menos 10 caracteres.')}
        )
    if len(clean_reason) > TERMINAL_REASON_MAX_LENGTH:
        raise InvalidStateTransitionError(
            {'reason': _('El motivo no puede exceder 500 caracteres.')}
        )
    return clean_reason


# PRE: entity is a persisted Project, Donation, or FundAllocation targeted by ordinary editing.
# POST: returns only when its status is not closed or annulled.
def ensure_operational_entity_is_editable(entity):
    terminal_statuses = {
        Project: PROJECT_TERMINAL_STATUSES,
        Donation: DONATION_TERMINAL_STATUSES,
        FundAllocation: ALLOCATION_TERMINAL_STATUSES,
    }.get(type(entity))
    if terminal_statuses is None:
        raise TypeError('La guarda de edición recibió una entidad no soportada.')
    if entity.status in terminal_statuses:
        raise OperationalEntityFinalizedError(
            {'status': _('Los registros cerrados o anulados no admiten edición ordinaria.')}
        )


# PRE: allocation is persisted and its related expense statuses are queryable.
# POST: returns True exactly when at least one expense still consumes allocation balance.
def allocation_has_effective_expenses(allocation):
    return allocation.expenses.exclude(status__in=Expense.non_executing_statuses()).exists()


# PRE: entity is locked, target status was validated, actor is authenticated, and reason is final.
# POST: persists terminal status/metadata and writes exactly one audit event in the current transaction.
def _finalize_operational_entity(*, entity, target_status, actor, reason, action, summary):
    assert transaction.get_connection().in_atomic_block
    entity.status = target_status
    entity.terminal_reason = reason
    entity.terminal_at = timezone.now()
    entity.terminal_by = actor
    entity.save(
        update_fields=(
            'status',
            'terminal_reason',
            'terminal_at',
            'terminal_by',
            'updated_at',
        )
    )
    log_action(actor, action, entity, summary)
    return entity


def finish_project(project_id: int, *, actor) -> Project:
    """
    PRE: actor is authenticated and project_id identifies an ACTIVE project.
    POST: atomically closes it, forces is_public=False, persists terminal metadata,
    creates one CLOSE audit event, and invalidates the public portal cache when it
    was previously public.
    """
    _require_transition_actor(actor)
    was_public = False
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        validate_state_transition(
            current_status=project.status,
            target_status=Project.Status.CLOSED,
            allowed_transitions=PROJECT_STATUS_TRANSITIONS,
        )
        if project.start_date and project.end_date and project.end_date < project.start_date:
            raise InvalidStateTransitionError(
                {'end_date': _('No se puede cerrar un proyecto con fechas incoherentes.')}
            )
        was_public = project.is_public
        project.status = Project.Status.CLOSED
        project.is_public = False
        project.terminal_reason = _('Proyecto terminado.')
        project.terminal_at = timezone.now()
        project.terminal_by = actor
        project.save(
            update_fields=(
                'status',
                'is_public',
                'terminal_reason',
                'terminal_at',
                'terminal_by',
                'updated_at',
            )
        )
        if was_public:
            summary = _(
                'Proyecto %(code)s terminado y retirado del portal público.'
            ) % {'code': project.code}
        else:
            summary = _('Proyecto %(code)s terminado.') % {'code': project.code}
        log_action(actor, AuditLog.Action.CLOSED, project, summary)
    if was_public:
        invalidate_public_portal_cache()
    return project


def publish_project(*, project_id: int, actor) -> Project:
    """
    PRE: actor is authenticated and project_id identifies an ACTIVE private Project.
    POST: atomically sets is_public=True, audits once, invalidates public portal cache.
    """
    _require_transition_actor(actor)
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if project.status != Project.Status.ACTIVE:
            raise InvalidStateTransitionError(
                {'status': _('Solo un proyecto activo puede publicarse en el portal público.')}
            )
        if project.is_public:
            raise InvalidStateTransitionError(
                {'is_public': _('El proyecto ya está publicado en el portal público.')}
            )
        project.is_public = True
        project.save(update_fields=('is_public', 'updated_at'))
        log_action(
            actor,
            AuditLog.Action.PUBLISHED,
            project,
            _('Proyecto %(code)s publicado en el portal público.') % {'code': project.code},
        )
    invalidate_public_portal_cache()
    return project


def unpublish_project(*, project_id: int, actor) -> Project:
    """
    PRE: actor is authenticated and project_id identifies an ACTIVE public Project.
    POST: atomically sets is_public=False, audits once, invalidates public portal cache.
    """
    _require_transition_actor(actor)
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        if project.status != Project.Status.ACTIVE:
            raise InvalidStateTransitionError(
                {
                    'status': _(
                        'Un proyecto cerrado no puede retirarse del portal; '
                        'corrija la inconsistencia con una operación de dominio adecuada.'
                    )
                }
            )
        if not project.is_public:
            raise InvalidStateTransitionError(
                {'is_public': _('El proyecto no está publicado en el portal público.')}
            )
        project.is_public = False
        project.save(update_fields=('is_public', 'updated_at'))
        log_action(
            actor,
            AuditLog.Action.UNPUBLISHED,
            project,
            _('Proyecto %(code)s retirado del portal público.') % {'code': project.code},
        )
    invalidate_public_portal_cache()
    return project


def annul_donation(donation_id: int, *, actor, reason) -> Donation:
    """
    PRE: actor is authenticated, reason is valid, and donation can transition to ANNULLED.
    POST: annuls only a donation without non-annulled allocations and audits atomically.
    """
    _require_transition_actor(actor)
    clean_reason = validate_terminal_reason(reason)
    with transaction.atomic():
        donation = Donation.objects.select_for_update().get(pk=donation_id)
        validate_state_transition(
            current_status=donation.status,
            target_status=Donation.Status.ANNULLED,
            allowed_transitions=DONATION_STATUS_TRANSITIONS,
        )
        if donation.allocations.exclude(status=FundAllocation.Status.ANNULLED).exists():
            raise InvalidStateTransitionError(
                {'allocations': _('La donación mantiene asignaciones no anuladas.')}
            )
        return _finalize_operational_entity(
            entity=donation,
            target_status=Donation.Status.ANNULLED,
            actor=actor,
            reason=clean_reason,
            action=AuditLog.Action.ANNULLED,
            summary=_('Donación %(code)s anulada. Motivo: %(reason)s')
            % {'code': donation.code, 'reason': clean_reason},
        )


def annul_fund_allocation(allocation_id: int, *, actor, reason) -> FundAllocation:
    """
    PRE: actor is authenticated, reason is valid, and allocation can transition to ANNULLED.
    POST: locks Donation then FundAllocation, rejects effective expenses, releases balance, and audits once.
    """
    _require_transition_actor(actor)
    clean_reason = validate_terminal_reason(reason)
    with transaction.atomic():
        allocation_reference = FundAllocation.objects.only('donation_id').get(pk=allocation_id)
        Donation.objects.select_for_update().get(pk=allocation_reference.donation_id)
        allocation = FundAllocation.objects.select_for_update().get(pk=allocation_id)
        validate_state_transition(
            current_status=allocation.status,
            target_status=FundAllocation.Status.ANNULLED,
            allowed_transitions=FUND_ALLOCATION_STATUS_TRANSITIONS,
        )
        if allocation_has_effective_expenses(allocation):
            raise InvalidStateTransitionError(
                {'expenses': _('La asignación tiene gastos efectivos y no puede anularse.')}
            )
        return _finalize_operational_entity(
            entity=allocation,
            target_status=FundAllocation.Status.ANNULLED,
            actor=actor,
            reason=clean_reason,
            action=AuditLog.Action.ANNULLED,
            summary=_('Asignación %(code)s anulada. Motivo: %(reason)s')
            % {'code': allocation.code, 'reason': clean_reason},
        )


def ensure_project_update_is_editable(project_update: ProjectUpdate) -> None:
    """
    PRE: project_update is a persisted advance targeted by ordinary editing.
    POST: returns only for DRAFT; published advances fail without mutation.
    """
    if project_update.status != ProjectUpdate.Status.DRAFT:
        raise ProjectUpdateImmutableError(
            {'status': _('Solo los avances en borrador pueden editarse.')}
        )


def ensure_project_update_is_deletable(project_update: ProjectUpdate) -> None:
    """
    PRE: project_update is a persisted advance targeted by physical deletion.
    POST: returns unless it is final; final states fail without mutation.
    """
    if project_update.status in PROJECT_UPDATE_FINAL_STATUSES:
        raise ProjectUpdateImmutableError(
            {'status': _('Los avances publicados no se pueden eliminar.')}
        )


def ensure_expense_is_editable(expense: Expense) -> None:
    """
    PRE: expense is a persisted operational expense.
    POST: returns only for ordinary editable states; finalized states raise a
    domain-specific error without modifying data.
    """
    if expense.status in EXPENSE_FINAL_STATUSES:
        raise ExpenseFinalizedError(
            _('Los gastos validados o anulados no admiten modificaciones ordinarias.')
        )


def ensure_expense_is_deletable(expense: Expense) -> None:
    """
    PRE: expense is a persisted candidate for ordinary physical deletion.
    POST: returns unless it is validated/cancelled; finalized states raise
    ExpenseFinalizedError without modifying data.
    """
    if expense.status in EXPENSE_FINAL_STATUSES:
        raise ExpenseFinalizedError(
            _('Los gastos validados o anulados no se pueden eliminar.')
        )


# PRE: instance is saved or still readable after deletion; summary and optional identity
# overrides are concise, non-secret values captured by the domain service.
# POST: creates and returns one append-only AuditLog event through the authorized helper.
def log_action(
    user,
    action: str,
    instance,
    summary: str,
    entity_label: str | None = None,
    entity_id: int | str | None = None,
):
    return AuditLog.objects.create(
        user=user if getattr(user, 'is_authenticated', False) else None,
        action=action,
        model_name=capfirst(instance._meta.verbose_name),
        entity_id=str(instance.pk if entity_id is None else entity_id),
        entity_label=entity_label or str(instance),
        summary=summary,
    )


def log_create(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.CREATED, instance, summary or _('Registro creado.'))


def log_update(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.UPDATED, instance, summary or _('Registro actualizado.'))


def log_delete(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.ANNULLED, instance, summary or _('Registro eliminado.'), str(instance))


class ProjectMilestoneError(ValidationError):
    """Raised when a project milestone mutation violates a domain invariant."""


# PRE: actor is the proposed technical author of a milestone mutation.
# POST: returns the persisted authenticated user or raises a domain validation error.
def _require_project_milestone_actor(actor):
    user_model = get_user_model()
    if (
        not isinstance(actor, user_model)
        or not getattr(actor, 'is_authenticated', False)
        or actor.pk is None
    ):
        raise ProjectMilestoneError({'actor': _('La operación exige un usuario autenticado.')})
    try:
        return user_model._default_manager.get(pk=actor.pk)
    except user_model.DoesNotExist as exc:
        raise ProjectMilestoneError(
            {'actor': _('El usuario responsable ya no existe en el sistema.')}
        ) from exc


# PRE: project is locked and belongs to the milestone collection being mutated.
# POST: raises only when the project is terminal and therefore immutable for milestones.
def _ensure_project_accepts_milestone_mutations(project):
    if project.status in PROJECT_TERMINAL_STATUSES:
        raise ProjectMilestoneError(
            {'project': _('Los proyectos cerrados no admiten cambios en sus hitos.')}
        )


# PRE: caller is inside transaction.atomic() and project_id identifies a persisted project.
# POST: returns the locked editable project and all its milestones locked in stable order.
def _lock_project_milestones(project_id):
    assert transaction.get_connection().in_atomic_block
    project = Project.objects.select_for_update().get(pk=project_id)
    _ensure_project_accepts_milestone_mutations(project)
    milestones = list(
        ProjectMilestone.objects.select_for_update()
        .filter(project_id=project.pk)
        .order_by('position', 'pk')
    )
    return project, milestones


# PRE: caller is inside transaction.atomic() and milestone_id identifies a persisted milestone.
# POST: locks its project first, then every project milestone, and returns the requested locked row.
def _lock_project_milestones_for_id(milestone_id):
    assert transaction.get_connection().in_atomic_block
    reference = ProjectMilestone.objects.only('project_id').get(pk=milestone_id)
    project, milestones = _lock_project_milestones(reference.project_id)
    try:
        milestone = next(item for item in milestones if item.pk == milestone_id)
    except StopIteration as exc:
        raise ProjectMilestone.DoesNotExist from exc
    return project, milestones, milestone


# PRE: milestones is a locked collection expected to be managed exclusively by these services.
# POST: returns only when positions are exactly 1..N; otherwise raises without mutation.
def _ensure_consecutive_milestone_positions(milestones):
    actual_positions = [milestone.position for milestone in milestones]
    expected_positions = list(range(1, len(milestones) + 1))
    if actual_positions != expected_positions:
        raise ProjectMilestoneError(
            {'position': _('Las posiciones de los hitos no son consecutivas y deben repararse.')}
        )


# PRE: before and after are derived from locked milestone collections in the same transaction.
# POST: writes exactly one project audit event only when derived 100 percent completion changes.
def _audit_project_milestone_completion_crossing(*, project, before, after, actor):
    if before.is_completed == after.is_completed:
        return None
    if after.is_completed:
        summary = _('El proyecto alcanzó el 100 % de sus hitos.')
    else:
        summary = _('El proyecto dejó de estar al 100 % de sus hitos.')
    return log_action(actor, AuditLog.Action.UPDATED, project, summary)


def create_project_milestone(*, project_id: int, title: str, description: str = '', actor):
    """
    PRE: project_id exists, actor is authenticated, and title/description are proposed content.
    POST: atomically appends one pending milestone, audits it, and records any 100 percent exit.
    """
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        project, milestones = _lock_project_milestones(project_id)
        _ensure_consecutive_milestone_positions(milestones)
        before = get_milestone_progress(milestones)
        milestone = ProjectMilestone(
            project=project,
            title=(title or '').strip(),
            description=description or '',
            position=len(milestones) + 1,
            is_completed=False,
            completed_at=None,
            completed_by=None,
            created_by=persisted_actor,
        )
        milestone.full_clean()
        milestone.save()
        log_action(
            persisted_actor,
            AuditLog.Action.CREATED,
            milestone,
            _('Hito de proyecto creado.'),
        )
        after = get_milestone_progress([*milestones, milestone])
        _audit_project_milestone_completion_crossing(
            project=project,
            before=before,
            after=after,
            actor=persisted_actor,
        )
        return milestone


def update_project_milestone(
    milestone_id: int,
    *,
    title: str,
    description: str = '',
    actor,
):
    """
    PRE: milestone_id exists and actor proposes only editable descriptive fields.
    POST: atomically persists and audits effective descriptive changes, or returns an unaudited no-op.
    """
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        _project, _milestones, milestone = _lock_project_milestones_for_id(milestone_id)
        clean_title = (title or '').strip()
        clean_description = description or ''
        changed_fields = []
        if milestone.title != clean_title:
            milestone.title = clean_title
            changed_fields.append('title')
        if milestone.description != clean_description:
            milestone.description = clean_description
            changed_fields.append('description')
        if not changed_fields:
            return milestone
        milestone.full_clean()
        milestone.save(update_fields=(*changed_fields, 'updated_at'))
        log_action(
            persisted_actor,
            AuditLog.Action.UPDATED,
            milestone,
            _('Contenido del hito de proyecto actualizado.'),
        )
        return milestone


def complete_project_milestone(milestone_id: int, *, actor):
    """
    PRE: milestone_id exists and actor is authenticated; client completion metadata is ignored.
    POST: atomically completes once, audits once, and records a crossing into 100 percent.
    """
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        project, milestones, milestone = _lock_project_milestones_for_id(milestone_id)
        if milestone.is_completed:
            return milestone
        before = get_milestone_progress(milestones)
        milestone.is_completed = True
        milestone.completed_at = timezone.now()
        milestone.completed_by = persisted_actor
        milestone.full_clean()
        milestone.save(
            update_fields=('is_completed', 'completed_at', 'completed_by', 'updated_at')
        )
        log_action(
            persisted_actor,
            AuditLog.Action.COMPLETED,
            milestone,
            _('Hito de proyecto completado.'),
        )
        after = get_milestone_progress(milestones)
        _audit_project_milestone_completion_crossing(
            project=project,
            before=before,
            after=after,
            actor=persisted_actor,
        )
        return milestone


def reopen_project_milestone(milestone_id: int, *, actor):
    """
    PRE: milestone_id exists and actor is authenticated.
    POST: atomically reopens once, clears completion metadata, audits, and records any 100 percent exit.
    """
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        project, milestones, milestone = _lock_project_milestones_for_id(milestone_id)
        if not milestone.is_completed:
            return milestone
        before = get_milestone_progress(milestones)
        milestone.is_completed = False
        milestone.completed_at = None
        milestone.completed_by = None
        milestone.full_clean()
        milestone.save(
            update_fields=('is_completed', 'completed_at', 'completed_by', 'updated_at')
        )
        log_action(
            persisted_actor,
            AuditLog.Action.REOPENED,
            milestone,
            _('Hito de proyecto reabierto.'),
        )
        after = get_milestone_progress(milestones)
        _audit_project_milestone_completion_crossing(
            project=project,
            before=before,
            after=after,
            actor=persisted_actor,
        )
        return milestone


def delete_project_milestone(milestone_id: int, *, actor):
    """
    PRE: milestone_id exists and actor is authenticated.
    POST: atomically deletes it, compacts positions, audits deletion, and records any 100 percent crossing.
    """
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        project, milestones, milestone = _lock_project_milestones_for_id(milestone_id)
        _ensure_consecutive_milestone_positions(milestones)
        before = get_milestone_progress(milestones)
        deleted_id = milestone.pk
        deleted_label = str(milestone)
        remaining = [item for item in milestones if item.pk != deleted_id]
        milestone.delete()
        for new_position, remaining_milestone in enumerate(remaining, start=1):
            if remaining_milestone.position == new_position:
                continue
            remaining_milestone.position = new_position
            remaining_milestone.full_clean()
            remaining_milestone.save(update_fields=('position', 'updated_at'))
        log_action(
            persisted_actor,
            AuditLog.Action.DELETED,
            milestone,
            _('Hito de proyecto eliminado definitivamente.'),
            entity_label=deleted_label,
            entity_id=deleted_id,
        )
        after = get_milestone_progress(remaining)
        _audit_project_milestone_completion_crossing(
            project=project,
            before=before,
            after=after,
            actor=persisted_actor,
        )
        return None


# PRE: milestone_id exists, actor is authenticated, and direction is -1 or 1.
# POST: locks the project and milestones, swaps through free position N+1, and audits real movement.
def _move_project_milestone(milestone_id: int, *, actor, direction: int):
    if direction not in {-1, 1}:
        raise AssertionError('La dirección interna del movimiento debe ser -1 o 1.')
    with transaction.atomic():
        persisted_actor = _require_project_milestone_actor(actor)
        _project, milestones, milestone = _lock_project_milestones_for_id(milestone_id)
        _ensure_consecutive_milestone_positions(milestones)
        current_index = milestones.index(milestone)
        destination_index = current_index + direction
        if destination_index < 0 or destination_index >= len(milestones):
            return milestone
        adjacent = milestones[destination_index]
        original_position = milestone.position
        destination_position = adjacent.position
        temporary_position = len(milestones) + 1

        milestone.position = temporary_position
        milestone.full_clean()
        milestone.save(update_fields=('position', 'updated_at'))
        adjacent.position = original_position
        adjacent.full_clean()
        adjacent.save(update_fields=('position', 'updated_at'))
        milestone.position = destination_position
        milestone.full_clean()
        milestone.save(update_fields=('position', 'updated_at'))
        log_action(
            persisted_actor,
            AuditLog.Action.REORDERED,
            milestone,
            _('Hito de proyecto movido de la posición %(before)s a la %(after)s.')
            % {'before': original_position, 'after': destination_position},
        )
        return milestone


def move_project_milestone_up(milestone_id: int, *, actor):
    """
    PRE: milestone_id exists and actor is authenticated.
    POST: moves it one position upward atomically, or returns an unaudited boundary no-op.
    """
    return _move_project_milestone(milestone_id, actor=actor, direction=-1)


def move_project_milestone_down(milestone_id: int, *, actor):
    """
    PRE: milestone_id exists and actor is authenticated.
    POST: moves it one position downward atomically, or returns an unaudited boundary no-op.
    """
    return _move_project_milestone(milestone_id, actor=actor, direction=1)


# PRE: currency is the ISO code proposed for an operational financial record.
# POST: raises ValidationError unless currency matches SIGEDON's single operating currency.
def _validate_operating_currency(currency, field_name='currency'):
    if currency != OPERATING_CURRENCY:
        raise ValidationError({field_name: _('SIGEDON solo permite operaciones financieras en USD.')})


# PRE: donation is locked for update before funds are evaluated or reserved.
# POST: returns only when the donation is RECEIVED; otherwise raises a domain validation error.
def _validate_donation_can_fund_allocations(donation):
    if donation.status != Donation.Status.RECEIVED:
        raise ValidationError(
            {'donation': _('Solo las donaciones recibidas pueden financiar asignaciones.')}
        )


# PRE: project is locked for update before an allocation is created or reassigned.
# POST: returns only when the project is ACTIVE; otherwise raises a domain validation error.
def _validate_project_accepts_allocations(project):
    if project.status != Project.Status.ACTIVE:
        raise ValidationError(
            {'project': _('Solo los proyectos activos admiten asignaciones.')}
        )


# PRE: project is locked for update before an expense or project update is written or published.
# POST: returns only when the project is ACTIVE; otherwise raises a domain validation error.
def _validate_project_is_active_for_execution_or_updates(project):
    if project.status != Project.Status.ACTIVE:
        raise ValidationError(
            {'project': _('Solo los proyectos activos admiten gastos y avances.')}
        )


# PRE: donation is locked for update and amount is the complete proposed allocation amount.
# POST: raises ValidationError unless amount is positive and fits the donation balance excluding exclude_pk.
def _validate_allocation_balance(donation, amount, exclude_pk=None):
    if amount <= ZERO_MONEY:
        raise ValidationError({'amount': _('El monto de la asignación debe ser positivo.')})
    allocations = donation.allocations.exclude(status=FundAllocation.Status.ANNULLED)
    if exclude_pk is not None:
        allocations = allocations.exclude(pk=exclude_pk)
    assigned_amount = allocations.aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    if amount > donation.amount - assigned_amount:
        raise ValidationError({'amount': _('El monto de la asignación excede el saldo disponible de la donación.')})


# PRE: allocation is locked for update and amount is the complete proposed expense amount.
# POST: raises ValidationError unless amount is positive and fits the allocation balance excluding exclude_pk.
def _validate_expense_balance(allocation, amount, exclude_pk=None):
    if amount <= ZERO_MONEY:
        raise ValidationError({'amount': _('El monto del gasto debe ser positivo.')})
    expenses = allocation.expenses.exclude(
        status__in=Expense.non_executing_statuses()
    )
    if exclude_pk is not None:
        expenses = expenses.exclude(pk=exclude_pk)
    executed_amount = expenses.aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    if amount > allocation.amount - executed_amount:
        raise ValidationError({'amount': _('El monto del gasto excede el saldo disponible de la asignación.')})


# PRE: allocation is locked for update and amount is its complete proposed amount.
# POST: raises ValidationError if existing non-annulled expenses would leave the allocation with a negative balance.
def _validate_allocation_execution(allocation, amount):
    executed_amount = allocation.expenses.exclude(
        status__in=Expense.non_executing_statuses()
    ).aggregate(
        total=Sum('amount')
    )['total'] or ZERO_MONEY
    if amount < executed_amount:
        raise ValidationError({'amount': _('El monto de la asignación no puede ser menor al monto ya ejecutado.')})


# PRE: donation and project are saved instances and all values come from validated operational input.
# POST: creates and returns one valid allocation after locking the donation and rechecking its balance.
def create_fund_allocation(
    *,
    donation,
    project,
    budget_category,
    amount,
    responsible_person,
    allocation_date,
    status,
    notes,
):
    with transaction.atomic():
        locked_donation = Donation.objects.select_for_update().get(pk=donation.pk)
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        _validate_donation_can_fund_allocations(locked_donation)
        _validate_project_accepts_allocations(locked_project)
        _validate_operating_currency(locked_donation.currency, 'donation')
        _validate_allocation_balance(locked_donation, amount)
        allocation = FundAllocation(
            donation=locked_donation,
            project=locked_project,
            budget_category=budget_category,
            amount=amount,
            responsible_person=responsible_person,
            allocation_date=allocation_date,
            status=status,
            notes=notes,
        )
        allocation.full_clean()
        allocation.save()
        return allocation


# PRE: allocation is saved and the proposed values represent its complete replacement state.
# POST: updates and returns the allocation after locking it and every affected donation balance.
def update_fund_allocation(
    *,
    allocation,
    donation,
    project,
    budget_category,
    amount,
    responsible_person,
    allocation_date,
    status,
    notes,
):
    with transaction.atomic():
        locked_allocation = FundAllocation.objects.select_for_update().get(pk=allocation.pk)
        ensure_operational_entity_is_editable(locked_allocation)
        donation_ids = {locked_allocation.donation_id, donation.pk}
        locked_donations = {
            item.pk: item
            for item in Donation.objects.select_for_update().filter(pk__in=donation_ids).order_by('pk')
        }
        locked_donation = locked_donations[donation.pk]
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        _validate_donation_can_fund_allocations(locked_donation)
        _validate_project_accepts_allocations(locked_project)
        _validate_operating_currency(locked_donation.currency, 'donation')
        _validate_allocation_balance(locked_donation, amount, exclude_pk=locked_allocation.pk)
        _validate_allocation_execution(locked_allocation, amount)
        locked_allocation.donation = locked_donation
        locked_allocation.project = locked_project
        locked_allocation.budget_category = budget_category
        locked_allocation.amount = amount
        locked_allocation.responsible_person = responsible_person
        locked_allocation.allocation_date = allocation_date
        locked_allocation.status = status
        locked_allocation.notes = notes
        locked_allocation.full_clean()
        locked_allocation.save()
        return locked_allocation


# PRE: allocation is active, actor is optional for non-request callers, and support_file is a real uploaded document.
# POST: creates one REGISTERED expense with protected support after locking Donation then FundAllocation.
def create_expense(
    *,
    allocation,
    expense_date,
    category,
    amount,
    reason,
    provider_or_recipient,
    payment_method,
    description,
    observations,
    currency=OPERATING_CURRENCY,
    actor=None,
    support_title='',
    support_file=None,
):
    _validate_operating_currency(currency)
    if not support_file:
        raise ValidationError({'support_file': _('Todo gasto debe tener un documento soporte.')})
    support_document = SupportingDocument(title=support_title or support_file.name)
    with _stored_upload(support_document, 'document', support_file) as stored_name, transaction.atomic():
        allocation_reference = FundAllocation.objects.only('donation_id').get(pk=allocation.pk)
        Donation.objects.select_for_update().get(pk=allocation_reference.donation_id)
        locked_allocation = FundAllocation.objects.select_for_update().get(pk=allocation.pk)
        locked_project = Project.objects.select_for_update().get(pk=locked_allocation.project_id)
        ensure_operational_entity_is_editable(locked_allocation)
        _validate_project_is_active_for_execution_or_updates(locked_project)
        _validate_operating_currency(locked_allocation.donation.currency, 'allocation')
        _validate_expense_balance(locked_allocation, amount)
        expense = Expense(
            allocation=locked_allocation,
            expense_date=expense_date,
            category=category,
            amount=amount,
            currency=OPERATING_CURRENCY,
            reason=reason,
            provider_or_recipient=provider_or_recipient,
            payment_method=payment_method,
            description=description,
            observations=observations,
            status=Expense.Status.REGISTERED,
        )
        expense.full_clean()
        expense.save()
        SupportingDocument.objects.create(
            expense=expense,
            title=support_title or support_file.name,
            document=stored_name,
        )
        if getattr(actor, 'is_authenticated', False):
            log_action(
                actor, AuditLog.Action.EXECUTED, expense,
                _('Gasto %(code)s registrado por %(amount)s %(currency)s.')
                % {'code': expense.code, 'amount': expense.amount, 'currency': expense.currency},
            )
        return expense


# PRE: expense is REGISTERED and proposed values plus support preserve a verifiable executed payment.
# POST: updates after stable parent-first locks and audits monetary allocation before/after.
def update_expense(
    *,
    expense,
    allocation,
    expense_date,
    category,
    amount,
    reason,
    provider_or_recipient,
    payment_method,
    description,
    observations,
    currency=OPERATING_CURRENCY,
    actor=None,
    support_title='',
    support_file=None,
):
    _validate_operating_currency(currency)
    support_document = (
        SupportingDocument(title=support_title or support_file.name)
        if support_file else None
    )
    upload_context = (
        _stored_upload(support_document, 'document', support_file)
        if support_file else nullcontext(None)
    )
    with upload_context as stored_name, transaction.atomic():
        expense_reference = Expense.objects.only('allocation_id').get(pk=expense.pk)
        allocation_ids = {expense_reference.allocation_id, allocation.pk}
        donation_ids = FundAllocation.objects.filter(pk__in=allocation_ids).values_list('donation_id', flat=True)
        list(Donation.objects.select_for_update().filter(pk__in=donation_ids).order_by('pk'))
        locked_allocations = {
            item.pk: item
            for item in FundAllocation.objects.select_for_update().filter(pk__in=allocation_ids).order_by('pk')
        }
        locked_expense = Expense.objects.select_for_update().get(pk=expense.pk)
        ensure_expense_is_editable(locked_expense)
        locked_allocation = locked_allocations[allocation.pk]
        locked_project = Project.objects.select_for_update().get(pk=locked_allocation.project_id)
        _validate_project_is_active_for_execution_or_updates(locked_project)
        _validate_operating_currency(locked_allocation.donation.currency, 'allocation')
        exclude_pk = locked_expense.pk if locked_expense.allocation_id == locked_allocation.pk else None
        _validate_expense_balance(locked_allocation, amount, exclude_pk=exclude_pk)
        has_support = locked_expense.supporting_documents.exists()
        if not support_file and not has_support:
            raise ValidationError({'support_file': _('Todo gasto debe tener un documento soporte.')})
        before = {'allocation': locked_expense.allocation_id, 'amount': str(locked_expense.amount)}
        locked_expense.allocation = locked_allocation
        locked_expense.expense_date = expense_date
        locked_expense.category = category
        locked_expense.amount = amount
        locked_expense.currency = OPERATING_CURRENCY
        locked_expense.reason = reason
        locked_expense.provider_or_recipient = provider_or_recipient
        locked_expense.payment_method = payment_method
        locked_expense.description = description
        locked_expense.observations = observations
        locked_expense.status = Expense.Status.REGISTERED
        locked_expense.full_clean()
        locked_expense.save()
        if support_file:
            SupportingDocument.objects.create(
                expense=locked_expense,
                title=support_title or support_file.name,
                document=stored_name,
            )
        if getattr(actor, 'is_authenticated', False):
            after = {'allocation': locked_expense.allocation_id, 'amount': str(locked_expense.amount)}
            log_action(
                actor, AuditLog.Action.UPDATED, locked_expense,
                _('Gasto %(code)s corregido. Antes: %(before)s. Después: %(after)s.')
                % {'code': locked_expense.code, 'before': before, 'after': after},
            )
        return locked_expense


def create_supporting_document(
    *, expense_id: int, title: str, file, notes: str, actor
) -> SupportingDocument:
    """
    PRE: expense_id exists and title/file/notes already passed structural form validation.
    POST: stores the private file outside the transaction, then creates and audits one document atomically.
    """
    expense = Expense.objects.get(pk=expense_id)
    draft_document = SupportingDocument(
        expense=expense,
        title=title,
        document=file,
        notes=notes,
    )
    draft_document.full_clean()
    with _stored_upload(draft_document, 'document', file) as stored_name, transaction.atomic():
        locked_expense = Expense.objects.select_for_update().get(pk=expense_id)
        document = SupportingDocument.objects.create(
            expense=locked_expense,
            title=title,
            document=stored_name,
            notes=notes,
        )
        log_action(
            actor,
            AuditLog.Action.CREATED,
            document,
            _('Documento soporte adjuntado.'),
        )
        return document


def delete_supporting_document(*, document_id: int, actor) -> int:
    """
    PRE: document_id identifies a supporting document proposed for deletion.
    POST: locks and revalidates its expense, then atomically audits and deletes only a redundant document.
    """
    document_reference = SupportingDocument.objects.only('expense_id').get(pk=document_id)
    with transaction.atomic():
        expense = Expense.objects.select_for_update().get(pk=document_reference.expense_id)
        document = SupportingDocument.objects.select_for_update().get(pk=document_id)
        if (
            expense.status == Expense.Status.ANNULLED
            or expense.supporting_documents.count() <= 1
        ):
            raise SupportingDocumentError(
                _('El gasto debe conservar su documento soporte.')
            )
        expense_id = expense.pk
        log_delete(actor, document, _('Documento soporte eliminado.'))
        document.delete()
        return expense_id


def annul_expense(expense_id: int, *, actor, reason: str) -> Expense:
    """
    PRE: actor is authenticated, reason is valid, and expense_id identifies a REGISTERED expense.
    POST: locks Donation, FundAllocation and Expense, sets terminal metadata once and audits exactly once.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise ValidationError({'actor': _('La anulación exige un usuario autenticado.')})
    clean_reason = validate_terminal_reason(reason)
    with transaction.atomic():
        reference = Expense.objects.select_related('allocation').only('allocation_id', 'allocation__donation_id').get(pk=expense_id)
        Donation.objects.select_for_update().get(pk=reference.allocation.donation_id)
        FundAllocation.objects.select_for_update().get(pk=reference.allocation_id)
        expense = Expense.objects.select_for_update().get(pk=expense_id)
        if expense.status != Expense.Status.REGISTERED:
            raise ExpenseFinalizedError(_('El estado actual del gasto no admite anulación.'))
        expense.status = Expense.Status.ANNULLED
        expense.terminal_reason = clean_reason
        expense.terminal_at = timezone.now()
        expense.terminal_by = actor
        expense.full_clean()
        expense.save(update_fields=('status', 'terminal_reason', 'terminal_at', 'terminal_by', 'updated_at'))
        log_action(
            actor,
            AuditLog.Action.EXPENSE_CANCELLED,
            expense,
            _('Gasto %(code)s anulado; se liberaron %(amount)s %(currency)s. Motivo: %(reason)s')
            % {'code': expense.code, 'amount': expense.amount, 'currency': expense.currency, 'reason': clean_reason},
        )
        return expense


def sum_money(queryset, field_name: str):
    """
    PRE: queryset debe ser un QuerySet válido y field_name debe apuntar a un campo numérico agregable.
    POST: Retorna la suma del campo indicado o ZERO_MONEY si no hay registros.
    """
    return queryset.aggregate(total=Sum(field_name))['total'] or ZERO_MONEY


def get_dashboard_metrics(*, user) -> dict:
    """
    PRE: user es un usuario autenticado de Django.
    POST: retorna únicamente métricas y actividad autorizadas por sus permisos.
    """
    can_view_donations = user.has_perm('operations.view_donation')
    can_view_allocations = user.has_perm('operations.view_fundallocation')
    can_view_expenses = user.has_perm('operations.view_expense')
    can_view_audit = user.has_perm('operations.view_auditlog')

    context = {
        'can_view_donations': can_view_donations,
        'can_view_allocations': can_view_allocations,
        'can_view_expenses': can_view_expenses,
        'can_view_audit': can_view_audit,
        'can_view_available_balance': (
            can_view_donations and can_view_allocations
        ),
        'total_donations': None,
        'total_assigned': None,
        'total_executed': None,
        'available_balance': None,
        'recent_donations': Donation.objects.none(),
        'recent_expenses': Expense.objects.none(),
        'recent_audit_logs': AuditLog.objects.none(),
    }

    donations = None
    allocations = None

    if can_view_donations:
        donations = Donation.objects.filter(
            currency=OPERATING_CURRENCY,
        ).exclude(status=Donation.Status.ANNULLED)
        context['total_donations'] = sum_money(donations, 'amount')
        context['recent_donations'] = donations.select_related('donor')[:5]

    if can_view_allocations:
        allocations = FundAllocation.objects.filter(
            donation__currency=OPERATING_CURRENCY,
        ).exclude(status=FundAllocation.Status.ANNULLED)
        context['total_assigned'] = sum_money(allocations, 'amount')

    if can_view_expenses:
        expenses = Expense.objects.filter(
            currency=OPERATING_CURRENCY,
            allocation__donation__currency=OPERATING_CURRENCY,
        ).exclude(status__in=Expense.non_executing_statuses())
        context['total_executed'] = sum_money(expenses, 'amount')
        context['recent_expenses'] = expenses.select_related(
            'allocation',
            'allocation__project',
        )[:5]

    if can_view_donations and can_view_allocations:
        context['available_balance'] = max(
            context['total_donations'] - context['total_assigned'],
            ZERO_MONEY,
        )

    if can_view_audit:
        context['recent_audit_logs'] = AuditLog.objects.select_related('user')[:5]

    return context


def get_project_financial_summary(project: Project) -> dict:
    """
    PRE: project debe ser una instancia válida de Project.
    POST: Retorna un resumen financiero del proyecto con fondos asignados, ejecutados y disponibles.
    """
    funded_amount = project.funded_amount
    executed_amount = project.executed_amount
    return {
        'estimated_budget': project.estimated_budget,
        'funded_amount': funded_amount,
        'executed_amount': executed_amount,
        'available_amount': max(funded_amount - executed_amount, ZERO_MONEY),
    }


def get_donation_financial_summary(donation: Donation) -> dict:
    """
    PRE: donation debe ser una instancia válida de Donation.
    POST: Retorna un resumen financiero de la donación con total, asignado y disponible.
    """
    return {
        'total_amount': donation.amount,
        'assigned_amount': donation.total_assigned,
        'available_amount': donation.available_balance,
    }


def get_allocation_financial_summary(allocation: FundAllocation) -> dict:
    """
    PRE: allocation debe ser una instancia válida de FundAllocation.
    POST: Retorna un resumen financiero de la asignación con asignado, ejecutado y disponible.
    """
    return {
        'allocated_amount': allocation.amount,
        'executed_amount': allocation.executed_amount,
        'available_amount': allocation.available_balance,
    }


def register_advance(
    project_id: int,
    title: str,
    description: str,
    update_date=None,
    attachments=(),
    created_by=None,
    reported_by=None,
) -> ProjectUpdate:
    """
    PRE: project_id debe corresponder a un Project existente y apto para recibir avances.
    POST: crea un avance DRAFT validado y deja una auditoría de creación.
    """
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        _validate_project_is_active_for_execution_or_updates(project)
        validate_project_update_reporter(reported_by)
        project_update = ProjectUpdate(
            project=project,
            title=title,
            description=description,
            update_date=update_date or timezone.localdate(),
            created_by=created_by,
            reported_by=reported_by,
            status=ProjectUpdate.Status.DRAFT,
        )
        project_update.full_clean()
        project_update.save()
        log_create(
            created_by,
            project_update,
            _('Avance de proyecto creado como borrador con persona responsable asignada.'),
        )
    _create_project_update_attachments(project_update, attachments, created_by)
    return project_update


def update_project_update(
    *, update_id: int, project, title: str, description: str, update_date,
    reported_by, actor, attachments=()
) -> ProjectUpdate:
    """
    PRE: update_id identifies a DRAFT advance and submitted values are validated form data.
    POST: atomically locks and updates only that draft's material fields, then returns it.
    """
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        ensure_project_update_is_editable(project_update)
        validate_project_update_reporter(reported_by)
        reported_by_changed = project_update.reported_by_id != reported_by.pk
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        _validate_project_is_active_for_execution_or_updates(locked_project)
        project_update.project = locked_project
        project_update.title = title
        project_update.description = description
        project_update.update_date = update_date
        project_update.reported_by = reported_by
        project_update.full_clean()
        project_update.save()
        summary = (
            _('Atribución de la persona responsable del avance actualizada.')
            if reported_by_changed
            else _('Borrador de avance actualizado.')
        )
        log_update(actor, project_update, summary)
    _create_project_update_attachments(project_update, attachments, actor)
    return project_update


def _create_project_update_attachments(project_update, files, actor) -> list[ProjectUpdateAttachment]:
    """
    PRE: project_update está bloqueado o acaba de crearse como DRAFT; files ya superó validación de formulario.
    POST: crea un adjunto por archivo y devuelve las filas persistidas.
    """
    assert project_update.status == ProjectUpdate.Status.DRAFT
    return [
        add_project_update_attachment(
            update_id=project_update.pk,
            file=file,
            title='',
            actor=actor,
        )
        for file in files
    ]


def add_project_update_attachment(*, update_id: int, file, title: str, actor) -> ProjectUpdateAttachment:
    """
    PRE: update_id identifica un avance y file es un archivo validado.
    POST: crea atómicamente un adjunto solo si el avance continúa DRAFT.
    """
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        ensure_project_update_is_editable(project_update)
        draft_attachment = ProjectUpdateAttachment(
            project_update=project_update,
            title=(title or '').strip(),
            uploaded_by=actor if getattr(actor, 'is_authenticated', False) else None,
        )
    storage, stored_name = _store_upload_for_field(draft_attachment, 'file', file)
    try:
        with transaction.atomic():
            project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
            ensure_project_update_is_editable(project_update)
            attachment = ProjectUpdateAttachment.objects.create(
                project_update=project_update,
                file=stored_name,
                title=draft_attachment.title,
                uploaded_by=draft_attachment.uploaded_by,
            )
            log_create(actor, attachment, _('Adjunto de avance agregado.'))
            return attachment
    except Exception:
        _compensate_stored_upload(storage, stored_name)
        raise


def delete_project_update_attachment(*, attachment_id: int, actor) -> int:
    """
    PRE: attachment_id identifica un adjunto existente.
    POST: elimina y audita el adjunto solo si su avance continúa DRAFT; retorna el avance padre.
    """
    with transaction.atomic():
        attachment = ProjectUpdateAttachment.objects.select_related('project_update').get(pk=attachment_id)
        project_update = ProjectUpdate.objects.select_for_update().get(pk=attachment.project_update_id)
        ensure_project_update_is_editable(project_update)
        update_id = project_update.pk
        log_delete(actor, attachment, _('Adjunto de avance eliminado.'))
        attachment.delete()
        return update_id


def _require_remediation_actor(actor):
    if not getattr(actor, 'is_authenticated', False):
        raise ProjectUpdateRemediationError(_('La remediación exige un usuario autenticado.'))


def _require_draft_remediation(remediation):
    if remediation.status != ProjectUpdateRemediation.Status.DRAFT:
        raise ProjectUpdateRemediationError(_('Solo las remediaciones en borrador admiten esta operación.'))


def create_project_update_remediation(*, decision_id, response, actor):
    """PRE: decision_id identifies an OBSERVED decision and actor is authenticated. POST: creates one audited DRAFT remediation."""
    _require_remediation_actor(actor)
    with transaction.atomic():
        decision = ProjectUpdateReviewDecision.objects.select_for_update().get(pk=decision_id)
        if decision.outcome != ProjectUpdateReviewDecision.Outcome.OBSERVED:
            raise ProjectUpdateRemediationError(_('Solo las decisiones observadas admiten remediación.'))
        if hasattr(decision, 'remediation'):
            raise ProjectUpdateRemediationError(_('La decisión ya tiene una remediación registrada.'))
        remediation = ProjectUpdateRemediation(
            decision=decision,
            response=(response or '').strip(),
            created_by=actor,
        )
        remediation.full_clean()
        remediation.save()
        log_create(actor, remediation, _('Remediación creada como borrador.'))
        return remediation


def update_project_update_remediation(*, remediation_id, response, actor):
    """PRE: remediation_id is DRAFT and actor is authenticated. POST: updates only response and audits once."""
    _require_remediation_actor(actor)
    with transaction.atomic():
        remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=remediation_id)
        _require_draft_remediation(remediation)
        remediation.response = (response or '').strip()
        remediation.full_clean()
        remediation.save()
        log_update(actor, remediation, _('Borrador de remediación actualizado.'))
        return remediation


def add_project_update_remediation_attachment(*, remediation_id, file, title, actor):
    """PRE: remediation_id is DRAFT, file is valid, and actor is authenticated. POST: creates one audited attachment."""
    _require_remediation_actor(actor)
    with transaction.atomic():
        remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=remediation_id)
        _require_draft_remediation(remediation)
        draft_attachment = ProjectUpdateRemediationAttachment(
            remediation=remediation,
            title=(title or '').strip(),
            uploaded_by=actor,
        )
    storage, stored_name = _store_upload_for_field(draft_attachment, 'file', file)
    try:
        with transaction.atomic():
            remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=remediation_id)
            _require_draft_remediation(remediation)
            attachment = ProjectUpdateRemediationAttachment.objects.create(
                remediation=remediation,
                file=stored_name,
                title=draft_attachment.title,
                uploaded_by=actor,
            )
            log_create(actor, attachment, _('Adjunto de remediación agregado.'))
            return attachment
    except Exception:
        _compensate_stored_upload(storage, stored_name)
        raise


def delete_project_update_remediation_attachment(*, attachment_id, actor):
    """PRE: attachment_id exists and actor is authenticated. POST: deletes only DRAFT attachment and audits once."""
    _require_remediation_actor(actor)
    with transaction.atomic():
        attachment = ProjectUpdateRemediationAttachment.objects.select_related('remediation').get(pk=attachment_id)
        remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=attachment.remediation_id)
        _require_draft_remediation(remediation)
        log_delete(actor, attachment, _('Adjunto de remediación eliminado.'))
        attachment.delete()
        return remediation.pk


def submit_project_update_remediation(*, remediation_id, actor):
    """PRE: remediation_id is DRAFT and actor authenticated. POST: locks decision/remediation and records submission metadata."""
    _require_remediation_actor(actor)
    with transaction.atomic():
        remediation_ref = ProjectUpdateRemediation.objects.only('decision_id').get(pk=remediation_id)
        ProjectUpdateReviewDecision.objects.select_for_update().get(pk=remediation_ref.decision_id)
        remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=remediation_id)
        _require_draft_remediation(remediation)
        remediation.status = ProjectUpdateRemediation.Status.SUBMITTED
        remediation.submitted_by = actor
        remediation.submitted_at = timezone.now()
        remediation.full_clean()
        remediation.save()
        log_action(actor, AuditLog.Action.UPDATED, remediation, _('Remediación enviada.'))
        return remediation


def resolve_project_update_remediation(*, remediation_id, status, resolution_notes, actor):
    """PRE: remediation_id is SUBMITTED, status terminal, notes valid, actor authenticated. POST: resolves and audits once."""
    _require_remediation_actor(actor)
    if status not in {ProjectUpdateRemediation.Status.ACCEPTED, ProjectUpdateRemediation.Status.REJECTED}:
        raise ProjectUpdateRemediationError(_('La resolución debe ser aceptada o rechazada.'))
    with transaction.atomic():
        remediation = ProjectUpdateRemediation.objects.select_for_update().get(pk=remediation_id)
        if remediation.status != ProjectUpdateRemediation.Status.SUBMITTED:
            raise ProjectUpdateRemediationError(_('Solo las remediaciones enviadas pueden resolverse.'))
        remediation.status = status
        remediation.resolution_notes = (resolution_notes or '').strip()
        remediation.resolved_by = actor
        remediation.resolved_at = timezone.now()
        remediation.full_clean()
        remediation._allow_lifecycle_transition = True
        remediation.save()
        log_action(actor, AuditLog.Action.UPDATED, remediation, _('Remediación resuelta.'))
        return remediation


def publish_project_update(update_id: int, actor) -> ProjectUpdate:
    """
    PRE: update_id identifica un avance DRAFT y actor es un usuario autenticado.
    POST: cambia atómicamente el avance a PUBLISHED y registra una auditoría.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise ValidationError({'actor': _('La publicación exige un usuario autenticado.')})
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        project = Project.objects.select_for_update().get(pk=project_update.project_id)
        _validate_project_is_active_for_execution_or_updates(project)
        if project_update.status != ProjectUpdate.Status.DRAFT:
            raise ValidationError(
                {'status': _('Solo un avance en borrador puede publicarse.')}
            )
        validate_project_update_reporter(project_update.reported_by)
        project_update.status = ProjectUpdate.Status.PUBLISHED
        project_update.full_clean()
        project_update.save(update_fields=('status', 'updated_at'))
        log_action(
            actor,
            AuditLog.Action.PUBLISHED,
            project_update,
            _('Avance de proyecto publicado.'),
        )
        return project_update


def create_project_update_review(*, update_id: int, observations: str, actor) -> ProjectUpdateReview:
    """
    PRE: update_id identifies a persisted advance and actor is the authenticated committee member.
    POST: creates and audits exactly one trimmed documentary review without modifying the advance.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise ProjectUpdateReviewError({'actor': _('La revisión exige un usuario autenticado.')})
    clean_observations = (observations or '').strip()
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        if project_update.status != ProjectUpdate.Status.PUBLISHED:
            raise ProjectUpdateReviewError(
                {'project_update': _('Solo los avances publicados pueden recibir revisión documental.')}
            )
        if ProjectUpdateReview.objects.filter(project_update_id=project_update.pk).exists():
            raise ProjectUpdateReviewError(
                {'project_update': _('Este avance ya tiene una revisión documental registrada.')}
            )
        review = ProjectUpdateReview(
            project_update=project_update,
            observations=clean_observations,
            reviewed_by=actor,
        )
        review.full_clean()
        review.save()
        log_create(actor, review, _('Revisión documental del Comité registrada.'))
        return review


def create_project_update_review_decision(
    *, review_id: int, outcome: str, rationale: str, actor
) -> ProjectUpdateReviewDecision:
    """
    PRE: review_id identifies a persisted review and actor is an authenticated committee member.
    POST: creates and audits exactly one trimmed institutional outcome without modifying the review or advance.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise ProjectUpdateReviewDecisionError(
            {'actor': _('El resultado de revisión exige un usuario autenticado.')}
        )
    clean_rationale = (rationale or '').strip()
    with transaction.atomic():
        review = ProjectUpdateReview.objects.select_for_update().get(pk=review_id)
        project_update = ProjectUpdate.objects.select_for_update().get(pk=review.project_update_id)
        if project_update.status != ProjectUpdate.Status.PUBLISHED:
            raise ProjectUpdateReviewDecisionError(
                {'review': _('La revisión debe pertenecer a un avance publicado.')}
            )
        if ProjectUpdateReviewDecision.objects.filter(review_id=review.pk).exists():
            raise ProjectUpdateReviewDecisionError(
                {'review': _('Esta revisión ya tiene un resultado institucional registrado.')}
            )
        decision = ProjectUpdateReviewDecision(
            review=review,
            outcome=outcome,
            rationale=clean_rationale,
            decided_by=actor,
        )
        decision.full_clean()
        decision.save()
        log_create(actor, decision, _('Resultado de revisión del Comité registrado.'))
        return decision
