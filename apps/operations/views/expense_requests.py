from django.contrib.auth import get_user_model
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from ..choices import BUDGET_CATEGORY_CHOICES
from ..models import ExpenseRequest, Project, ZERO_MONEY
from ..selectors import (
    attachment_display_filename,
    get_expense_request_financial_display,
    user_has_global_expense_request_visibility,
    visible_expense_requests_for_user,
    with_expense_request_detail_data,
    with_expense_request_list_data,
)
from .common import (
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
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
        has_global = self._has_global_visibility()
        context['has_global_expense_request_visibility'] = has_global
        context['filters_active'] = any(
            (self.request.GET.get(key) or '').strip()
            for key in EXPENSE_REQUEST_LIST_FILTER_KEYS
        )
        visible_base = visible_expense_requests_for_user(self.request.user)
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
