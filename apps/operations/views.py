import csv
from dataclasses import dataclass
from decimal import Decimal

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.text import get_valid_filename
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from .forms import (
    DonationForm,
    ExpenseAnnulmentForm,
    ExpenseForm,
    FundAllocationForm,
    InstitutionForm,
    ProjectForm,
    ProjectDocumentForm,
    ProjectUpdateForProjectForm,
    ProjectUpdateAttachmentForm,
    ProjectUpdateForm,
    ProjectUpdateReviewForm,
    ProjectUpdateReviewDecisionForm,
    ProjectUpdateRemediationAttachmentForm,
    ProjectUpdateRemediationForm,
    ProjectUpdateRemediationResolveForm,
    SupportingDocumentForm,
    TerminalActionConfirmationForm,
    TerminalActionReasonForm,
)
from .models import (
    AuditLog, Donation, Expense, FundAllocation, Institution, Project,
    ProjectDocument, ProjectUpdate, ProjectUpdateAttachment, ProjectUpdateReview, ProjectUpdateReviewDecision,
    ProjectUpdateRemediation, ProjectUpdateRemediationAttachment, SupportingDocument,
)
from .selectors import (
    with_allocation_list_metrics,
    with_donation_list_metrics,
    with_expense_list_support,
    with_project_update_attachment_count,
)
from .services import (
    create_expense,
    create_fund_allocation,
    create_supporting_document,
    create_project_update_review,
    create_project_update_review_decision,
    create_project_update_remediation,
    update_project_update_remediation,
    add_project_update_remediation_attachment,
    delete_project_update_remediation_attachment,
    delete_supporting_document,
    submit_project_update_remediation,
    resolve_project_update_remediation,
    get_allocation_financial_summary,
    get_dashboard_metrics,
    get_donation_financial_summary,
    get_project_financial_summary,
    annul_expense,
    ensure_expense_is_deletable,
    ensure_expense_is_editable,
    ExpenseFinalizedError,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    ProjectUpdateImmutableError,
    ProjectUpdateReviewError,
    ProjectUpdateReviewDecisionError,
    ProjectUpdateRemediationError,
    SupportingDocumentError,
    OperationalEntityFinalizedError,
    allocation_has_effective_expenses,
    add_project_update_attachment,
    annul_donation,
    annul_fund_allocation,
    annul_project,
    ensure_operational_entity_is_editable,
    finish_project,
    log_action,
    log_create,
    log_delete,
    register_advance,
    publish_project_update,
    delete_project_update_attachment,
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
        context.update(get_dashboard_metrics(user=self.request.user))
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


def parse_optional_date(value):
    # PRE: value proviene de un parámetro GET opcional y no es confiable.
    # POST: retorna una fecha ISO válida o None sin propagar errores del navegador.
    try:
        return parse_date(value) if value else None
    except ValueError:
        return None


def apply_list_filters(
    queryset, params, *, text_fields, date_field, status_field='status',
    institution_field=None, project_field=None,
):
    """
    PRE: queryset pertenece a un listado interno y los nombres de campo son allowlists del servidor.
    POST: retorna el queryset filtrado por los parámetros GET simples presentes, sin mutación.
    """
    search = (params.get('q') or '').strip()
    if search:
        text_query = Q()
        for field in text_fields:
            text_query |= Q(**{f'{field}__icontains': search})
        queryset = queryset.filter(text_query)
    if status_field and params.get('status'):
        queryset = queryset.filter(**{status_field: params['status']})
    date_from = parse_optional_date(params.get('date_from'))
    date_to = parse_optional_date(params.get('date_to'))
    if date_field and date_from:
        queryset = queryset.filter(**{f'{date_field}__gte': date_from})
    if date_field and date_to:
        queryset = queryset.filter(**{f'{date_field}__lte': date_to})
    if institution_field and (params.get('institution') or '').isdigit():
        queryset = queryset.filter(**{institution_field: params['institution']})
    if project_field and (params.get('project') or '').isdigit():
        queryset = queryset.filter(**{project_field: params['project']})
    return queryset.distinct()


class FilteredListContextMixin:
    status_choices = ()
    institution_filter = False
    project_filter = False
    export_url_name = None

    def get_context_data(self, **kwargs):
        # PRE: la vista ya resolvió su queryset mediante parámetros GET.
        # POST: expone opciones y querystring para un formulario simple y exportación equivalente.
        context = super().get_context_data(**kwargs)
        context['filter_status_choices'] = self.status_choices
        context['filter_institutions'] = Institution.objects.order_by('name') if self.institution_filter else ()
        context['filter_projects'] = Project.objects.order_by('code') if self.project_filter else ()
        context['export_url_name'] = self.export_url_name
        context['active_filter_query'] = self.request.GET.urlencode()
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
            if target not in {
                getattr(self.object.Status, 'CLOSED', None),
                getattr(self.object.Status, 'FINISHED', None),
                self.object.Status.ANNULLED,
            }
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


class TerminalActionView(OperationsPermissionRequiredMixin, FormView):
    template_name = 'web/terminal_action_confirm.html'
    model = None
    action_service = None
    detail_url_name = ''
    action_title = ''
    consequence = ''
    submit_label = ''
    success_message = ''
    is_destructive = True
    requires_reason = True

    def dispatch(self, request, *args, **kwargs):
        # PRE: route identifies a supported entity and permission handling remains authoritative.
        # POST: loads the entity without mutation; only valid POST can execute the named service.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(self.model, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return TerminalActionReasonForm if self.requires_reason else TerminalActionConfirmationForm

    def get_context_data(self, **kwargs):
        # PRE: self.object was loaded and form context may include validation errors.
        # POST: provides explicit irreversible-action confirmation data without changing state.
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'object': self.object,
                'action_title': self.action_title,
                'consequence': self.consequence,
                'submit_label': self.submit_label,
                'is_destructive': self.is_destructive,
                'detail_url_name': self.detail_url_name,
            }
        )
        return context

    def form_valid(self, form):
        # PRE: POST confirmation is valid and the user has the model change permission.
        # POST: executes one named terminal service or redisplays a human domain error without success.
        service_kwargs = {'actor': self.request.user}
        if self.requires_reason:
            service_kwargs['reason'] = form.cleaned_data['reason']
        try:
            self.object = self.action_service(self.object.pk, **service_kwargs)
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse(self.detail_url_name, args=[self.object.pk])


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


