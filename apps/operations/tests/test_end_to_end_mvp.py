import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    ExpenseRequest,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
    SupportingDocument,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import TEST_DATE, create_support_upload


class EndToEndMVPFlowTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        cache.clear()
        sync_operation_roles()
        self.admin = self._role_user('admin-e2e', ROLE_SIGEDON_ADMIN)
        self.operator = self._role_user('operador-e2e', ROLE_FIELD_OPERATOR)
        self.committee = self._role_user('committee-e2e', ROLE_PROJECT_COMMITTEE)
        self.client.force_login(self.admin)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)
        cache.clear()

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

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
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertFalse(project.is_public)

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

        available_before_request = allocation.available_balance
        self.client.force_login(self.operator)
        request_response = self.client.post(
            reverse('expense_request_create_for_project', args=[project.pk]),
            data={
                'fund_allocation': str(allocation.pk),
                'requested_amount': '120.10',
                'purpose': 'Compra de alimentos para el flujo E2E',
                'requested_date': TEST_DATE.isoformat(),
            },
        )
        expense_request = ExpenseRequest.objects.get(
            purpose='Compra de alimentos para el flujo E2E',
        )
        self.assertEqual(request_response.status_code, 302)
        self.assertEqual(
            request_response['Location'],
            reverse('expense_request_detail', args=[expense_request.pk]),
        )
        self.assertRegex(expense_request.code, r'^SGS-\d+')
        self.assertEqual(expense_request.status, ExpenseRequest.Status.PENDING_DECISION)
        self.assertEqual(expense_request.requested_amount, Decimal('120.10'))
        self.assertEqual(expense_request.requested_by_id, self.operator.pk)
        self.assertIsNone(expense_request.reserved_amount)
        allocation.refresh_from_db()
        self.assertEqual(allocation.available_balance, available_before_request)

        self.client.force_login(self.committee)
        approve_response = self.client.post(
            reverse('expense_request_approve', args=[expense_request.pk]),
            data={'decision_note': 'Aprobación E2E del Comité.'},
        )
        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(
            approve_response['Location'],
            reverse('expense_request_detail', args=[expense_request.pk]),
        )
        expense_request.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(expense_request.status, ExpenseRequest.Status.APPROVED_RESERVED)
        self.assertEqual(expense_request.reserved_amount, Decimal('120.10'))
        self.assertEqual(expense_request.decided_by_id, self.committee.pk)
        available_after_reservation = available_before_request - Decimal('120.10')
        self.assertEqual(allocation.available_balance, available_after_reservation)

        expenses_before = Expense.objects.count()
        docs_before = SupportingDocument.objects.count()
        self.client.force_login(self.admin)
        fulfill_response = self.client.post(
            reverse('expense_request_fulfill', args=[expense_request.pk]),
            data={
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '120.10',
                'reason': 'Compra de alimentos',
                'provider_or_recipient': 'Proveedor local',
                'payment_method': 'bank_transfer',
                'description': 'Compra operativa.',
                'observations': '',
                'support_title': 'Factura de alimentos',
                'support_notes': '',
                'support_file': create_support_upload(
                    'factura.pdf',
                    content=b'factura',
                ),
            },
        )
        self.assertEqual(fulfill_response.status_code, 302)
        self.assertEqual(
            fulfill_response['Location'],
            reverse('expense_request_detail', args=[expense_request.pk]),
        )
        expense_request.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(expense_request.status, ExpenseRequest.Status.FULFILLED)
        self.assertIsNotNone(expense_request.expense_id)
        self.assertEqual(Expense.objects.count(), expenses_before + 1)
        self.assertEqual(SupportingDocument.objects.count(), docs_before + 1)
        expense = Expense.objects.get(pk=expense_request.expense_id)
        self.assertEqual(expense.allocation_id, allocation.pk)
        self.assertEqual(expense.amount, Decimal('120.10'))
        self.assertEqual(expense.supporting_documents.count(), 1)
        self.assertEqual(allocation.available_balance, available_after_reservation)
        self.assertEqual(allocation.executed_amount, Decimal('120.10'))

        direct_create = self.client.get(reverse('expense_create'))
        self.assertEqual(direct_create.status_code, 302)
        self.assertIn(reverse('expense_request_list'), direct_create['Location'])
        self.assertIn('status=approved_reserved', direct_create['Location'])
        self.assertEqual(Expense.objects.count(), expenses_before + 1)

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

        list_response = self.client.get(reverse('expense_list'))
        detail_response = self.client.get(reverse('expense_detail', args=[expense.pk]))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, expense.code)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, expense.code)

        approved_update_response = self.client.post(
            reverse('project_update_create_for_project', args=[project.pk]),
            data={
                'title': 'Entrega aprobada E2E',
                'description': 'Se completó una entrega verificable.',
                'update_date': '2026-07-12',
                'reported_by': self.admin.pk,
            },
        )
        self.assertRedirects(approved_update_response, reverse('project_detail', args=[project.pk]))
        approved_update = ProjectUpdate.objects.get(title='Entrega aprobada E2E')

        pending_update_response = self.client.post(
            reverse('project_update_create_for_project', args=[project.pk]),
            data={
                'title': 'Entrega pendiente E2E',
                'description': 'Aún no debe publicarse.',
                'reported_by': self.admin.pk,
                'update_date': '2026-07-12',
            },
        )
        self.assertRedirects(pending_update_response, reverse('project_detail', args=[project.pk]))
        pending_update = ProjectUpdate.objects.get(title='Entrega pendiente E2E')

        review_response = self.client.post(reverse('project_update_publish', args=[approved_update.pk]))
        self.assertRedirects(review_response, reverse('project_update_detail', args=[approved_update.pk]))
        approved_update.refresh_from_db()
        self.assertEqual(approved_update.status, ProjectUpdate.Status.PUBLISHED)
        self.assertEqual(pending_update.status, ProjectUpdate.Status.UNPUBLISHED)

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
        project.is_public = True
        project.save(update_fields=['is_public'])
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
