from pathlib import PurePosixPath

from django.db.models import Count, DecimalField, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import get_object_or_404

from .choices import OPERATING_CURRENCY
from .models import (
    Donation,
    Expense,
    ExpenseRequest,
    ExpenseRequestAttachment,
    ExpenseRequestEvent,
    FundAllocation,
    SupportingDocument,
    ZERO_MONEY,
)


MONEY_OUTPUT_FIELD = DecimalField(max_digits=14, decimal_places=2)

# Elevated workflow permissions grant global Expense Request visibility.
EXPENSE_REQUEST_GLOBAL_WORKFLOW_PERMISSIONS = (
    'operations.decide_expenserequest',
    'operations.fulfill_expenserequest',
    'operations.annul_expenserequest',
)
# Ownership-workflow indicator (Operator-style) without elevated global powers.
EXPENSE_REQUEST_OWNERSHIP_PERMISSION = 'operations.withdraw_expenserequest'


def _zero_money_value():
    return Value(ZERO_MONEY, output_field=MONEY_OUTPUT_FIELD)


def with_donation_list_metrics(queryset):
    """
    PRE: queryset selects Donation rows for an operational listing.
    POST: returns it with effective USD allocation totals and clamped balances annotated once.
    """
    effective_allocations = (
        Q(currency=OPERATING_CURRENCY)
        & ~Q(allocations__status=FundAllocation.Status.ANNULLED)
    )
    queryset = queryset.annotate(
        annotated_total_assigned=Coalesce(
            Sum('allocations__amount', filter=effective_allocations),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        )
    )
    return queryset.annotate(
        annotated_available_balance=Greatest(
            F('amount') - F('annotated_total_assigned'),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        )
    )


def _reserved_amount_subquery():
    """
    PRE: outer query rows are FundAllocation instances.
    POST: returns an independent subquery summing APPROVED_RESERVED reserved_amount only.
    """
    return Coalesce(
        Subquery(
            ExpenseRequest.objects.filter(
                fund_allocation_id=OuterRef('pk'),
                status=ExpenseRequest.Status.APPROVED_RESERVED,
            )
            .values('fund_allocation_id')
            .annotate(total=Sum('reserved_amount'))
            .values('total')[:1],
            output_field=MONEY_OUTPUT_FIELD,
        ),
        _zero_money_value(),
        output_field=MONEY_OUTPUT_FIELD,
    )


def with_allocation_list_metrics(queryset):
    """
    PRE: queryset selects FundAllocation rows for an operational listing.
    POST: returns it with executed, reserved, and clamped available balances without join multiplication.
    """
    effective_expenses = (
        Q(expenses__currency=OPERATING_CURRENCY)
        & ~Q(expenses__status__in=Expense.non_executing_statuses())
    )
    queryset = queryset.annotate(
        annotated_executed_amount=Coalesce(
            Sum('expenses__amount', filter=effective_expenses),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        ),
        annotated_reserved_amount=_reserved_amount_subquery(),
    )
    return queryset.annotate(
        annotated_available_balance=Greatest(
            F('amount') - F('annotated_executed_amount') - F('annotated_reserved_amount'),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        )
    )


def with_expense_list_support(queryset):
    """
    PRE: queryset selects Expense rows for a list that displays support presence.
    POST: returns it with one correlated existence annotation and no document prefetch.
    """
    return queryset.annotate(
        annotated_has_support=Exists(
            SupportingDocument.objects.filter(expense_id=OuterRef('pk'))
        )
    )


def with_project_update_attachment_count(queryset):
    """
    PRE: queryset selects ProjectUpdate rows for a list that displays attachment totals.
    POST: returns it with the related attachment count computed in the listing query.
    """
    return queryset.annotate(annotated_attachment_count=Count('attachments'))


def user_has_elevated_expense_request_visibility(user):
    """
    PRE: user may be anonymous or authenticated.
    POST: True when effective permissions include any elevated ER workflow permission.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return any(
        user.has_perm(permission)
        for permission in EXPENSE_REQUEST_GLOBAL_WORKFLOW_PERMISSIONS
    )


def user_has_ownership_scoped_expense_requests(user):
    """
    PRE: user may be anonymous or authenticated.
    POST: True for ownership-workflow users (withdraw without elevated global powers).

    Visibility policy (permission-based, not role-name-based):
    - elevated decide/fulfill/annul → all requests;
    - withdraw without elevated → own requests only (Operator-style);
    - view without ownership mutation powers → all requests (Auditor-style);
    - unauthenticated → none.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user_has_elevated_expense_request_visibility(user):
        return False
    return user.has_perm(EXPENSE_REQUEST_OWNERSHIP_PERMISSION)