class DonationAnnulView(TerminalActionView):
    permission_required = 'operations.change_donation'
    model = Donation
    action_service = staticmethod(annul_donation)
    detail_url_name = 'donation_detail'
    action_title = _('Anular donación')
    consequence = _('Solo puede anularse si no tiene fondos asignados. Esta acción es irreversible.')
    submit_label = _('Confirmar anulación')
    success_message = _('Donación anulada.')


class FundAllocationAnnulView(TerminalActionView):
    permission_required = 'operations.change_fundallocation'
    model = FundAllocation
    action_service = staticmethod(annul_fund_allocation)
    detail_url_name = 'allocation_detail'
    action_title = _('Anular asignación')
    consequence = _(
        'Al anular esta asignación, el monto volverá al saldo disponible de la donación. '
        'No puede anularse si ya existen gastos efectivos.'
    )
    submit_label = _('Confirmar anulación')
    success_message = _('Asignación anulada.')


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


class ProjectListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, RouteContextMixin, ListView):
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
        )


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


class ProjectUpdateListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_list.html'
    context_object_name = 'objects'
    route_prefix = 'project_update'
    page_title = _('Avances de proyecto')

    def get_queryset(self):
        return with_project_update_attachment_count(
            ProjectUpdate.objects.select_related('project', 'created_by', 'reported_by')
        )


class ProjectUpdateDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_detail.html'
    route_prefix = 'project_update'
    page_title = _('Avance de proyecto')

    def get_queryset(self):
        return ProjectUpdate.objects.select_related(
            'project', 'created_by', 'reported_by', 'committee_review__reviewed_by'
        )


class ProjectUpdateReviewCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.review_projectupdate'
    form_class = ProjectUpdateReviewForm
    template_name = 'web/project_update_review_form.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.project_update = get_object_or_404(ProjectUpdate, pk=kwargs['update_pk'])
            if self.project_update.status != ProjectUpdate.Status.PUBLISHED:
                raise PermissionDenied(_('Solo los avances publicados pueden recibir revisión documental.'))
            if ProjectUpdateReview.objects.filter(project_update_id=self.project_update.pk).exists():
                raise PermissionDenied(_('Este avance ya tiene una revisión documental registrada.'))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_update'] = self.project_update
        return context

    def form_valid(self, form):
        """
        PRE: form contains validated observations for a published advance without a review.
        POST: creates the review through the domain service and redirects to its detail.
        """
        try:
            review = create_project_update_review(
                update_id=self.project_update.pk,
                observations=form.cleaned_data['observations'],
                actor=self.request.user,
            )
        except ProjectUpdateReviewError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(self.request, _('Revisión documental del Comité registrada.'))
        return HttpResponseRedirect(reverse('project_update_review_detail', args=[review.pk]))


class ProjectUpdateReviewDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdatereview'
    model = ProjectUpdateReview
    template_name = 'web/project_update_review_detail.html'

    def get_queryset(self):
        return ProjectUpdateReview.objects.select_related(
            'project_update__project', 'reviewed_by', 'decision__decided_by'
        )


class ProjectUpdateReviewDecisionCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.decide_projectupdate'
    form_class = ProjectUpdateReviewDecisionForm
    template_name = 'web/project_update_review_decision_form.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.review = get_object_or_404(ProjectUpdateReview, pk=kwargs['review_pk'])
            if self.review.project_update.status != ProjectUpdate.Status.PUBLISHED:
                raise PermissionDenied(_('La revisión debe pertenecer a un avance publicado.'))
            if ProjectUpdateReviewDecision.objects.filter(review_id=self.review.pk).exists():
                raise PermissionDenied(_('Esta revisión ya tiene un resultado institucional registrado.'))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['review'] = self.review
        return context

    def form_valid(self, form):
        """
        PRE: form contains a valid outcome and rationale for a review without a decision.
        POST: creates the decision through the domain service and redirects to the review detail.
        """
        try:
            create_project_update_review_decision(
                review_id=self.review.pk,
                outcome=form.cleaned_data['outcome'],
                rationale=form.cleaned_data['rationale'],
                actor=self.request.user,
            )
        except ProjectUpdateReviewDecisionError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(self.request, _('Resultado de revisión del Comité registrado.'))
        return HttpResponseRedirect(reverse('project_update_review_detail', args=[self.review.pk]))


class ProjectUpdateReviewDecisionDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdatereviewdecision'
    model = ProjectUpdateReviewDecision
    template_name = 'web/project_update_review_decision_detail.html'

    def get_queryset(self):
        return ProjectUpdateReviewDecision.objects.select_related(
            'review__project_update__project', 'decided_by'
        )


class ProjectUpdateRemediationCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.submit_projectupdateremediation'
    form_class = ProjectUpdateRemediationForm
    template_name = 'web/project_update_remediation_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.decision = get_object_or_404(ProjectUpdateReviewDecision, pk=kwargs['decision_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            remediation = create_project_update_remediation(
                decision_id=self.decision.pk, response=form.cleaned_data['response'], actor=self.request.user
            )
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation.pk]))


class ProjectUpdateRemediationDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdateremediation'
    model = ProjectUpdateRemediation
    template_name = 'web/project_update_remediation_detail.html'

    def get_queryset(self):
        return ProjectUpdateRemediation.objects.select_related('decision__review__project_update', 'created_by', 'submitted_by', 'resolved_by').prefetch_related('attachments')


class ProjectUpdateRemediationUpdateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_projectupdateremediation'
    form_class = ProjectUpdateRemediationForm
    template_name = 'web/project_update_remediation_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.remediation = get_object_or_404(ProjectUpdateRemediation, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'response': self.remediation.response}

    def form_valid(self, form):
        try:
            update_project_update_remediation(remediation_id=self.remediation.pk, response=form.cleaned_data['response'], actor=self.request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationSubmitView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.submit_projectupdateremediation'

    def post(self, request, *args, **kwargs):
        try:
            remediation = submit_project_update_remediation(remediation_id=kwargs['pk'], actor=request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation.pk]))


