from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.operations.admin import DonationAdmin, ExpenseAdmin, FundAllocationAdmin, ProjectAdmin
from apps.operations.forms import DonationForm, ExpenseForm, FundAllocationForm, ProjectForm
from apps.operations.models import (
    Donation,
    Expense,
    FundAllocation,
    OperationalCodeSequence,
    Project,
    reserve_operational_code,
)
from apps.operations.services import create_expense, create_fund_allocation
from apps.operations.tests.helpers import TEST_DATE, create_donation, create_institution, create_project


class OperationalCodeTests(TestCase):
    def setUp(self):
        self.donor = create_institution()

    def test_all_entities_receive_unique_six_digit_codes(self):
        projects = [
            Project.objects.create(name=f'Proyecto {index}', status=Project.Status.ACTIVE)
            for index in range(2)
        ]
        donations = [
            Donation.objects.create(
                donor=self.donor,
                amount=Decimal('100.00'),
                status=Donation.Status.RECEIVED,
            )
            for _index in range(2)
        ]
        allocations = [
            create_fund_allocation(
                donation=donations[0],
                project=projects[index],
                budget_category='health_psychosocial',
                amount=Decimal('20.00'),
                responsible_person='',
                allocation_date=TEST_DATE,
                status=FundAllocation.Status.ACTIVE,
                notes='',
            )
            for index in range(2)
        ]
        expenses = [
            create_expense(
                allocation=allocations[0],
                expense_date=TEST_DATE,
                category='food',
                amount=Decimal('5.00'),
                reason=f'Gasto {index}',
                provider_or_recipient='Proveedor',
                payment_method='bank_transfer',
                description='',
                observations='',
                support_file=SimpleUploadedFile(f'gasto-{index}.pdf', b'%PDF soporte'),
            )
            for index in range(2)
        ]

        for objects, prefix in (
            (projects, 'PRJ'),
            (donations, 'DON'),
            (allocations, 'ASG'),
            (expenses, 'GAS'),
        ):
            codes = [instance.code for instance in objects]
            self.assertEqual(len(codes), len(set(codes)))
            for code in codes:
                self.assertRegex(code, rf'^{prefix}-\d{{6}}$')

    def test_codes_are_immutable_and_absent_from_operational_forms(self):
        project = Project.objects.create(name='Código inmutable')
        original_code = project.code
        project.code = 'PRJ-999999'

        with self.assertRaises(ValidationError):
            project.save()
        project.refresh_from_db()
        self.assertEqual(project.code, original_code)

        for form_class in (ProjectForm, DonationForm, FundAllocationForm, ExpenseForm):
            self.assertNotIn('code', form_class().fields)

    def test_admin_exposes_every_code_as_readonly(self):
        for admin_class, model in (
            (ProjectAdmin, Project),
            (DonationAdmin, Donation),
            (FundAllocationAdmin, FundAllocation),
            (ExpenseAdmin, Expense),
        ):
            model_admin = admin_class(model, admin.site)
            self.assertIn('code', model_admin.get_readonly_fields(request=None))

    def test_reservation_rolls_back_with_its_transaction(self):
        before = OperationalCodeSequence.objects.get(namespace='project').next_value

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                reserve_operational_code(namespace='project', prefix='PRJ')
                raise RuntimeError('Fallo posterior a la reserva.')

        self.assertEqual(
            OperationalCodeSequence.objects.get(namespace='project').next_value,
            before,
        )

    def test_reservation_rejects_invalid_namespace_and_prefix(self):
        with transaction.atomic(), self.assertRaises(ValidationError):
            reserve_operational_code(namespace='unknown', prefix='UNK')
        with transaction.atomic(), self.assertRaises(ValidationError):
            reserve_operational_code(namespace='project', prefix='DON')


class OperationalCodeMigrationTests(TransactionTestCase):
    migrate_from = ('operations', '0006_monetary_row_constraints')
    migrate_to = ('operations', '0007_operational_codes')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(transaction.get_connection())
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Institution = old_apps.get_model('operations', 'Institution')
        OldProject = old_apps.get_model('operations', 'Project')
        OldDonation = old_apps.get_model('operations', 'Donation')
        OldAllocation = old_apps.get_model('operations', 'FundAllocation')
        OldExpense = old_apps.get_model('operations', 'Expense')

        donor = Institution.objects.create(
            name='Donante migración',
            institution_type='foundation',
            role='donor',
            country='VE',
        )
        project = OldProject.objects.create(code='PRJ-000010', name='Proyecto existente')
        OldProject.objects.create(code='PRJ-DEMO-001', name='Proyecto demo')
        donation = OldDonation.objects.create(
            code='DON-000007',
            donor=donor,
            amount=Decimal('100.00'),
            currency='USD',
        )
        allocation = OldAllocation.objects.create(
            donation=donation,
            project=project,
            budget_category='health_psychosocial',
            amount=Decimal('50.00'),
            allocation_date=TEST_DATE,
        )
        OldExpense.objects.create(
            allocation=allocation,
            expense_date=TEST_DATE,
            category='food',
            amount=Decimal('10.00'),
            currency='USD',
            reason='Gasto existente',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
        )

        executor = MigrationExecutor(transaction.get_connection())
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(transaction.get_connection()).migrate([self.migrate_to])
        super().tearDown()

    def test_migration_preserves_and_backfills_codes_and_sequences(self):
        MigratedProject = self.apps.get_model('operations', 'Project')
        MigratedDonation = self.apps.get_model('operations', 'Donation')
        MigratedAllocation = self.apps.get_model('operations', 'FundAllocation')
        MigratedExpense = self.apps.get_model('operations', 'Expense')
        Sequence = self.apps.get_model('operations', 'OperationalCodeSequence')

        self.assertTrue(MigratedProject.objects.filter(code='PRJ-000010').exists())
        self.assertTrue(MigratedProject.objects.filter(code='PRJ-DEMO-001').exists())
        self.assertTrue(MigratedDonation.objects.filter(code='DON-000007').exists())
        self.assertEqual(MigratedAllocation.objects.get().code, 'ASG-000001')
        self.assertEqual(MigratedExpense.objects.get().code, 'GAS-000001')
        self.assertEqual(Sequence.objects.get(namespace='project').next_value, 11)
        self.assertEqual(Sequence.objects.get(namespace='donation').next_value, 8)
