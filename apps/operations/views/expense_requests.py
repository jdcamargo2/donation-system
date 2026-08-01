from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, ListView

from ..choices import BUDGET_CATEGORY_CHOICES
from ..expense_request_services import (
    create_expense_request,
    update_expense_request,
    withdraw_expense_request,
)
from ..forms import ExpenseRequestForProjectForm, ExpenseRequestForm
from ..models import ExpenseRequest, Project, ZERO_MONEY
from ..selectors import (
    attachment_display_filename,
    get_expense_request_financial_display,
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
    POST: returns edit/withdraw flags for PENDING_DECISION rows owned by the actor.
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

        attachments = []
        if context['can_view_attachments']:
            for attachment in expense_request.attachments.all():
                attachments.append(
                    {
                        'title': attachment.title,
                        'notes': attachment.notes,
                        'uploaded_by_display': _user_display_name(attachment.uploaded_by),
                        'uploaded_at': attachment.uploaded_at,
                        'filename': attachment_display_filename(attachment),
                    }
                )
        context['detail_attachments'] = attachments

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        context['cancel_url'] = reverse('project_detail', args=[self.project.pk])
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
