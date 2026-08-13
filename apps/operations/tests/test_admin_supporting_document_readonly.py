from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.operations.admin import (
    DonationAdmin,
    ExpenseAdmin,
    FundAllocationAdmin,
    InstitutionAdmin,
    ProjectAdmin,
    SupportingDocumentAdmin,
    SupportingDocumentInline,
)
from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    SupportingDocument,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_project,
)


User = get_user_model()

SUPPORT_ADMIN_PERMS = (
    'view_supportingdocument',
    'add_supportingdocument',
    'change_supportingdocument',
    'delete_supportingdocument',
    'view_expense',
    'view_project',
)

ORIGINAL_DOCUMENT_BYTES = b'%PDF-1.4 soporte admin p1c'


def create_staff_user(username, *permission_codenames, is_superuser=False):
    """
    PRE: permission_codenames are operations model permission codenames.
    POST: returns a staff user with exactly those permissions (or a superuser).
    """
    if is_superuser:
        return User.objects.create_superuser(username=username, password='pass-12345')
    user = User.objects.create_user(username=username, password='pass-12345')
    user.is_staff = True
    user.save(update_fields=('is_staff',))
    permissions = Permission.objects.filter(
        content_type__app_label='operations',
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


class SupportingDocumentAdminReadonlyTests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from django.conf import settings

        self._media_tmpdir = tempfile.TemporaryDirectory(prefix='sigedon-p1c-media-')
        self._media_override = override_settings(MEDIA_ROOT=self._media_tmpdir.name)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(self._media_tmpdir.cleanup)
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)

        self.factory = RequestFactory()
        self.model_admin = SupportingDocumentAdmin(SupportingDocument, admin.site)
        self.donation = create_donation(code='DON-ADMIN-SD', amount=Decimal('500.00'))
        self.project = create_project(code='PRJ-ADMIN-SD', name='Proyecto admin support')
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('120.00'),
            category='health_psychosocial',
        )
        self.expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('25.50'),
            reason='Gasto soporte admin',
        )
        self.other_expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('12.00'),
            reason='Otro gasto reassignment',
        )
        self.support = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte admin p1c',
            document=SimpleUploadedFile(
                'soporte-admin-p1c.pdf',
                ORIGINAL_DOCUMENT_BYTES,
                content_type='application/pdf',
            ),
            notes='nota original p1c',
        )
        # Retain a second document so historical “last document” delete rules
        # cannot confuse assertions about unconditional admin denial.
        SupportingDocument.objects.create(
            expense=self.expense,
            title='Segundo soporte',
            document=SimpleUploadedFile(
                'segundo.pdf',
                b'%PDF-1.4 segundo',
                content_type='application/pdf',
            ),
        )
        self.superuser = create_staff_user('sd-admin-super', is_superuser=True)
        self.staff_all_perms = create_staff_user('sd-admin-staff', *SUPPORT_ADMIN_PERMS)
        self.view_only = create_staff_user(
            'sd-admin-viewer',
            'view_supportingdocument',
        )
        self.unauthorized_staff = create_staff_user('sd-admin-none')

    def request_for(self, user, path='/admin/operations/supportingdocument/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def snapshot(self, document):
        document.refresh_from_db()
        stored_bytes = document.document.read()
        document.document.close()
        return {
            'title': document.title,
            'notes': document.notes,
            'document_name': document.document.name,
            'expense_id': document.expense_id,
            'document_bytes': stored_bytes,
        }

    def assert_mutation_permissions_denied(self, user):
        request = self.request_for(user)
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request, self.support))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.support))
        self.assertNotIn('delete_selected', self.model_admin.get_actions(request))

    def assert_readonly_inspection_page(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.support.title)
        self.assertContains(response, self.support.notes)
        self.assertContains(
            response,
            reverse(
                'supporting_document_preview',
                args=[self.expense.pk, self.support.pk],
            ),
        )
        self.assertContains(response, 'Ver vista previa protegida')
        self.assertContains(
            response,
            reverse(
                'supporting_document_download',
                args=[self.expense.pk, self.support.pk],
            ),
        )
        self.assertContains(response, 'Descargar mediante SIGEDON')
        self.assertContains(
            response,
            reverse('expense_detail', args=[self.expense.pk]),
        )
        self.assertContains(response, 'Ver gasto relacionado')
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'name="_continue"')
        self.assertNotContains(response, 'name="_addanother"')
        delete_url = reverse(
            'admin:operations_supportingdocument_delete',
            args=[self.support.pk],
        )
        self.assertNotContains(response, delete_url)
        for field_name in ('title', 'notes', 'expense', 'document'):
            self.assertNotContains(response, f'name="{field_name}"')
        self.assertNotContains(response, 'type="file"')
        # Never expose a direct storage / MEDIA link for the FileField.
        if self.support.document.name:
            self.assertNotContains(response, self.support.document.url)
        self.assertNotContains(response, '/media/')

    def test_superuser_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.superuser)

    def test_staff_with_all_model_permissions_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.staff_all_perms)

    def test_changelist_and_detail_are_readable_for_superuser(self):
        self.client.force_login(self.superuser)
        changelist = reverse('admin:operations_supportingdocument_changelist')
        detail = reverse(
            'admin:operations_supportingdocument_change',
            args=[self.support.pk],
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.support.title)
        add_url = reverse('admin:operations_supportingdocument_add')
        self.assertNotContains(list_response, add_url)
        self.assertNotContains(list_response, 'delete_selected')

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_changelist_and_detail_are_readable_for_staff_with_all_perms(self):
        self.client.force_login(self.staff_all_perms)
        changelist = reverse('admin:operations_supportingdocument_changelist')
        detail = reverse(
            'admin:operations_supportingdocument_change',
            args=[self.support.pk],
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.support.title)
        self.assertNotContains(
            list_response,
            reverse('admin:operations_supportingdocument_add'),
        )
        self.assertNotContains(list_response, 'delete_selected')

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_view_only_staff_can_inspect(self):
        self.client.force_login(self.view_only)
        changelist = reverse('admin:operations_supportingdocument_changelist')
        detail = reverse(
            'admin:operations_supportingdocument_change',
            args=[self.support.pk],
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.support.title)
        self.assertNotContains(
            list_response,
            reverse('admin:operations_supportingdocument_add'),
        )

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)
        request = self.request_for(self.view_only)
        self.assertTrue(self.model_admin.has_view_permission(request, self.support))
        self.assert_mutation_permissions_denied(self.view_only)

    def test_unauthorized_staff_cannot_inspect(self):
        self.client.force_login(self.unauthorized_staff)
        changelist = reverse('admin:operations_supportingdocument_changelist')
        detail = reverse(
            'admin:operations_supportingdocument_change',
            args=[self.support.pk],
        )

        list_response = self.client.get(changelist)
        detail_response = self.client.get(detail)
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(detail_response.status_code, 403)
        self.assertNotContains(list_response, self.support.title, status_code=403)
        self.assertNotContains(detail_response, self.support.notes, status_code=403)
        self.assertNotContains(
            detail_response,
            self.support.document.name,
            status_code=403,
        )

    def test_add_is_denied_and_creates_nothing(self):
        add_url = reverse('admin:operations_supportingdocument_add')
        before_count = SupportingDocument.objects.count()

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(add_url)
                post_response = self.client.post(
                    add_url,
                    {
                        'expense': self.expense.pk,
                        'title': 'forged add',
                        'notes': 'forged',
                        'document': SimpleUploadedFile(
                            'forged-add.pdf',
                            b'%PDF-1.4 forged add',
                            content_type='application/pdf',
                        ),
                        '_save': '1',
                    },
                )
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertEqual(SupportingDocument.objects.count(), before_count)

    def test_change_post_cannot_alter_metadata_file_or_parent(self):
        change_url = reverse(
            'admin:operations_supportingdocument_change',
            args=[self.support.pk],
        )
        original = self.snapshot(self.support)
        audit_before = AuditLog.objects.count()
        replacement = SimpleUploadedFile(
            'forged-replace.pdf',
            b'%PDF-1.4 forged replace',
            content_type='application/pdf',
        )

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    change_url,
                    {
                        'expense': self.other_expense.pk,
                        'title': 'forged title',
                        'notes': 'forged notes',
                        'document': replacement,
                        '_save': '1',
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.snapshot(self.support), original)
                self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_delete_is_denied_and_preserves_row_and_file(self):
        delete_url = reverse(
            'admin:operations_supportingdocument_delete',
            args=[self.support.pk],
        )
        original = self.snapshot(self.support)

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(delete_url)
                post_response = self.client.post(delete_url, {'post': 'yes'})
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertTrue(
                    SupportingDocument.objects.filter(pk=self.support.pk).exists()
                )
                self.assertEqual(self.snapshot(self.support), original)

    def test_forged_bulk_delete_cannot_remove_documents(self):
        second = SupportingDocument.objects.filter(expense=self.expense).exclude(
            pk=self.support.pk
        ).get()
        changelist = reverse('admin:operations_supportingdocument_changelist')
        ids = [self.support.pk, second.pk]
        support_original = self.snapshot(self.support)
        second_original = self.snapshot(second)

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    changelist,
                    {
                        'action': 'delete_selected',
                        '_selected_action': ids,
                        'post': 'yes',
                    },
                )
                self.assertIn(response.status_code, (200, 302))
                self.assertTrue(
                    SupportingDocument.objects.filter(pk=self.support.pk).exists()
                )
                self.assertTrue(
                    SupportingDocument.objects.filter(pk=second.pk).exists()
                )
                self.assertEqual(self.snapshot(self.support), support_original)
                self.assertEqual(self.snapshot(second), second_original)

    def test_expense_inline_remains_readonly_without_media_url(self):
        expense_admin = ExpenseAdmin(Expense, admin.site)
        request = self.request_for(self.superuser)
        inline = SupportingDocumentInline(SupportingDocument, admin.site)
        self.assertFalse(inline.has_add_permission(request, self.expense))
        self.assertFalse(inline.has_change_permission(request, self.support))
        self.assertFalse(inline.has_delete_permission(request, self.support))
        self.assertFalse(inline.can_delete)

        self.client.force_login(self.superuser)
        detail = reverse('admin:operations_expense_change', args=[self.expense.pk])
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.support.title)
        self.assertNotContains(response, 'name="supportingdocument_set-0-title"')
        self.assertNotContains(response, 'name="supportingdocument_set-0-document"')
        self.assertNotContains(response, 'name="supportingdocument_set-0-notes"')
        self.assertNotContains(response, 'type="file"')
        if self.support.document.name:
            self.assertNotContains(response, self.support.document.url)
        self.assertNotContains(response, '/media/')
        self.assertIs(expense_admin.inlines[0], SupportingDocumentInline)

    def test_no_editable_supporting_document_inline_elsewhere(self):
        for admin_class, model in (
            (DonationAdmin, Donation),
            (FundAllocationAdmin, FundAllocation),
            (ProjectAdmin, Project),
            (InstitutionAdmin, Institution),
        ):
            model_admin = admin_class(model, admin.site)
            for inline in getattr(model_admin, 'inlines', ()):
                self.assertIsNot(inline.model, SupportingDocument)

        registered = admin.site._registry[SupportingDocument]
        self.assertIsInstance(registered, SupportingDocumentAdmin)
        request = self.request_for(self.superuser)
        self.assertFalse(registered.has_add_permission(request))
        self.assertFalse(registered.has_change_permission(request, self.support))
        self.assertFalse(registered.has_delete_permission(request, self.support))
        self.assertNotIn('delete_selected', registered.get_actions(request))
