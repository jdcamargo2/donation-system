"""Focused tests for the full-role SIGEDON demo seed (DEMO-E2E-1)."""

from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.demo_seed import (
    DEMO_ER_PURPOSE,
    DEMO_FORBIDDEN_SUBSTRINGS,
    DEMO_INSTITUTION_COUNTRY,
    DEMO_UPDATE_TITLE,
    collect_demo_counts,
    verify_sigedon_demo,
)
from apps.operations.models import (
    Donation,
    Expense,
    ExpenseRequest,
    ExpenseRequestEvent,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
    ProjectUpdateRemediation,
    ProjectUpdateReview,
    SupportingDocument,
)
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import create_institution, create_project


@override_settings(DEBUG=True)
class SeedSigedonDemoCommandTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def run_seed(self, **extra):
        stdout = StringIO()
        secret = extra.pop('password', 'demo-test-12345')
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': secret}):
            call_command(
                'seed_sigedon_demo',
                password=secret,
                stdout=stdout,
                **extra,
            )
        return stdout.getvalue()

    def test_seed_command_creates_demo_operational_data(self):
        self.run_seed()

        self.assertTrue(Project.objects.filter(code='PRJ-DEMO-001').exists())
        self.assertTrue(Donation.objects.filter(code='DON-DEMO-001').exists())
        self.assertTrue(
            ProjectUpdate.objects.filter(status=ProjectUpdate.Status.PUBLISHED).exists()
        )
        self.assertTrue(
            ProjectUpdate.objects.filter(status=ProjectUpdate.Status.UNPUBLISHED).exists()
        )
        self.assertFalse(Donation.objects.exclude(currency='USD').exists())
        self.assertFalse(Expense.objects.exclude(currency='USD').exists())

    def test_seed_command_creates_all_four_role_demo_users(self):
        output = self.run_seed()

        expected_users = {
            'admin_demo': ROLE_SIGEDON_ADMIN,
            'operador_demo': ROLE_FIELD_OPERATOR,
            'auditor_demo': ROLE_EXTERNAL_AUDITOR,
            'comite_demo': ROLE_PROJECT_COMMITTEE,
        }
        for username, role_name in expected_users.items():
            with self.subTest(username=username):
                user = get_user_model().objects.get(username=username)
                self.assertTrue(user.groups.filter(name=role_name).exists())
                self.assertTrue(Group.objects.filter(name=role_name).exists())
                self.assertEqual(user.groups.filter(name__in=expected_users.values()).count(), 1)
                self.assertIn(username, output)

        committee = get_user_model().objects.get(username='comite_demo')
        self.assertFalse(committee.is_staff)
        self.assertFalse(committee.is_superuser)
        self.assertTrue(committee.is_active)

    def test_seed_command_is_idempotent_for_key_entities(self):
        self.run_seed()
        first_counts = collect_demo_counts()
        first_events = ExpenseRequestEvent.objects.filter(
            expense_request__purpose__in=DEMO_ER_PURPOSE.values()
        ).count()
        first_balances = {
            code: Donation.objects.get(code=code).available_balance
            for code in ('DON-DEMO-001', 'DON-DEMO-003', 'DON-DEMO-004')
        }

        self.run_seed()
        second_counts = collect_demo_counts()
        second_events = ExpenseRequestEvent.objects.filter(
            expense_request__purpose__in=DEMO_ER_PURPOSE.values()
        ).count()
        second_balances = {
            code: Donation.objects.get(code=code).available_balance
            for code in ('DON-DEMO-001', 'DON-DEMO-003', 'DON-DEMO-004')
        }

        self.assertEqual(first_counts, second_counts)
        self.assertEqual(first_events, second_events)
        self.assertEqual(first_balances, second_balances)
        self.assertEqual(Project.objects.filter(code='PRJ-DEMO-001').count(), 1)
        self.assertEqual(Donation.objects.filter(code='DON-DEMO-001').count(), 1)
        self.assertEqual(
            ProjectUpdate.objects.filter(title=DEMO_UPDATE_TITLE['draft']).count(),
            1,
        )
        for purpose in DEMO_ER_PURPOSE.values():
            self.assertEqual(ExpenseRequest.objects.filter(purpose=purpose).count(), 1)

    def test_seed_operator_created_updates_self_report(self):
        self.run_seed()

        operator = get_user_model().objects.get(username='operador_demo')
        operator_updates = ProjectUpdate.objects.filter(created_by=operator)
        self.assertTrue(operator_updates.exists())
        for update in operator_updates:
            with self.subTest(title=update.title):
                self.assertEqual(update.created_by, operator)
                self.assertEqual(update.reported_by, operator)

    def test_password_never_printed_and_override_works(self):
        secret = 'local-only-secret-never-print'
        output = self.run_seed(password=secret)
        self.assertIn('Demo credentials configured.', output)
        self.assertNotIn(secret, output)
        user = get_user_model().objects.get(username='admin_demo')
        self.assertTrue(user.check_password(secret))

    def test_env_password_is_used_when_cli_password_omitted(self):
        secret = 'env-only-secret-never-print'
        stdout = StringIO()
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': secret}):
            call_command('seed_sigedon_demo', stdout=stdout)
        output = stdout.getvalue()
        self.assertIn('Demo credentials configured.', output)
        self.assertNotIn(secret, output)
        user = get_user_model().objects.get(username='admin_demo')
        self.assertTrue(user.check_password(secret))

    def test_cli_password_overrides_env_password(self):
        cli_secret = 'cli-secret-never-print'
        env_secret = 'env-secret-never-print'
        stdout = StringIO()
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': env_secret}):
            call_command(
                'seed_sigedon_demo',
                password=cli_secret,
                stdout=stdout,
            )
        output = stdout.getvalue()
        self.assertNotIn(cli_secret, output)
        self.assertNotIn(env_secret, output)
        user = get_user_model().objects.get(username='admin_demo')
        self.assertTrue(user.check_password(cli_secret))
        self.assertFalse(user.check_password(env_secret))

    def test_missing_password_is_refused_before_mutation(self):
        stdout = StringIO()
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': ''}):
            with self.assertRaisesMessage(
                CommandError,
                'Falta la contraseña demo. Use --password o defina SIGEDON_DEMO_PASSWORD.',
            ):
                call_command('seed_sigedon_demo', stdout=stdout)
        self.assertEqual(stdout.getvalue(), '')
        self.assertFalse(Project.objects.filter(code='PRJ-DEMO-001').exists())

    def test_empty_password_refused(self):
        with patch.dict('os.environ', {'SIGEDON_DEMO_PASSWORD': 'env-should-not-be-used'}):
            with self.assertRaises(CommandError):
                call_command('seed_sigedon_demo', password='   ', stdout=StringIO())

    def test_demo_matrix_covers_institutions_projects_donations(self):
        self.run_seed()

        self.assertTrue(
            Institution.objects.filter(role=Institution.Role.DONOR, status='active').exists()
        )
        self.assertTrue(
            Institution.objects.filter(role=Institution.Role.ALLY, status='active').exists()
        )
        self.assertTrue(
            Institution.objects.filter(
                role=Institution.Role.SUPERVISOR,
                status='active',
            ).exists()
        )
        self.assertTrue(
            Institution.objects.filter(status=Institution.Status.INACTIVE).exists()
        )

        expected_institutions = {
            'Agencia Humanitaria Delta',
            'Fundación Horizonte',
            'Red Comunitaria Aurora',
            'Observatorio Cívico Monteluz',
            'Asociación Comunitaria Río Claro',
            'Centro de Innovación Archimango',
        }
        self.assertEqual(
            set(
                Institution.objects.filter(name__in=expected_institutions).values_list(
                    'name', flat=True
                )
            ),
            expected_institutions,
        )
        for institution in Institution.objects.filter(name__in=expected_institutions):
            with self.subTest(institution=institution.name):
                self.assertEqual(institution.country.code, DEMO_INSTITUTION_COUNTRY)
                self.assertEqual(institution.country.code, 'ZZ')
                self.assertEqual(institution.country.name, 'República de Monteluz')

        expected_projects = {
            'PRJ-DEMO-001': 'Centro Comunitario Aurora',
            'PRJ-DEMO-002': 'Sistema de Agua Río Claro',
            'PRJ-DEMO-003': 'Rehabilitación Escuela Horizonte',
            'PRJ-DEMO-004': 'Red de Atención Comunitaria Norte',
            'PRJ-DEMO-005': 'Laboratorio Archimango',
            'PRJ-DEMO-006': 'Proyecto DEMO sin actividad financiera',
            'PRJ-DEMO-007': 'Programa Comunitario Finalizado',
        }
        for code, name in expected_projects.items():
            with self.subTest(code=code):
                project = Project.objects.get(code=code)
                self.assertEqual(project.name, name)
                self.assertIn('Monteluz', project.location)
                self.assertNotIn('Guaira', project.location)
                self.assertNotIn('Catia', project.name)

        public_project = Project.objects.get(code='PRJ-DEMO-001')
        self.assertEqual(public_project.location, 'Aurora, Valle Sereno, República de Monteluz')

        self.assertTrue(
            Project.objects.filter(code='PRJ-DEMO-001', is_public=True, status='active').exists()
        )
        self.assertTrue(
            Project.objects.filter(code='PRJ-DEMO-002', is_public=False).exists()
        )
        self.assertFalse(
            FundAllocation.objects.filter(project__code='PRJ-DEMO-006').exists()
        )
        self.assertTrue(
            Project.objects.filter(code='PRJ-DEMO-007', status=Project.Status.CLOSED).exists()
        )

        self.assertEqual(
            Donation.objects.get(code='DON-DEMO-002').status,
            Donation.Status.REGISTERED,
        )
        self.assertIsNone(Donation.objects.get(code='DON-DEMO-002').received_date)
        self.assertEqual(
            Donation.objects.get(code='DON-DEMO-001').status,
            Donation.Status.RECEIVED,
        )
        self.assertGreater(
            Donation.objects.get(code='DON-DEMO-003').available_balance,
            Decimal('0.00'),
        )
        self.assertEqual(
            Donation.objects.get(code='DON-DEMO-004').allocation_progress,
            'fully_allocated',
        )

        donation = Donation.objects.get(code='DON-DEMO-001')
        self.assertIn('Valle Sereno', donation.objective)
        self.assertIn('República de Monteluz', donation.objective)
        self.assertNotIn('La Guaira', donation.objective)
        self.assertNotIn('Núcleo Vital', donation.objective)

    def test_demo_visible_copy_stays_inside_monteluz_universe(self):
        self.run_seed()

        self.assertEqual(verify_sigedon_demo(), [])

        for institution in Institution.objects.filter(
            name__in={
                'Agencia Humanitaria Delta',
                'Fundación Horizonte',
                'Red Comunitaria Aurora',
                'Observatorio Cívico Monteluz',
                'Asociación Comunitaria Río Claro',
                'Centro de Innovación Archimango',
            }
        ):
            with self.subTest(institution=institution.name):
                email = institution.contact_email.lower()
                self.assertTrue(
                    email.endswith('@sigedon.local') or email.endswith('.example.invalid')
                )
                self.assertTrue(institution.contact_phone.startswith('+000 555 01'))
                blob = ' '.join(
                    [
                        institution.name,
                        institution.contact_email,
                        institution.contact_phone,
                        institution.responsible_person,
                    ]
                ).casefold()
                for forbidden in DEMO_FORBIDDEN_SUBSTRINGS:
                    self.assertNotIn(forbidden, blob)

        for expense in Expense.objects.filter(allocation__donation__code__startswith='DON-DEMO-'):
            with self.subTest(expense=expense.code):
                blob = ' '.join(
                    [
                        expense.reason,
                        expense.provider_or_recipient,
                        expense.description,
                    ]
                ).casefold()
                for forbidden in DEMO_FORBIDDEN_SUBSTRINGS:
                    self.assertNotIn(forbidden, blob)

        operator = get_user_model().objects.get(username='operador_demo')
        self.assertEqual(operator.first_name, 'Mateo')
        self.assertEqual(operator.last_name, 'Solano')
        self.assertTrue(operator.email.endswith('@sigedon.local'))

    def test_seed_institution_list_and_detail_render_monteluz_not_venezuela(self):
        self.run_seed()
        admin = get_user_model().objects.get(username='admin_demo')
        self.client.force_login(admin)
        institution = Institution.objects.get(name='Agencia Humanitaria Delta')

        list_response = self.client.get(reverse('institution_list'))
        detail_response = self.client.get(
            reverse('institution_detail', args=[institution.pk])
        )

        for response in (list_response, detail_response):
            with self.subTest(path=response.request['PATH_INFO']):
                self.assertContains(response, 'República de Monteluz')
                self.assertNotContains(response, 'Venezuela')
                self.assertNotContains(response, '>ZZ<')

    def test_expense_request_scenarios_and_events(self):
        self.run_seed()

        expected = {
            'pending': ExpenseRequest.Status.PENDING_DECISION,
            'approved': ExpenseRequest.Status.APPROVED_RESERVED,
            'denied': ExpenseRequest.Status.DENIED,
            'withdrawn': ExpenseRequest.Status.WITHDRAWN,
            'fulfilled': ExpenseRequest.Status.FULFILLED,
            'annulled': ExpenseRequest.Status.ANNULLED,
        }
        for key, status in expected.items():
            with self.subTest(key=key):
                req = ExpenseRequest.objects.get(purpose=DEMO_ER_PURPOSE[key])
                self.assertEqual(req.status, status)
                self.assertGreaterEqual(
                    ExpenseRequestEvent.objects.filter(expense_request=req).count(),
                    1,
                )
                self.assertEqual(req.requested_by.username, 'operador_demo')

        fulfilled = ExpenseRequest.objects.get(purpose=DEMO_ER_PURPOSE['fulfilled'])
        self.assertIsNotNone(fulfilled.expense_id)
        self.assertTrue(
            SupportingDocument.objects.filter(expense_id=fulfilled.expense_id).exists()
        )

    def test_project_update_scenarios(self):
        self.run_seed()

        draft = ProjectUpdate.objects.get(title=DEMO_UPDATE_TITLE['draft'])
        self.assertEqual(draft.status, ProjectUpdate.Status.UNPUBLISHED)

        published = ProjectUpdate.objects.get(title=DEMO_UPDATE_TITLE['published_public'])
        self.assertEqual(published.status, ProjectUpdate.Status.PUBLISHED)
        self.assertTrue(published.attachments.exists())
        self.assertTrue(published.project.is_public)

        conforming = ProjectUpdate.objects.get(title=DEMO_UPDATE_TITLE['reviewed_ok'])
        review = ProjectUpdateReview.objects.get(project_update=conforming)
        self.assertEqual(review.decision.outcome, 'conforming')

        observed = ProjectUpdate.objects.get(title=DEMO_UPDATE_TITLE['observed'])
        observed_review = ProjectUpdateReview.objects.get(project_update=observed)
        self.assertEqual(observed_review.decision.outcome, 'observed')
        self.assertTrue(
            ProjectUpdateRemediation.objects.filter(decision=observed_review.decision).exists()
        )

    def test_allocations_and_expenses_respect_balances(self):
        self.run_seed()

        for donation in Donation.objects.filter(code__startswith='DON-DEMO-'):
            with self.subTest(code=donation.code):
                self.assertGreaterEqual(donation.available_balance, Decimal('0.00'))
                assigned = donation.total_assigned
                self.assertLessEqual(assigned, donation.amount)

        for allocation in FundAllocation.objects.filter(
            donation__code__startswith='DON-DEMO-'
        ):
            with self.subTest(allocation=allocation.code):
                self.assertGreaterEqual(allocation.available_balance, Decimal('0.00'))
                self.assertLessEqual(
                    allocation.executed_amount + allocation.reserved_amount,
                    allocation.amount,
                )

        for expense in Expense.objects.filter(
            allocation__donation__code__startswith='DON-DEMO-'
        ).exclude(status=Expense.Status.ANNULLED):
            with self.subTest(expense=expense.code):
                self.assertTrue(expense.supporting_documents.exists())

    def test_verify_succeeds_after_seed_and_fails_before(self):
        before = verify_sigedon_demo()
        self.assertTrue(before)

        fail_stdout = StringIO()
        fail_stderr = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command(
                'seed_sigedon_demo',
                verify=True,
                stdout=fail_stdout,
                stderr=fail_stderr,
            )
        fail_combined = fail_stdout.getvalue() + fail_stderr.getvalue() + str(raised.exception)
        self.assertIn('La verificación de la demo falló:', fail_combined)
        self.assertNotIn('Demo verification', fail_combined)
        self.assertNotIn('demo-test-12345', fail_combined)
        self.assertNotIn('SIGEDON_DEMO_PASSWORD', fail_combined)

        self.run_seed()
        problems = verify_sigedon_demo()
        self.assertEqual(problems, [])

        stdout = StringIO()
        call_command('seed_sigedon_demo', verify=True, stdout=stdout)
        success_output = stdout.getvalue()
        self.assertIn('Verificación de la demo correcta.', success_output)
        self.assertNotIn('Demo verification', success_output)
        self.assertNotIn('demo-test-12345', success_output)

    def test_verify_performs_zero_writes(self):
        self.run_seed()
        before = collect_demo_counts()
        call_command('seed_sigedon_demo', verify=True, stdout=StringIO())
        self.assertEqual(collect_demo_counts(), before)

    def test_seed_preserves_non_demo_records(self):
        foreign_institution = create_institution(name='Institución No Demo')
        foreign_project = create_project(code='PRJ-KEEP-001', name='Proyecto no demo')

        self.run_seed()
        self.run_seed()

        self.assertTrue(
            Institution.objects.filter(pk=foreign_institution.pk, name='Institución No Demo').exists()
        )
        self.assertTrue(
            Project.objects.filter(pk=foreign_project.pk, code='PRJ-KEEP-001').exists()
        )


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
class SeedSigedonDemoVerifyGuardTests(TestCase):
    def test_verify_also_refuses_when_debug_false(self):
        with self.assertRaisesMessage(
            CommandError,
            'seed_sigedon_demo is disabled when DEBUG=False.',
        ):
            call_command('seed_sigedon_demo', verify=True, stdout=StringIO())
