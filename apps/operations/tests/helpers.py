from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project


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
