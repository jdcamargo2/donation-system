from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import SigedonGroupAdmin, _is_canonical_sigedon_group
from apps.operations.role_services import operation_role_names, sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)


User = get_user_model()


class SigedonGroupAdminProtectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_operation_roles()
        cls.canonical_admin = Group.objects.get(name=ROLE_SIGEDON_ADMIN)
        cls.canonical_operator = Group.objects.get(name=ROLE_FIELD_OPERATOR)
        cls.canonical_auditor = Group.objects.get(name=ROLE_EXTERNAL_AUDITOR)
        cls.canonical_committee = Group.objects.get(name=ROLE_PROJECT_COMMITTEE)
        cls.technical = Group.objects.create(name='Technical Group Protect')
        cls.technical_other = Group.objects.create(name='Technical Group Other')
        cls.view_project = Permission.objects.get(
            content_type__app_label='operations',
            codename='view_project',
        )
        cls.view_donation = Permission.objects.get(
            content_type__app_label='operations',
            codename='view_donation',
        )
        cls.superuser = User.objects.create_superuser(
            username='group-protect-super',
            email='group-protect-super@example.com',
            password='pass-12345',
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.superuser)
        self.factory = RequestFactory()
        self.model_admin = SigedonGroupAdmin(Group, admin.site)

    def request_for(self, path='/admin/auth/group/'):
        request = self.factory.get(path)
        request.user = self.superuser
        return request

    def permission_ids(self, group):
        return set(group.permissions.values_list('pk', flat=True))

    def change_payload(self, group, **overrides):
        payload = {
            'name': group.name,
            'permissions': list(group.permissions.values_list('pk', flat=True)),
        }
        payload.update(overrides)
        return payload

    def bulk_delete(self, *groups):
        return self.client.post(
            reverse('admin:auth_group_changelist'),
            {
                'action': 'delete_selected',
                '_selected_action': [str(group.pk) for group in groups],
                'post': 'yes',
            },
        )

    def test_admin_registration_replaced_stock_group_admin(self):
        registered = admin.site._registry[Group]
        self.assertIsInstance(registered, SigedonGroupAdmin)
        self.assertIs(type(registered), SigedonGroupAdmin)

    def test_helper_identifies_canonical_vs_technical(self):
        self.assertTrue(_is_canonical_sigedon_group(self.canonical_operator))
        self.assertFalse(_is_canonical_sigedon_group(self.technical))
        self.assertFalse(_is_canonical_sigedon_group(None))

    def test_changelist_shows_canonical_and_technical_with_labels(self):
        response = self.client.get(reverse('admin:auth_group_changelist'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for role_name in operation_role_names():
            self.assertIn(role_name, content)
        self.assertIn(self.technical.name, content)
        self.assertContains(response, 'Grupo funcional SIGEDON')
        self.assertContains(response, 'Sincronizado — solo lectura')
        self.assertContains(response, 'Grupo técnico')
        self.assertContains(response, 'Editable')

    def test_canonical_change_page_is_inspection_only(self):
        url = reverse('admin:auth_group_change', args=[self.canonical_operator.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ROLE_FIELD_OPERATOR)
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'name="_continue"')
        self.assertNotContains(response, 'name="_addanother"')
        delete_url = reverse(
            'admin:auth_group_delete', args=[self.canonical_operator.pk]
        )
        self.assertNotContains(response, delete_url)
        readonly = self.model_admin.get_readonly_fields(
            self.request_for(url), self.canonical_operator
        )
        self.assertEqual(readonly, ('name', 'permissions'))

    def test_superuser_cannot_change_or_delete_canonical_permissions_flags(self):
        request = self.request_for()
        self.assertFalse(
            self.model_admin.has_change_permission(request, self.canonical_admin)
        )
        self.assertFalse(
            self.model_admin.has_delete_permission(request, self.canonical_admin)
        )
        self.assertTrue(self.model_admin.has_change_permission(request, self.technical))
        self.assertTrue(self.model_admin.has_delete_permission(request, self.technical))
        self.assertTrue(self.model_admin.has_change_permission(request, None))
        self.assertTrue(self.model_admin.has_add_permission(request))

    def test_post_cannot_rename_canonical_group(self):
        original_name = self.canonical_auditor.name
        url = reverse('admin:auth_group_change', args=[self.canonical_auditor.pk])
        response = self.client.post(
            url,
            self.change_payload(self.canonical_auditor, name='Renamed Auditor'),
        )
        self.assertEqual(response.status_code, 403)
        self.canonical_auditor.refresh_from_db()
        self.assertEqual(self.canonical_auditor.name, original_name)

    def test_post_cannot_change_canonical_permissions(self):
        original_perms = self.permission_ids(self.canonical_operator)
        url = reverse('admin:auth_group_change', args=[self.canonical_operator.pk])
        response = self.client.post(
            url,
            self.change_payload(
                self.canonical_operator,
                permissions=[str(self.view_donation.pk)],
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.canonical_operator.refresh_from_db()
        self.assertEqual(self.permission_ids(self.canonical_operator), original_perms)

    def test_technical_group_cannot_be_renamed_to_canonical_name(self):
        url = reverse('admin:auth_group_change', args=[self.technical.pk])
        response = self.client.post(
            url,
            self.change_payload(self.technical, name=ROLE_PROJECT_COMMITTEE),
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['adminform'].form
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.technical.refresh_from_db()
        self.assertEqual(self.technical.name, 'Technical Group Protect')
        self.assertTrue(
            Group.objects.filter(name=ROLE_PROJECT_COMMITTEE).count() == 1
        )

    def test_add_form_cannot_create_canonical_name(self):
        url = reverse('admin:auth_group_add')
        response = self.client.post(
            url,
            {
                'name': ROLE_SIGEDON_ADMIN,
                'permissions': [],
            },
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['adminform'].form
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.assertEqual(
            Group.objects.filter(name=ROLE_SIGEDON_ADMIN).count(),
            1,
        )

    def test_canonical_delete_get_and_post_denied(self):
        url = reverse('admin:auth_group_delete', args=[self.canonical_committee.pk])
        get_response = self.client.get(url)
        post_response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        self.assertTrue(
            Group.objects.filter(pk=self.canonical_committee.pk).exists()
        )

    def test_bulk_delete_canonical_only_deletes_nothing(self):
        response = self.bulk_delete(self.canonical_admin, self.canonical_operator)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.canonical_admin.pk).exists())
        self.assertTrue(Group.objects.filter(pk=self.canonical_operator.pk).exists())

    def test_bulk_delete_mixed_canonical_and_technical_deletes_nothing(self):
        response = self.bulk_delete(self.canonical_auditor, self.technical)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.canonical_auditor.pk).exists())
        self.assertTrue(Group.objects.filter(pk=self.technical.pk).exists())

    def test_bulk_delete_technical_only_succeeds(self):
        disposable = Group.objects.create(name='Technical Disposable Bulk')
        response = self.bulk_delete(disposable, self.technical_other)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(pk=disposable.pk).exists())
        self.assertFalse(Group.objects.filter(pk=self.technical_other.pk).exists())

    def test_technical_group_can_be_created(self):
        url = reverse('admin:auth_group_add')
        response = self.client.post(
            url,
            {
                'name': 'Technical Created Via Admin',
                'permissions': [str(self.view_project.pk)],
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Group.objects.get(name='Technical Created Via Admin')
        self.assertEqual(self.permission_ids(created), {self.view_project.pk})

    def test_technical_group_can_be_renamed(self):
        url = reverse('admin:auth_group_change', args=[self.technical.pk])
        response = self.client.post(
            url,
            self.change_payload(self.technical, name='Technical Renamed'),
        )
        self.assertEqual(response.status_code, 302)
        self.technical.refresh_from_db()
        self.assertEqual(self.technical.name, 'Technical Renamed')

    def test_technical_permissions_can_be_changed(self):
        url = reverse('admin:auth_group_change', args=[self.technical.pk])
        response = self.client.post(
            url,
            self.change_payload(
                self.technical,
                permissions=[str(self.view_donation.pk)],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.technical.refresh_from_db()
        self.assertEqual(self.permission_ids(self.technical), {self.view_donation.pk})

    def test_technical_group_can_be_deleted(self):
        disposable = Group.objects.create(name='Technical Disposable Single')
        url = reverse('admin:auth_group_delete', args=[disposable.pk])
        response = self.client.post(url, {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(pk=disposable.pk).exists())

    def test_save_model_rejects_canonical_mutation_directly(self):
        request = self.request_for()
        self.canonical_operator.name = 'Hacked Operator'
        with self.assertRaises(PermissionDenied):
            self.model_admin.save_model(
                request,
                self.canonical_operator,
                form=None,
                change=True,
            )
        self.canonical_operator.refresh_from_db()
        self.assertEqual(self.canonical_operator.name, ROLE_FIELD_OPERATOR)

    def test_delete_queryset_all_or_nothing(self):
        request = self.request_for()
        queryset = Group.objects.filter(
            pk__in=[self.canonical_admin.pk, self.technical.pk]
        )
        with self.assertRaises(PermissionDenied):
            self.model_admin.delete_queryset(request, queryset)
        self.assertTrue(Group.objects.filter(pk=self.canonical_admin.pk).exists())
        self.assertTrue(Group.objects.filter(pk=self.technical.pk).exists())

    def test_sync_operation_roles_still_updates_canonical_permissions(self):
        group = Group.objects.get(name=ROLE_FIELD_OPERATOR)
        group.permissions.clear()
        self.assertEqual(group.permissions.count(), 0)
        sync_operation_roles()
        group.refresh_from_db()
        self.assertGreater(group.permissions.count(), 0)
        self.assertTrue(group.permissions.filter(codename='view_project').exists())

    def test_technical_change_page_remains_editable(self):
        url = reverse('admin:auth_group_change', args=[self.technical.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="_save"')
        readonly = self.model_admin.get_readonly_fields(
            self.request_for(url), self.technical
        )
        self.assertNotIn('name', readonly)
        self.assertNotIn('permissions', readonly)
