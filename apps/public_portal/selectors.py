from pathlib import PurePosixPath

from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import strip_tags

from apps.operations.choices import OPERATING_CURRENCY
from apps.operations.models import (
    Donation,
    Expense,
    FundAllocation,
    Project,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ZERO_MONEY,
)
from apps.operations.private_files import (
    can_preview_persisted_file,
    sanitize_download_filename,
)


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


def _public_attachment_queryset():
    """
    PRE: ProjectUpdateAttachment rows may mix private and public flags.
    POST: returns only attachments explicitly marked public (parent visibility is
          enforced by the caller or by get_eligible_public_update_attachment).
    """
    return ProjectUpdateAttachment.objects.filter(is_public=True).order_by('created_at')


def _sanitize_public_attachment_title(attachment) -> str:
    raw_title = strip_tags((attachment.title or '').strip())
    if raw_title:
        return raw_title[:200]
    return sanitize_download_filename(attachment.file)


def _public_attachment_extension(attachment) -> str:
    if not attachment.file or not getattr(attachment.file, 'name', None):
        return ''
    name = str(attachment.file.name).replace('\\', '/')
    return PurePosixPath(name).suffix.lower().lstrip('.')


def serialize_public_update_attachment(attachment) -> dict:
    """
    PRE: attachment already passed public eligibility (is_public + parent visibility).
    POST: returns only sanitized metadata safe for anonymous templates.
    """
    filename = sanitize_download_filename(attachment.file)
    extension = _public_attachment_extension(attachment)
    can_preview = can_preview_persisted_file(attachment.file)
    payload = {
        'id': attachment.pk,
        'title': _sanitize_public_attachment_title(attachment),
        'filename': filename,
        'extension': extension,
        'can_preview': can_preview,
        'download_url': reverse(
            'public_portal:public_project_update_attachment_download',
            args=[attachment.project_update_id, attachment.pk],
        ),
        'preview_url': None,
    }
    if can_preview:
        payload['preview_url'] = reverse(
            'public_portal:public_project_update_attachment_preview',
            args=[attachment.project_update_id, attachment.pk],
        )
    return payload


def get_public_update_documents(project_update) -> list[dict]:
    """
    PRE: project_update is a PUBLISHED update of an ACTIVE public project (or will
         be empty when that invariant does not hold).
    POST: returns sanitized public attachment payloads only; never remediation or
          private update attachments.
    """
    if (
        project_update.status != ProjectUpdate.Status.PUBLISHED
        or not project_update.project.is_public
        or project_update.project.status != Project.Status.ACTIVE
    ):
        return []

    # Prefer prefetched public attachments when present; otherwise query once.
    prefetched = getattr(project_update, '_prefetched_objects_cache', {})
    if 'attachments' in prefetched:
        attachments = [
            attachment
            for attachment in project_update.attachments.all()
            if attachment.is_public and attachment.file and attachment.file.name
        ]
    else:
        attachments = list(
            _public_attachment_queryset()
            .filter(project_update_id=project_update.pk)
            .exclude(file='')
        )
    return [
        serialize_public_update_attachment(attachment)
        for attachment in attachments
        if attachment.file and getattr(attachment.file, 'name', None)
    ]


def get_eligible_public_update_attachment(*, update_id: int, attachment_id: int):
    """
    PRE: update_id and attachment_id come from a public URL.
    POST: returns the attachment only when every public eligibility condition holds;
          otherwise raises Http404 via get_object_or_404 (no existence leak).
    """
    return get_object_or_404(
        ProjectUpdateAttachment.objects.select_related('project_update__project').filter(
            pk=attachment_id,
            project_update_id=update_id,
            is_public=True,
            project_update__status=ProjectUpdate.Status.PUBLISHED,
            project_update__project__is_public=True,
            project_update__project__status=Project.Status.ACTIVE,
        ),
    )


def get_public_project_update_detail(update_id: int):
    """
    PRE: update_id identifica un avance que se solicita desde el portal público.
    POST: retorna únicamente un avance PUBLISHED de un proyecto ACTIVE y público, con los
    campos públicos necesarios y adjuntos públicos prefetched; cualquier otro avance
    produce 404.
    """
    return get_object_or_404(
        ProjectUpdate.objects.filter(
            status=ProjectUpdate.Status.PUBLISHED,
            project__is_public=True,
            project__status=Project.Status.ACTIVE,
        )
        .select_related('project')
        .prefetch_related(
            Prefetch(
                'attachments',
                queryset=_public_attachment_queryset(),
            )
        )
        .only(
            'id',
            'project_id',
            'title',
            'description',
            'update_date',
            'status',
            'project__id',
            'project__code',
            'project__name',
            'project__is_public',
            'project__status',
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
