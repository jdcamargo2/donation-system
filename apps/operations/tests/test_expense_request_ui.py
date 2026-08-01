"""Expense Request read-only UI and sidebar tests (ER3A)."""

import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.expense_request_services import (
    approve_expense_request,
    create_expense_request,
    fulfill_expense_request,
)
from apps.operations.models import ExpenseRequestAttachment, ExpenseRequestEvent
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import TEST_DATE, create_allocation
from apps.operations.tests.test_permissions import create_user_with_permissions


MUTATION_LABELS = (
    'Nueva solicitud',
    'Editar',
    'Retirar',
    'Aprobar',
    'Denegar',
    'Anular solicitud',
    'Registrar gasto',
    'Agregar adjunto',
    'Eliminar adjunto',
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExpenseRequestUITests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.allocation = create_allocation(amount=Decimal('400.00'))
        self.admin = self._user('er3a-ui-admin', ROLE_SIGEDON_ADMIN)
        self.operator = self._user('er3a-ui-operator', ROLE_FIELD_OPERATOR)
        self.committee = self._user('er3a-ui-committee', ROLE_PROJECT_COMMITTEE)
        self.auditor = self._user('er3a-ui-auditor', ROLE_EXTERNAL_AUDITOR)
        self.request_obj = create_expense_request(
            fund_allocation=self.allocation,
            requested_amount=Decimal('45.00'),
            purpose='Solicitud UI de prueba',
            requested_date=TEST_DATE,
            actor=self.operator,
        )

    def _user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def _assert_read_only(self, response):
        html = response.content.decode()
        for label in MUTATION_LABELS:
            self.assertNotIn(label, html)
        self.assertNotIn('expense_request_create', html)
        self.assertNotIn('expense_request_update', html)
        self.assertNotIn('expense_request_withdraw', html)
        self.assertNotIn('expense_request_approve', html)
        self.assertNotIn('expense_request_deny', html)
        self.assertNotIn('expense_request_annul', html)
        self.assertNotIn('expense_request_fulfill', html)

    def test_list_and_detail_are_read_only_for_all_roles(self):
        roles = (self.admin, self.operator, self.committee, self.auditor)
        for user in roles:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                list_response = self.client.get(reverse('expense_request_list'))
                if list_response.status_code == 302:
                    list_response = self.client.get(list_response['Location'])
                self.assertEqual(list_response.status_code, 200)
                self._assert_read_only(list_response)

                detail_response = self.client.get(
                    reverse('expense_request_detail', args=[self.request_obj.pk])
                )
                self.assertEqual(detail_response.status_code, 200)
                self._assert_read_only(detail_response)

    def test_attachments_render_metadata_without_file_url(self):
        attachment = ExpenseRequestAttachment.objects.create(
            expense_request=self.request_obj,
            title='Cotización',
            notes='Nota de adjunto',
            uploaded_by=self.operator,
            file=SimpleUploadedFile(
                'cotizacion.pdf',
                b'%PDF-1.4',
                content_type='application/pdf',
            ),
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_detail', args=[self.request_obj.pk])
        )
        self.assertContains(response, 'Cotización')
        self.assertContains(response, 'Nota de adjunto')
        self.assertContains(response, 'cotizacion.pdf')
        self.assertNotContains(response, attachment.file.url)
        self.assertNotContains(response, '/media/')

    def test_timeline_renders_labels_without_raw_metadata(self):
        event = ExpenseRequestEvent.objects.filter(
            expense_request=self.request_obj
        ).earliest('created_at', 'pk')
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_detail', args=[self.request_obj.pk])
        )
        self.assertContains(response, event.get_event_type_display())
        self.assertContains(response, 'Historial de eventos')
        self.assertNotContains(response, '"allocation_code"')
        self.assertNotContains(response, str(event.metadata))

    def test_sidebar_visible_with_permission_and_hidden_without(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'title="Solicitudes de gasto"')
        self.assertContains(response, reverse('expense_request_list'))

        no_perm = create_user_with_permissions('er3a-no-er-nav', 'view_project')
        self.client.force_login(no_perm)
        denied = self.client.get(reverse('dashboard'))
        self.assertNotContains(denied, 'title="Solicitudes de gasto"')
        self.assertNotContains(denied, reverse('expense_request_list'))

    def test_sidebar_active_on_list_and_detail(self):
        self.client.force_login(self.admin)
        list_url = reverse('expense_request_list')
        detail_url = reverse('expense_request_detail', args=[self.request_obj.pk])
        list_response = self.client.get(list_url)
        detail_response = self.client.get(detail_url)
        active = f'href="{list_url}" title="Solicitudes de gasto" aria-current="page"'
        self.assertContains(list_response, active)
        self.assertContains(detail_response, active)

    def test_sidebar_placement_between_allocations_and_expenses(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('dashboard')).content.decode()
        allocations = html.find('title="Asignaciones"')
        requests = html.find('title="Solicitudes de gasto"')
        expenses = html.find('title="Gastos"')
        self.assertNotEqual(allocations, -1)
        self.assertNotEqual(requests, -1)
        self.assertNotEqual(expenses, -1)
        self.assertLess(allocations, requests)
        self.assertLess(requests, expenses)

    def test_auditor_sees_sidebar_item_despite_hidden_quick_actions(self):
        self.client.force_login(self.auditor)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['show_financial_quick_actions'])
        self.assertNotContains(response, 'Accesos rápidos')
        self.assertContains(response, 'title="Solicitudes de gasto"')
        self.assertContains(response, reverse('expense_request_list'))

    def test_fulfilled_detail_helper_text_and_no_mutation_controls(self):
        approved = approve_expense_request(self.request_obj, actor=self.committee)
        fulfilled = fulfill_expense_request(
            approved,
            expense_date=TEST_DATE,
            amount=Decimal('45.00'),
            reason='Cumplimiento UI',
            provider_or_recipient='Proveedor',
            payment_method='bank_transfer',
            description='Detalle',
            support_file=SimpleUploadedFile('ui.pdf', b'%PDF-1.4', content_type='application/pdf'),
            support_title='Soporte',
            category='food',
            actor=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('expense_request_detail', args=[fulfilled.pk])
        )
        self.assertContains(response, 'La reserva fue convertida en gasto.')
        self._assert_read_only(response)
