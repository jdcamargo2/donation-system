from dataclasses import dataclass

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from .forms import (
    DonationForm,
    ExpenseCancellationForm,
    ExpenseForm,
    FundAllocationForm,
    InstitutionForm,
    ProjectForm,
    ProjectUpdateForProjectForm,
    ProjectUpdateForm,
    ProjectUpdateReviewForm,
    SupportingDocumentForm,
)
from .models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument
from .services import (
    create_expense,
    create_fund_allocation,
    get_allocation_financial_summary,
    get_dashboard_metrics,
    get_donation_financial_summary,
    get_project_financial_summary,
    cancel_expense,
    ensure_expense_is_deletable,
    ensure_expense_is_editable,
    ExpenseFinalizedError,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    ProjectUpdateImmutableError,
    log_action,
    log_delete,
    register_advance,
    review_project_update,
    update_expense,
    update_fund_allocation,
    update_project_update,
    DONATION_STATUS_TRANSITIONS,
    FUND_ALLOCATION_STATUS_TRANSITIONS,
    PROJECT_STATUS_TRANSITIONS,
    transition_donation_status,
    transition_fund_allocation_status,
    transition_project_status,
)


@dataclass(frozen=True)
class ProtectedRelationInfo:
    label: str
    count: int
    recommendation: str


PROTECTED_RELATION_PRESENTATION = {
    Donation: (_('donación asociada'), _('donaciones asociadas')),
    FundAllocation: (_('asignación asociada'), _('asignaciones asociadas')),
    Expense: (_('gasto asociado'), _('gastos asociados')),
    AuditLog: (_('registro de auditoría asociado'), _('registros de auditoría asociados')),
}


# PRE: protected_error is a real Django ProtectedError containing persisted protected objects.
# POST: returns related objects grouped by human model label, count, and realistic recommendation.
def describe_protected_relations(protected_error):
    grouped_objects = {}
    for protected_object in protected_error.protected_objects:
        grouped_objects.setdefault(type(protected_object), []).append(protected_object)

    related_groups = []
    for model_class, objects in grouped_objects.items():
        singular, plural = PROTECTED_RELATION_PRESENTATION.get(
            model_class,
            (model_class._meta.verbose_name, model_class._meta.verbose_name_plural),
        )
        count = len(objects)
        label = singular if count == 1 else plural
        if model_class is Expense:
            recommendation = _(
                'Conserve la asignación como registro histórico o gestione primero los gastos relacionados.'
            )
        else:
            recommendation = _(
                'Conserve el registro como historial o gestione primero los registros relacionados.'
            )
        related_groups.append(
            ProtectedRelationInfo(
                label=str(label),
                count=count,
                recommendation=str(recommendation),
            )
        )
    return sorted(related_groups, key=lambda group: group.label)


# PRE: object_label is human-readable and related_groups contains at least one protected group.
# POST: returns a non-technical message that never claims the object was deleted.
def build_protected_delete_message(*, object_label, related_groups):
    if not related_groups:
        raise ValueError('Se requiere al menos una relación protegida.')
    relation_summary = ' y '.join(
        f'{group.count} {group.label}' for group in related_groups
    )
    recommendations = ' '.join(
        dict.fromkeys(group.recommendation for group in related_groups)
    )
    return _(
        'No se puede eliminar %(object)s porque tiene %(relations)s. %(recommendations)s'
    ) % {
        'object': object_label,
        'relations': relation_summary,
        'recommendations': recommendations,
    }


# PRE: instance is a persisted operations object shown in a delete flow.
# POST: returns a human label without exposing internal model names or database identifiers.
def get_delete_object_label(instance):
    if isinstance(instance, Institution):
        return _('la institución %(name)s') % {'name': instance.name}
    if isinstance(instance, Project):
        return _('el proyecto %(code)s - %(name)s') % {'code': instance.code, 'name': instance.name}
    if isinstance(instance, Donation):
        return _('la donación %(code)s') % {'code': instance.code}
    if isinstance(instance, FundAllocation):
        return _('la asignación %(code)s') % {'code': instance.code}
    if isinstance(instance, Expense):
        return _('el gasto %(code)s') % {'code': instance.code}
    if isinstance(instance, ProjectUpdate):
        return _('el avance %(title)s') % {'title': instance.title}
    if isinstance(instance, SupportingDocument):
        return _('el documento soporte %(title)s') % {'title': instance.title}
    return str(instance)


