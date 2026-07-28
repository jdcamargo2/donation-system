from django.db.models import Sum
from django.shortcuts import get_object_or_404

from apps.operations.choices import OPERATING_CURRENCY
from apps.operations.models import Donation, Expense, FundAllocation, Project, ProjectUpdate, ZERO_MONEY


# PRE: Operational models are available and project is either a saved Project or None.
# POST: returns only non-annulled allocations backed by non-annulled donations for public active projects.
def _get_public_allocations(project=None):
    allocations = FundAllocation.objects.filter(
        project__is_public=True,
        project__status=Project.Status.ACTIVE,
        donation__currency=OPERATING_CURRENCY,
    ).exclude(
        status=FundAllocation.Status.ANNULLED,
    ).exclude(
        donation__status=Donation.Status.ANNULLED,
    )
    if project is not None:
        allocations = allocations.filter(project=project)
    return allocations


# PRE: allocations contains the allocation scope approved for public aggregation.
# POST: returns only non-annulled expenses belonging to that exact public allocation scope.
def _get_public_expenses(allocations):
    return Expense.objects.filter(
        allocation__in=allocations,
        currency=OPERATING_CURRENCY,
    ).exclude(status=Expense.Status.ANNULLED)


# PRE: project is an active public Project returned by get_public_projects().
# POST: returns project financial metrics calculated only from its public allocation scope.
def _get_public_project_financial_summary(project):
    allocations = _get_public_allocations(project)
    expenses = _get_public_expenses(allocations)
    funded_amount = allocations.aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    executed_amount = expenses.aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    return {
        'estimated_budget': project.estimated_budget,
        'funded_amount': funded_amount,
        'executed_amount': executed_amount,
        'available_amount': max(funded_amount - executed_amount, ZERO_MONEY),
    }


def get_public_projects():
    """
    PRE: Los modelos operativos están disponibles.
    POST: Retorna solo proyectos activos y públicos aptos para visualización, sin exponer datos sensibles.
    """
    return Project.objects.filter(
        is_public=True,
        status=Project.Status.ACTIVE,
    ).order_by('code')


def get_published_project_updates(project):
    """
    PRE: project es una instancia válida de Project.
    POST: Retorna solo avances publicados cuando el proyecto continúa activo y público.
    """
    return project.updates.filter(
        status=ProjectUpdate.Status.PUBLISHED,
        project__is_public=True,
        project__status=Project.Status.ACTIVE,
    ).order_by('-created_at')


def get_recent_project_updates(project, limit: int = 20):
    """
    PRE: project es una instancia válida de Project y limit debe ser positivo.
    POST: Retorna hasta limit avances publicados del proyecto.
    """
    return get_published_project_updates(project)[:limit]


def get_recent_published_updates(limit: int = 10):
    """
    PRE: limit debe ser un entero positivo.
    POST: Retorna avances publicados recientes de proyectos activos y públicos, sin datos privados.
    """
    return ProjectUpdate.objects.filter(
        status=ProjectUpdate.Status.PUBLISHED,
        project__is_public=True,
        project__status=Project.Status.ACTIVE,
    ).select_related('project').order_by('-created_at')[:limit]


def get_public_project_update_detail(update_id: int):
    """
    PRE: update_id identifica un avance que se solicita desde el portal público.
    POST: retorna únicamente un avance PUBLISHED de un proyecto ACTIVE y público, con los
    campos públicos necesarios; cualquier otro avance produce 404.
    """
    return get_object_or_404(
        ProjectUpdate.objects.filter(
            status=ProjectUpdate.Status.PUBLISHED,
            project__is_public=True,
            project__status=Project.Status.ACTIVE,
        )
        .select_related('project')
        .only(
            'id',
            'project_id',
            'title',
            'description',
            'update_date',
            'project__id',
            'project__code',
            'project__name',
        ),
        pk=update_id,
    )


def get_public_project_detail(project_id: int):
    """
    PRE: project_id corresponde a un proyecto existente.
    POST: Retorna el proyecto y datos públicos derivados para su visualización.
    """
    project = get_object_or_404(get_public_projects(), pk=project_id)
    return {
        'project': project,
        'financial_summary': _get_public_project_financial_summary(project),
        'published_updates': get_recent_project_updates(project),
    }


def get_public_transparency_summary():
    """
    PRE: Los modelos operativos están migrados.
    POST: Retorna métricas agregadas públicas sin datos privados.
    """
    allocations = _get_public_allocations()
    expenses = _get_public_expenses(allocations)
    donations = Donation.objects.filter(
        pk__in=allocations.values('donation_id'),
    ).exclude(status=Donation.Status.ANNULLED)
    total_received = donations.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    total_assigned = allocations.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    total_executed = expenses.aggregate(total=Sum('amount', default=ZERO_MONEY))['total'] or ZERO_MONEY
    return {
        'project_count': get_public_projects().count(),
        'total_received': total_received,
        'total_assigned': total_assigned,
        'total_executed': total_executed,
        'available_balance': max(total_assigned - total_executed, ZERO_MONEY),
        'published_update_count': ProjectUpdate.objects.filter(
            status=ProjectUpdate.Status.PUBLISHED,
            project__is_public=True,
            project__status=Project.Status.ACTIVE,
        ).count(),
    }
