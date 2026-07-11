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


# PRE: instance is a saved domain object or a still-readable object about to be deleted.
# POST: creates one AuditLog row with the supplied action and human-readable summary.
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
    if notes:
        summary = f'{summary} {notes}'
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
    expenses = allocation.expenses.exclude(status=Expense.Status.ANNULLED)
    if exclude_pk is not None:
        expenses = expenses.exclude(pk=exclude_pk)
    executed_amount = expenses.aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    if amount > allocation.amount - executed_amount:
        raise ValidationError({'amount': _('El monto del gasto excede el saldo disponible de la asignación.')})


# PRE: allocation is locked for update and amount is its complete proposed amount.
# POST: raises ValidationError if existing non-annulled expenses would leave the allocation with a negative balance.
def _validate_allocation_execution(allocation, amount):
    executed_amount = allocation.expenses.exclude(status=Expense.Status.ANNULLED).aggregate(
        total=Sum('amount')
    )['total'] or ZERO_MONEY
    if amount < executed_amount:
        raise ValidationError({'amount': _('El monto de la asignación no puede ser menor al monto ya ejecutado.')})


# PRE: expense_id identifies a saved expense and user is either authenticated or None.
# POST: returns the locked expense in validated state, with validator metadata and one audit event for a new transition.
def validate_expense(expense_id: int, user=None) -> Expense:
    with transaction.atomic():
        expense = Expense.objects.select_for_update().get(pk=expense_id)
        if not expense.supporting_documents.exists():
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        if expense.status == Expense.Status.VALIDATED:
            return expense
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
    ).exclude(status=Expense.Status.ANNULLED)
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


def review_project_update(update_id: int, reviewer, status: str, notes: str = '') -> ProjectUpdate:
    """
    PRE: update_id debe corresponder a un ProjectUpdate existente y status debe ser approved o rejected.
    POST: Actualiza el hito con estado final de revisión, reviewer, reviewed_at y notas.
    """
    if status not in {ProjectUpdate.Status.APPROVED, ProjectUpdate.Status.REJECTED}:
        raise ValidationError({'status': _('El estado de revisión debe ser aprobado o rechazado.')})
    project_update = ProjectUpdate.objects.get(pk=update_id)
    if project_update.status in {ProjectUpdate.Status.APPROVED, ProjectUpdate.Status.REJECTED}:
        raise ValidationError({'status': _('Un avance ya revisado no puede revisarse nuevamente.')})
    project_update.status = status
    project_update.reviewed_by = reviewer
    project_update.reviewed_at = timezone.now()
    project_update.review_notes = notes
    project_update.full_clean()
    project_update.save()
    log_review(reviewer, project_update, notes)
    return project_update
