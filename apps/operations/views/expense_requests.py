from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from ..choices import BUDGET_CATEGORY_CHOICES
from ..expense_request_services import (
    add_expense_request_attachments,
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    delete_expense_request_attachment,
    deny_expense_request,
    fulfill_expense_request,
    update_expense_request,
    withdraw_expense_request,
)
from ..file_access import build_protected_file_actions
from ..forms import (
    ExpenseRequestApproveForm,
    ExpenseRequestAttachmentForm,
    ExpenseRequestForProjectForm,
    ExpenseRequestForm,
    ExpenseRequestFulfillmentForm,
)
from ..models import ExpenseRequest, Project, ZERO_MONEY
from ..selectors import (
    annullable_expense_requests_for_user,
    attachment_display_filename,
    decidable_pending_expense_requests_for_user,
    expense_request_allocation_choices,
    fulfillable_expense_requests_for_user,
    get_expense_request_financial_display,
    mutable_own_pending_expense_requests_for_attachments,
    mutable_own_pending_expense_requests_for_user,
    user_can_create_global_expense_request,
    user_has_global_expense_request_visibility,
    user_may_use_global_expense_request_allocations,
    visible_expense_requests_for_user,
    with_expense_request_detail_data,
    with_expense_request_list_data,
)
from .common import (
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
    TerminalActionView,
    add_service_errors_to_form,
    apply_list_filters,
)


EXPENSE_REQUEST_DECISION_FAILURE_MESSAGE = _(
    'No se pudo completar la acción. Intente nuevamente.'
)


EXPENSE_REQUEST_LIST_FILTER_KEYS = (
    'q',
    'status',
    'date_from',
    'date_to',
    'project',
    'requester',
    'category',
)


def _user_display_name(user):
    if user is None:
        return ''
    full_name = (user.get_full_name() or '').strip()
    return full_name or user.get_username()


def _status_label(status_value):
    if not status_value:
        return ''
    return dict(ExpenseRequest.Status.choices).get(status_value, status_value)


def _requester_action_flags(*, user, expense_request):
    """
    PRE: expense_request is visible to user; permissions are effective, not role names.
    POST: returns edit/withdraw/attachment flags for PENDING_DECISION rows owned by the actor.
    """
    is_owner = expense_request.requested_by_id == user.id
    is_pending = expense_request.status == ExpenseRequest.Status.PENDING_DECISION
    return {
        'can_edit_expense_request': (
            is_owner
            and is_pending
            and user.has_perm('operations.change_expenserequest')
        ),
        'can_withdraw_expense_request': (
            is_owner
            and is_pending
            and user.has_perm('operations.withdraw_expenserequest')
        ),
        'can_add_expense_request_attachment': (
            is_owner
            and is_pending
            and user.has_perm('operations.add_expenserequestattachment')
        ),
        'can_delete_expense_request_attachments': (
            is_owner
            and is_pending
            and user.has_perm('operations.delete_expenserequestattachment')
        ),
    }


def _decision_action_flags(*, user, expense_request):
    """
    PRE: expense_request is visible to user; permissions are effective, not role names.
    POST: returns approve/deny flags only for PENDING_DECISION with decide_expenserequest.
    """
    can_decide = (
        expense_request.status == ExpenseRequest.Status.PENDING_DECISION
        and user.has_perm('operations.decide_expenserequest')
    )
    return {
        'can_approve_expense_request': can_decide,
        'can_deny_expense_request': can_decide,
    }


def _annul_action_flags(*, user, expense_request):
    """
    PRE: expense_request is visible to user; permissions are effective, not role names.
    POST: returns annul flag for PENDING_DECISION or APPROVED_RESERVED with annul_expenserequest.
    """
    can_annul = (
        expense_request.status
        in {
            ExpenseRequest.Status.PENDING_DECISION,
            ExpenseRequest.Status.APPROVED_RESERVED,
        }
        and user.has_perm('operations.annul_expenserequest')
    )
    return {
        'can_annul_expense_request': can_annul,
    }


