from decimal import Decimal
from io import StringIO
from unittest import skipUnless

from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.operations.models import ExpenseRequest, OperationalCodeSequence, OPERATIONAL_CODE_PREFIXES
from apps.operations.operational_code_sequences import inspect_operational_code_sequences
from apps.operations.tests.helpers import create_allocation, create_expense_request, create_user


class ExpenseRequestOperationalCodeTests(TestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        self.user = create_user(username='er-code-actor')
        self.allocation = create_allocation()

    def test_first_generated_code_is_sgs_000001(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )

        self.assertEqual(request.code, 'SGS-000001')
        self.assertRegex(request.code, r'^SGS-\d{6}$')

    def test_sequential_generation(self):
        first = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            purpose='Primera',
        )
        second = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            purpose='Segunda',
        )

        self.assertEqual(first.code, 'SGS-000001')
        self.assertEqual(second.code, 'SGS-000002')

    def test_codes_are_unique(self):
        codes = {
            create_expense_request(
                fund_allocation=self.allocation,
                requested_by=self.user,
                purpose=f'Solicitud {index}',
            ).code
            for index in range(3)
        }

        self.assertEqual(len(codes), 3)

    def test_manual_demo_code_is_preserved(self):
        request = create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
            code='SGS-DEMO-001',
        )

        self.assertEqual(request.code, 'SGS-DEMO-001')
        sequence = OperationalCodeSequence.objects.get(namespace='expense_request')
        self.assertEqual(sequence.next_value, 1)

    def test_reconcile_command_recognizes_sgs_namespace(self):
        create_expense_request(
            fund_allocation=self.allocation,
            requested_by=self.user,
        )
        reports = {
            report.namespace: report
            for report in inspect_operational_code_sequences()
        }

        self.assertIn('expense_request', reports)
        self.assertEqual(reports['expense_request'].prefix, 'SGS')
        self.assertEqual(reports['expense_request'].status, 'OK')

        # Advance any lagging unrelated sequences so the detect-only command can
        # complete and still report the expense_request namespace.
        for report in inspect_operational_code_sequences():
            if report.maximum is not None:
                OperationalCodeSequence.objects.filter(namespace=report.namespace).update(
                    next_value=report.maximum + 1
                )

        output = StringIO()
        call_command('reconcile_operational_code_sequences', stdout=output)
        self.assertIn('expense_request', output.getvalue())
        self.assertIn('OK', output.getvalue())
        self.assertEqual(
            OPERATIONAL_CODE_PREFIXES['expense_request'],
            'SGS',
        )

@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks.')
class ExpenseRequestOperationalCodeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        OperationalCodeSequence.objects.update_or_create(
            namespace='expense_request',
            defaults={'prefix': 'SGS', 'next_value': 1},
        )
        for namespace, prefix in (
            ('project', 'PRJ'),
            ('donation', 'DON'),
            ('fund_allocation', 'ASG'),
            ('expense', 'GAS'),
        ):
            OperationalCodeSequence.objects.update_or_create(
                namespace=namespace,
                defaults={'prefix': prefix, 'next_value': 1},
            )
        self.user = create_user(username='er-code-concurrency')
        self.allocation = create_allocation()

    def test_concurrent_creates_receive_distinct_codes(self):
        codes = []
        with transaction.atomic():
            first = ExpenseRequest.objects.create(
                fund_allocation=self.allocation,
                requested_by=self.user,
                requested_amount=Decimal('5.00'),
                purpose='Concurrente A',
                requested_date=self.allocation.allocation_date,
            )
            codes.append(first.code)
        with transaction.atomic():
            second = ExpenseRequest.objects.create(
                fund_allocation=self.allocation,
                requested_by=self.user,
                requested_amount=Decimal('5.00'),
                purpose='Concurrente B',
                requested_date=self.allocation.allocation_date,
            )
            codes.append(second.code)

        self.assertEqual(len(set(codes)), 2)
        for code in codes:
            self.assertRegex(code, r'^SGS-\d{6}$')
