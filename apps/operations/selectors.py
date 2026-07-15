from django.db.models import Count, DecimalField, Exists, F, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce, Greatest

from .choices import OPERATING_CURRENCY
from .models import Donation, Expense, FundAllocation, SupportingDocument, ZERO_MONEY


MONEY_OUTPUT_FIELD = DecimalField(max_digits=14, decimal_places=2)


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


def with_allocation_list_metrics(queryset):
    """
    PRE: queryset selects FundAllocation rows for an operational listing.
    POST: returns it with effective USD expense totals and clamped balances annotated once.
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
        )
    )
    return queryset.annotate(
        annotated_available_balance=Greatest(
            F('amount') - F('annotated_executed_amount'),
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