def _fulfill_action_flags(*, user, expense_request):
    """
    PRE: expense_request is visible to user; permissions are effective, not role names.
    POST: returns fulfill flag for APPROVED_RESERVED rows without a linked Expense.
    """
    can_fulfill = (
        expense_request.status == ExpenseRequest.Status.APPROVED_RESERVED
        and expense_request.expense_id is None
        and user.has_perm('operations.fulfill_expenserequest')
    )
    return {
        'can_fulfill_expense_request': can_fulfill,
    }


def _approval_preview_context(expense_request):
    """
    PRE: expense_request has fund_allocation loaded for display.
    POST: returns display-only approval amounts; service remains authoritative on POST.
    """
    requested = expense_request.requested_amount
    available = expense_request.fund_allocation.available_balance
    return {
        'approval_requested_amount': requested,
        'approval_available_balance': available,
        'approval_balance_after': available - requested,
        'approval_balance_insufficient': available < requested,
    }


class ExpenseRequestListView(
    OperationsPermissionRequiredMixin,
    FilteredListContextMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_expenserequest'
    model = ExpenseRequest
    template_name = 'web/expense_request_list.html'
    context_object_name = 'expense_requests'
    route_prefix = 'expense_request'
    page_title = _('Solicitudes de gasto')
    status_choices = ExpenseRequest.Status.choices
    institution_filter = False
    project_filter = False
    export_url_name = None

    def get(self, request, *args, **kwargs):
        # PRE: committee-capable users may land without an explicit status filter.
        # POST: one controlled redirect applies pending_decision default without looping.
        if self._should_default_pending_decision(request):
            params = request.GET.copy()
            params['status'] = ExpenseRequest.Status.PENDING_DECISION
            query = params.urlencode()
            target = request.path if not query else f'{request.path}?{query}'
            return HttpResponseRedirect(target)
        return super().get(request, *args, **kwargs)

    def _should_default_pending_decision(self, request):
        user = request.user
        if not user.has_perm('operations.decide_expenserequest'):
            return False
        if 'status' in request.GET:
            return False
        return not any(
            (request.GET.get(key) or '').strip()
            for key in EXPENSE_REQUEST_LIST_FILTER_KEYS
            if key != 'status'
        )

    def _has_global_visibility(self):
        return user_has_global_expense_request_visibility(self.request.user)

    def get_queryset(self):
        queryset = with_expense_request_list_data(
            visible_expense_requests_for_user(self.request.user)
        )
        text_fields = (
            'code',
            'purpose',
            'fund_allocation__project__code',
            'fund_allocation__project__name',
            'expense__code',
        )
        queryset = apply_list_filters(
            queryset,
            self.request.GET,
            text_fields=text_fields,
            date_field='requested_date',
            project_field='fund_allocation__project_id',
        )
        queryset = self._apply_extra_filters(queryset)
        return self._order_queryset(queryset)

    def _apply_extra_filters(self, queryset):
        params = self.request.GET
        if self._has_global_visibility():
            requester = (params.get('requester') or '').strip()
            if requester.isdigit():
                queryset = queryset.filter(requested_by_id=int(requester))
            category = (params.get('category') or '').strip()
            if category:
                queryset = queryset.filter(fund_allocation__budget_category=category)
        return queryset

    def _order_queryset(self, queryset):
        # Pending-first only for committee-capable users on the default pending view.
        if (
            self.request.user.has_perm('operations.decide_expenserequest')
            and self.request.GET.get('status') == ExpenseRequest.Status.PENDING_DECISION
        ):
            return queryset.annotate(
                _pending_rank=Case(
                    When(
                        status=ExpenseRequest.Status.PENDING_DECISION,
                        then=Value(0),
                    ),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by('_pending_rank', '-requested_date', '-created_at', '-pk')
        return queryset.order_by('-requested_date', '-created_at', '-pk')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        has_global = self._has_global_visibility()
        context['has_global_expense_request_visibility'] = has_global
        context['can_create_global_expense_request'] = user_can_create_global_expense_request(
            user
        )
        context['can_create_expense_request'] = user.has_perm(
            'operations.add_expenserequest'
        )
        context['can_decide_expense_request'] = user.has_perm(
            'operations.decide_expenserequest'
        )
        context['filters_active'] = any(
            (self.request.GET.get(key) or '').strip()
            for key in EXPENSE_REQUEST_LIST_FILTER_KEYS
        )
        visible_base = visible_expense_requests_for_user(user)
        if has_global:
            project_ids = (
                visible_base.values_list('fund_allocation__project_id', flat=True)
                .distinct()
            )
            context['filter_projects'] = Project.objects.filter(
                pk__in=project_ids
            ).order_by('code')
            requester_ids = visible_base.values_list('requested_by_id', flat=True).distinct()
            context['filter_requesters'] = (
                get_user_model()
                .objects.filter(pk__in=requester_ids)
                .order_by('first_name', 'last_name', 'username')
            )
            context['filter_categories'] = BUDGET_CATEGORY_CHOICES
            self.project_filter = True
        else:
            context['filter_projects'] = ()
            context['filter_requesters'] = ()
            context['filter_categories'] = ()
        return context


class ExpenseRequestDetailView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    DetailView,
):
    permission_required = 'operations.view_expenserequest'
    model = ExpenseRequest
    template_name = 'web/expense_request_detail.html'
    context_object_name = 'expense_request'
    route_prefix = 'expense_request'
    page_title = _('Solicitud de gasto')

    def get_queryset(self):
        # PRE: detail must refuse unrelated Operator-owned rows with 404.
        # POST: returns visibility-scoped queryset with detail relations loaded.
        return with_expense_request_detail_data(
            visible_expense_requests_for_user(self.request.user)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        expense_request = self.object
        user = self.request.user
        financial = get_expense_request_financial_display(expense_request)
        context['financial_summary'] = financial
        context['requested_by_display'] = _user_display_name(expense_request.requested_by)
        context['decided_by_display'] = _user_display_name(expense_request.decided_by)
        context['terminal_by_display'] = _user_display_name(expense_request.terminal_by)
        context['can_view_project'] = user.has_perm('operations.view_project')
        context['can_view_fundallocation'] = user.has_perm('operations.view_fundallocation')
        context['can_view_expense'] = user.has_perm('operations.view_expense')
        context['can_view_attachments'] = user.has_perm(
            'operations.view_expenserequestattachment'
        )
        context['can_view_events'] = user.has_perm('operations.view_expenserequestevent')
        context.update(
            _requester_action_flags(user=user, expense_request=expense_request)
        )
        context.update(
            _decision_action_flags(user=user, expense_request=expense_request)
        )
        context.update(
            _annul_action_flags(user=user, expense_request=expense_request)
        )
        context.update(
            _fulfill_action_flags(user=user, expense_request=expense_request)
        )
        if context['can_approve_expense_request']:
            context.update(_approval_preview_context(expense_request))

        attachments_frozen = (
            expense_request.status != ExpenseRequest.Status.PENDING_DECISION
        )
        context['attachments_frozen'] = attachments_frozen

        attachment_items = []
        if context['can_view_attachments']:
            can_download = (
                user.has_perm('operations.view_expenserequest')
                and user.has_perm('operations.view_expenserequestattachment')
            )
            can_delete = context['can_delete_expense_request_attachments']
            for attachment in expense_request.attachments.all():
                delete_url = None
                if can_delete:
                    delete_url = reverse(
                        'expense_request_attachment_delete',
                        args=[expense_request.pk, attachment.pk],
                    )
                file_actions = build_protected_file_actions(
                    file_field=attachment.file,
                    file_label=attachment.title or str(attachment),
                    uploaded_at=attachment.uploaded_at,
                    can_download=can_download,
                    preview_url_name='expense_request_attachment_preview',
                    download_url_name='expense_request_attachment_download',
                    url_args=(expense_request.pk, attachment.pk),
                    delete_url=delete_url,
                    can_delete=can_delete,
                )
                attachment_items.append(
                    {
                        'object': attachment,
                        'title': attachment.title,
                        'notes': attachment.notes,
                        'uploaded_by_display': _user_display_name(attachment.uploaded_by),
                        'uploaded_at': attachment.uploaded_at,
                        'filename': attachment_display_filename(attachment),
                        'file_actions': file_actions,
                        'preview_url': file_actions.preview_url,
                        'download_url': file_actions.download_url,
                        'can_delete': file_actions.can_delete,
                    }
                )
        context['detail_attachments'] = attachment_items
        context['attachment_items'] = attachment_items

        events = []
        if context['can_view_events']:
            for event in expense_request.events.all():
                events.append(self._event_display(event, can_view_expense=context['can_view_expense']))
        context['detail_events'] = events

        context['has_decision_metadata'] = (
            expense_request.decided_by_id is not None
            or expense_request.decided_at is not None
            or bool((expense_request.decision_note or '').strip())
        )
        context['show_terminal_card'] = expense_request.status in {
            ExpenseRequest.Status.WITHDRAWN,
            ExpenseRequest.Status.ANNULLED,
        }
        return context

    def _event_display(self, event, *, can_view_expense):
        financial_rows = []
        optional_amounts = (
            (_('Reservado'), event.reserved_amount),
            (_('Ejecutado'), event.executed_amount),
            (_('Liberado'), event.released_amount),
        )
        financial_rows.append(
            {'label': _('Solicitado'), 'amount': event.requested_amount}
        )
        for label, amount in optional_amounts:
            if amount is None or amount == ZERO_MONEY:
                continue
            financial_rows.append({'label': label, 'amount': amount})
        for label, amount in (
            (_('Saldo asignación antes'), event.allocation_balance_before),
            (_('Saldo asignación después'), event.allocation_balance_after),
        ):
            if amount is None:
                continue
            financial_rows.append({'label': label, 'amount': amount})

        linked_expense_code = ''
        linked_expense_pk = None
        if event.expense_id and can_view_expense:
            linked_expense_code = event.expense.code
            linked_expense_pk = event.expense_id

        return {
            'created_at': event.created_at,
            'actor_display': _user_display_name(event.actor) or _('Sistema'),
            'event_label': event.get_event_type_display(),
            'from_status_label': _status_label(event.from_status),
            'to_status_label': _status_label(event.to_status),
            'reason': (event.reason or '').strip(),
            'linked_expense_code': linked_expense_code,
            'linked_expense_pk': linked_expense_pk,
            'financial_rows': financial_rows,
        }


class ExpenseRequestAttachmentCreateView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.add_expenserequestattachment'
    form_class = ExpenseRequestAttachmentForm
    template_name = 'web/expense_request_attachment_form.html'
    route_prefix = 'expense_request'
    page_title = _('Agregar adjunto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request_pk targets an own PENDING_DECISION row under visibility rules.
        # POST: loads the mutable parent or 404 (stale/foreign/terminal stay hidden).
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.expense_request = get_object_or_404(
                mutable_own_pending_expense_requests_for_attachments(
                    request.user
                ).select_related(
                    'fund_allocation',
                    'fund_allocation__project',
                ),
                pk=kwargs['request_pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expense_request'] = self.expense_request
        context['title'] = self.page_title
        return context

    def form_valid(self, form):
        # PRE: parent is URL-scoped own pending; files/title/notes passed form validation.
        # POST: creates attachments via service (uploader=session user) or 404 on stale parent.
        self.expense_request = get_object_or_404(
            mutable_own_pending_expense_requests_for_attachments(self.request.user),
            pk=self.expense_request.pk,
        )
        files = form.cleaned_data['files']
        try:
            created = add_expense_request_attachments(
                expense_request_id=self.expense_request.pk,
                files=files,
                title=form.cleaned_data['title'],
                notes=form.cleaned_data.get('notes', ''),
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        if len(created) == 1:
            messages.success(self.request, _('Adjunto agregado a la solicitud.'))
        else:
            messages.success(self.request, _('Adjuntos agregados a la solicitud.'))
        return HttpResponseRedirect(
            reverse('expense_request_detail', args=[self.expense_request.pk])
        )


class ExpenseRequestAttachmentDeleteView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.delete_expenserequestattachment'

    def post(self, request, *args, **kwargs):
        # PRE: request_pk/pk identify an attachment on an own PENDING_DECISION request.
        # POST: deletes via service or 404 when parent/attachment scope fails.
        expense_request = get_object_or_404(
            mutable_own_pending_expense_requests_for_attachments(request.user),
            pk=kwargs['request_pk'],
        )
        get_object_or_404(
            expense_request.attachments.all(),
            pk=kwargs['pk'],
        )
        try:
            delete_expense_request_attachment(
                expense_request_id=expense_request.pk,
                attachment_id=kwargs['pk'],
                actor=request.user,
            )
        except ValidationError as error:
            raise PermissionDenied(error.messages[0]) from error
        messages.success(request, _('Adjunto eliminado de la solicitud.'))
        return HttpResponseRedirect(
            reverse('expense_request_detail', args=[expense_request.pk])
        )

    def get(self, request, *args, **kwargs):
        # PRE: delete is POST-only.
        # POST: refuses GET mutation with 405.
        return HttpResponseNotAllowed(['POST'])

class ExpenseRequestCreateForProjectView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.add_expenserequest'
    form_class = ExpenseRequestForProjectForm
    template_name = 'web/expense_request_form.html'
    route_prefix = 'expense_request'
    page_title = _('Solicitar gasto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: project_pk identifies a project the actor may view.
        # POST: loads an ACTIVE project or 404; mutations remain service-owned.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.project = get_object_or_404(
                Project.objects.filter(status=Project.Status.ACTIVE),
                pk=kwargs['project_pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['project'] = self.project
        return kwargs

    def get_initial(self):
        # PRE: optional ?allocation=<pk> is advisory only.
        # POST: preselects only when the pk is inside the authoritative eligible queryset.
        initial = super().get_initial()
        raw_allocation = (self.request.GET.get('allocation') or '').strip()
        if not raw_allocation.isdigit():
            return initial
        eligible = expense_request_allocation_choices(project=self.project)
        selected = eligible.filter(pk=int(raw_allocation)).first()
        if selected is not None:
            initial['fund_allocation'] = selected.pk
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context['form']
        context['project'] = self.project
        context['cancel_url'] = reverse('project_detail', args=[self.project.pk])
        context['submit_label'] = _('Registrar solicitud')
        context['has_eligible_allocations'] = form.fields[
            'fund_allocation'
        ].queryset.exists()
        return context

    def form_valid(self, form):
        try:
            self.object = create_expense_request(
                fund_allocation=form.cleaned_data['fund_allocation'],
                requested_amount=form.cleaned_data['requested_amount'],
                purpose=form.cleaned_data['purpose'],
                requested_date=form.cleaned_data['requested_date'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Solicitud de gasto registrada.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_request_detail', args=[self.object.pk])


class ExpenseRequestCreateView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.add_expenserequest'
    form_class = ExpenseRequestForm
    template_name = 'web/object_form.html'
    route_prefix = 'expense_request'
    page_title = _('Nueva solicitud de gasto')

    def has_permission(self):
        # PRE: PermissionRequiredMixin already authenticated the user when applicable.
        # POST: requires add_expenserequest plus Admin-style global create powers.
        return (
            super().has_permission()
            and user_can_create_global_expense_request(self.request.user)
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['include_project_in_label'] = True
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = self.page_title
        context['form_subtitle'] = _(
            'Registre una solicitud para evaluación del Comité de proyectos.'
        )
        context['submit_label'] = _('Registrar solicitud')
        return context

    def form_valid(self, form):
        try:
            self.object = create_expense_request(
                fund_allocation=form.cleaned_data['fund_allocation'],
                requested_amount=form.cleaned_data['requested_amount'],
                purpose=form.cleaned_data['purpose'],
                requested_date=form.cleaned_data['requested_date'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Solicitud de gasto registrada.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_request_detail', args=[self.object.pk])


class ExpenseRequestUpdateView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.change_expenserequest'
    form_class = ExpenseRequestForm
    template_name = 'web/object_form.html'
    route_prefix = 'expense_request'
    page_title = _('Editar solicitud de gasto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: route targets an editable own pending request under visibility rules.
        # POST: loads the owned PENDING_DECISION row or 404; foreign rows stay hidden.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                mutable_own_pending_expense_requests_for_user(request.user).select_related(
                    'fund_allocation',
                    'fund_allocation__project',
                ),
                pk=kwargs['pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        use_global = user_may_use_global_expense_request_allocations(self.request.user)
        kwargs['include_project_in_label'] = use_global
        kwargs['include_allocation_id'] = self.object.fund_allocation_id
        if not use_global:
            kwargs['project'] = self.object.fund_allocation.project
        if 'data' not in kwargs and 'files' not in kwargs:
            kwargs['initial'] = {
                'fund_allocation': self.object.fund_allocation_id,
                'requested_amount': self.object.requested_amount,
                'purpose': self.object.purpose,
                'requested_date': self.object.requested_date,
            }
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = self.page_title
        context['submit_label'] = _('Guardar cambios')
        context['cancel_object_pk'] = self.object.pk
        context['list_url_name'] = 'expense_request_detail'
        return context

    def form_valid(self, form):
        try:
            self.object = update_expense_request(
                self.object,
                fund_allocation=form.cleaned_data['fund_allocation'],
                requested_amount=form.cleaned_data['requested_amount'],
                purpose=form.cleaned_data['purpose'],
                requested_date=form.cleaned_data['requested_date'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Solicitud de gasto actualizada.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_request_detail', args=[self.object.pk])


class ExpenseRequestWithdrawView(TerminalActionView):
    permission_required = 'operations.withdraw_expenserequest'
    model = ExpenseRequest
    detail_url_name = 'expense_request_detail'
    action_title = _('Retirar solicitud de gasto')
    consequence = _(
        'La solicitud quedará cerrada y no podrá ser evaluada por el Comité.'
    )
    submit_label = _('Retirar solicitud')
    success_message = _('Solicitud de gasto retirada.')
    is_destructive = True
    requires_reason = True

    def dispatch(self, request, *args, **kwargs):
        # PRE: withdraw route must not leak foreign or non-pending rows.
        # POST: loads owned PENDING_DECISION request or 404 before form handling.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                mutable_own_pending_expense_requests_for_user(request.user),
                pk=kwargs['pk'],
            )
            # Skip TerminalActionView.dispatch object load (unscoped model queryset).
            return FormView.dispatch(self, request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # PRE: confirmation reason is valid and actor owns the pending request.
        # POST: withdraws through the domain service or redisplays service errors.
        try:
            self.object = withdraw_expense_request(
                self.object,
                reason=form.cleaned_data['reason'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseRequestAnnulView(TerminalActionView):
    permission_required = 'operations.annul_expenserequest'
    model = ExpenseRequest
    template_name = 'web/expense_request_annul.html'
    detail_url_name = 'expense_request_detail'
    action_title = _('Anular solicitud de gasto')
    submit_label = _('Anular solicitud')
    is_destructive = True
    requires_reason = True

    def dispatch(self, request, *args, **kwargs):
        # PRE: annul route must not leak non-visible or non-annullable rows.
        # POST: loads PENDING_DECISION/APPROVED_RESERVED request or 404 before form handling.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                annullable_expense_requests_for_user(request.user).select_related(
                    'fund_allocation',
                    'fund_allocation__project',
                ),
                pk=kwargs['pk'],
            )
            # Skip TerminalActionView.dispatch object load (unscoped model queryset).
            return FormView.dispatch(self, request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        # PRE: self.object is an annullable request loaded for confirmation display.
        # POST: provides summary context and status-specific consequence copy; no mutation.
        context = super().get_context_data(**kwargs)
        expense_request = self.object
        is_reserved = expense_request.status == ExpenseRequest.Status.APPROVED_RESERVED
        reserved_amount = (
            expense_request.reserved_amount
            if expense_request.reserved_amount is not None
            else ZERO_MONEY
        )
        context.update(
            {
                'expense_request': expense_request,
                'annul_is_reserved': is_reserved,
                'annul_requested_amount': expense_request.requested_amount,
                'annul_reserved_amount': reserved_amount,
                'cancel_url': reverse('expense_request_detail', args=[expense_request.pk]),
            }
        )
        return context

    def form_valid(self, form):
        # PRE: mandatory reason is valid; actor may annul; pre-action status drives success copy.
        # POST: annuls through the domain service or redisplays errors without partial writes.
        had_active_reservation = (
            self.object.status == ExpenseRequest.Status.APPROVED_RESERVED
        )
        try:
            self.object = annul_expense_request(
                self.object,
                reason=form.cleaned_data['reason'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        except PermissionDenied:
            raise
        except Exception:
            form.add_error(None, EXPENSE_REQUEST_DECISION_FAILURE_MESSAGE)
            return self.form_invalid(form)
        if had_active_reservation:
            messages.success(
                self.request,
                _('Solicitud anulada. La reserva fue liberada.'),
            )
        else:
            messages.success(self.request, _('Solicitud de gasto anulada.'))
        return HttpResponseRedirect(self.get_success_url())


class ExpenseRequestApproveView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.decide_expenserequest'
    form_class = ExpenseRequestApproveForm
    template_name = 'web/expense_request_approve.html'
    route_prefix = 'expense_request'
    page_title = _('Aprobar solicitud de gasto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: decide route targets a visible PENDING_DECISION request.
        # POST: loads decidable pending row or 404; no mutation on GET.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                decidable_pending_expense_requests_for_user(request.user).select_related(
                    'fund_allocation',
                    'fund_allocation__project',
                ),
                pk=kwargs['pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expense_request'] = self.object
        context['cancel_url'] = reverse('expense_request_detail', args=[self.object.pk])
        context['submit_label'] = _('Aprobar y reservar fondos')
        context.update(_approval_preview_context(self.object))
        return context

    def form_valid(self, form):
        # PRE: optional note validated; actor has decide_expenserequest; object was pending at GET.
        # POST: approves via service or redisplays domain errors without partial writes.
        try:
            self.object = approve_expense_request(
                self.object,
                decision_note=form.cleaned_data.get('decision_note') or '',
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        except PermissionDenied:
            raise
        except Exception:
            form.add_error(None, EXPENSE_REQUEST_DECISION_FAILURE_MESSAGE)
            return self.form_invalid(form)
        messages.success(self.request, _('Solicitud aprobada y fondos reservados.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_request_detail', args=[self.object.pk])


class ExpenseRequestDenyView(TerminalActionView):
    permission_required = 'operations.decide_expenserequest'
    model = ExpenseRequest
    detail_url_name = 'expense_request_detail'
    action_title = _('Denegar solicitud de gasto')
    consequence = _(
        'La solicitud quedará cerrada y no podrá registrarse un gasto a partir de ella.'
    )
    submit_label = _('Denegar solicitud')
    success_message = _('Solicitud de gasto denegada.')
    is_destructive = True
    requires_reason = True

    def dispatch(self, request, *args, **kwargs):
        # PRE: deny route must not leak non-visible or non-pending rows.
        # POST: loads decidable PENDING_DECISION request or 404 before form handling.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                decidable_pending_expense_requests_for_user(request.user),
                pk=kwargs['pk'],
            )
            # Skip TerminalActionView.dispatch object load (unscoped model queryset).
            return FormView.dispatch(self, request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # PRE: mandatory reason is valid and actor may decide the pending request.
        # POST: denies through the domain service or redisplays service errors.
        try:
            self.object = deny_expense_request(
                self.object,
                decision_note=form.cleaned_data['reason'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        except PermissionDenied:
            raise
        except Exception:
            form.add_error(None, EXPENSE_REQUEST_DECISION_FAILURE_MESSAGE)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseRequestFulfillView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    FormView,
):
    permission_required = 'operations.fulfill_expenserequest'
    form_class = ExpenseRequestFulfillmentForm
    template_name = 'web/expense_request_fulfillment_form.html'
    route_prefix = 'expense_request'
    page_title = _('Registrar gasto desde solicitud')

    def dispatch(self, request, *args, **kwargs):
        # PRE: fulfill route targets a visible APPROVED_RESERVED request without Expense.
        # POST: loads fulfillable row or 404; no mutation on GET.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.object = get_object_or_404(
                fulfillable_expense_requests_for_user(request.user).select_related(
                    'fund_allocation',
                    'fund_allocation__project',
                    'requested_by',
                ),
                pk=kwargs['pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        reserved = self.object.reserved_amount
        kwargs['reserved_amount'] = reserved
        if 'data' not in kwargs and 'files' not in kwargs:
            kwargs['initial'] = {
                'amount': reserved,
                'reason': self.object.purpose,
            }
        return kwargs

    def get_context_data(self, **kwargs):
        # PRE: self.object is a fulfillable APPROVED_RESERVED request.
        # POST: provides read-only summary context; no mutation.
        context = super().get_context_data(**kwargs)
        expense_request = self.object
        reserved = (
            expense_request.reserved_amount
            if expense_request.reserved_amount is not None
            else ZERO_MONEY
        )
        context.update(
            {
                'expense_request': expense_request,
                'fulfill_requested_amount': expense_request.requested_amount,
                'fulfill_reserved_amount': reserved,
                'fulfill_available_balance': (
                    expense_request.fund_allocation.available_balance
                ),
                'requested_by_display': _user_display_name(expense_request.requested_by),
                'cancel_url': reverse(
                    'expense_request_detail', args=[expense_request.pk]
                ),
                'submit_label': _('Registrar gasto'),
            }
        )
        return context

    def form_valid(self, form):
        # PRE: form fields validated; actor has fulfill_expenserequest; object was fulfillable at GET.
        # POST: fulfills via service or redisplays domain errors without partial writes.
        try:
            self.object = fulfill_expense_request(
                self.object,
                expense_date=form.cleaned_data['expense_date'],
                amount=form.cleaned_data['amount'],
                category=form.cleaned_data['category'],
                reason=form.cleaned_data['reason'],
                provider_or_recipient=form.cleaned_data['provider_or_recipient'],
                payment_method=form.cleaned_data['payment_method'],
                description=form.cleaned_data.get('description') or '',
                observations=form.cleaned_data.get('observations') or '',
                support_file=form.cleaned_data['support_file'],
                support_title=form.cleaned_data.get('support_title') or '',
                support_notes=form.cleaned_data.get('support_notes') or '',
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        except PermissionDenied:
            raise
        except Exception:
            form.add_error(None, EXPENSE_REQUEST_DECISION_FAILURE_MESSAGE)
            return self.form_invalid(form)

        reserved = self.object.reserved_amount or ZERO_MONEY
        executed = self.object.expense.amount
        if executed < reserved:
            messages.success(
                self.request,
                _(
                    'Gasto registrado desde la solicitud. '
                    'La reserva no utilizada fue liberada.'
                ),
            )
        else:
            messages.success(
                self.request,
                _('Gasto registrado desde la solicitud.'),
            )
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_request_detail', args=[self.object.pk])