class ProjectUpdateRemediationResolveView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.resolve_projectupdateremediation'
    form_class = ProjectUpdateRemediationResolveForm
    template_name = 'web/project_update_remediation_resolve_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.remediation = get_object_or_404(ProjectUpdateRemediation, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            resolve_project_update_remediation(remediation_id=self.remediation.pk, actor=self.request.user, **form.cleaned_data)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationAttachmentCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.add_projectupdateremediationattachment'
    form_class = ProjectUpdateRemediationAttachmentForm
    template_name = 'web/project_update_remediation_attachment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.remediation = get_object_or_404(ProjectUpdateRemediation, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            add_project_update_remediation_attachment(remediation_id=self.remediation.pk, actor=self.request.user, **form.cleaned_data)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationAttachmentDeleteView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.delete_projectupdateremediationattachment'

    def post(self, request, *args, **kwargs):
        try:
            remediation_id = delete_project_update_remediation_attachment(attachment_id=kwargs['pk'], actor=request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation_id]))


class ProjectUpdateRemediationAttachmentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdateremediationattachment'
    model = ProjectUpdateRemediationAttachment

    def get(self, request, *args, **kwargs):
        return _protected_file_response(self.get_object().file, missing_message=_('El adjunto de remediación no está disponible.'))


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
            update_date=form.cleaned_data['update_date'],
            progress_percentage=form.cleaned_data['progress_percentage'],
            attachments=form.cleaned_data.get('attachments', ()),
            created_by=self.request.user if self.request.user.is_authenticated else None,
            reported_by=form.cleaned_data['reported_by'],
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
            update_date=form.cleaned_data['update_date'],
            progress_percentage=form.cleaned_data['progress_percentage'],
            attachments=form.cleaned_data.get('attachments', ()),
            created_by=self.request.user if self.request.user.is_authenticated else None,
            reported_by=form.cleaned_data['reported_by'],
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
        # POST: permits DRAFT advances only; published advances return 403.
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
                update_date=form.cleaned_data['update_date'],
                progress_percentage=form.cleaned_data['progress_percentage'],
                reported_by=form.cleaned_data['reported_by'],
                actor=self.request.user,
                attachments=form.cleaned_data.get('attachments', ()),
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


class ProjectUpdatePublishView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.publish_projectupdate'

    def post(self, request, *args, **kwargs):
        """
        PRE: el usuario tiene permiso funcional de publicación y pk identifica un avance.
        POST: publica mediante el servicio de dominio o responde 403 sin mutar.
        """
        try:
            project_update = publish_project_update(kwargs['pk'], request.user)
        except ValidationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(request, _('Avance de proyecto publicado.'))
        return HttpResponseRedirect(reverse('project_update_detail', args=[project_update.pk]))


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


