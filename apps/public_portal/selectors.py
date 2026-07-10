from django.db.models import Sum
from django.shortcuts import get_object_or_404

from apps.operations.models import Donation, Expense, FundAllocation, Project, ProjectUpdate, ZERO_MONEY
from apps.operations.services import get_project_financial_summary


def get_public_projects():
    """
    PRE: Los modelos operativos están disponibles.
    POST: Retorna solo proyectos activos aptos para visualización pública, sin exponer datos sensibles.
    """
    return Project.objects.filter(status=Project.Status.ACTIVE).order_by('code')


def get_approved_project_updates(project):
    """
    PRE: project es una instancia válida de Project.
    POST: Retorna solo avances con estado approved.
    """
    return project.updates.filter(status=ProjectUpdate.Status.APPROVED).order_by('-created_at')


def get_recent_project_updates(project, limit: int = 20):
    """
    PRE: project es una instancia válida de Project y limit debe ser positivo.
    POST: Retorna hasta limit avances aprobados del proyecto.
    """
    return get_approved_project_updates(project)[:limit]


def get_recent_approved_updates(limit: int = 10):
    """
    PRE: limit debe ser un entero positivo.
    POST: Retorna avances aprobados recientes, sin datos privados de usuarios.
    """
    return ProjectUpdate.objects.filter(status=ProjectUpdate.Status.APPROVED).select_related('project').order_by('-created_at')[:limit]


def get_public_project_detail(project_id: int):
    """
    PRE: project_id corresponde a un proyecto existente.
    POST: Retorna el proyecto y datos públicos derivados para su visualización.
    """
    project = get_object_or_404(get_public_projects(), pk=project_id)
    return {
        'project': project,
        'financial_summary': get_project_financial_summary(project),
        'approved_updates': get_recent_project_updates(project),
    }


def get_public_transparency_summary():
    """
    PRE: Los modelos operativos están migrados.
    POST: Retorna métricas agregadas públicas sin datos privados.
    """
    donations = Donation.objects.exclude(status=Donation.Status.ANNULLED)
    allocations = FundAllocation.objects.exclude(status=FundAllocation.Status.ANNULLED)
    expenses = Expense.objects.exclude(status=Expense.Status.ANNULLED)
    total_received = donations.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    total_assigned = allocations.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    total_executed = expenses.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    return {
        'project_count': get_public_projects().count(),
        'total_received': total_received,
        'total_assigned': total_assigned,
        'total_executed': total_executed,
        'available_balance': max(total_received - total_assigned, ZERO_MONEY),
        'approved_update_count': ProjectUpdate.objects.filter(status=ProjectUpdate.Status.APPROVED).count(),
    }
