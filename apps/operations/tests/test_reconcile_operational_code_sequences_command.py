from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.operations.models import (
    Donation,
    Expense,
    FundAllocation,
    OPERATIONAL_CODE_PREFIXES,
    OperationalCodeSequence,
    Project,
)
from apps.operations.operational_code_sequences import inspect_operational_code_sequences
from apps.operations.tests.helpers import (
    create_donation,
    create_institution,
    create_project,
)


COMMAND_MODULE = 'apps.operations.management.commands.reconcile_operational_code_sequences'


class ReconcileOperationalCodeSequencesCommandTests(TestCase):
    def setUp(self):
        self.donor = create_institution(name='Donante de reconciliación')
        for namespace, prefix in OPERATIONAL_CODE_PREFIXES.items():
            OperationalCodeSequence.objects.get_or_create(
                namespace=namespace,
                defaults={'prefix': prefix, 'next_value': 1},
            )

    def _set_next_value(self, namespace, value):
        OperationalCodeSequence.objects.filter(namespace=namespace).update(
            next_value=value
        )

    def _canonical_project_maximum(self):
        maximum = None
        for code in Project.objects.values_list('code', flat=True):
            if not code or not code.startswith('PRJ-'):
                continue
            suffix = code.removeprefix('PRJ-')
            if suffix.isdigit() and int(suffix) > 0:
                value = int(suffix)
                if maximum is None or value > maximum:
                    maximum = value
        return maximum

    def _create_canonical_entity(self, namespace, number):
        prefix = OPERATIONAL_CODE_PREFIXES[namespace]
        code = f'{prefix}-{number:06d}'
        if namespace == 'project':
            return Project.objects.create(code=code, name=f'Proyecto {number}')
        if namespace == 'donation':
            return Donation.objects.create(
                code=code,
                donor=self.donor,
                amount='100.00',
                status=Donation.Status.RECEIVED,
            )
        if namespace == 'fund_allocation':
            return FundAllocation.objects.create(
                code=code,
                donation=create_donation(code=f'DON-FUND-{number}', donor=self.donor),
                project=create_project(code=f'PRJ-FUND-{number}'),
                budget_category='health_psychosocial',
                amount='10.00',
                allocation_date='2026-07-15',
                status=FundAllocation.Status.ACTIVE,
            )
        # Dependencias con códigos explícitos no canónicos: no deben alimentar el
        # máximo de fund_allocation ni avanzar su secuencia automática.
        return Expense.objects.create(
            code=code,
            allocation=FundAllocation.objects.create(
                code=f'ASG-EXP-{number}',
                donation=create_donation(code=f'DON-EXP-{number}', donor=self.donor),
                project=create_project(code=f'PRJ-EXP-{number}'),
                budget_category='health_psychosocial',
                amount='10.00',
                allocation_date='2026-07-15',
                status=FundAllocation.Status.ACTIVE,
            ),
            expense_date='2026-07-15',
            category='food',
            amount='1.00',
            reason=f'Gasto {number}',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            status=Expense.Status.REGISTERED,
        )

    def _run_command(self, **options):
        output = StringIO()
        call_command(
            'reconcile_operational_code_sequences',
            stdout=output,
            **options,
        )
        return output.getvalue()

    def _reports_by_namespace(self):
        return {
            report.namespace: report
            for report in inspect_operational_code_sequences()
        }

    def test_empty_database_without_sequences_is_safe(self):
        OperationalCodeSequence.objects.all().delete()

        output = self._run_command()

        self.assertIn('project: OK_EMPTY', output)
        self.assertFalse(OperationalCodeSequence.objects.exists())

    def test_each_namespace_can_be_safely_synchronized(self):
        expected_numbers = {
            'project': 12,
            'donation': 23,
            'fund_allocation': 34,
            'expense': 45,
        }
        for namespace, number in expected_numbers.items():
            with self.subTest(namespace=namespace):
                Expense.objects.all().delete()
                FundAllocation.objects.all().delete()
                Donation.objects.all().delete()
                # Projects are immutable; keep leftover rows and advance their sequence safely.
                leftover_project_maximum = self._canonical_project_maximum()
                for sequence_namespace, prefix in OPERATIONAL_CODE_PREFIXES.items():
                    next_value = 1
                    if sequence_namespace == 'project' and leftover_project_maximum is not None:
                        next_value = leftover_project_maximum + 1
                    OperationalCodeSequence.objects.update_or_create(
                        namespace=sequence_namespace,
                        defaults={'prefix': prefix, 'next_value': next_value},
                    )
                self._create_canonical_entity(namespace, number)
                self._set_next_value(namespace, number + 1)
                if namespace != 'project' and leftover_project_maximum is not None:
                    self._set_next_value('project', leftover_project_maximum + 1)

                output = self._run_command()

                self.assertIn(
                    f'{namespace}: OK | max={number} | next={number + 1}',
                    output,
                )

    def test_missing_sequence_with_canonical_code_fails(self):
        self._create_canonical_entity('project', 120)
        OperationalCodeSequence.objects.filter(namespace='project').delete()

        with self.assertRaisesMessage(CommandError, 'project: MISSING_SEQUENCE'):
            self._run_command()

    def test_lagging_and_equal_sequences_fail(self):
        self._create_canonical_entity('project', 120)
        for next_value in (119, 120):
            with self.subTest(next_value=next_value):
                self._set_next_value('project', next_value)
                with self.assertRaisesMessage(CommandError, 'project: LAGGING_SEQUENCE'):
                    self._run_command()

    def test_invalid_sequence_fails(self):
        OperationalCodeSequence.objects.filter(namespace='project').update(
            next_value=0
        )

        with self.assertRaisesMessage(CommandError, 'project: INVALID_SEQUENCE'):
            self._run_command()

    def test_sequence_with_wrong_prefix_fails(self):
        OperationalCodeSequence.objects.filter(namespace='project').update(
            prefix='XXX'
        )

        with self.assertRaisesMessage(CommandError, 'project: INVALID_SEQUENCE'):
            self._run_command()

    def test_ahead_sequence_is_valid_and_gaps_are_allowed(self):
        self._create_canonical_entity('project', 120)
        self._set_next_value('project', 150)

        output = self._run_command()

        self.assertIn('project: OK | max=120 | next=150', output)

    def test_noncanonical_codes_are_reported_but_ignored_for_safety(self):
        Project.objects.create(code='PRJ-DEMO-001', name='Proyecto demo')
        Project.objects.create(code='PRJ-0', name='Proyecto cero')

        report = self._reports_by_namespace()['project']
        output = self._run_command()

        self.assertEqual(report.canonical, 0)
        self.assertEqual(report.noncanonical, 2)
        self.assertIsNone(report.maximum)
        self.assertIn('project: OK_EMPTY | max=None | next=1 | canonical=0 | noncanonical=2', output)

    def test_extended_and_zero_padded_codes_participate_in_maximum(self):
        Project.objects.create(code='PRJ-000120', name='Proyecto con ceros')
        Project.objects.create(code='PRJ-1000000', name='Proyecto siete dígitos')
        Project.objects.create(code='PRJ-999999999', name='Proyecto manual numérico')
        self._set_next_value('project', 1000000000)

        report = self._reports_by_namespace()['project']

        self.assertEqual(report.canonical, 3)
        self.assertEqual(report.maximum, 999999999)
        self.assertEqual(report.status, 'OK')

    def test_namespaces_remain_independent(self):
        self._create_canonical_entity('project', 42)
        self._create_canonical_entity('donation', 7)
        self._set_next_value('project', 43)
        self._set_next_value('donation', 8)

        reports = self._reports_by_namespace()

        self.assertEqual(reports['project'].maximum, 42)
        self.assertEqual(reports['donation'].maximum, 7)
        self.assertIsNone(reports['expense'].maximum)
        self.assertEqual(reports['expense'].next_value, 1)

    def test_command_respects_database_option_and_does_not_modify_rows(self):
        self._create_canonical_entity('project', 9)
        self._set_next_value('project', 10)
        before_sequences = list(
            OperationalCodeSequence.objects.order_by('namespace').values_list(
                'namespace', 'prefix', 'next_value'
            )
        )
        before_codes = list(Project.objects.values_list('pk', 'code'))

        with patch(
            f'{COMMAND_MODULE}.inspect_operational_code_sequences',
            wraps=inspect_operational_code_sequences,
        ) as inspect:
            self._run_command(database='default')

        self.assertEqual(inspect.call_args.kwargs['using'], 'default')
        self.assertEqual(
            list(
                OperationalCodeSequence.objects.order_by('namespace').values_list(
                    'namespace', 'prefix', 'next_value'
                )
            ),
            before_sequences,
        )
        self.assertEqual(list(Project.objects.values_list('pk', 'code')), before_codes)
