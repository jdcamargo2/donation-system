from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.operations.models import Donation, Expense, ExpenseRequest, FundAllocation, Institution, Project


TEST_DATE = date(2026, 7, 8)


def create_user(username='auditor'):
    return get_user_model().objects.create_superuser(username=username, password='pass-12345')


def create_institution(name='Caritas Test', role=Institution.Role.DONOR):
    return Institution.objects.create(name=name, role=role, institution_type='foundation', country='VE')


def create_project(code='PRJ-001', name='Food support'):
    return Project.objects.create(code=code, name=name, estimated_budget=Decimal('1000.00'))


def create_donation(code='DON-001', donor=None, amount=Decimal('100.00'), status=Donation.Status.RECEIVED):
    return Donation.objects.create(
        code=code,
        donor=donor or create_institution(),
        amount=amount,
        currency='USD',
        status=status,
    )


def create_allocation(
    donation=None,
    project=None,
    amount=Decimal('60.00'),
    category='health_psychosocial',
    status=FundAllocation.Status.ACTIVE,
):
    return FundAllocation.objects.create(
        donation=donation or create_donation(),
        project=project or create_project(),
        budget_category=category,
        amount=amount,
        allocation_date=TEST_DATE,
        status=status,
    )


def create_expense(
    allocation=None,
    amount=Decimal('20.00'),
    reason='Food purchase',
    status=Expense.Status.REGISTERED,
):
    return Expense.objects.create(
        allocation=allocation or create_allocation(),
        expense_date=TEST_DATE,
        category='food',
        amount=amount,
        currency='USD',
        reason=reason,
        provider_or_recipient='Provider A',
        payment_method='bank_transfer',
        status=status,
    )


def create_expense_request(
    *,
    fund_allocation=None,
    requested_by=None,
    requested_amount=Decimal('15.00'),
    purpose='Solicitud de gasto de prueba',
    requested_date=TEST_DATE,
    status=ExpenseRequest.Status.PENDING_DECISION,
    code='',
    **extra_fields,
):
    return ExpenseRequest.objects.create(
        code=code,
        fund_allocation=fund_allocation or create_allocation(),
        requested_by=requested_by or create_user(username='expense-request-actor'),
        requested_amount=requested_amount,
        purpose=purpose,
        requested_date=requested_date,
        status=status,
        **extra_fields,
    )


def create_approved_reserved_request(
    *,
    fund_allocation=None,
    requested_by=None,
    decided_by=None,
    requested_amount=Decimal('15.00'),
    reserved_amount=None,
    purpose='Solicitud aprobada de prueba',
    code='',
):
    """
    PRE: callers supply operational parents when sharing an allocation across fixtures.
    POST: returns one APPROVED_RESERVED request satisfying ER1 constraints.
    """
    actor = requested_by or create_user(username='reserved-requester')
    decider = decided_by or create_user(username='reserved-decider')
    now = timezone.now()
    reserved = reserved_amount if reserved_amount is not None else requested_amount
    return create_expense_request(
        fund_allocation=fund_allocation,
        requested_by=actor,
        requested_amount=requested_amount,
        purpose=purpose,
        status=ExpenseRequest.Status.APPROVED_RESERVED,
        code=code,
        decided_by=decider,
        decided_at=now,
        reserved_amount=reserved,
        reserved_at=now,
    )
