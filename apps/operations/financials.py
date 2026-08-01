"""Reservation-aware financial helpers for FundAllocation balances."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from .models import ExpenseRequest, ZERO_MONEY

MONEY_QUANTUM = Decimal('0.01')


# PRE: value is a Decimal-compatible monetary amount.
# POST: returns a Decimal quantized to two places with half-up rounding.
def quantize_money(value) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


# PRE: allocation is a persisted FundAllocation; callers that mutate must already hold locks.
# POST: returns the quantized sum of APPROVED_RESERVED reserved_amount, excluding exclude_request_id.
def get_allocation_reserved_amount(allocation, *, exclude_request_id=None) -> Decimal:
    reservations = ExpenseRequest.objects.filter(
        fund_allocation_id=allocation.pk,
        status=ExpenseRequest.Status.APPROVED_RESERVED,
    )
    if exclude_request_id is not None:
        reservations = reservations.exclude(pk=exclude_request_id)
    total = reservations.aggregate(total=Sum('reserved_amount'))['total']
    if total is None:
        return ZERO_MONEY
    return quantize_money(total)
