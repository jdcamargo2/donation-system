from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from .models import AuditLog, Donation, Expense, FundAllocation, Project, ProjectUpdate, ZERO_MONEY


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
    donations = Donation.objects.exclude(status=Donation.Status.ANNULLED)
    allocations = FundAllocation.objects.exclude(status=FundAllocation.Status.ANNULLED)
    expenses = Expense.objects.exclude(status=Expense.Status.ANNULLED)
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