def user_has_global_expense_request_visibility(user):
    """
    PRE: user may be anonymous or authenticated.
    POST: True when the user may see all Expense Requests (not ownership-scoped).
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user_has_ownership_scoped_expense_requests(user):
        return False
    return user_has_elevated_expense_request_visibility(user) or user.has_perm(
        'operations.view_expenserequest'
    )


def visible_expense_requests_for_user(user):
    """
    PRE: caller enforces operations.view_expenserequest at the view layer.
    POST: returns ExpenseRequest rows the user may list/open; empty for anonymous.

    Policy:
    - unauthenticated → none;
    - elevated workflow (decide/fulfill/annul) or superuser via those perms → all;
    - ownership workflow (withdraw without elevated) → requested_by=user;
    - read-only viewers with view_expenserequest (Auditor-style) → all.
    Do not scope Auditor to own rows merely because they lack mutation permissions.
    """
    base = ExpenseRequest.objects.all()
    if not getattr(user, 'is_authenticated', False):
        return base.none()
    if user_has_ownership_scoped_expense_requests(user):
        return base.filter(requested_by=user)
    return base


def with_expense_request_list_data(queryset):
    """
    PRE: queryset selects ExpenseRequest rows for the shared list.
    POST: returns it with list relations loaded once; no event aggregation.
    """
    return queryset.select_related(
        'fund_allocation',
        'fund_allocation__project',
        'requested_by',
        'expense',
    )


def with_expense_request_detail_data(queryset):
    """
    PRE: queryset already scopes ExpenseRequest rows for the requesting user.
    POST: returns it with detail relations and deterministic event ordering loaded.
    """
    return queryset.select_related(
        'fund_allocation',
        'fund_allocation__project',
        'requested_by',
        'decided_by',
        'terminal_by',
        'expense',
    ).prefetch_related(
        Prefetch(
            'attachments',
            queryset=ExpenseRequestAttachment.objects.select_related('uploaded_by').order_by(
                'uploaded_at', 'pk'
            ),
        ),
        Prefetch(
            'events',
            queryset=ExpenseRequestEvent.objects.select_related(
                'actor',
                'expense',
            ).order_by('created_at', 'pk'),
        ),
    )


def get_expense_request_detail_for_user(*, user, pk):
    """
    PRE: user is authenticated for UI access; pk identifies a candidate request.
    POST: returns the visible detail row or raises Http404-compatible DoesNotExist path.
    """
    return get_object_or_404(
        with_expense_request_detail_data(visible_expense_requests_for_user(user)),
        pk=pk,
    )


def _released_amount_from_events(expense_request):
    """
    PRE: expense_request may have prefetched events.
    POST: returns RESERVATION_RELEASED amount when present, else ZERO_MONEY.
    """
    events = getattr(expense_request, '_prefetched_objects_cache', {}).get('events')
    if events is None:
        events = expense_request.events.filter(
            event_type=ExpenseRequestEvent.EventType.RESERVATION_RELEASED,
        ).order_by('created_at', 'pk')
    for event in events:
        if (
            event.event_type == ExpenseRequestEvent.EventType.RESERVATION_RELEASED
            and event.released_amount is not None
        ):
            return event.released_amount
    return ZERO_MONEY


def get_expense_request_financial_display(expense_request):
    """
    PRE: expense_request is persisted; expense may be select_related.
    POST: returns display-only financial summary without changing domain balances.

    Definitions:
    - requested: request.requested_amount
    - reserved: historical reserved_amount or zero
    - executed: linked expense.amount when present (may remain after expense annulment)
    - released: fulfilled → max(reserved − executed, 0);
      approved-then-annulled → RESERVATION_RELEASED event amount; otherwise zero
    """
    reserved_amount = (
        expense_request.reserved_amount
        if expense_request.reserved_amount is not None
        else ZERO_MONEY
    )
    linked_expense = expense_request.expense if expense_request.expense_id else None
    executed_amount = linked_expense.amount if linked_expense is not None else ZERO_MONEY

    if (
        expense_request.status == ExpenseRequest.Status.FULFILLED
        and linked_expense is not None
    ):
        released_amount = max(reserved_amount - executed_amount, ZERO_MONEY)
    elif expense_request.status == ExpenseRequest.Status.ANNULLED:
        released_amount = _released_amount_from_events(expense_request)
    else:
        released_amount = ZERO_MONEY

    has_linked_expense = linked_expense is not None
    linked_expense_is_annulled = (
        has_linked_expense and linked_expense.status == Expense.Status.ANNULLED
    )
    return {
        'requested_amount': expense_request.requested_amount,
        'reserved_amount': reserved_amount,
        'executed_amount': executed_amount,
        'released_amount': released_amount,
        'currency': expense_request.currency,
        'has_active_reservation': expense_request.has_active_reservation,
        'has_historical_reservation': expense_request.reserved_amount is not None,
        'has_linked_expense': has_linked_expense,
        'linked_expense_is_annulled': linked_expense_is_annulled,
        'show_reserved': expense_request.reserved_amount is not None,
        'show_executed': has_linked_expense,
        'show_released': released_amount > ZERO_MONEY,
    }


def attachment_display_filename(attachment):
    """
    PRE: attachment has a FileField that may be empty.
    POST: returns basename-only label without calling .url.
    """
    name = getattr(getattr(attachment, 'file', None), 'name', '') or ''
    if not name:
        return ''
    return PurePosixPath(name).name
