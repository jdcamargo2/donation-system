from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

from django.db.models import Prefetch

from django.http import Http404, HttpResponseRedirect

from django.shortcuts import get_object_or_404, render

from django.urls import (
    reverse,
    reverse_lazy,
)

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from ..forms import (
    ProjectForm,
    ProjectDocumentForm,
)

from ..models import (
    AuditLog,
    FundAllocation,
    Project,
    ProjectDocument,
    ProjectUpdate,
)

from ..services import (
    get_project_financial_summary,
    OperationalEntityFinalizedError,
    ensure_operational_entity_is_editable,
    finish_project,
    log_create,
    log_delete,
    publish_project,
    unpublish_project,
)

from .common import (
    AuditMixin,
    DeleteAuditMixin,
    DetailMetricsMixin,
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
    TerminalActionView,
    _protected_file_response,
    apply_list_filters,
)
from .project_milestone_context import (
    build_project_milestone_context,
    project_milestone_prefetch,
)
from ..integrations import get_project_detail_integration_context


RECENT_PROJECT_UPDATES_LIMIT = 5


def get_visible_project_updates(project, user):
    """
    PRE: project is persisted and user is authenticated.
    POST: returns an unevaluated, stably ordered queryset visible to that user.
    """
    updates = project.updates.select_related('reported_by')
    if not user.has_perm('operations.view_projectupdate'):
        updates = updates.filter(status=ProjectUpdate.Status.PUBLISHED)
    return updates.order_by('-created_at', '-pk')


class ProjectFinishView(TerminalActionView):
    permission_required = 'operations.change_project'
    model = Project
    action_service = staticmethod(finish_project)
    detail_url_name = 'project_detail'
    action_title = _('Terminar proyecto')
    consequence = _('Al terminar el proyecto no podrá volver a editarlo ni reactivarlo.')
    submit_label = _('Confirmar terminación')
    success_message = _('Proyecto terminado.')
    is_destructive = False
    requires_reason = False


class ProjectPublishView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.manage_project_publication'

    def post(self, request, *args, **kwargs):
        """
        PRE: the user holds manage_project_publication and pk identifies a Project.
        POST: publishes via domain service or reports the domain error without mutating.
        """
        try:
            project = publish_project(project_id=kwargs['pk'], actor=request.user)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return HttpResponseRedirect(reverse('project_detail', args=[kwargs['pk']]))
        messages.success(request, _('Proyecto publicado en el portal público.'))
        return HttpResponseRedirect(reverse('project_detail', args=[project.pk]))


class ProjectUnpublishView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.manage_project_publication'

    def post(self, request, *args, **kwargs):
        """
        PRE: the user holds manage_project_publication and pk identifies a Project.
        POST: unpublishes via domain service or reports the domain error without mutating.
        """
        try:
            project = unpublish_project(project_id=kwargs['pk'], actor=request.user)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return HttpResponseRedirect(reverse('project_detail', args=[kwargs['pk']]))
        messages.success(request, _('Proyecto retirado del portal público.'))
        return HttpResponseRedirect(reverse('project_detail', args=[project.pk]))


