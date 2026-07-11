from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Project,
    ProjectUpdate,
    SupportingDocument,
    ZERO_MONEY,
)


class ExpenseFinalizedError(ValidationError):
    """Raised when ordinary mutation targets a validated or cancelled expense."""


EXPENSE_FINAL_STATUSES = frozenset(
    {Expense.Status.VALIDATED, Expense.Status.CANCELLED}
)
PROJECT_UPDATE_FINAL_STATUSES = frozenset(
    {ProjectUpdate.Status.APPROVED, ProjectUpdate.Status.REJECTED}
)


class ProjectUpdateImmutableError(ValidationError):
    """Raised when ordinary mutation targets a non-draft project update."""


class InvalidStateTransitionError(ValidationError):
    """Raised when an explicit domain state action is not allowed."""


DONATION_STATUS_TRANSITIONS = {
    Donation.Status.REGISTERED: frozenset({Donation.Status.COMMITTED, Donation.Status.RECEIVED, Donation.Status.ANNULLED}),
    Donation.Status.COMMITTED: frozenset({Donation.Status.RECEIVED, Donation.Status.ANNULLED}),
    Donation.Status.RECEIVED: frozenset({Donation.Status.PARTIALLY_ALLOCATED, Donation.Status.FULLY_ALLOCATED, Donation.Status.CLOSED, Donation.Status.ANNULLED}),
    Donation.Status.PARTIALLY_ALLOCATED: frozenset({Donation.Status.FULLY_ALLOCATED, Donation.Status.CLOSED, Donation.Status.ANNULLED}),
    Donation.Status.FULLY_ALLOCATED: frozenset({Donation.Status.CLOSED, Donation.Status.ANNULLED}),
    Donation.Status.CLOSED: frozenset(),
    Donation.Status.ANNULLED: frozenset(),
}
PROJECT_STATUS_TRANSITIONS = {
    Project.Status.PLANNED: frozenset({Project.Status.ACTIVE, Project.Status.ANNULLED}),
    Project.Status.ACTIVE: frozenset({Project.Status.SUSPENDED, Project.Status.CLOSED, Project.Status.ANNULLED}),
    Project.Status.SUSPENDED: frozenset({Project.Status.ACTIVE, Project.Status.CLOSED, Project.Status.ANNULLED}),
    Project.Status.CLOSED: frozenset(),
    Project.Status.ANNULLED: frozenset(),
}
FUND_ALLOCATION_STATUS_TRANSITIONS = {
    FundAllocation.Status.CREATED: frozenset({FundAllocation.Status.ACTIVE, FundAllocation.Status.ANNULLED}),
    FundAllocation.Status.ACTIVE: frozenset({FundAllocation.Status.PARTIALLY_EXECUTED, FundAllocation.Status.FULLY_EXECUTED, FundAllocation.Status.CLOSED, FundAllocation.Status.ANNULLED}),
    FundAllocation.Status.PARTIALLY_EXECUTED: frozenset({FundAllocation.Status.FULLY_EXECUTED, FundAllocation.Status.CLOSED, FundAllocation.Status.ANNULLED}),
    FundAllocation.Status.FULLY_EXECUTED: frozenset({FundAllocation.Status.CLOSED, FundAllocation.Status.ANNULLED}),
    FundAllocation.Status.CLOSED: frozenset(),
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


def transition_project_status(project_id: int, *, actor, target_status: str) -> Project:
    """
    PRE: project_id exists, actor is authenticated, and target_status is requested explicitly.
    POST: atomically locks, validates and audits exactly one permitted status transition.
    """
    _require_transition_actor(actor)
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_id)
        previous_status = project.status
        validate_state_transition(current_status=previous_status, target_status=target_status, allowed_transitions=PROJECT_STATUS_TRANSITIONS)
        if target_status == Project.Status.CLOSED and project.start_date and project.end_date and project.end_date < project.start_date:
            raise InvalidStateTransitionError({'end_date': _('No se puede cerrar un proyecto con fechas incoherentes.')})
        project.status = target_status
        project.full_clean()
        project.save(update_fields=('status', 'updated_at'))
        _log_status_transition(actor, project, previous_status, target_status)
        return project


def transition_fund_allocation_status(allocation_id: int, *, actor, target_status: str) -> FundAllocation:
    """
    PRE: allocation_id exists, actor is authenticated, and target_status is requested explicitly.
    POST: atomically locks, validates and audits exactly one permitted status transition.
    """
    _require_transition_actor(actor)
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