# PRE: instance is the persisted object displayed by an operational delete confirmation.
# POST: returns only known CASCADE consequences with positive counts; it does not mark them protected.
def describe_known_cascade_consequences(instance):
    if isinstance(instance, Project):
        count = instance.updates.count()
        label = _('avance de proyecto') if count == 1 else _('avances de proyecto')
    elif isinstance(instance, Expense):
        count = instance.supporting_documents.count()
        label = _('documento soporte') if count == 1 else _('documentos soporte')
    else:
        return []
    return [{'count': count, 'label': label}] if count else []


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_dashboard_metrics())
        return context


class OperationsPermissionRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    raise_exception = True

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())
        return redirect_to_login(self.request.get_full_path(), self.get_login_url(), self.get_redirect_field_name())


# PRE: form is bound and error is a domain ValidationError raised after initial form validation.
# POST: adds every domain error to its matching form field, or as a non-field error when no field matches.
def add_service_errors_to_form(form, error):
    if hasattr(error, 'error_dict'):
        for field_name, field_errors in error.message_dict.items():
            target_field = field_name if field_name in form.fields else None
            for message in field_errors:
                form.add_error(target_field, message)
        return
    for message in error.messages:
        form.add_error(None, message)


class RouteContextMixin:
    route_prefix = ''
    page_title = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': self.page_title,
                'list_url_name': f'{self.route_prefix}_list',
                'create_url_name': f'{self.route_prefix}_create',
                'detail_url_name': f'{self.route_prefix}_detail',
                'update_url_name': f'{self.route_prefix}_update',
                'delete_url_name': f'{self.route_prefix}_delete',
            }
        )
        return context


class DetailMetricsMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        metrics = []
        if isinstance(self.object, Donation):
            summary = get_donation_financial_summary(self.object)
            metrics = [
                (_('Monto'), summary['total_amount']),
                (_('Monto asignado'), summary['assigned_amount']),
                (_('Saldo disponible'), summary['available_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, Project):
            summary = get_project_financial_summary(self.object)
            metrics = [
                (_('Presupuesto estimado'), summary['estimated_budget']),
                (_('Monto financiado'), summary['funded_amount']),
                (_('Monto ejecutado'), summary['executed_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, FundAllocation):
            summary = get_allocation_financial_summary(self.object)
            metrics = [
                (_('Monto'), summary['allocated_amount']),
                (_('Monto ejecutado'), summary['executed_amount']),
                (_('Saldo disponible'), summary['available_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, Institution):
            metrics = [
                (_('Rol'), self.object.get_role_display()),
                (_('País'), self.object.country.name or '-'),
                (_('Estado'), self.object.get_status_display()),
            ]
        context['metrics'] = metrics
        return context


class StateTransitionContextMixin:
    transition_map = {}
    transition_url_name = ''

    def get_context_data(self, **kwargs):
        """
        PRE: self.object has model status choices and transition_map is explicit.
        POST: exposes only allowed target states and their POST endpoint to the template.
        """
        context = super().get_context_data(**kwargs)
        labels = dict(self.object.Status.choices)
        context['status_transitions'] = [
            {'value': target, 'label': labels[target]}
            for target in self.transition_map.get(self.object.status, ())
        ]
        context['transition_url_name'] = self.transition_url_name
        return context


class StateTransitionView(OperationsPermissionRequiredMixin, View):
    transition_service = None
    detail_url_name = ''

    def post(self, request, *args, **kwargs):
        """
        PRE: user has the model change permission and target state comes from the URL.
        POST: delegates one explicit transition to its locked service and redirects to detail.
        """
        object_id = kwargs['pk']
        try:
            self.transition_service(
                object_id,
                actor=request.user,
                target_status=kwargs['target_status'],
            )
            messages.success(request, _('Estado actualizado.'))
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        return HttpResponseRedirect(reverse(self.detail_url_name, args=[object_id]))


class AuditMixin:
    audit_action = AuditLog.Action.UPDATED
    audit_summary = _('Registro actualizado.')

    # PRE: self.object has just been saved and request.user is available.
    # POST: creates one audit log describing the saved entity and user action.
    def write_audit_log(self):
        log_action(self.request.user, self.audit_action, self.object, self.audit_summary)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return response


class DeleteAuditMixin:
    audit_summary = _('Registro eliminado.')

    def get_context_data(self, **kwargs):
        # PRE: self.object is the persisted object shown by the delete confirmation.
        # POST: adds a human label and known cascade consequences without deleting anything.
        context = super().get_context_data(**kwargs)
        context['delete_object_label'] = get_delete_object_label(self.object)
        context['cascade_consequences'] = describe_known_cascade_consequences(self.object)
        return context

    # PRE: self.object exists and POST passed permission and confirmation handling.
    # POST: deletes and audits atomically, or reports protected relations without success/audit mutation.
    def form_valid(self, form):
        object_label = get_delete_object_label(self.object)
        try:
            with transaction.atomic():
                log_delete(self.request.user, self.object, self.audit_summary)
                response = super().form_valid(form)
        except ProtectedError as error:
            related_groups = describe_protected_relations(error)
            messages.error(
                self.request,
                build_protected_delete_message(
                    object_label=object_label,
                    related_groups=related_groups,
                ),
            )
            return HttpResponseRedirect(self.get_success_url())
        messages.success(self.request, self.audit_summary)
        return response


class InstitutionListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_list.html'
    context_object_name = 'objects'
    route_prefix = 'institution'
    page_title = _('Instituciones')


class InstitutionDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_detail.html'
    route_prefix = 'institution'
    page_title = _('Institución')


def _protected_file_response(file_field, *, missing_message):
    """
    PRE: file_field belongs to an authorized object and missing_message is safe.
    POST: streams an existing file as an attachment using only a safe basename;
    otherwise raises Http404 without exposing its storage path.
    """
    if not file_field or not file_field.name:
        raise Http404(missing_message)
    stored_basename = file_field.name.replace('\\', '/').rsplit('/', 1)[-1]
    safe_filename = get_valid_filename(stored_basename) or 'documento'
    try:
        file_handle = file_field.open('rb')
    except (FileNotFoundError, OSError) as exc:
        raise Http404(missing_message) from exc
    return FileResponse(file_handle, as_attachment=True, filename=safe_filename)


class InstitutionLegalDocumentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_institution'
    model = Institution

    def get(self, request, *args, **kwargs):
        """
        PRE: user is authenticated with view_institution and pk identifies an
        institution whose legal document exists in storage.
        POST: returns an attachment response without mutation or storage paths.
        """
        institution = self.get_object()
        return _protected_file_response(
            institution.legal_document,
            missing_message=_('El documento legal no está disponible.'),
        )


class InstitutionCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_institution'
    model = Institution
    form_class = InstitutionForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Nueva institución')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Institución creada.')


class InstitutionUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_institution'
    model = Institution
    form_class = InstitutionForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Editar institución')
    audit_summary = _('Institución actualizada.')


class InstitutionDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_institution'
    model = Institution
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Eliminar institución')
    audit_summary = _('Institución eliminada.')


class ProjectListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_list.html'
    context_object_name = 'objects'
    route_prefix = 'project'
    page_title = _('Proyectos')


class ProjectDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_detail.html'
    route_prefix = 'project'
    page_title = _('Proyecto')
    transition_map = PROJECT_STATUS_TRANSITIONS
    transition_url_name = 'project_status_transition'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_updates'] = self.object.updates.all().order_by('-created_at')
        context['kobo_enabled'] = settings.KOBO_ENABLED
        if settings.KOBO_ENABLED:
            from apps.integrations.kobo.models import KoboAsset
            from apps.integrations.kobo.services import get_project_imported_submissions

            context['kobo_submissions'] = get_project_imported_submissions(
                self.object,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            )
        else:
            context['kobo_submissions'] = ()
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


class ProjectUpdateListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_list.html'
    context_object_name = 'objects'
    route_prefix = 'project_update'
    page_title = _('Avances de proyecto')

    def get_queryset(self):
        return ProjectUpdate.objects.select_related('project', 'created_by', 'reviewed_by')


class ProjectUpdateDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_detail.html'
    route_prefix = 'project_update'
    page_title = _('Avance de proyecto')


class ProjectUpdateEvidenceDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate

    def get(self, request, *args, **kwargs):
        """
        PRE: user is authenticated with view_projectupdate and pk identifies an
        update whose evidence exists in storage.
        POST: returns an attachment response without mutation or storage paths.
        """
        project_update = self.get_object()
        return _protected_file_response(
            project_update.evidence,
            missing_message=_('La evidencia del avance no está disponible.'),
        )


class ProjectUpdateCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Nuevo avance de proyecto')

    def form_valid(self, form):
        self.object = register_advance(
            project_id=form.cleaned_data['project'].pk,
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            evidence=form.cleaned_data.get('evidence'),
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectUpdateCreateForProjectView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForProjectForm
    template_name = 'web/project_update_form.html'
    route_prefix = 'project_update'
    page_title = _('Registrar avance')

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        context['cancel_url'] = reverse('project_detail', args=[self.project.pk])
        return context

    def form_valid(self, form):
        self.object = register_advance(
            project_id=self.project.pk,
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            evidence=form.cleaned_data.get('evidence'),
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('project_detail', args=[self.project.pk])


class ProjectUpdateUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Editar avance de proyecto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary editing and permission handling remains authoritative.
        # POST: permits DRAFT advances only; pending/final advances return 403.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            project_update = get_object_or_404(ProjectUpdate, pk=kwargs['pk'])
            try:
                ensure_project_update_is_editable(project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """
        PRE: form is valid and the route targets a DRAFT advance.
        POST: updates through the locked domain service or redisplays domain errors.
        """
        try:
            self.object = update_project_update(
                update_id=self.object.pk,
                project=form.cleaned_data['project'],
                title=form.cleaned_data['title'],
                description=form.cleaned_data['description'],
                evidence=form.cleaned_data.get('evidence'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Avance de proyecto actualizado.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectUpdateDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_projectupdate'
    model = ProjectUpdate
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Eliminar avance de proyecto')
    audit_summary = _('Avance de proyecto eliminado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary deletion and permission handling remains authoritative.
        # POST: blocks final advances on GET and POST without deleting or auditing them.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            project_update = get_object_or_404(ProjectUpdate, pk=kwargs['pk'])
            try:
                ensure_project_update_is_deletable(project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


class ProjectUpdateReviewView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_projectupdate'
    form_class = ProjectUpdateReviewForm
    template_name = 'web/project_update_review.html'

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(ProjectUpdate.objects.select_related('project', 'created_by'), pk=kwargs['pk'])
        if (
            request.user.is_authenticated
            and request.user.has_perm(self.permission_required)
            and self.object.status != ProjectUpdate.Status.PENDING_REVIEW
        ):
            raise PermissionDenied(_('Solo un avance pendiente de revisión puede revisarse.'))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['object'] = self.object
        return context

    def form_valid(self, form):
        """
        PRE: POST form is valid and user has change_projectupdate permission.
        POST: reviews only through the atomic service or redisplays domain errors.
        """
        try:
            review_project_update(
                update_id=self.object.pk,
                reviewer=self.request.user,
                status=form.cleaned_data['status'],
                notes=form.cleaned_data.get('review_notes', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Revisión de avance guardada.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('project_detail', args=[self.object.project.pk])


class DonationListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_list.html'
    context_object_name = 'objects'
    route_prefix = 'donation'
    page_title = _('Donaciones')

    def get_queryset(self):
        return Donation.objects.select_related('donor')


class DonationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_detail.html'
    route_prefix = 'donation'
    page_title = _('Donación')
    transition_map = DONATION_STATUS_TRANSITIONS
    transition_url_name = 'donation_status_transition'


class DonationCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Nueva donación')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Donación creada.')


class DonationUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Editar donación')
    audit_summary = _('Donación actualizada.')


class DonationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_donation'
    model = Donation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Eliminar donación')
    audit_summary = _('Donación eliminada.')


class DonationStatusTransitionView(StateTransitionView):
    permission_required = 'operations.change_donation'
    transition_service = staticmethod(transition_donation_status)
    detail_url_name = 'donation_detail'


class FundAllocationListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_list.html'
    context_object_name = 'objects'
    route_prefix = 'allocation'
    page_title = _('Asignaciones de fondos')

    def get_queryset(self):
        return FundAllocation.objects.select_related('donation__donor', 'project')


class FundAllocationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_detail.html'
    route_prefix = 'allocation'
    page_title = _('Asignación de fondos')
    transition_map = FUND_ALLOCATION_STATUS_TRANSITIONS
    transition_url_name = 'allocation_status_transition'


class FundAllocationCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_fundallocation'
    model = FundAllocation
    form_class = FundAllocationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Nueva asignación de fondos')
    audit_action = AuditLog.Action.ASSIGNED
    audit_summary = _('Asignación de fondos registrada.')

    def form_valid(self, form):
        try:
            self.object = create_fund_allocation(
                donation=form.cleaned_data['donation'],
                project=form.cleaned_data['project'],
                budget_category=form.cleaned_data['budget_category'],
                amount=form.cleaned_data['amount'],
                responsible_person=form.cleaned_data.get('responsible_person', ''),
                allocation_date=form.cleaned_data['allocation_date'],
                status=FundAllocation.Status.CREATED,
                notes=form.cleaned_data.get('notes', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class FundAllocationUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_fundallocation'
    model = FundAllocation
    form_class = FundAllocationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Editar asignación de fondos')
    audit_action = AuditLog.Action.ASSIGNED
    audit_summary = _('Asignación de fondos actualizada.')

    def form_valid(self, form):
        try:
            self.object = update_fund_allocation(
                allocation=self.object,
                donation=form.cleaned_data['donation'],
                project=form.cleaned_data['project'],
                budget_category=form.cleaned_data['budget_category'],
                amount=form.cleaned_data['amount'],
                responsible_person=form.cleaned_data.get('responsible_person', ''),
                allocation_date=form.cleaned_data['allocation_date'],
                status=self.object.status,
                notes=form.cleaned_data.get('notes', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class FundAllocationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_fundallocation'
    model = FundAllocation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Eliminar asignación de fondos')
    audit_summary = _('Asignación de fondos eliminada.')


class FundAllocationStatusTransitionView(StateTransitionView):
    permission_required = 'operations.change_fundallocation'
    transition_service = staticmethod(transition_fund_allocation_status)
    detail_url_name = 'allocation_detail'


class ExpenseListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_list.html'
    context_object_name = 'objects'
    route_prefix = 'expense'
    page_title = _('Gastos')

    def get_queryset(self):
        return Expense.objects.select_related(
            'allocation__donation__donor',
            'allocation__project',
        )


class ExpenseDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_detail.html'
    route_prefix = 'expense'
    page_title = _('Gasto')


class ExpenseCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_expense'
    model = Expense
    form_class = ExpenseForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Nuevo gasto')
    audit_action = AuditLog.Action.EXECUTED
    audit_summary = _('Gasto registrado.')

    def form_valid(self, form):
        try:
            self.object = create_expense(
                allocation=form.cleaned_data['allocation'],
                expense_date=form.cleaned_data['expense_date'],
                category=form.cleaned_data['category'],
                amount=form.cleaned_data['amount'],
                reason=form.cleaned_data['reason'],
                provider_or_recipient=form.cleaned_data['provider_or_recipient'],
                payment_method=form.cleaned_data['payment_method'],
                description=form.cleaned_data.get('description', ''),
                observations=form.cleaned_data.get('observations', ''),
                status=form.cleaned_data['status'],
                user=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_expense'
    model = Expense
    form_class = ExpenseForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Editar gasto')
    audit_action = AuditLog.Action.EXECUTED
    audit_summary = _('Gasto actualizado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary editing of an existing expense.
        # POST: permits editable expenses only; finalized expenses return 403.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        expense = get_object_or_404(Expense, pk=kwargs['pk'])
        try:
            ensure_expense_is_editable(expense)
        except ExpenseFinalizedError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            self.object = update_expense(
                expense=self.object,
                allocation=form.cleaned_data['allocation'],
                expense_date=form.cleaned_data['expense_date'],
                category=form.cleaned_data['category'],
                amount=form.cleaned_data['amount'],
                reason=form.cleaned_data['reason'],
                provider_or_recipient=form.cleaned_data['provider_or_recipient'],
                payment_method=form.cleaned_data['payment_method'],
                description=form.cleaned_data.get('description', ''),
                observations=form.cleaned_data.get('observations', ''),
                status=form.cleaned_data['status'],
                user=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_expense'
    model = Expense
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Eliminar gasto')
    audit_summary = _('Gasto eliminado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary deletion of an existing expense.
        # POST: permits deletable expenses only; finalized expenses return 403.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        expense = get_object_or_404(Expense, pk=kwargs['pk'])
        try:
            ensure_expense_is_deletable(expense)
        except ExpenseFinalizedError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


class ExpenseCancellationView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_expense'
    form_class = ExpenseCancellationForm
    template_name = 'web/object_form.html'

    def dispatch(self, request, *args, **kwargs):
        # PRE: request identifies an expense and user passed authentication/permission handling.
        # POST: loads the target without mutation; state changes remain POST-only.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        self.expense = get_object_or_404(Expense, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        # PRE: expense is loaded and form context may contain validation errors.
        # POST: returns cancellation-only UI context without financial/status inputs.
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'object': self.expense,
                'title': _('Anular gasto'),
                'list_url_name': 'expense_detail',
                'cancel_object_pk': self.expense.pk,
                'submit_label': _('Anular gasto'),
            }
        )
        return context

    def form_valid(self, form):
        # PRE: POST reason is valid and request user has change_expense.
        # POST: cancels through the atomic domain service and redirects to detail.
        try:
            cancel_expense(
                self.expense.pk,
                actor=self.request.user,
                reason=form.cleaned_data['reason'],
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Gasto anulado.'))
        return HttpResponseRedirect(
            reverse('expense_detail', args=[self.expense.pk])
        )


class SupportingDocumentCreateForExpenseView(OperationsPermissionRequiredMixin, CreateView):
    permission_required = 'operations.add_supportingdocument'
    model = SupportingDocument
    form_class = SupportingDocumentForm
    template_name = 'web/supporting_document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.expense = get_object_or_404(
            Expense.objects.select_related('allocation', 'allocation__project'),
            pk=kwargs['expense_pk'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expense'] = self.expense
        return context

    def form_valid(self, form):
        form.instance.expense = self.expense
        response = super().form_valid(form)
        log_action(
            self.request.user,
            AuditLog.Action.CREATED,
            self.object,
            _('Documento soporte adjuntado.'),
        )
        messages.success(self.request, _('Documento soporte adjuntado.'))
        return response

    def get_success_url(self):
        return reverse('expense_detail', args=[self.expense.pk])


class SupportingDocumentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_supportingdocument'
    model = SupportingDocument

    # PRE: the requester is authenticated and has permission to view supporting documents.
    # POST: streams the requested document as an attachment without exposing its storage path.
    def get(self, request, *args, **kwargs):
        document = self.get_object()
        if not document.document.name:
            raise Http404(_('El documento soporte no tiene un archivo asociado.'))
        try:
            file_handle = document.document.open('rb')
        except (FileNotFoundError, OSError) as exc:
            raise Http404(_('El archivo del documento soporte no está disponible.')) from exc
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=document.document.name.rsplit('/', 1)[-1],
        )


class SupportingDocumentDeleteView(OperationsPermissionRequiredMixin, DeleteView):
    permission_required = 'operations.delete_supportingdocument'
    model = SupportingDocument
    template_name = 'web/supporting_document_confirm_delete.html'

    def get_success_url(self):
        return reverse('expense_detail', args=[self.object.expense_id])

    # PRE: self.object identifies a support document the user is allowed to delete.
    # POST: deletes it atomically unless it is the last support of a validated expense.
    def form_valid(self, form):
        with transaction.atomic():
            expense = Expense.objects.select_for_update().get(pk=self.object.expense_id)
            document = SupportingDocument.objects.select_for_update().get(pk=self.object.pk)
            if expense.status == Expense.Status.VALIDATED and expense.supporting_documents.count() <= 1:
                messages.error(
                    self.request,
                    _('No se puede eliminar el último documento soporte de un gasto validado.'),
                )
                return HttpResponseRedirect(reverse('expense_detail', args=[expense.pk]))
            log_delete(self.request.user, document, _('Documento soporte eliminado.'))
            document.delete()
        messages.success(self.request, _('Documento soporte eliminado.'))
        return HttpResponseRedirect(reverse('expense_detail', args=[expense.pk]))


class AuditLogListView(OperationsPermissionRequiredMixin, ListView):
    permission_required = 'operations.view_auditlog'
    model = AuditLog
    template_name = 'web/audit_log_list.html'
    context_object_name = 'logs'
