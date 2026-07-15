import shutil
import tempfile
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate
from apps.operations.tests.helpers import TEST_DATE, create_user


class EndToEndMVPFlowTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        cache.clear()
        self.user = create_user(username='operador-e2e')
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)
        cache.clear()

    def test_complete_mvp_flow_preserves_balances_audit_and_public_visibility(self):
        institution_response = self.client.post(
            reverse('institution_create'),
            data={
                'name': 'Cáritas E2E',
                'institution_type': 'foundation',
                'role': Institution.Role.DONOR,
                'country': 'VE',
                'contact_email': '',
                'contact_phone': '',
                'responsible_person': 'Coordinación',
                'legal_document': '',
                'status': Institution.Status.ACTIVE,
            },
        )
        self.assertRedirects(institution_response, reverse('institution_list'))
        institution = Institution.objects.get(name='Cáritas E2E')

        project_response = self.client.post(
            reverse('project_create'),
            data={
                'name': 'Atención integral E2E',
                'description': 'Proyecto visible para trazabilidad pública.',
                'objective': 'Atender necesidades humanitarias prioritarias.',
                'responsible_unit': 'Pastoral Social',
                'location': 'Caracas',
                'estimated_budget': '1000.00',
                'start_date': TEST_DATE,
                'end_date': '',
                'status': Project.Status.ACTIVE,
            },
        )
        self.assertRedirects(project_response, reverse('project_list'))
        project = Project.objects.get(name='Atención integral E2E')
        self.assertEqual(project.code, 'PRJ-000001')
        self.assertRedirects(
            self.client.post(
                reverse('project_status_transition', args=[project.pk, Project.Status.ACTIVE])
            ),
            reverse('project_detail', args=[project.pk]),
        )

        donation_response = self.client.post(
            reverse('donation_create'),
            data={
                'donor': institution.pk,
                'donation_type': 'goods',
                'amount': '500.50',
                'currency': 'USD',
                'objective': 'Financiar atención integral.',
                'restrictions': '',
                'commitment_date': TEST_DATE,
                'received_date': TEST_DATE,
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )
        self.assertRedirects(donation_response, reverse('donation_list'))
        donation = Donation.objects.get(donor=institution)
        self.assertEqual(donation.code, 'DON-000001')
        self.assertRedirects(
            self.client.post(
                reverse('donation_status_transition', args=[donation.pk, Donation.Status.RECEIVED])
            ),
            reverse('donation_detail', args=[donation.pk]),
        )

        allocation_response = self.client.post(
            reverse('allocation_create'),
            data={
                'donation': donation.pk,
                'project': project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '300.25',
                'responsible_person': 'Administración',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': 'Asignación inicial.',
            },
        )
        self.assertRedirects(allocation_response, reverse('allocation_list'))
        allocation = FundAllocation.objects.get(donation=donation, project=project)
        self.assertRedirects(
            self.client.post(
                reverse('allocation_status_transition', args=[allocation.pk, FundAllocation.Status.ACTIVE])
            ),
            reverse('allocation_detail', args=[allocation.pk]),
        )

        expense_response = self.client.post(
            reverse('expense_create'),
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '120.10',
                'currency': 'USD',
                'reason': 'Compra de alimentos',
                'provider_or_recipient': 'Proveedor local',
                'payment_method': 'bank_transfer',
                'description': 'Compra operativa.',
                'observations': '',
                'support_title': 'Factura de alimentos',
                'support_file': SimpleUploadedFile('factura.pdf', b'factura', content_type='application/pdf'),
            },
        )
        self.assertRedirects(expense_response, reverse('expense_list'))
        expense = Expense.objects.get(allocation=allocation)

        self.assertEqual(expense.supporting_documents.count(), 1)

        validate_response = self.client.post(
            reverse('expense_update', args=[expense.pk]),
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '120.10',
                'currency': 'USD',
                'reason': 'Compra de alimentos',
                'provider_or_recipient': 'Proveedor local',
                'payment_method': 'bank_transfer',
                'description': 'Compra operativa.',
                'observations': '',
            },
        )
        self.assertRedirects(validate_response, reverse('expense_list'))
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.REGISTERED)
        self.assertTrue(expense.has_required_support())

        approved_update_response = self.client.post(
            reverse('project_update_create_for_project', args=[project.pk]),
            data={
                'title': 'Entrega aprobada E2E',
                'description': 'Se completó una entrega verificable.',
                'update_date': '2026-07-12',
                'progress_percentage': '60',
                'reported_by': self.user.pk,
            },
        )
        self.assertRedirects(approved_update_response, reverse('project_detail', args=[project.pk]))
        approved_update = ProjectUpdate.objects.get(title='Entrega aprobada E2E')

        pending_update_response = self.client.post(
            reverse('project_update_create_for_project', args=[project.pk]),
            data={
                'title': 'Entrega pendiente E2E',
                'description': 'Aún no debe publicarse.',
                'reported_by': self.user.pk,
                'update_date': '2026-07-12',
                'progress_percentage': '45',
            },
        )
        self.assertRedirects(pending_update_response, reverse('project_detail', args=[project.pk]))
        pending_update = ProjectUpdate.objects.get(title='Entrega pendiente E2E')

        review_response = self.client.post(reverse('project_update_publish', args=[approved_update.pk]))
        self.assertRedirects(review_response, reverse('project_update_detail', args=[approved_update.pk]))
        approved_update.refresh_from_db()
        self.assertEqual(approved_update.status, ProjectUpdate.Status.PUBLISHED)
        self.assertEqual(pending_update.status, ProjectUpdate.Status.DRAFT)

        donation.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(donation.total_assigned, Decimal('300.25'))
        self.assertEqual(donation.available_balance, Decimal('200.25'))
        self.assertEqual(allocation.executed_amount, Decimal('120.10'))
        self.assertEqual(allocation.available_balance, Decimal('180.15'))

        dashboard_response = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(dashboard_response.context['total_donations'], Decimal('500.50'))
        self.assertEqual(dashboard_response.context['total_assigned'], Decimal('300.25'))
        self.assertEqual(dashboard_response.context['total_executed'], Decimal('120.10'))
        self.assertEqual(dashboard_response.context['available_balance'], Decimal('200.25'))

        cache.clear()
        public_detail_response = self.client.get(reverse('public_portal:public_project_detail', args=[project.pk]))
        public_feed_response = self.client.get(reverse('public_portal:public_updates_feed'))
        self.assertContains(public_detail_response, approved_update.title)
        self.assertNotContains(public_detail_response, pending_update.title)
        self.assertContains(public_feed_response, approved_update.title)
        self.assertNotContains(public_feed_response, pending_update.title)

        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.CREATED, summary='Donación creada.').exists())
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.ASSIGNED, summary='Asignación de fondos registrada.').exists()
        )
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXECUTED, summary__contains='registrado').exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PUBLISHED, summary__contains='Avance de proyecto publicado.').exists())
