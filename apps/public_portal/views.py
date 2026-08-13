from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.operations.private_files import (
    DISPOSITION_ATTACHMENT,
    DISPOSITION_INLINE,
    deliver_public_file,
)

from .selectors import (
    get_eligible_public_update_attachment,
    get_public_project_detail,
    get_public_project_update_detail,
    get_public_projects,
    get_public_transparency_summary,
    get_public_update_documents,
    get_recent_published_updates,
)


class PublicHomeView(TemplateView):
    template_name = 'public_portal/public_home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['summary'] = get_public_transparency_summary()
        context['recent_updates'] = get_recent_published_updates()
        return context


class PublicProjectListView(ListView):
    template_name = 'public_portal/public_project_list.html'
    context_object_name = 'projects'
    paginate_by = 20

    def get_queryset(self):
        return get_public_projects()


class PublicProjectDetailView(TemplateView):
    template_name = 'public_portal/public_project_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_public_project_detail(self.kwargs['pk']))
        return context


class PublicProjectUpdateDetailView(TemplateView):
    template_name = 'public_portal/public_project_update_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        update = get_public_project_update_detail(self.kwargs['pk'])
        context['update'] = update
        context['public_attachments'] = get_public_update_documents(update)
        return context


class PublicUpdatesFeedView(ListView):
    template_name = 'public_portal/public_updates_feed.html'
    context_object_name = 'updates'
    paginate_by = 20

    def get_queryset(self):
        return get_recent_published_updates(limit=200)


class PublicProjectUpdateAttachmentDeliveryView(View):
    """Anonymous delivery of explicitly public project-update attachments."""

    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El documento no está disponible.')

    def get(self, request, *args, **kwargs):
        """
        PRE: URL nests update_id and attachment_id from the public portal.
        POST: streams the file only when every public eligibility condition holds;
              otherwise 404 (never 403) so private existence is not revealed.
        """
        attachment = get_eligible_public_update_attachment(
            update_id=kwargs['update_id'],
            attachment_id=kwargs['attachment_id'],
        )
        return deliver_public_file(
            attachment.file,
            disposition=self.disposition,
            missing_message=self.missing_message,
        )


class PublicProjectUpdateAttachmentPreviewView(PublicProjectUpdateAttachmentDeliveryView):
    disposition = DISPOSITION_INLINE


def public_projects_json(request):
    """
    PRE: el selector público aplica la política vigente de visibilidad de proyectos.
    POST: devuelve solo campos ya públicos, sin usuarios, contactos ni rutas de archivos.
    """
    projects = [
        {
            'code': project.code,
            'name': project.name,
            'description': project.description,
            'location': project.location,
            'status': project.status,
            'start_date': project.start_date,
            'end_date': project.end_date,
        }
        for project in get_public_projects()
    ]
    return JsonResponse({'projects': projects})


def public_metrics_json(request):
    """
    PRE: el resumen público contiene solo agregados aprobados por los selectores existentes.
    POST: devuelve una estructura JSON estable de métricas, sin datos privados ni archivos.
    """
    return JsonResponse({'metrics': get_public_transparency_summary()})
