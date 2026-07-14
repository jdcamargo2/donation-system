import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import Expense, Project, SupportingDocument
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_expense


TEMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class SupportingDocumentWorkflowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='support-admin', password='pass-12345')
        self.expense = create_expense(reason='Compra con soporte')
        self.expense.allocation.project.status = Project.Status.ACTIVE
        self.expense.allocation.project.save(update_fields=('status', 'updated_at'))

    def uploaded_file(self, name='support.pdf', content=b'file-content'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def expense_form_data(self):
        return {
            'allocation': self.expense.allocation.pk,
            'expense_date': TEST_DATE,
            'category': self.expense.category,
            'amount': str(self.expense.amount),
            'currency': self.expense.currency,
            'reason': self.expense.reason,
            'provider_or_recipient': self.expense.provider_or_recipient,
            'payment_method': self.expense.payment_method,
            'description': self.expense.description,
            'observations': self.expense.observations,
            'support_title': '',
        }

    def test_user_with_permission_can_open_support_form(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('supporting_document_create_for_expense', args=[self.expense.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/supporting_document_form.html')
        self.assertContains(response, 'Adjuntar soporte')
        self.assertContains(response, 'name="title"')
        self.assertContains(response, 'name="document"')
        self.assertContains(response, 'name="notes"')
        self.assertNotContains(response, 'name="expense"')

    def test_post_creates_supporting_document_for_the_expected_expense(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('supporting_document_create_for_expense', args=[self.expense.pk]),
            data={
                'title': 'Factura principal',
                'document': self.uploaded_file(),
                'notes': 'Soporte cargado después del gasto.',
            },
        )

        document = SupportingDocument.objects.get(title='Factura principal')
        self.assertEqual(document.expense, self.expense)
        self.assertEqual(document.notes, 'Soporte cargado después del gasto.')
        self.assertRedirects(response, reverse('expense_detail', args=[self.expense.pk]))

    def test_expense_detail_shows_supporting_documents(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Recibo visible',
            document=self.uploaded_file('receipt.pdf'),
            notes='Nota visible del soporte.',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('expense_detail', args=[self.expense.pk]))

        self.assertContains(response, 'Documentos soporte')
        self.assertContains(response, 'Adjuntar soporte')
        self.assertContains(response, 'Recibo visible')
        self.assertContains(response, 'Nota visible del soporte.')
        self.assertContains(response, 'Eliminar')
        self.assertContains(response, reverse('supporting_document_download', args=[document.pk]))
        self.assertNotContains(response, document.document.url)

    def test_anonymous_user_cannot_download_supporting_document(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte privado',
            document=self.uploaded_file('private.pdf'),
        )

        response = self.client.get(reverse('supporting_document_download', args=[document.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_user_without_permission_cannot_download_supporting_document(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte restringido',
            document=self.uploaded_file('restricted.pdf'),
        )
        limited_user = get_user_model().objects.create_user(username='no-download-support', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('supporting_document_download', args=[document.pk]))

        self.assertEqual(response.status_code, 403)

    def test_external_auditor_can_download_supporting_document(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte auditable',
            document=self.uploaded_file('auditable.pdf', b'audit-content'),
        )
        sync_operation_roles()
        auditor = get_user_model().objects.create_user(username='support-auditor', password='pass-12345')
        auditor.groups.add(Group.objects.get(name=ROLE_EXTERNAL_AUDITOR))
        self.client.force_login(auditor)

        response = self.client.get(reverse('supporting_document_download', args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'audit-content')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('auditable.pdf', response['Content-Disposition'])

    def test_more_than_one_supporting_document_can_be_attached_to_same_expense(self):
        self.client.force_login(self.user)

        for index in range(2):
            self.client.post(
                reverse('supporting_document_create_for_expense', args=[self.expense.pk]),
                data={
                    'title': f'Soporte {index}',
                    'document': self.uploaded_file(f'support-{index}.pdf'),
                    'notes': '',
                },
            )

        self.assertEqual(self.expense.supporting_documents.count(), 2)

    def test_user_without_permission_cannot_attach_support(self):
        limited_user = get_user_model().objects.create_user(username='no-support', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('supporting_document_create_for_expense', args=[self.expense.pk]))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_redirected_from_support_form(self):
        response = self.client.get(reverse('supporting_document_create_for_expense', args=[self.expense.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_operational_flow_cannot_edit_expense_without_support(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data=self.expense_form_data(),
        )

        self.expense.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.expense.status, Expense.Status.REGISTERED)
        self.assertFormError(
            response.context['form'],
            'support_file',
            'Falta el documento soporte obligatorio para verificar el gasto.',
        )

    def test_operational_flow_can_edit_expense_with_existing_support(self):
        SupportingDocument.objects.create(
            expense=self.expense,
            title='Factura para validar',
            document=self.uploaded_file('validation.pdf'),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data=self.expense_form_data(),
        )

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, Expense.Status.REGISTERED)
        self.assertRedirects(response, reverse('expense_list'))

    def test_user_with_permission_can_delete_only_redundant_support(self):
        SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte conservado',
            document=self.uploaded_file('keep.pdf'),
        )
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte redundante',
            document=self.uploaded_file('delete.pdf'),
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('supporting_document_delete', args=[document.pk]))

        self.assertRedirects(response, reverse('expense_detail', args=[self.expense.pk]))
        self.assertFalse(SupportingDocument.objects.filter(pk=document.pk).exists())

    def test_last_support_of_expense_cannot_be_deleted(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte requerido',
            document=self.uploaded_file('required.pdf'),
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('supporting_document_delete', args=[document.pk]))

        self.assertRedirects(response, reverse('expense_detail', args=[self.expense.pk]))
        self.assertTrue(SupportingDocument.objects.filter(pk=document.pk).exists())

    def test_user_without_permission_cannot_delete_support(self):
        document = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte protegido',
            document=self.uploaded_file('protected.pdf'),
        )
        limited_user = get_user_model().objects.create_user(username='no-delete-support', password='pass-12345')
        view_permission = Permission.objects.get(codename='view_expense', content_type__app_label='operations')
        limited_user.user_permissions.add(view_permission)
        self.client.force_login(limited_user)

        response = self.client.post(reverse('supporting_document_delete', args=[document.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(SupportingDocument.objects.filter(pk=document.pk).exists())
