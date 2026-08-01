from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import FundAllocationAdmin
from apps.operations.models import AuditLog, FundAllocation
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_project,
)


User = get_user_model()

ALLOCATION_ADMIN_PERMS = (
    'view_fundallocation',
    'add_fundallocation',
    'change_fundallocation',
    'delete_fundallocation',
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


class FundAllocationAdminContainmentTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = FundAllocationAdmin(FundAllocation, admin.site)
        self.donation = create_donation(code='DON-ADMIN-FA', amount=Decimal('500.00'))
        self.project = create_project(code='PRJ-ADMIN-FA', name='Proyecto admin FA')
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('80.00'),
            category='health_psychosocial',
        )
        self.superuser = create_staff_user('fa-admin-super', is_superuser=True)
        self.staff_all_perms = create_staff_user('fa-admin-staff', *ALLOCATION_ADMIN_PERMS)
        self.view_only = create_staff_user('fa-admin-viewer', 'view_fundallocation')

    def request_for(self, user, path='/admin/operations/fundallocation/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def snapshot(self, allocation):
        allocation.refresh_from_db()
        return {
            'donation_id': allocation.donation_id,
            'project_id': allocation.project_id,
            'amount': allocation.amount,
            'status': allocation.status,
            'code': allocation.code,
            'budget_category': allocation.budget_category,
            'notes': allocation.notes,
        }

    def assert_mutation_permissions_denied(self, user):
        request = self.request_for(user)
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request, self.allocation))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.allocation))
        self.assertNotIn('delete_selected', self.model_admin.get_actions(request))

    def assert_readonly_inspection_page(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.allocation.code)
        self.assertContains(response, str(self.allocation.amount))
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'name="_continue"')
        self.assertNotContains(response, 'name="_addanother"')
        delete_url = reverse(
            'admin:operations_fundallocation_delete', args=[self.allocation.pk]
        )
        self.assertNotContains(response, delete_url)
        # Editable model fields must not appear as writable inputs/selects.
        self.assertNotContains(response, 'name="donation"')
        self.assertNotContains(response, 'name="project"')
        self.assertNotContains(response, 'name="amount"')
        self.assertNotContains(response, 'name="status"')
        self.assertNotContains(response, 'name="budget_category"')
        self.assertNotContains(response, 'name="notes"')

    def test_superuser_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.superuser)

    def test_staff_with_all_model_permissions_mutation_permissions_are_false(self):
        self.assert_mutation_permissions_denied(self.staff_all_perms)

    def test_changelist_and_detail_are_readable_for_superuser(self):
        self.client.force_login(self.superuser)
        changelist = reverse('admin:operations_fundallocation_changelist')
        detail = reverse(
            'admin:operations_fundallocation_change', args=[self.allocation.pk]
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.allocation.code)
        add_url = reverse('admin:operations_fundallocation_add')
        self.assertNotContains(list_response, add_url)
        self.assertNotContains(list_response, 'delete_selected')

        search_response = self.client.get(changelist, {'q': self.allocation.code})
        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, self.allocation.code)

        filter_response = self.client.get(changelist, {'status__exact': 'active'})
        self.assertEqual(filter_response.status_code, 200)
        self.assertContains(filter_response, self.allocation.code)

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_changelist_and_detail_are_readable_for_staff_with_all_perms(self):
        self.client.force_login(self.staff_all_perms)
        changelist = reverse('admin:operations_fundallocation_changelist')
        detail = reverse(
            'admin:operations_fundallocation_change', args=[self.allocation.pk]
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.allocation.code)
        self.assertNotContains(
            list_response, reverse('admin:operations_fundallocation_add')
        )
        self.assertNotContains(list_response, 'delete_selected')

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)

    def test_view_only_staff_can_inspect(self):
        self.client.force_login(self.view_only)
        changelist = reverse('admin:operations_fundallocation_changelist')
        detail = reverse(
            'admin:operations_fundallocation_change', args=[self.allocation.pk]
        )

        list_response = self.client.get(changelist)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.allocation.code)
        self.assertNotContains(
            list_response, reverse('admin:operations_fundallocation_add')
        )

        detail_response = self.client.get(detail)
        self.assert_readonly_inspection_page(detail_response)
        request = self.request_for(self.view_only)
        self.assertTrue(self.model_admin.has_view_permission(request, self.allocation))
        self.assert_mutation_permissions_denied(self.view_only)

    def test_add_is_denied_and_creates_nothing(self):
        add_url = reverse('admin:operations_fundallocation_add')
        before_count = FundAllocation.objects.count()
        other_project = create_project(code='PRJ-ADMIN-FA-2', name='Otro proyecto FA')

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(add_url)
                post_response = self.client.post(
                    add_url,
                    {
                        'donation': self.donation.pk,
                        'project': other_project.pk,
                        'budget_category': 'food',
                        'amount': '10.00',
                        'allocation_date': '2026-07-08',
                        'responsible_person': '',
                        'notes': 'forged add',
                        '_save': '1',
                    },
                )
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertEqual(FundAllocation.objects.count(), before_count)

    def test_change_post_cannot_alter_allocation_or_write_audit(self):
        change_url = reverse(
            'admin:operations_fundallocation_change', args=[self.allocation.pk]
        )
        other_donation = create_donation(code='DON-ADMIN-FA-2', amount=Decimal('200.00'))
        other_project = create_project(code='PRJ-ADMIN-FA-3', name='Proyecto alterado FA')
        original = self.snapshot(self.allocation)
        audit_before = AuditLog.objects.count()

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    change_url,
                    {
                        'donation': other_donation.pk,
                        'project': other_project.pk,
                        'budget_category': 'food',
                        'amount': '1.00',
                        'allocation_date': '2026-01-01',
                        'status': FundAllocation.Status.ANNULLED,
                        'responsible_person': 'forged',
                        'notes': 'forged change',
                        '_save': '1',
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.snapshot(self.allocation), original)
                self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_delete_is_denied_and_preserves_row(self):
        delete_url = reverse(
            'admin:operations_fundallocation_delete', args=[self.allocation.pk]
        )

        for user in (self.superuser, self.staff_all_perms):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                get_response = self.client.get(delete_url)
                post_response = self.client.post(delete_url, {'post': 'yes'})
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(post_response.status_code, 403)
                self.assertTrue(
                    FundAllocation.objects.filter(pk=self.allocation.pk).exists()
                )

    def test_forged_bulk_delete_cannot_remove_allocations(self):
        second = create_allocation(
            donation=self.donation,
            project=create_project(code='PRJ-ADMIN-FA-BULK', name='Bulk FA'),
            amount=Decimal('15.00'),
        )
        changelist = reverse('admin:operations_fundallocation_changelist')
        ids = [self.allocation.pk, second.pk]

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
                self.assertTrue(FundAllocation.objects.filter(pk=self.allocation.pk).exists())
                self.assertTrue(FundAllocation.objects.filter(pk=second.pk).exists())
