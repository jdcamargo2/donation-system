import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, Expense, Institution, Project, ProjectUpdate, SupportingDocument
from apps.operations.services import register_advance
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_expense, create_institution, create_project


TEMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class CriticalAuditTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='audit-user', password='pass-12345')
        self.client.force_login(self.user)

    def uploaded_file(self, name='audit.pdf', content=b'audit-file'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def test_project_delete_generates_audit_log(self):
        project = create_project(code='PRJ-DEL-001', name='Proyecto eliminable')

        response = self.client.post(reverse('project_delete', args=[project.pk]))

        self.assertRedirects(response, reverse('project_list'))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                model_name='Proyecto',
                entity_label='PRJ-DEL-001 - Proyecto eliminable',
                summary='Proyecto eliminado.',
            ).exists()
        )

    def test_donation_delete_generates_audit_log(self):
        donor = create_institution(name='Donante eliminación')
        donation = create_donation(code='DON-DEL-001', donor=donor)

        response = self.client.post(reverse('donation_delete', args=[donation.pk]))

        self.assertRedirects(response, reverse('donation_list'))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                model_name='Donación',
                entity_label='DON-DEL-001 - Donante eliminación',
                summary='Donación eliminada.',
            ).exists()
        )

    def test_expense_delete_generates_audit_log(self):
        expense = create_expense(reason='Gasto eliminable')

        response = self.client.post(reverse('expense_delete', args=[expense.pk]))

        self.assertRedirects(response, reverse('expense_list'))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                model_name='Gasto',
                entity_label='Gasto eliminable - 20.00',
                summary='Gasto eliminado.',
            ).exists()
        )

    def test_project_update_approved_review_generates_audit_log(self):
        project = create_project(code='PRJ-REV-001', name='Proyecto revisión')
        project.status = Project.Status.ACTIVE
        project.save()
        update = register_advance(project_id=project.pk, title='Avance aprobado', description='Listo.')
        AuditLog.objects.all().delete()

        response = self.client.post(
            reverse('project_update_review', args=[update.pk]),
            data={'status': ProjectUpdate.Status.APPROVED, 'review_notes': 'Aprobado.'},
        )

        self.assertRedirects(response, reverse('project_detail', args=[project.pk]))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.VALIDATED,
                model_name='Avance de proyecto',
                entity_label='PRJ-REV-001 - Avance aprobado',
                summary__contains='Avance de proyecto aprobado.',
            ).exists()
        )

    def test_project_update_rejected_review_generates_audit_log(self):
        project = create_project(code='PRJ-REJ-001', name='Proyecto rechazo')
        project.status = Project.Status.ACTIVE
        project.save()
        update = register_advance(project_id=project.pk, title='Avance rechazado', description='Listo.')
        AuditLog.objects.all().delete()

        response = self.client.post(
            reverse('project_update_review', args=[update.pk]),
            data={'status': ProjectUpdate.Status.REJECTED, 'review_notes': 'Falta evidencia.'},
        )

        self.assertRedirects(response, reverse('project_detail', args=[project.pk]))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.REJECTED,
                model_name='Avance de proyecto',
                entity_label='PRJ-REJ-001 - Avance rechazado',
                summary__contains='Avance de proyecto rechazado.',
            ).exists()
        )

    def test_attaching_support_generates_audit_log(self):
        expense = create_expense(reason='Gasto con soporte auditado')

        response = self.client.post(
            reverse('supporting_document_create_for_expense', args=[expense.pk]),
            data={'title': 'Factura auditada', 'document': self.uploaded_file(), 'notes': ''},
        )

        self.assertRedirects(response, reverse('expense_detail', args=[expense.pk]))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED,
                model_name='Documento soporte',
                entity_label='Factura auditada',
                summary='Documento soporte adjuntado.',
            ).exists()
        )

    def test_deleting_support_generates_audit_log(self):
        expense = create_expense(reason='Gasto con soporte eliminable')
        document = SupportingDocument.objects.create(
            expense=expense,
            title='Soporte eliminado auditado',
            document=self.uploaded_file('delete-audit.pdf'),
        )

        response = self.client.post(reverse('supporting_document_delete', args=[document.pk]))

        self.assertRedirects(response, reverse('expense_detail', args=[expense.pk]))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ANNULLED,
                model_name='Documento soporte',
                entity_label='Soporte eliminado auditado',
                summary='Documento soporte eliminado.',
            ).exists()
        )

    def test_validating_expense_generates_audit_log(self):
        allocation = create_allocation(amount='80.00')
        expense = create_expense(allocation=allocation, amount='20.00', reason='Gasto validable')
        SupportingDocument.objects.create(
            expense=expense,
            title='Soporte para validar',
            document=self.uploaded_file('validate-audit.pdf'),
        )
        AuditLog.objects.all().delete()

        response = self.client.post(
            reverse('expense_update', args=[expense.pk]),
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': expense.category,
                'amount': str(expense.amount),
                'currency': expense.currency,
                'reason': expense.reason,
                'provider_or_recipient': expense.provider_or_recipient,
                'payment_method': expense.payment_method,
                'description': expense.description,
                'observations': expense.observations,
                'status': Expense.Status.VALIDATED,
                'support_title': '',
            },
        )

        self.assertRedirects(response, reverse('expense_list'))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.VALIDATED,
                model_name='Gasto',
                entity_label='Gasto validable - 20.00',
                summary='Gasto validado.',
            ).exists()
        )

    def test_audit_log_list_still_requires_view_permission(self):
        limited_user = get_user_model().objects.create_user(username='no-audit-view', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 403)

    def test_audit_log_list_shows_recent_events(self):
        log = AuditLog.objects.create(
            user=self.user,
            action=AuditLog.Action.ANNULLED,
            model_name='Proyecto',
            entity_id='1',
            entity_label='PRJ-AUD-001',
            summary='Proyecto eliminado.',
        )

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registro de auditoría')
        self.assertContains(response, 'Modelo')
        self.assertContains(response, 'Objeto')
        self.assertContains(response, 'Descripción')
        self.assertContains(response, log.entity_label)
        self.assertContains(response, 'Proyecto eliminado.')
