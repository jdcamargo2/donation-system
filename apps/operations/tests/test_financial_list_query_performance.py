from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.models import (
    AllocationExecutionProgress,
    Donation,
    DonationAllocationProgress,
    Expense,
    FundAllocation,
    ProjectUpdate,
    SupportingDocument,
    ZERO_MONEY,
)
from apps.operations.selectors import (
    with_allocation_list_metrics,
    with_donation_list_metrics,
    with_expense_list_support,
    with_project_update_attachment_count,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
)


class FinancialListAnnotationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='financial-list-annotations', password='pass-12345'
        )
        self.client.force_login(self.user)
        self.donor = create_institution(name='Donante de métricas anotadas')
        self.project = create_project(
            code='PRJ-ANNOTATED-LISTS', name='Proyecto de métricas anotadas'
        )

    def test_donation_without_allocations_has_zero_safe_annotated_metrics(self):
        donation = create_donation(
            code='DON-ANNOTATED-EMPTY',
            donor=self.donor,
            amount=Decimal('200.00'),
        )

        annotated = with_donation_list_metrics(Donation.objects.all()).get(pk=donation.pk)

        self.assertEqual(annotated.total_assigned, ZERO_MONEY)
        self.assertEqual(annotated.available_balance, Decimal('200.00'))
        self.assertEqual(
            annotated.allocation_progress,
            DonationAllocationProgress.UNALLOCATED,
        )

    def test_donation_annotations_equal_domain_properties_with_multiple_allocations(self):
        donation = create_donation(
            code='DON-ANNOTATED-MULTIPLE',
            donor=self.donor,
            amount=Decimal('200.00'),
        )
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('30.00'),
            status=FundAllocation.Status.ACTIVE,
        )
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('20.00'),
            status=FundAllocation.Status.FINISHED,
        )
        create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('90.00'),
            status=FundAllocation.Status.ANNULLED,
        )

        domain = Donation.objects.get(pk=donation.pk)
        annotated = with_donation_list_metrics(Donation.objects.all()).get(pk=donation.pk)

        self.assertEqual(annotated.annotated_total_assigned, domain.total_assigned)
        self.assertEqual(annotated.annotated_available_balance, domain.available_balance)
        self.assertEqual(annotated.allocation_progress, domain.allocation_progress)
        self.assertEqual(annotated.total_assigned, Decimal('50.00'))
        self.assertEqual(annotated.available_balance, Decimal('150.00'))
        self.assertEqual(
            annotated.allocation_progress,
            DonationAllocationProgress.PARTIALLY_ALLOCATED,
        )

    def test_allocation_without_expenses_has_zero_safe_annotated_metrics(self):
        donation = create_donation(
            code='DON-ANNOTATED-EMPTY-ALLOCATION',
            donor=self.donor,
            amount=Decimal('200.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('100.00'),
        )

        annotated = with_allocation_list_metrics(
            FundAllocation.objects.all()
        ).get(pk=allocation.pk)

        self.assertEqual(annotated.executed_amount, ZERO_MONEY)
        self.assertEqual(annotated.available_balance, Decimal('100.00'))
        self.assertEqual(
            annotated.execution_progress,
            AllocationExecutionProgress.UNEXECUTED,
        )

    def test_allocation_annotations_equal_domain_properties_and_exclude_annulled_expenses(self):
        donation = create_donation(
            code='DON-ANNOTATED-EXPENSES',
            donor=self.donor,
            amount=Decimal('300.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        create_expense(
            allocation=allocation, amount=Decimal('10.00'), reason='Efectivo uno'
        )
        create_expense(
            allocation=allocation, amount=Decimal('15.00'), reason='Efectivo dos'
        )
        create_expense(
            allocation=allocation,
            amount=Decimal('40.00'),
            reason='Gasto anulado',
            status=Expense.Status.ANNULLED,
        )

        domain = FundAllocation.objects.get(pk=allocation.pk)
        annotated = with_allocation_list_metrics(FundAllocation.objects.all()).get(
            pk=allocation.pk
        )

        self.assertEqual(annotated.annotated_executed_amount, domain.executed_amount)
        self.assertEqual(annotated.annotated_available_balance, domain.available_balance)
        self.assertEqual(annotated.execution_progress, domain.execution_progress)
        self.assertEqual(annotated.executed_amount, Decimal('25.00'))
        self.assertEqual(annotated.available_balance, Decimal('75.00'))
        self.assertEqual(
            annotated.execution_progress,
            AllocationExecutionProgress.PARTIALLY_EXECUTED,
        )

    def test_multiple_joins_do_not_duplicate_donations_or_allocation_amounts(self):
        donation = create_donation(
            code='DON-ANNOTATED-CARDINALITY',
            donor=self.donor,
            amount=Decimal('500.00'),
        )
        allocations = [
            create_allocation(
                donation=donation,
                project=self.project,
                amount=Decimal('100.00'),
            )
            for _ in range(2)
        ]
        for allocation in allocations:
            create_expense(allocation=allocation, amount=Decimal('10.00'))
            create_expense(allocation=allocation, amount=Decimal('20.00'))

        donation_rows = with_donation_list_metrics(
            Donation.objects.filter(pk=donation.pk)
        )
        allocation_rows = with_allocation_list_metrics(
            FundAllocation.objects.filter(pk__in=[item.pk for item in allocations])
        )

        self.assertEqual(donation_rows.count(), 1)
        self.assertEqual(donation_rows.get().total_assigned, Decimal('200.00'))
        self.assertEqual(allocation_rows.count(), 2)
        self.assertEqual(
            list(allocation_rows.order_by('pk').values_list('annotated_executed_amount', flat=True)),
            [Decimal('30.00'), Decimal('30.00')],
        )

    def test_financial_list_html_uses_the_annotated_values(self):
        donation = create_donation(
            code='DON-ANNOTATED-HTML',
            donor=self.donor,
            amount=Decimal('200.00'),
        )
        allocation = create_allocation(
            donation=donation,
            project=self.project,
            amount=Decimal('50.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('20.00'))

        donation_response = self.client.get(
            reverse('donation_list'), {'q': 'DON-ANNOTATED-HTML'}
        )
        allocation_response = self.client.get(
            reverse('allocation_list'), {'q': allocation.code}
        )

        self.assertContains(donation_response, '50,00 USD')
        self.assertContains(donation_response, '150,00 USD')
        self.assertContains(allocation_response, '20,00')
        self.assertContains(allocation_response, '30,00')

    def test_expense_and_project_update_lists_use_correlated_annotations(self):
        donation = create_donation(
            code='DON-ANNOTATED-RELATED', donor=self.donor, amount=Decimal('200.00')
        )
        allocation = create_allocation(
            donation=donation, project=self.project, amount=Decimal('100.00')
        )
        with_support = create_expense(
            allocation=allocation, reason='Con evidencia anotada'
        )
        without_support = create_expense(
            allocation=allocation, reason='Sin evidencia anotada'
        )
        SupportingDocument.objects.create(
            expense=with_support,
            title='Evidencia',
            document='supporting_documents/annotated.pdf',
        )
        update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance con conteo anotado',
            description='Descripción',
            update_date=date(2026, 7, 15),
            progress_percentage=10,
            created_by=self.user,
            reported_by=self.user,
        )
        update.attachments.create(file='project_update_attachments/one.pdf')
        update.attachments.create(file='project_update_attachments/two.pdf')

        expenses = with_expense_list_support(
            Expense.objects.filter(pk__in=(with_support.pk, without_support.pk))
        )
        annotated_update = with_project_update_attachment_count(
            ProjectUpdate.objects.all()
        ).get(pk=update.pk)
        expense_response = self.client.get(
            reverse('expense_list'), {'q': 'evidencia anotada'}
        )
        update_response = self.client.get(reverse('project_update_list'))

        support_by_pk = {
            expense.pk: expense.annotated_has_support for expense in expenses
        }
        self.assertEqual(
            support_by_pk,
            {with_support.pk: True, without_support.pk: False},
        )
        self.assertEqual(annotated_update.annotated_attachment_count, 2)
        self.assertContains(expense_response, 'Con soporte')
        self.assertContains(expense_response, 'Sin soporte')
        self.assertContains(update_response, '<td>2</td>', html=True)


class FinancialListQueryScalingTests(TestCase):
    ROW_COUNT = 20

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='financial-list-query-scaling', password='pass-12345'
        )
        donor = create_institution(name='Donante de escalamiento')
        project = create_project(code='PRJ-QUERY-SCALING')
        donation = create_donation(
            code='DON-QUERY-SCALING', donor=donor, amount=Decimal('10000.00')
        )
        for index in range(cls.ROW_COUNT):
            row_donation = create_donation(
                code=f'DON-QUERY-{index:02d}',
                donor=donor,
                amount=Decimal('100.00'),
            )
            create_allocation(
                donation=row_donation,
                project=project,
                amount=Decimal('10.00'),
            )
            allocation = create_allocation(
                donation=donation,
                project=project,
                amount=Decimal('100.00'),
            )
            expense = create_expense(
                allocation=allocation,
                amount=Decimal('10.00'),
                reason=f'Query expense {index}',
            )
            SupportingDocument.objects.create(
                expense=expense,
                title=f'Query support {index}',
                document=f'supporting_documents/query-{index}.pdf',
            )
            update = ProjectUpdate.objects.create(
                project=project,
                title=f'Query update {index}',
                description='Query scaling',
                update_date=date(2026, 7, 15),
                progress_percentage=0,
                created_by=cls.user,
                reported_by=cls.user,
            )
            update.attachments.create(file=f'project_update_attachments/query-{index}.pdf')

    def query_count(self, operation):
        """PRE: operation performs the isolated read under test. POST: returns its SQL count."""
        with CaptureQueriesContext(connection) as queries:
            operation()
        return len(queries)

    def assert_constant_query_count(self, queryset, consume):
        """
        PRE: queryset returns fresh annotated rows and consume reads every displayed metric.
        POST: asserts that rendering twenty rows adds at most one query over one row.
        """
        one_row_queries = self.query_count(
            lambda: [consume(item) for item in queryset()[:1]]
        )
        twenty_row_queries = self.query_count(
            lambda: [consume(item) for item in queryset()[:self.ROW_COUNT]]
        )
        self.assertLessEqual(twenty_row_queries, one_row_queries + 1)

    def test_financial_and_related_list_annotations_do_not_scale_with_rows(self):
        self.assert_constant_query_count(
            lambda: with_donation_list_metrics(
                Donation.objects.filter(code__startswith='DON-QUERY-')
            ),
            lambda donation: (
                donation.total_assigned,
                donation.available_balance,
                donation.allocation_progress_label,
            ),
        )
        self.assert_constant_query_count(
            lambda: with_allocation_list_metrics(
                FundAllocation.objects.filter(donation__code='DON-QUERY-SCALING')
            ),
            lambda allocation: (
                allocation.executed_amount,
                allocation.available_balance,
                allocation.execution_progress_label,
            ),
        )
        self.assert_constant_query_count(
            lambda: with_expense_list_support(
                Expense.objects.filter(reason__startswith='Query expense')
            ),
            lambda expense: expense.annotated_has_support,
        )
        self.assert_constant_query_count(
            lambda: with_project_update_attachment_count(
                ProjectUpdate.objects.filter(title__startswith='Query update')
            ),
            lambda update: update.annotated_attachment_count,
        )
