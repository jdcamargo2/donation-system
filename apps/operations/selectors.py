from pathlib import PurePosixPath

from django.db.models import (
    BooleanField,
    Case,
    Count,
    DecimalField,
    Exists,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
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
    Institution,
    Project,
    ProjectUpdate,
    ProjectUpdateRemediation,
    ProjectUpdateReview,
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
# Admin-style global create (allocation browser) without role-name checks.
EXPENSE_REQUEST_GLOBAL_CREATE_PERMISSIONS = (
    'operations.fulfill_expenserequest',
    'operations.annul_expenserequest',
)
# Ownership-workflow indicator (Operator-style) without elevated global powers.
EXPENSE_REQUEST_OWNERSHIP_PERMISSION = 'operations.withdraw_expenserequest'


def _zero_money_value():
    return Value(ZERO_MONEY, output_field=MONEY_OUTPUT_FIELD)


def eligible_donation_donors(*, current_donor_id=None):
    """
    PRE: optional current_donor_id identifies the donor already linked to an existing donation.
    POST: ACTIVE institutions for new activity, plus the current donor when editing historically.
    """
    active = Q(status=Institution.Status.ACTIVE)
    if current_donor_id is not None:
        return Institution.objects.filter(active | Q(pk=current_donor_id)).order_by('name')
    return Institution.objects.filter(active).order_by('name')


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


def _project_executed_amount_subquery():
    """
    PRE: outer query rows are Project instances.
    POST: returns an isolated subquery summing effective operating-currency expenses.
    """
    return Coalesce(
        Subquery(
            Expense.objects.filter(
                allocation__project_id=OuterRef('pk'),
                currency=OPERATING_CURRENCY,
                allocation__donation__currency=OPERATING_CURRENCY,
            )
            .exclude(allocation__status=FundAllocation.Status.ANNULLED)
            .exclude(status__in=Expense.non_executing_statuses())
            .values('allocation__project_id')
            .annotate(total=Sum('amount'))
            .values('total')[:1],
            output_field=MONEY_OUTPUT_FIELD,
        ),
        _zero_money_value(),
        output_field=MONEY_OUTPUT_FIELD,
    )


def _project_reserved_amount_subquery():
    """
    PRE: outer query rows are Project instances.
    POST: returns an isolated subquery summing APPROVED_RESERVED reserved_amount only.
    """
    return Coalesce(
        Subquery(
            ExpenseRequest.objects.filter(
                fund_allocation__project_id=OuterRef('pk'),
                status=ExpenseRequest.Status.APPROVED_RESERVED,
            )
            .values('fund_allocation__project_id')
            .annotate(total=Sum('reserved_amount'))
            .values('total')[:1],
            output_field=MONEY_OUTPUT_FIELD,
        ),
        _zero_money_value(),
        output_field=MONEY_OUTPUT_FIELD,
    )


def with_project_financial_metrics(queryset):
    """
    PRE: queryset selects Project rows for an operational financial listing or detail.
    POST: annotates funded/executed/reserved/available amounts without multi-join multiplication.
    """
    effective_allocations = (
        Q(allocations__donation__currency=OPERATING_CURRENCY)
        & ~Q(allocations__status=FundAllocation.Status.ANNULLED)
    )
    queryset = queryset.annotate(
        annotated_funded_amount=Coalesce(
            Sum('allocations__amount', filter=effective_allocations),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        ),
        annotated_executed_amount=_project_executed_amount_subquery(),
        annotated_reserved_amount=_project_reserved_amount_subquery(),
    )
    return queryset.annotate(
        annotated_available_amount=Greatest(
            (
                F('annotated_funded_amount')
                - F('annotated_executed_amount')
                - F('annotated_reserved_amount')
            ),
            _zero_money_value(),
            output_field=MONEY_OUTPUT_FIELD,
        ),
        annotated_has_financial_activity=Case(
            When(
                Q(annotated_funded_amount__gt=ZERO_MONEY)
                | Q(annotated_executed_amount__gt=ZERO_MONEY)
                | Q(annotated_reserved_amount__gt=ZERO_MONEY),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
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


def user_can_view_project_financials(user) -> bool:
    """
    PRE: user may be anonymous or authenticated.
    POST: True when the user may see project operational financial metrics
          (DASH-FIN3 and project detail). Requires both view_fundallocation and
          view_expense. No role-name checks; superuser works via has_perm.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return (
        user.has_perm('operations.view_fundallocation')
        and user.has_perm('operations.view_expense')
    )


def user_can_create_global_expense_request(user):
    """
    PRE: user may be anonymous or authenticated.
    POST: True when add_expenserequest combines with Admin-style global workflow perms.

    Operator has add_expenserequest but lacks fulfill/annul, so global create stays closed.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if not user.has_perm('operations.add_expenserequest'):
        return False
    return any(
        user.has_perm(permission)
        for permission in EXPENSE_REQUEST_GLOBAL_CREATE_PERMISSIONS
    )


def user_may_use_global_expense_request_allocations(user):
    """
    PRE: user may be anonymous or authenticated.
    POST: True when update/create allocation choices may span all projects (Admin-style).
    """
    return user_can_create_global_expense_request(user)


def operational_fund_allocation_choices(*, project=None, include_allocation_id=None):
    """
    PRE: optional project scopes choices; include_allocation_id may keep one historical row.
    POST: returns structurally eligible FundAllocations (ACTIVE allocation, ACTIVE project,
          RECEIVED operating-currency donation) union the included pk when provided;
          no balance filter; stable select ordering. Callers apply operation-specific
          availability (e.g. reservation-aware balance for Expense Requests).
    """
    eligible = Q(
        status=FundAllocation.Status.ACTIVE,
        project__status=Project.Status.ACTIVE,
        donation__status=Donation.Status.RECEIVED,
        donation__currency=OPERATING_CURRENCY,
    )
    if include_allocation_id is not None:
        eligible |= Q(pk=include_allocation_id)
    queryset = FundAllocation.objects.filter(eligible).select_related('project', 'donation')
    if project is not None:
        project_scope = Q(project_id=project.pk)
        if include_allocation_id is not None:
            project_scope |= Q(pk=include_allocation_id)
        queryset = queryset.filter(project_scope)
    return queryset.order_by(
        'project__code',
        'budget_category',
        'code',
        'pk',
    )


def expense_request_allocation_choices(*, project=None, include_allocation_id=None):
    """
    PRE: optional project scopes choices; include_allocation_id keeps the current edit row.
    POST: returns annotated structurally eligible allocations with reservation-aware
          balance > 0 (or the included id still inside the structural set), ordered
          for stable selects. include_allocation_id does not admit terminal/historical
          parents — only bypasses the zero-balance filter.
    """
    queryset = with_allocation_list_metrics(
        operational_fund_allocation_choices(project=project)
    )
    balance_filter = Q(annotated_available_balance__gt=0)
    if include_allocation_id is not None:
        balance_filter |= Q(pk=include_allocation_id)
    return queryset.filter(balance_filter).order_by(
        'project__code',
        'budget_category',
        'code',
        'pk',
    )


def visible_expense_requests_for_allocation(*, user, allocation):
    """
    PRE: caller enforces operations.view_expenserequest at the view layer;
         allocation is the FundAllocation whose linked requests are previewed.
    POST: returns ExpenseRequest rows for that allocation visible to user,
          list relations loaded, ordered like the shared Expense Request list.
    """
    return with_expense_request_list_data(
        visible_expense_requests_for_user(user).filter(fund_allocation=allocation)
    ).order_by('-requested_date', '-created_at', '-pk')


def mutable_own_pending_expense_requests_for_user(user):
    """
    PRE: caller enforces change/withdraw permission at the view layer.
    POST: returns visible PENDING_DECISION requests owned by the actor (404-safe scope).
    """
    if not getattr(user, 'is_authenticated', False):
        return ExpenseRequest.objects.none()
    return visible_expense_requests_for_user(user).filter(
        requested_by=user,
        status=ExpenseRequest.Status.PENDING_DECISION,
    )


def mutable_own_pending_expense_requests_for_attachments(user):
    """
    PRE: caller enforces add/delete_expenserequestattachment at the view layer.
    POST: returns own PENDING_DECISION rows eligible for attachment mutation (404-safe).

    Reuses parent ownership/visibility policy; does not check role names.
    """
    return mutable_own_pending_expense_requests_for_user(user)


def visible_expense_request_attachments_for_user(*, user, request_pk):
    """
    PRE: caller enforces view_expenserequestattachment at the view layer;
         request_pk identifies the parent Expense Request.
    POST: returns attachments of that parent when the parent is visible to user;
          empty queryset when the parent is invisible (mismatched/foreign → 404 upstream).
    """
    visible_parents = visible_expense_requests_for_user(user).filter(pk=request_pk)
    return (
        ExpenseRequestAttachment.objects.filter(
            expense_request_id=request_pk,
            expense_request__in=visible_parents,
        )
        .select_related('expense_request', 'uploaded_by')
        .order_by('uploaded_at', 'pk')
    )


def decidable_pending_expense_requests_for_user(user):
    """
    PRE: caller enforces decide_expenserequest at the view layer when loading routes.
    POST: returns visible PENDING_DECISION rows the actor may open for committee decision.

    Empty when the user lacks decide_expenserequest. Does not check role names.
    Non-pending rows are excluded so GET action pages resolve as 404.
    """
    if not getattr(user, 'is_authenticated', False):
        return ExpenseRequest.objects.none()
    if not user.has_perm('operations.decide_expenserequest'):
        return ExpenseRequest.objects.none()
    return visible_expense_requests_for_user(user).filter(
        status=ExpenseRequest.Status.PENDING_DECISION,
    )


def annullable_expense_requests_for_user(user):
    """
    PRE: caller enforces annul_expenserequest at the view layer when loading routes.
    POST: returns visible PENDING_DECISION or APPROVED_RESERVED rows for admin annulment.

    Empty when the user lacks annul_expenserequest. Does not check role names.
    Terminal/non-annullable rows are excluded so GET action pages resolve as 404.
    """
    if not getattr(user, 'is_authenticated', False):
        return ExpenseRequest.objects.none()
    if not user.has_perm('operations.annul_expenserequest'):
        return ExpenseRequest.objects.none()
    return visible_expense_requests_for_user(user).filter(
        status__in=(
            ExpenseRequest.Status.PENDING_DECISION,
            ExpenseRequest.Status.APPROVED_RESERVED,
        ),
    )


def fulfillable_expense_requests_for_user(user):
    """
    PRE: caller enforces fulfill_expenserequest at the view layer when loading routes.
    POST: returns visible APPROVED_RESERVED rows without a linked Expense for fulfillment.

    Empty when the user lacks fulfill_expenserequest. Does not check role names.
    Stale/terminal rows are excluded so GET action pages resolve as 404.
    """
    if not getattr(user, 'is_authenticated', False):
        return ExpenseRequest.objects.none()
    if not user.has_perm('operations.fulfill_expenserequest'):
        return ExpenseRequest.objects.none()
    return visible_expense_requests_for_user(user).filter(
        status=ExpenseRequest.Status.APPROVED_RESERVED,
        expense__isnull=True,
    )


# Active statuses for personal/read-only dashboard tracking (awareness, not mutation).
# Same authoritative set that blocks financial scope closure.
DASHBOARD_TRACKING_EXPENSE_REQUEST_STATUSES = ExpenseRequest.open_financial_statuses()


def open_expense_requests_for_allocation(allocation):
    """
    PRE: allocation is a persisted FundAllocation (or exposes .pk).
    POST: returns an unevaluated queryset of ExpenseRequests that block finishing that allocation.
    """
    return ExpenseRequest.objects.filter(
        fund_allocation_id=allocation.pk,
        status__in=ExpenseRequest.open_financial_statuses(),
    )


def allocation_has_open_financial_work(allocation) -> bool:
    """
    PRE: allocation is a persisted FundAllocation.
    POST: True when any PENDING_DECISION or APPROVED_RESERVED request remains on it.
    """
    return open_expense_requests_for_allocation(allocation).exists()


def open_expense_requests_for_project(project):
    """
    PRE: project is a persisted Project (or exposes .pk).
    POST: returns an unevaluated queryset of open ExpenseRequests under the project's allocations.
    """
    return ExpenseRequest.objects.filter(
        fund_allocation__project_id=project.pk,
        status__in=ExpenseRequest.open_financial_statuses(),
    )


def project_has_active_allocations(project) -> bool:
    """
    PRE: project is a persisted Project.
    POST: True when at least one FundAllocation under the project is still ACTIVE.
    """
    return FundAllocation.objects.filter(
        project_id=project.pk,
        status=FundAllocation.Status.ACTIVE,
    ).exists()


def project_has_open_financial_work(project) -> bool:
    """
    PRE: project is a persisted Project.
    POST: True when the project still has ACTIVE allocations or open ExpenseRequests.
    """
    return project_has_active_allocations(project) or open_expense_requests_for_project(
        project
    ).exists()


def tracking_expense_requests_for_user(user):
    """
    PRE: caller gates on view_expenserequest and only when no actionable queue applies.
    POST: returns visible pending/approved requests in the user's authoritative scope.

    Ownership-scoped users see only their own rows via visible_expense_requests_for_user.
    Does not check role names. Empty for anonymous or users without view permission.
    """
    if not getattr(user, 'is_authenticated', False):
        return ExpenseRequest.objects.none()
    if not user.has_perm('operations.view_expenserequest'):
        return ExpenseRequest.objects.none()
    return visible_expense_requests_for_user(user).filter(
        status__in=DASHBOARD_TRACKING_EXPENSE_REQUEST_STATUSES,
    )


def reviewable_project_updates_for_user(user):
    """
    PRE: caller enforces review_projectupdate at the view layer when loading routes.
    POST: returns PUBLISHED advances without a committee review the actor may open.

    Empty when the user lacks review_projectupdate. Does not check role names.
    Advances already reviewed or not published are excluded so GET action pages 404.
    Project-update visibility is global for holders of the action permission (no
    ownership scope exists on ProjectUpdate listings).
    """
    if not getattr(user, 'is_authenticated', False):
        return ProjectUpdate.objects.none()
    if not user.has_perm('operations.review_projectupdate'):
        return ProjectUpdate.objects.none()
    return ProjectUpdate.objects.filter(
        status=ProjectUpdate.Status.PUBLISHED,
        committee_review__isnull=True,
    )


def decidable_project_update_reviews_for_user(user):
    """
    PRE: caller enforces decide_projectupdate at the view layer when loading routes.
    POST: returns reviews of PUBLISHED advances without a decision the actor may open.

    Empty when the user lacks decide_projectupdate. Does not check role names.
    Reviews that already have a decision or whose parent update is not published
    are excluded so GET action pages resolve as 404.
    """
    if not getattr(user, 'is_authenticated', False):
        return ProjectUpdateReview.objects.none()
    if not user.has_perm('operations.decide_projectupdate'):
        return ProjectUpdateReview.objects.none()
    return ProjectUpdateReview.objects.filter(
        decision__isnull=True,
        project_update__status=ProjectUpdate.Status.PUBLISHED,
    )


def resolvable_project_update_remediations_for_user(user):
    """
    PRE: caller enforces resolve_projectupdateremediation at the view layer.
    POST: returns SUBMITTED remediations the actor may open for resolution.

    Empty when the user lacks resolve_projectupdateremediation. Does not check
    role names. DRAFT/ACCEPTED/REJECTED rows are excluded so GET action pages 404.
    Parent decision/review/update remain joinable for display; terminal remediations
    never appear.
    """
    if not getattr(user, 'is_authenticated', False):
        return ProjectUpdateRemediation.objects.none()
    if not user.has_perm('operations.resolve_projectupdateremediation'):
        return ProjectUpdateRemediation.objects.none()
    return ProjectUpdateRemediation.objects.filter(
        status=ProjectUpdateRemediation.Status.SUBMITTED,
        decision__review__project_update__status=ProjectUpdate.Status.PUBLISHED,
    )