class ProjectUpdateAttachmentCreateView(OperationsPermissionRequiredMixin, CreateView):
    permission_required = 'operations.add_projectupdateattachment'
    model = ProjectUpdateAttachment
    form_class = ProjectUpdateAttachmentForm
    template_name = 'web/project_update_attachment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project_update = get_object_or_404(ProjectUpdate, pk=kwargs['update_pk'])
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            try:
                ensure_project_update_is_editable(self.project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            add_project_update_attachment(
                update_id=self.project_update.pk,
                file=form.cleaned_data['file'],
                title=form.cleaned_data.get('title', ''),
                actor=self.request.user,
            )
        except ValidationError as exc:
            add_service_errors_to_form(form, exc)
            return self.form_invalid(form)
        return HttpResponseRedirect(reverse('project_update_detail', args=[self.project_update.pk]))


class ProjectUpdateAttachmentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdateattachment'
    model = ProjectUpdateAttachment

    def get(self, request, *args, **kwargs):
        # PRE: el usuario tiene permiso de lectura y pk identifica un adjunto.
        # POST: descarga el archivo sin revelar su ruta de almacenamiento.
        return _protected_file_response(
            self.get_object().file,
            missing_message=_('El adjunto del avance no está disponible.'),
        )


class ProjectUpdateAttachmentDeleteView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.delete_projectupdateattachment'

    def post(self, request, *args, **kwargs):
        # PRE: el usuario tiene permiso y pk identifica un adjunto.
        # POST: elimina mediante el servicio solo si el avance padre es DRAFT.
        try:
            update_id = delete_project_update_attachment(
                attachment_id=kwargs['pk'], actor=request.user
            )
        except ValidationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_detail', args=[update_id]))


class DonationListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_list.html'
    context_object_name = 'objects'
    route_prefix = 'donation'
    page_title = _('Donaciones')
    status_choices = Donation.Status.choices
    institution_filter = True
    export_url_name = 'donation_export_csv'

    def get_queryset(self):
        return apply_list_filters(
            with_donation_list_metrics(Donation.objects.select_related('donor')),
            self.request.GET,
            text_fields=('code', 'donor__name'), date_field='received_date',
            institution_field='donor_id',
        )


class DonationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_detail.html'
    route_prefix = 'donation'
    page_title = _('Donación')
    transition_map = DONATION_STATUS_TRANSITIONS
    transition_url_name = 'donation_status_transition'

    def get_queryset(self):
        # PRE: la vista consulta una donación autorizada por clave primaria.
        # POST: carga donante, metadata terminal y asignaciones relacionadas con sus destinos.
        return Donation.objects.select_related('donor', 'terminal_by').prefetch_related(
            Prefetch(
                'allocations',
                queryset=FundAllocation.objects.select_related('project').prefetch_related('expenses'),
                to_attr='detail_allocations',
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed_targets = DONATION_STATUS_TRANSITIONS.get(self.object.status, ())
        context['can_annul'] = (
            Donation.Status.ANNULLED in allowed_targets
            and not self.object.allocations.exclude(status=FundAllocation.Status.ANNULLED).exists()
        )
        context['donation_financial_summary'] = get_donation_financial_summary(self.object)
        context['related_allocations'] = self.object.detail_allocations
        return context


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

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary donation editing and permission handling remains authoritative.
        # POST: terminal donations return 403 without form mutation.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            donation = get_object_or_404(Donation, pk=kwargs['pk'])
            try:
                ensure_operational_entity_is_editable(donation)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


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


class FundAllocationListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_list.html'
    context_object_name = 'objects'
    route_prefix = 'allocation'
    page_title = _('Asignaciones de fondos')
    status_choices = FundAllocation.Status.choices
    institution_filter = True
    project_filter = True
    export_url_name = 'allocation_export_csv'

    def get_queryset(self):
        return apply_list_filters(
            with_allocation_list_metrics(
                FundAllocation.objects.select_related('donation__donor', 'project')
            ),
            self.request.GET,
            text_fields=('code', 'donation__code', 'project__code', 'project__name'),
            date_field='allocation_date', institution_field='donation__donor_id',
            project_field='project_id',
        )


class FundAllocationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_detail.html'
    route_prefix = 'allocation'
    page_title = _('Asignación de fondos')
    transition_map = FUND_ALLOCATION_STATUS_TRANSITIONS
    transition_url_name = 'allocation_status_transition'

    def get_queryset(self):
        # PRE: la vista consulta una asignación autorizada por clave primaria.
        # POST: carga origen, destino, metadata terminal y gastos para render sin N+1.
        return FundAllocation.objects.select_related(
            'donation__donor', 'project', 'terminal_by'
        ).prefetch_related('expenses')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed_targets = FUND_ALLOCATION_STATUS_TRANSITIONS.get(self.object.status, ())
        context['can_annul'] = (
            FundAllocation.Status.ANNULLED in allowed_targets
            and not allocation_has_effective_expenses(self.object)
        )
        expenses = list(self.object.expenses.all())
        context['allocation_financial_summary'] = get_allocation_financial_summary(self.object)
        context['registered_expenses'] = [
            expense for expense in expenses if expense.status != Expense.Status.ANNULLED
        ]
        context['annulled_expenses'] = [
            expense for expense in expenses if expense.status == Expense.Status.ANNULLED
        ]
        return context


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
                status=FundAllocation.Status.ACTIVE,
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

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary allocation editing and permission handling remains authoritative.
        # POST: terminal allocations return 403 without form or service mutation.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            allocation = get_object_or_404(FundAllocation, pk=kwargs['pk'])
            try:
                ensure_operational_entity_is_editable(allocation)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

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


class ExpenseListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_list.html'
    context_object_name = 'objects'
    route_prefix = 'expense'
    page_title = _('Gastos')
    status_choices = Expense.Status.choices
    institution_filter = True
    project_filter = True
    export_url_name = 'expense_export_csv'

    def get_queryset(self):
        queryset = with_expense_list_support(
            Expense.objects.select_related(
                'allocation__donation__donor',
                'allocation__project',
            )
        )
        return apply_list_filters(
            queryset, self.request.GET,
            text_fields=('code', 'reason', 'provider_or_recipient', 'allocation__project__code'),
            date_field='expense_date', institution_field='allocation__donation__donor_id',
            project_field='allocation__project_id',
        )


class ExpenseDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_detail.html'
    route_prefix = 'expense'
    page_title = _('Gasto')


class ExpenseCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
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
                actor=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
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
                actor=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
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


class ExpenseAnnulView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_expense'
    form_class = ExpenseAnnulmentForm
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
            annul_expense(
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
        # PRE: the form is valid and the request user may add supporting documents.
        # POST: delegates persistence and audit to the service, preserving the existing redirect.
        try:
            self.object = create_supporting_document(
                expense_id=self.expense.pk,
                title=form.cleaned_data['title'],
                file=form.cleaned_data['document'],
                notes=form.cleaned_data['notes'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        form.instance = self.object
        messages.success(self.request, _('Documento soporte adjuntado.'))
        return HttpResponseRedirect(self.get_success_url())

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
    # POST: delegates the locked mutation and translates its domain outcome to the existing messages.
    def form_valid(self, form):
        try:
            expense_id = delete_supporting_document(
                document_id=self.object.pk,
                actor=self.request.user,
            )
        except SupportingDocumentError as error:
            messages.error(self.request, error.messages[0])
            return HttpResponseRedirect(
                reverse('expense_detail', args=[self.object.expense_id])
            )
        messages.success(self.request, _('Documento soporte eliminado.'))
        return HttpResponseRedirect(reverse('expense_detail', args=[expense_id]))


class AuditLogListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, ListView):
    permission_required = 'operations.view_auditlog'
    model = AuditLog
    template_name = 'web/audit_log_list.html'
    context_object_name = 'logs'
    status_choices = AuditLog.Action.choices

    def get_queryset(self):
        return apply_list_filters(
            AuditLog.objects.select_related('user'), self.request.GET,
            text_fields=('entity_id', 'entity_label', 'model_name', 'summary'),
            date_field='created_at__date', status_field='action',
        )


class FilteredCsvExportView(OperationsPermissionRequiredMixin, View):
    list_view_class = None
    filename = 'export.csv'
    headers = ()
    row_builder = None

    def get(self, request, *args, **kwargs):
        """
        PRE: el usuario tiene permiso de lectura y la configuración declara columnas seguras.
        POST: descarga CSV con encabezados legibles y el mismo queryset filtrado del listado.
        """
        list_view = self.list_view_class()
        list_view.request = request
        queryset = list_view.get_queryset()
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{self.filename}"'
        writer = csv.writer(response)
        writer.writerow(self.headers)
        for item in queryset:
            writer.writerow(self.row_builder(item))
        return response


class ProjectCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_project'
    list_view_class = ProjectListView
    filename = 'proyectos.csv'
    headers = ('Código', 'Nombre', 'Estado', 'Presupuesto USD', 'Inicio', 'Cierre', 'Ubicación')
    row_builder = staticmethod(lambda item: (
        item.code, item.name, item.get_status_display(), str(item.estimated_budget),
        item.start_date or '', item.end_date or '', item.location,
    ))


class DonationCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_donation'
    list_view_class = DonationListView
    filename = 'donaciones.csv'
    headers = ('Código', 'Institución donante', 'Monto', 'Moneda', 'Estado', 'Compromiso', 'Recepción')
    row_builder = staticmethod(lambda item: (
        item.code, item.donor.name, str(item.amount), item.currency,
        item.get_status_display(), item.commitment_date or '', item.received_date or '',
    ))


class FundAllocationCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_fundallocation'
    list_view_class = FundAllocationListView
    filename = 'asignaciones.csv'
    headers = ('Código', 'Donación', 'Proyecto', 'Monto USD', 'Estado', 'Fecha', 'Categoría')
    row_builder = staticmethod(lambda item: (
        item.code, item.donation.code, item.project.code, str(item.amount),
        item.get_status_display(), item.allocation_date, item.get_budget_category_display(),
    ))


class ExpenseCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_expense'
    list_view_class = ExpenseListView
    filename = 'gastos.csv'
    headers = ('Código', 'Proyecto', 'Asignación', 'Motivo', 'Monto', 'Moneda', 'Estado', 'Fecha')
    row_builder = staticmethod(lambda item: (
        item.code, item.allocation.project.code, item.allocation.code, item.reason,
        str(item.amount), item.currency, item.get_status_display(), item.expense_date,
    ))
