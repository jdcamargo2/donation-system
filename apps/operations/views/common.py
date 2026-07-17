from dataclasses import dataclass

from django.contrib import messages

from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)

from django.contrib.auth.views import redirect_to_login

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)

from django.db import transaction

from django.db.models import Q

from django.db.models.deletion import ProtectedError

from django.shortcuts import get_object_or_404

from django.http import (
    FileResponse,
    Http404,
    HttpResponseRedirect,
)

from django.urls import reverse

from django.utils.text import get_valid_filename

from django.utils.dateparse import parse_date

from django.utils.translation import gettext_lazy as _

from django.views import View

from django.views.generic import FormView

from ..pagination import (
    ALLOWED_PAGE_SIZES,
    DEFAULT_PAGE_SIZE,
    build_pagination_page_numbers,
    parse_page_size,
)

from ..forms import (
    TerminalActionConfirmationForm,
    TerminalActionReasonForm,
)

from ..models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
    SupportingDocument,
)

from ..services import (
    get_allocation_financial_summary,
    get_donation_financial_summary,
    get_project_financial_summary,
    log_action,
    log_delete,
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


class PaginatedListMixin:
    """
    PRE: la vista es un ListView interno que pagina en base de datos.
    POST: expone page_size validado, querystring estable y números de página con elipsis.
    """
    paginate_by = DEFAULT_PAGE_SIZE

    def get_paginate_by(self, queryset):
        return parse_page_size(self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_size = self.get_paginate_by(self.object_list)
        context['page_size'] = page_size
        context['page_size_choices'] = ALLOWED_PAGE_SIZES
        query = self.request.GET.copy()
        query.pop('page', None)
        query['page_size'] = str(page_size)
        context['pagination_query'] = query.urlencode()
        page_obj = context.get('page_obj')
        if page_obj is not None:
            context['pagination_page_numbers'] = build_pagination_page_numbers(page_obj)
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
