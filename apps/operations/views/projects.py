from decimal import Decimal

from django.conf import settings

from django.core.exceptions import PermissionDenied

from django.db.models import Prefetch

from django.shortcuts import get_object_or_404

from django.http import HttpResponseRedirect

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
    annul_project,
    ensure_operational_entity_is_editable,
    finish_project,
    log_create,
    log_delete,
    PROJECT_STATUS_TRANSITIONS,
    transition_project_status,
)

from .common import (
    AuditMixin,
    DeleteAuditMixin,
    DetailMetricsMixin,
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
    StateTransitionContextMixin,
    StateTransitionView,
    TerminalActionView,
    _protected_file_response,
    apply_list_filters,
)


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


class ProjectAnnulView(TerminalActionView):
    permission_required = 'operations.change_project'
    model = Project
    action_service = staticmethod(annul_project)
    detail_url_name = 'project_detail'
    action_title = _('Anular proyecto')
    consequence = _('Solo puede anularse si no mantiene asignaciones activas. Esta acción es irreversible.')
    submit_label = _('Confirmar anulación')
    success_message = _('Proyecto anulado.')


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

class ProjectDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_detail.html'
    route_prefix = 'project'
    page_title = _('Proyecto')
    transition_map = PROJECT_STATUS_TRANSITIONS
    transition_url_name = 'project_status_transition'

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
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed_targets = PROJECT_STATUS_TRANSITIONS.get(self.object.status, ())
        context['can_finish'] = Project.Status.CLOSED in allowed_targets
        context['can_annul'] = (
            Project.Status.ANNULLED in allowed_targets
            and not self.object.allocations.exclude(status=FundAllocation.Status.ANNULLED).exists()
        )
        updates = self.object.updates.select_related('created_by', 'reported_by').prefetch_related('attachments')
        if not self.request.user.has_perm('operations.view_projectupdate'):
            updates = updates.filter(status=ProjectUpdate.Status.PUBLISHED)
        context['project_updates'] = updates.order_by('-update_date', '-created_at')
        context['project_documents'] = self.object.detail_documents
        summary = get_project_financial_summary(self.object)
        context['project_financial_summary'] = summary
        context['execution_percentage'] = (
            (summary['executed_amount'] / summary['funded_amount']) * Decimal('100')
            if summary['funded_amount'] > 0 else Decimal('0')
        )
        has_kobo_binding = settings.KOBO_ENABLED and self.object.kobo_bindings.filter(
            is_active=True
        ).exists()
        context['show_kobo_section'] = has_kobo_binding
        if has_kobo_binding:
            from apps.integrations.kobo.models import KoboAsset
            from apps.integrations.kobo.services import (
                get_project_imported_submissions,
                get_project_pending_submissions,
            )

            context['kobo_territorial_submissions'] = get_project_imported_submissions(
                self.object,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            )
            context['kobo_microproject_submissions'] = get_project_imported_submissions(
                self.object,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            )
            context['kobo_prioritization_submissions'] = get_project_imported_submissions(
                self.object,
                form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            )
            context['kobo_submissions'] = context['kobo_territorial_submissions']
            context['kobo_pending_submissions'] = get_project_pending_submissions(
                self.object
            )
            context['kobo_pending_submission_count'] = context[
                'kobo_pending_submissions'
            ].count()
            context['can_import_kobo_submissions'] = self.request.user.has_perm(
                'operations.change_project'
            )
        else:
            context['kobo_territorial_submissions'] = ()
            context['kobo_microproject_submissions'] = ()
            context['kobo_prioritization_submissions'] = ()
            context['kobo_submissions'] = ()
            context['kobo_pending_submissions'] = ()
            context['kobo_pending_submission_count'] = 0
            context['can_import_kobo_submissions'] = False
        return context

    def get_template_names(self):
        # PRE: project detail routing and settings are available.
        # POST: uses Kobo-aware UI only while enabled, preserving legacy UI otherwise.
        if settings.KOBO_ENABLED:
            return ['operations/project_detail.html']
        return super().get_template_names()


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


class ProjectStatusTransitionView(StateTransitionView):
    permission_required = 'operations.change_project'
    transition_service = staticmethod(transition_project_status)
    detail_url_name = 'project_detail'


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

    def form_valid(self, form):
        # PRE: el usuario tiene permiso y self.object es el documento confirmado.
        # POST: audita y elimina el registro; el proyecto permanece intacto.
        project_id = self.object.project_id
        log_delete(self.request.user, self.object, _('Documento de proyecto eliminado.'))
        self.object.delete()
        return HttpResponseRedirect(reverse('project_detail', args=[project_id]))
