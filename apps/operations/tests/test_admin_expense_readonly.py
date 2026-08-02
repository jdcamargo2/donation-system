from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import (
    DonationAdmin,
    ExpenseAdmin,
    FundAllocationAdmin,
    ProjectAdmin,
    SupportingDocumentInline,
)
from apps.operations.models import AuditLog, Expense, SupportingDocument
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_project,
)


User = get_user_model()

EXPENSE_ADMIN_PERMS = (
    'view_expense',
    'add_expense',
    'change_expense',
    'delete_expense',
    'view_supportingdocument',
    'add_supportingdocument',
    'change_supportingdocument',
    'delete_supportingdocument',
)


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


class ExpenseAdminReadonlyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = ExpenseAdmin(Expense, admin.site)
        self.donation = create_donation(code='DON-ADMIN-EX', amount=Decimal('500.00'))
        self.project = create_project(code='PRJ-ADMIN-EX', name='Proyecto admin expense')
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('120.00'),
            category='health_psychosocial',
        )
        self.expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('25.50'),
            reason='Gasto inspección admin',
        )
        self.support = SupportingDocument.objects.create(
            expense=self.expense,
            title='Soporte admin',
            document=SimpleUploadedFile(
                'soporte-admin.pdf',
                b'%PDF-1.4 soporte admin',
                content_type='application/pdf',
            ),
            notes='nota original',
        )
        self.superuser = create_staff_user('ex-admin-super', is_superuser=True)
        self.staff_all_perms = create_staff_user('ex-admin-staff', *EXPENSE_ADMIN_PERMS)
        self.view_only = create_staff_user('ex-admin-viewer', 'view_expense')
        self.unauthorized_staff = create_staff_user('ex-admin-none')

    def request_for(self, user, path='/admin/operations/expense/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def snapshot(self, expense):
        expense.refresh_from_db()
        return {
            'code': expense.code,
            'allocation_id': expense.allocation_id,
            'amount': expense.amount,
            'currency': expense.currency,
            'expense_date': expense.expense_date,
            'status': expense.status,
            'reason': expense.reason,
            'provider_or_recipient': expense.provider_or_recipient,
            'payment_method': expense.payment_method,
            'description': expense.description,
            'observations': expense.observations,
            'category': expense.category,
            'terminal_reason': expense.terminal_reason,
            'terminal_at': expense.terminal_at,
            'terminal_by_id': expense.terminal_by_id,
        }

    def support_snapshot(self, document):
        document.refresh_from_db()
        return {
            'title': document.title,
            'notes': document.notes,
            'document_name': document.document.name,
            'expense_id': document.expense_id,
        }

    def assert_mutation_permissions_denied(self, user):
        request = self.request_for(user)
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request, self.expense))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.expense))
        self.assertNotIn('delete_selected', self.model_admin.get_actions(request))
        inline = SupportingDocumentInline(SupportingDocument, admin.site)
        self.assertFalse(inline.has_add_permission(request, self.expense))
        self.assertFalse(inline.has_change_permission(request, self.support))
        self.assertFalse(inline.has_delete_permission(request, self.support))

    def assert_readonly_inspection_page(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.expense.code)
        self.assertContains(response, str(self.expense.amount))
        self.assertContains(response, self.expense.get_status_display())
        self.assertContains(response, reverse('expense_detail', args=[self.expense.pk]))
        self.assertContains(response, 'Ver en SIGEDON')
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'name="_continue"')
        self.assertNotContains(response, 'name="_addanother"')
        delete_url = reverse('admin:operations_expense_delete', args=[self.expense.pk])
        self.assertNotContains(response, delete_url)
        for field_name in (
            'allocation',
            'amount',
            'currency',
            'expense_date',
            'status',
            'reason',
            'provider_or_recipient',
            'payment_method',
            'description',
            'observations',
            'category',
        ):
            self.assertNotContains(response, f'name="{field_name}"')
        self.assertNotContains(response, 'name="supportingdocument_set-0-title"')
        self.assertNotContains(response, 'name="supportingdocument_set-0-document"')
        self.assertNotContains(response, 'name="supportingdocument_set-0-notes"')

    def test_superuser_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.superuser)

    def test_staff_with_all_model_permissions_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.staff_all_perms)

    def test_changelist_and_detail_are_readable_for_superuser(self):
        self.client.force_login(self.superuser)
        changelist = reverse('admin:operations_expense_changelist')
        detail = reverse('admin:operations_expense_change', args=[self.expense.pk])

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.expense.code)
        add_url = reverse('admin:operations_expense_add')
        self.assertNotContains(list_response, add_url)
        self.assertNotContains(list_response, 'delete_selected')

        search_response = self.client.get(changelist, {'q': self.expense.code})
        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, self.expense.code)

        filter_response = self.client.get(
            changelist, {'status__exact': Expense.Status.REGISTERED}
        )
        self.assertEqual(filter_response.status_code, 200)
        self.assertContains(filter_response, self.expense.code)

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_changelist_and_detail_are_readable_for_staff_with_all_perms(self):
        self.client.force_login(self.staff_all_perms)
        changelist = reverse('admin:operations_expense_changelist')
        detail = reverse('admin:operations_expense_change', args=[self.expense.pk])

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.expense.code)
        self.assertNotContains(list_response, reverse('admin:operations_expense_add'))
        self.assertNotContains(list_response, 'delete_selected')

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_view_only_staff_can_inspect(self):
        self.client.force_login(self.view_only)
        changelist = reverse('admin:operations_expense_changelist')
        detail = reverse('admin:operations_expense_change', args=[self.expense.pk])

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.expense.code)
        self.assertNotContains(list_response, reverse('admin:operations_expense_add'))

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)
        request = self.request_for(self.view_only)
        self.assertTrue(self.model_admin.has_view_permission(request, self.expense))
        self.assert_mutation_permissions_denied(self.view_only)

    def test_unauthorized_staff_cannot_inspect(self):
        self.client.force_login(self.unauthorized_staff)
        changelist = reverse('admin:operations_expense_changelist')
        detail = reverse('admin:operations_expense_change', args=[self.expense.pk])

        list_response = self.client.get(changelist)
        detail_response = self.client.get(detail)
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(detail_response.status_code, 403)
        self.assertNotContains(list_response, self.expense.code, status_code=403)
        self.assertNotContains(detail_response, self.expense.reason, status_code=403)

    def test_add_is_denied_and_creates_nothing(self):
        add_url = reverse('admin:operations_expense_add')
        before_count = Expense.objects.count()
        other_allocation = create_allocation(
            donation=self.donation,
            project=create_project(code='PRJ-ADMIN-EX-2', name='Otro proyecto EX'),
            amount=Decimal('40.00'),
        )

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(add_url)
                post_response = self.client.post(
                    add_url,
                    {
                        'allocation': other_allocation.pk,
                        'expense_date': '2026-07-08',
                        'category': 'food',
                        'amount': '10.00',
                        'reason': 'forged add',
                        'provider_or_recipient': 'forged',
                        'payment_method': 'bank_transfer',
                        'description': '',
                        'observations': '',
                        '_save': '1',
                    },
                )
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertEqual(Expense.objects.count(), before_count)

    def test_change_post_cannot_alter_financial_fields_or_write_audit(self):
        change_url = reverse(
            'admin:operations_expense_change', args=[self.expense.pk]
        )
        other_allocation = create_allocation(
            donation=create_donation(code='DON-ADMIN-EX-2', amount=Decimal('200.00')),
            project=create_project(code='PRJ-ADMIN-EX-3', name='Proyecto alterado EX'),
            amount=Decimal('50.00'),
        )
        original = self.snapshot(self.expense)
        support_original = self.support_snapshot(self.support)
        audit_before = AuditLog.objects.count()

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    change_url,
                    {
                        'allocation': other_allocation.pk,
                        'expense_date': '2026-01-01',
                        'category': 'food',
                        'amount': '1.00',
                        'currency': 'USD',
                        'status': Expense.Status.ANNULLED,
                        'reason': 'forged change',
                        'provider_or_recipient': 'forged recipient',
                        'payment_method': 'cash',
                        'description': 'forged description',
                        'observations': 'forged notes',
                        'terminal_reason': 'forged terminal',
                        'supportingdocument_set-TOTAL_FORMS': '1',
                        'supportingdocument_set-INITIAL_FORMS': '1',
                        'supportingdocument_set-MIN_NUM_FORMS': '0',
                        'supportingdocument_set-MAX_NUM_FORMS': '1000',
                        'supportingdocument_set-0-id': self.support.pk,
                        'supportingdocument_set-0-expense': self.expense.pk,
                        'supportingdocument_set-0-title': 'forged title',
                        'supportingdocument_set-0-notes': 'forged notes',
                        'supportingdocument_set-0-DELETE': 'on',
                        '_save': '1',
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.snapshot(self.expense), original)
                self.assertEqual(self.support_snapshot(self.support), support_original)
                self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_inline_post_cannot_add_supporting_document(self):
        change_url = reverse(
            'admin:operations_expense_change', args=[self.expense.pk]
        )
        original = self.snapshot(self.expense)
        before_docs = SupportingDocument.objects.filter(expense=self.expense).count()

        self.client.force_login(self.superuser)
        response = self.client.post(
            change_url,
            {
                'allocation': self.allocation.pk,
                'expense_date': self.expense.expense_date.isoformat(),
                'category': self.expense.category,
                'amount': str(self.expense.amount),
                'reason': self.expense.reason,
                'provider_or_recipient': self.expense.provider_or_recipient,
                'payment_method': self.expense.payment_method,
                'description': self.expense.description,
                'observations': self.expense.observations,
                'supportingdocument_set-TOTAL_FORMS': '2',
                'supportingdocument_set-INITIAL_FORMS': '1',
                'supportingdocument_set-MIN_NUM_FORMS': '0',
                'supportingdocument_set-MAX_NUM_FORMS': '1000',
                'supportingdocument_set-0-id': self.support.pk,
                'supportingdocument_set-0-expense': self.expense.pk,
                'supportingdocument_set-0-title': self.support.title,
                'supportingdocument_set-0-notes': self.support.notes,
                'supportingdocument_set-1-expense': self.expense.pk,
                'supportingdocument_set-1-title': 'nuevo soporte forged',
                'supportingdocument_set-1-notes': 'forged',
                'supportingdocument_set-1-document': SimpleUploadedFile(
                    'forged.pdf',
                    b'%PDF-1.4 forged',
                    content_type='application/pdf',
                ),
                '_save': '1',
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(self.expense), original)
        self.assertEqual(
            SupportingDocument.objects.filter(expense=self.expense).count(),
            before_docs,
        )

    def test_delete_is_denied_and_preserves_row(self):
        delete_url = reverse(
            'admin:operations_expense_delete', args=[self.expense.pk]
        )

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(delete_url)
                post_response = self.client.post(delete_url, {'post': 'yes'})
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())
                self.assertTrue(
                    SupportingDocument.objects.filter(pk=self.support.pk).exists()
                )

    def test_forged_bulk_delete_cannot_remove_expenses(self):
        second = create_expense(
            allocation=self.allocation,
            amount=Decimal('11.00'),
            reason='Segundo gasto bulk',
        )
        SupportingDocument.objects.create(
            expense=second,
            title='Soporte bulk',
            document=SimpleUploadedFile(
                'bulk.pdf', b'%PDF-1.4 bulk', content_type='application/pdf'
            ),
        )
        changelist = reverse('admin:operations_expense_changelist')
        ids = [self.expense.pk, second.pk]

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
                self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())
                self.assertTrue(Expense.objects.filter(pk=second.pk).exists())

    def test_no_editable_expense_inline_on_related_admins(self):
        from apps.operations.models import Donation, FundAllocation, Project

        for admin_class, model in (
            (DonationAdmin, Donation),
            (FundAllocationAdmin, FundAllocation),
            (ProjectAdmin, Project),
        ):
            model_admin = admin_class(model, admin.site)
            for inline in getattr(model_admin, 'inlines', ()):
                self.assertIsNot(inline.model, Expense)

        registered_expense_admin = admin.site._registry[Expense]
        self.assertIsInstance(registered_expense_admin, ExpenseAdmin)
        request = self.request_for(self.superuser)
        self.assertFalse(registered_expense_admin.has_add_permission(request))
        self.assertFalse(
            registered_expense_admin.has_change_permission(request, self.expense)
        )
        self.assertFalse(
            registered_expense_admin.has_delete_permission(request, self.expense)
        )
        self.assertNotIn('delete_selected', registered_expense_admin.get_actions(request))
        # No custom ModelAdmin.actions mutation list is configured.
        self.assertIn(
            registered_expense_admin.actions,
            (None, (), []),
        )