class ProjectListView(
    OperationsPermissionRequiredMixin,
    FilteredListContextMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_list.html'
    context_object_name = 'objects'
    route_prefix = 'project'
    page_title = _('Proyectos')
    status_choices = Project.Status.choices
    export_url_name = 'project_export_csv'

    def get_queryset(self):
        return apply_list_filters(
            Project.objects.all(), self.request.GET,
            text_fields=('code', 'name'), date_field='start_date',
        ).order_by('code', 'pk')

class ProjectDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_detail.html'
    route_prefix = 'project'
    page_title = _('Proyecto')

    def get_queryset(self):
        # PRE: la vista consulta un proyecto autorizado por clave primaria.
        # POST: carga metadata y relaciones visibles evitando consultas por cada fila renderizada.
        return Project.objects.select_related('terminal_by').prefetch_related(
            Prefetch(
                'allocations',
                queryset=FundAllocation.objects.prefetch_related('expenses'),
            ),
            Prefetch(
                'documents',
                queryset=ProjectDocument.objects.select_related('uploaded_by'),
                to_attr='detail_documents',
            ),
            project_milestone_prefetch(),
        )

    def get_context_data(self, **kwargs):
        """
        PRE: self.object was loaded through get_queryset with detail relations prefetched.
        POST: returns one coherent detail context with derived milestone progress and UI permissions.
        """
        context = super().get_context_data(**kwargs)
        context['can_finish'] = (
            self.request.user.has_perm('operations.change_project')
            and self.object.status == Project.Status.ACTIVE
        )
        context['can_manage_publication'] = self.request.user.has_perm(
            'operations.manage_project_publication'
        )
        context['can_publish'] = (
            context['can_manage_publication']
            and self.object.status == Project.Status.ACTIVE
            and not self.object.is_public
        )
        context['can_unpublish'] = (
            context['can_manage_publication']
            and self.object.status == Project.Status.ACTIVE
            and self.object.is_public
        )
        visible_updates = get_visible_project_updates(self.object, self.request.user)
        update_paginator = Paginator(
            visible_updates,
            RECENT_PROJECT_UPDATES_LIMIT,
        )
        update_page = update_paginator.page(1)
        context['recent_project_updates'] = update_page.object_list
        context['project_update_page'] = update_page
        context['project_update_count'] = update_paginator.count
        context['has_more_project_updates'] = update_page.has_next()
        context['project_documents'] = self.object.detail_documents
        context.update(
            build_project_milestone_context(self.object, self.request.user)
        )
        summary = get_project_financial_summary(self.object)
        context['project_financial_summary'] = summary
        context['execution_percentage'] = (
            (summary['executed_amount'] / summary['funded_amount']) * Decimal('100')
            if summary['funded_amount'] > 0 else Decimal('0')
        )
        context.update(
            get_project_detail_integration_context(self.object, self.request.user)
        )
        return context

    def get_template_names(self):
        # PRE: project detail routing and settings are available.
        # POST: retains the existing Kobo wrapper while enabled without importing Kobo.
        if settings.KOBO_ENABLED:
            return ['operations/project_detail.html']
        return super().get_template_names()


class ProjectUpdateChunkView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.view_project'

    def get(self, request, project_pk):
        """
        PRE: request.user may view projects and project_pk identifies the requested project.
        POST: returns one database-paginated update fragment using detail visibility rules.
        """
        project = get_object_or_404(Project, pk=project_pk)
        paginator = Paginator(
            get_visible_project_updates(project, request.user),
            RECENT_PROJECT_UPDATES_LIMIT,
        )
        try:
            page = paginator.page(request.GET.get('page', 1))
        except (PageNotAnInteger, EmptyPage) as exc:
            raise Http404('La página de avances solicitada no existe.') from exc
        return render(
            request,
            'web/includes/project_update_chunk.html',
            {
                'object': project,
                'project_update_page': page,
                'project_updates': page.object_list,
                'is_update_chunk': True,
            },
        )


class ProjectCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_project'
    model = Project
    form_class = ProjectForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Nuevo proyecto')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Proyecto creado.')


class ProjectUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_project'
    model = Project
    form_class = ProjectForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Editar proyecto')
    audit_summary = _('Proyecto actualizado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary project editing and permission handling remains authoritative.
        # POST: terminal projects return 403 without form mutation.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            project = get_object_or_404(Project, pk=kwargs['pk'])
            try:
                ensure_operational_entity_is_editable(project)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


class ProjectDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_project'
    model = Project
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Eliminar proyecto')
    audit_summary = _('Proyecto eliminado.')


class ProjectDocumentCreateView(OperationsPermissionRequiredMixin, CreateView):
    permission_required = 'operations.add_projectdocument'
    model = ProjectDocument
    form_class = ProjectDocumentForm
    template_name = 'web/project_document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # PRE: el formulario contiene metadatos y archivo válidos para self.project.
        # POST: guarda el documento, atribuye al usuario y registra auditoría.
        form.instance.project = self.project
        form.instance.uploaded_by = self.request.user
        response = super().form_valid(form)
        log_create(self.request.user, self.object, _('Documento de proyecto agregado.'))
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def get_success_url(self):
        return reverse('project_detail', args=[self.project.pk])


class ProjectDocumentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectdocument'
    model = ProjectDocument

    def get(self, request, *args, **kwargs):
        # PRE: el usuario tiene permiso de lectura y pk identifica un documento.
        # POST: descarga el archivo sin revelar su ruta de almacenamiento.
        return _protected_file_response(
            self.get_object().file,
            missing_message=_('El documento de proyecto no está disponible.'),
        )


class ProjectDocumentDeleteView(OperationsPermissionRequiredMixin, DeleteView):
    permission_required = 'operations.delete_projectdocument'
    model = ProjectDocument
    template_name = 'web/object_confirm_delete.html'

    def get_context_data(self, **kwargs):
        """
        PRE: self.object is the project document selected for confirmation.
        POST: gives the shared confirmation page a valid return URL to its project.
        """
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = reverse(
            'project_detail', args=[self.object.project_id]
        )
        return context

    def form_valid(self, form):
        # PRE: el usuario tiene permiso y self.object es el documento confirmado.
        # POST: audita y elimina el registro; el proyecto permanece intacto.
        project_id = self.object.project_id
        log_delete(self.request.user, self.object, _('Documento de proyecto eliminado.'))
        self.object.delete()
        return HttpResponseRedirect(reverse('project_detail', args=[project_id]))