def ensure_project_update_is_editable(project_update: ProjectUpdate) -> None:
    """
    PRE: project_update is a persisted advance targeted by ordinary editing.
    POST: returns only for DRAFT; review and final states fail without mutation.
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
            {'status': _('Los avances aprobados o rechazados no se pueden eliminar.')}
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


# PRE: instance is saved or still readable before deletion; summary is a concise,
# non-secret description rather than a serialized payload.
# POST: creates and returns one append-only AuditLog event through the authorized helper.
def log_action(user, action: str, instance, summary: str, entity_label: str | None = None):
    return AuditLog.objects.create(
        user=user if getattr(user, 'is_authenticated', False) else None,
        action=action,
        model_name=capfirst(instance._meta.verbose_name),
        entity_id=str(instance.pk),
        entity_label=entity_label or str(instance),
        summary=summary,
    )


def log_create(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.CREATED, instance, summary or _('Registro creado.'))


def log_update(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.UPDATED, instance, summary or _('Registro actualizado.'))


def log_delete(user, instance, summary: str | None = None):
    return log_action(user, AuditLog.Action.ANNULLED, instance, summary or _('Registro eliminado.'), str(instance))


def log_review(user, project_update: ProjectUpdate, notes: str = ''):
    action = AuditLog.Action.VALIDATED if project_update.status == ProjectUpdate.Status.APPROVED else AuditLog.Action.REJECTED
    summary = _('Avance de proyecto aprobado.') if action == AuditLog.Action.VALIDATED else _('Avance de proyecto rechazado.')
    return log_action(user, action, project_update, summary)


# PRE: currency is the ISO code proposed for an operational financial record.
# POST: raises ValidationError unless currency matches SIGEDON's single operating currency.
def _validate_operating_currency(currency, field_name='currency'):
    if currency != OPERATING_CURRENCY:
        raise ValidationError({field_name: _('SIGEDON solo permite operaciones financieras en USD.')})


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


# PRE: expense_id identifies a saved expense and user is either authenticated or None.
# POST: returns the locked expense in validated state, with validator metadata and one audit event for a new transition.
def validate_expense(expense_id: int, user=None) -> Expense:
    with transaction.atomic():
        expense = Expense.objects.select_for_update().get(pk=expense_id)
        if expense.status == Expense.Status.VALIDATED:
            return expense
        ensure_expense_is_editable(expense)
        if not expense.supporting_documents.exists():
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        expense.status = Expense.Status.VALIDATED
        expense.validated_by = user if getattr(user, 'is_authenticated', False) else None
        expense.validated_at = timezone.now()
        expense.full_clean()
        expense.save(update_fields=['status', 'validated_by', 'validated_at', 'updated_at'])
        log_action(user, AuditLog.Action.VALIDATED, expense, _('Gasto validado.'))
        return expense


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
        _validate_operating_currency(locked_donation.currency, 'donation')
        _validate_allocation_balance(locked_donation, amount)
        allocation = FundAllocation(
            donation=locked_donation,
            project=project,
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
        donation_ids = {locked_allocation.donation_id, donation.pk}
        locked_donations = {
            item.pk: item
            for item in Donation.objects.select_for_update().filter(pk__in=donation_ids).order_by('pk')
        }
        locked_donation = locked_donations[donation.pk]
        _validate_operating_currency(locked_donation.currency, 'donation')
        _validate_allocation_balance(locked_donation, amount, exclude_pk=locked_allocation.pk)
        _validate_allocation_execution(locked_allocation, amount)
        locked_allocation.donation = locked_donation
        locked_allocation.project = project
        locked_allocation.budget_category = budget_category
        locked_allocation.amount = amount
        locked_allocation.responsible_person = responsible_person
        locked_allocation.allocation_date = allocation_date
        locked_allocation.status = status
        locked_allocation.notes = notes
        locked_allocation.full_clean()
        locked_allocation.save()
        return locked_allocation


# PRE: allocation is saved, user may be None, and support_file is supplied when creating a validated expense.
# POST: creates and returns one valid expense, plus its optional support, after locking the allocation balance.
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
    status,
    currency=OPERATING_CURRENCY,
    user=None,
    support_title='',
    support_file=None,
):
    with transaction.atomic():
        if status == Expense.Status.CANCELLED:
            raise ExpenseFinalizedError(
                _('Un gasto solo puede anularse mediante la acción de anulación.')
            )
        locked_allocation = FundAllocation.objects.select_for_update().get(pk=allocation.pk)
        _validate_operating_currency(currency)
        _validate_operating_currency(locked_allocation.donation.currency, 'allocation')
        _validate_expense_balance(locked_allocation, amount)
        if status == Expense.Status.VALIDATED and not support_file:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        requested_validation = status == Expense.Status.VALIDATED
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
            status=Expense.Status.REGISTERED if requested_validation else status,
        )
        expense.full_clean()
        expense.save()
        if support_file:
            SupportingDocument.objects.create(
                expense=expense,
                title=support_title or support_file.name,
                document=support_file,
            )
        if requested_validation:
            return validate_expense(expense.pk, user)
        return expense


# PRE: expense is saved and the proposed values represent its complete replacement state.
# POST: updates and returns the expense after locking it and every affected allocation balance.
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
    status,
    currency=OPERATING_CURRENCY,
    user=None,
    support_title='',
    support_file=None,
):
    with transaction.atomic():
        locked_expense = Expense.objects.select_for_update().get(pk=expense.pk)
        ensure_expense_is_editable(locked_expense)
        allocation_ids = {locked_expense.allocation_id, allocation.pk}
        locked_allocations = {
            item.pk: item
            for item in FundAllocation.objects.select_for_update().filter(pk__in=allocation_ids).order_by('pk')
        }
        locked_allocation = locked_allocations[allocation.pk]
        _validate_operating_currency(currency)
        _validate_operating_currency(locked_allocation.donation.currency, 'allocation')
        _validate_expense_balance(locked_allocation, amount, exclude_pk=locked_expense.pk)
        has_support = locked_expense.supporting_documents.exists()
        if status == Expense.Status.VALIDATED and not support_file and not has_support:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        requested_validation = (
            status == Expense.Status.VALIDATED
            and locked_expense.status != Expense.Status.VALIDATED
        )
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
        locked_expense.status = locked_expense.status if requested_validation else status
        locked_expense.full_clean()
        locked_expense.save()
        if support_file:
            SupportingDocument.objects.create(
                expense=locked_expense,
                title=support_title or support_file.name,
                document=support_file,
            )
        if requested_validation:
            return validate_expense(locked_expense.pk, user)
        return locked_expense


def cancel_expense(expense_id: int, *, actor, reason: str) -> Expense:
    """
    PRE: actor is authenticated, reason is non-empty, and expense_id identifies
    a pending/editable or validated expense that has not been cancelled.
    POST: atomically locks expense/allocation, marks only status as CANCELLED,
    preserves validation metadata and writes one safe audit event.
    """
    if not getattr(actor, 'is_authenticated', False):
        raise ValidationError({'actor': _('La anulación exige un usuario autenticado.')})
    clean_reason = reason.strip() if isinstance(reason, str) else ''
    if not clean_reason:
        raise ValidationError({'reason': _('La razón de anulación es obligatoria.')})
    with transaction.atomic():
        expense = Expense.objects.select_for_update().get(pk=expense_id)
        FundAllocation.objects.select_for_update().get(pk=expense.allocation_id)
        if expense.status == Expense.Status.CANCELLED:
            raise ExpenseFinalizedError(_('El gasto ya fue anulado.'))
        if expense.status == Expense.Status.ANNULLED:
            raise ExpenseFinalizedError(_('El gasto legado ya está anulado.'))
        previous_status = expense.status
        allowed_statuses = {
            Expense.Status.REGISTERED,
            Expense.Status.IN_REVIEW,
            Expense.Status.REJECTED,
            Expense.Status.VALIDATED,
        }
        if previous_status not in allowed_statuses:
            raise ExpenseFinalizedError(_('El estado actual del gasto no admite anulación.'))
        expense.status = Expense.Status.CANCELLED
        expense.full_clean()
        expense.save(update_fields=('status', 'updated_at'))
        log_action(
            actor,
            AuditLog.Action.EXPENSE_CANCELLED,
            expense,
            _('Gasto anulado. Estado anterior: %(status)s. Razón registrada por separado.')
            % {'status': previous_status},
        )
        return expense


def sum_money(queryset, field_name: str):
    """
    PRE: queryset debe ser un QuerySet válido y field_name debe apuntar a un campo numérico agregable.
    POST: Retorna la suma del campo indicado o ZERO_MONEY si no hay registros.
    """
    return queryset.aggregate(total=Sum(field_name))['total'] or ZERO_MONEY


def get_dashboard_metrics() -> dict:
    """
    PRE: La base de datos debe estar migrada y los modelos operativos disponibles.
    POST: Retorna un diccionario con las métricas financieras y operativas necesarias para el dashboard.
    """
    donations = Donation.objects.filter(currency=OPERATING_CURRENCY).exclude(status=Donation.Status.ANNULLED)
    allocations = FundAllocation.objects.filter(
        donation__currency=OPERATING_CURRENCY
    ).exclude(status=FundAllocation.Status.ANNULLED)
    expenses = Expense.objects.filter(
        currency=OPERATING_CURRENCY,
        allocation__donation__currency=OPERATING_CURRENCY,
    ).exclude(status__in=Expense.non_executing_statuses())
    total_donations = sum_money(donations, 'amount')
    total_assigned = sum_money(allocations, 'amount')
    total_executed = sum_money(expenses, 'amount')
    return {
        'total_donations': total_donations,
        'total_assigned': total_assigned,
        'total_executed': total_executed,
        'available_balance': max(total_donations - total_assigned, ZERO_MONEY),
        'recent_donations': donations.select_related('donor')[:5],
        'recent_expenses': expenses.select_related('allocation', 'allocation__project')[:5],
        'recent_audit_logs': AuditLog.objects.select_related('user')[:5],
    }


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


def register_advance(project_id: int, title: str, description: str, evidence=None, created_by=None) -> ProjectUpdate:
    """
    PRE: project_id debe corresponder a un Project existente y apto para recibir avances.
    POST: Retorna una instancia ProjectUpdate guardada en BD con estado pending_review.
    """
    project = Project.objects.get(pk=project_id)
    project_update = ProjectUpdate(
        project=project,
        title=title,
        description=description,
        evidence=evidence,
        created_by=created_by,
        status=ProjectUpdate.Status.PENDING_REVIEW,
    )
    project_update.full_clean()
    project_update.save()
    return project_update


def update_project_update(*, update_id: int, project, title: str, description: str, evidence=None) -> ProjectUpdate:
    """
    PRE: update_id identifies a DRAFT advance and submitted values are validated form data.
    POST: atomically locks and updates only that draft's material fields, then returns it.
    """
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        ensure_project_update_is_editable(project_update)
        project_update.project = project
        project_update.title = title
        project_update.description = description
        project_update.evidence = evidence
        project_update.full_clean()
        project_update.save()
        return project_update


def review_project_update(update_id: int, reviewer, status: str, notes: str = '') -> ProjectUpdate:
    """
    PRE: update_id exists, reviewer is authenticated, status is APPROVED or
    REJECTED, current state is PENDING_REVIEW, and rejection includes a reason.
    POST: atomically locks and transitions the advance exactly once, records
    reviewer/time and one audit event, preserves material evidence, and returns it.
    """
    if not getattr(reviewer, 'is_authenticated', False):
        raise ValidationError({'reviewer': _('La revisión exige un usuario autenticado.')})
    if status not in {ProjectUpdate.Status.APPROVED, ProjectUpdate.Status.REJECTED}:
        raise ValidationError({'status': _('El estado de revisión debe ser aprobado o rechazado.')})
    clean_notes = notes.strip() if isinstance(notes, str) else ''
    if status == ProjectUpdate.Status.REJECTED and not clean_notes:
        raise ValidationError({'review_notes': _('La razón del rechazo es obligatoria.')})
    with transaction.atomic():
        project_update = ProjectUpdate.objects.select_for_update().get(pk=update_id)
        if project_update.status != ProjectUpdate.Status.PENDING_REVIEW:
            raise ValidationError(
                {'status': _('Solo un avance pendiente de revisión puede revisarse.')}
            )
        project_update.status = status
        project_update.reviewed_by = reviewer
        project_update.reviewed_at = timezone.now()
        project_update.review_notes = clean_notes
        project_update.full_clean()
        project_update.save(
            update_fields=('status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at')
        )
        log_review(reviewer, project_update)
        return project_update
