from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.operations.models import Expense, Project
from apps.operations.services import register_advance
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
)


def create_user(username):
    return get_user_model().objects.create_user(username=username, password='pass-12345')


def create_user_with_permissions(username, *permission_codenames):
    user = create_user(username)
    permissions = Permission.objects.filter(
        content_type__app_label='operations',
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


class OperationsPermissionTests(TestCase):
    def setUp(self):
        self.project = create_project()
        self.institution = create_institution(name='Institución de permisos')
        self.donation = create_donation(
            code='DON-PERMISSIONS',
            donor=self.institution,
        )
        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
        )
        self.expense = create_expense(allocation=self.allocation)
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.reporter = create_user_with_permissions(
            'permission-update-reporter', 'add_projectupdate'
        )
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance pendiente',
            description='Listo para revisión.',
            reported_by=self.reporter,
        )

    def test_anonymous_user_is_redirected_from_dashboard(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_authenticated_user_can_access_dashboard(self):
        self.client.force_login(create_user('dashboard-user'))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_from_project_list(self):
        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_user_without_view_project_permission_gets_403_on_project_list(self):
        self.client.force_login(create_user('no-view-project'))

        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 403)

    def test_user_with_view_project_permission_can_access_project_list(self):
        self.client.force_login(create_user_with_permissions('view-project', 'view_project'))

        response = self.client.get(reverse('project_list'))

        self.assertEqual(response.status_code, 200)

    def test_user_with_add_project_permission_can_access_project_create(self):
        self.client.force_login(create_user_with_permissions('add-project', 'add_project'))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 200)

    def test_user_without_add_project_permission_gets_403_on_project_create(self):
        self.client.force_login(create_user('no-add-project'))

        response = self.client.get(reverse('project_create'))

        self.assertEqual(response.status_code, 403)

    def test_project_and_institution_secondary_actions_follow_permissions(self):
        cases = (
            (
                'compact-viewer',
                ('view_project', 'view_institution'),
                (),
            ),
            (
                'compact-editor',
                (
                    'view_project', 'view_institution',
                    'change_project', 'change_institution',
                ),
                (
                    reverse('project_update', args=[self.project.pk]),
                    reverse('institution_update', args=[self.institution.pk]),
                ),
            ),
            (
                'compact-deleter',
                (
                    'view_project', 'view_institution',
                    'delete_project', 'delete_institution',
                ),
                (
                    reverse('project_delete', args=[self.project.pk]),
                    reverse('institution_delete', args=[self.institution.pk]),
                ),
            ),
        )
        action_urls = (
            reverse('project_update', args=[self.project.pk]),
            reverse('project_delete', args=[self.project.pk]),
            reverse('institution_update', args=[self.institution.pk]),
            reverse('institution_delete', args=[self.institution.pk]),
        )

        for username, permissions, expected_urls in cases:
            with self.subTest(username=username):
                user = create_user_with_permissions(username, *permissions)
                self.client.force_login(user)
                responses = (
                    self.client.get(reverse('project_list')),
                    self.client.get(reverse('institution_list')),
                )
                combined_html = ''.join(
                    response.content.decode() for response in responses
                )

                self.assertIn(
                    reverse('project_detail', args=[self.project.pk]),
                    combined_html,
                )
                self.assertIn(
                    reverse('institution_detail', args=[self.institution.pk]),
                    combined_html,
                )
                for action_url in action_urls:
                    assertion = self.assertIn if action_url in expected_urls else self.assertNotIn
                    assertion(action_url, combined_html)
                if expected_urls:
                    self.assertIn('data-bs-boundary="viewport"', combined_html)
                    self.assertIn('dropdown-menu dropdown-menu-end', combined_html)

    def test_project_and_institution_delete_links_keep_post_confirmation_and_csrf(self):
        user = create_user_with_permissions(
            'compact-delete-confirmation',
            'view_project', 'view_institution', 'delete_project', 'delete_institution',
        )
        self.client.force_login(user)

        cases = (
            (
                reverse('project_list'),
                reverse('project_delete', args=[self.project.pk]),
                self.project,
            ),
            (
                reverse('institution_list'),
                reverse('institution_delete', args=[self.institution.pk]),
                self.institution,
            ),
        )
        for list_url, delete_url, instance in cases:
            with self.subTest(delete_url=delete_url):
                list_response = self.client.get(list_url)
                confirmation_response = self.client.get(delete_url)

                self.assertContains(list_response, delete_url)
                self.assertContains(confirmation_response, '<form method="post">')
                self.assertContains(confirmation_response, 'name="csrfmiddlewaretoken"')
                self.assertTrue(type(instance).objects.filter(pk=instance.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        for _, delete_url, instance in cases:
            with self.subTest(csrf_delete_url=delete_url):
                self.assertEqual(csrf_client.post(delete_url).status_code, 403)
                self.assertTrue(type(instance).objects.filter(pk=instance.pk).exists())

    def test_donation_secondary_actions_follow_permissions(self):
        edit_url = reverse('donation_update', args=[self.donation.pk])
        delete_url = reverse('donation_delete', args=[self.donation.pk])
        cases = (
            ('donation-viewer', ('view_donation',), ()),
            (
                'donation-editor',
                ('view_donation', 'change_donation'),
                (edit_url,),
            ),
            (
                'donation-deleter',
                ('view_donation', 'delete_donation'),
                (delete_url,),
            ),
        )

        for username, permissions, expected_urls in cases:
            with self.subTest(username=username):
                user = create_user_with_permissions(username, *permissions)
                self.client.force_login(user)
                response = self.client.get(reverse('donation_list'))

                self.assertContains(
                    response,
                    reverse('donation_detail', args=[self.donation.pk]),
                )
                for action_url in (edit_url, delete_url):
                    assertion = (
                        self.assertContains
                        if action_url in expected_urls
                        else self.assertNotContains
                    )
                    assertion(response, action_url)
                if expected_urls:
                    self.assertContains(response, 'data-bs-boundary="viewport"')
                    self.assertContains(response, 'dropdown-menu dropdown-menu-end')

    def test_donation_delete_link_keeps_post_confirmation_and_csrf(self):
        user = create_user_with_permissions(
            'donation-delete-confirmation',
            'view_donation',
            'delete_donation',
        )
        self.client.force_login(user)
        delete_url = reverse('donation_delete', args=[self.donation.pk])

        list_response = self.client.get(reverse('donation_list'))
        confirmation_response = self.client.get(delete_url)

        self.assertContains(list_response, delete_url)
        self.assertContains(confirmation_response, '<form method="post">')
        self.assertContains(confirmation_response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(type(self.donation).objects.filter(pk=self.donation.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        self.assertEqual(csrf_client.post(delete_url).status_code, 403)
        self.assertTrue(type(self.donation).objects.filter(pk=self.donation.pk).exists())

    def test_allocation_secondary_actions_follow_permissions(self):
        edit_url = reverse('allocation_update', args=[self.allocation.pk])
        delete_url = reverse('allocation_delete', args=[self.allocation.pk])
        cases = (
            ('allocation-viewer', ('view_fundallocation',), ()),
            (
                'allocation-editor',
                ('view_fundallocation', 'change_fundallocation'),
                (edit_url,),
            ),
            (
                'allocation-deleter',
                ('view_fundallocation', 'delete_fundallocation'),
                (delete_url,),
            ),
        )

        for username, permissions, expected_urls in cases:
            with self.subTest(username=username):
                user = create_user_with_permissions(username, *permissions)
                self.client.force_login(user)
                response = self.client.get(reverse('allocation_list'))

                self.assertContains(
                    response,
                    reverse('allocation_detail', args=[self.allocation.pk]),
                )
                for action_url in (edit_url, delete_url):
                    assertion = (
                        self.assertContains
                        if action_url in expected_urls
                        else self.assertNotContains
                    )
                    assertion(response, action_url)
                if expected_urls:
                    self.assertContains(response, 'data-bs-boundary="viewport"')
                    self.assertContains(response, 'dropdown-menu dropdown-menu-end')

    def test_allocation_delete_link_keeps_post_confirmation_and_csrf(self):
        user = create_user_with_permissions(
            'allocation-delete-confirmation',
            'view_fundallocation',
            'delete_fundallocation',
        )
        self.client.force_login(user)
        delete_url = reverse('allocation_delete', args=[self.allocation.pk])

        list_response = self.client.get(reverse('allocation_list'))
        confirmation_response = self.client.get(delete_url)

        self.assertContains(list_response, delete_url)
        self.assertContains(confirmation_response, '<form method="post">')
        self.assertContains(confirmation_response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(type(self.allocation).objects.filter(pk=self.allocation.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        self.assertEqual(csrf_client.post(delete_url).status_code, 403)
        self.assertTrue(type(self.allocation).objects.filter(pk=self.allocation.pk).exists())

    def test_expense_secondary_actions_follow_permissions_and_state(self):
        edit_url = reverse('expense_update', args=[self.expense.pk])
        delete_url = reverse('expense_delete', args=[self.expense.pk])
        cases = (
            ('expense-viewer', ('view_expense',), ()),
            (
                'expense-editor',
                ('view_expense', 'change_expense'),
                (edit_url,),
            ),
            (
                'expense-deleter',
                ('view_expense', 'delete_expense'),
                (delete_url,),
            ),
        )

        for username, permissions, expected_urls in cases:
            with self.subTest(username=username):
                user = create_user_with_permissions(username, *permissions)
                self.client.force_login(user)
                response = self.client.get(reverse('expense_list'))

                self.assertContains(
                    response,
                    reverse('expense_detail', args=[self.expense.pk]),
                )
                for action_url in (edit_url, delete_url):
                    assertion = (
                        self.assertContains
                        if action_url in expected_urls
                        else self.assertNotContains
                    )
                    assertion(response, action_url)
                if expected_urls:
                    self.assertContains(response, 'data-bs-boundary="viewport"')
                    self.assertContains(response, 'dropdown-menu dropdown-menu-end')

        annulled = create_expense(
            allocation=self.allocation,
            reason='Gasto anulado sin acciones',
            status=Expense.Status.ANNULLED,
        )
        user = create_user_with_permissions(
            'expense-annulled-actions',
            'view_expense',
            'change_expense',
            'delete_expense',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('expense_list'))

        self.assertContains(response, reverse('expense_detail', args=[annulled.pk]))
        self.assertNotContains(response, reverse('expense_update', args=[annulled.pk]))
        self.assertNotContains(response, reverse('expense_delete', args=[annulled.pk]))

    def test_expense_delete_link_keeps_post_confirmation_and_csrf(self):
        user = create_user_with_permissions(
            'expense-delete-confirmation',
            'view_expense',
            'delete_expense',
        )
        self.client.force_login(user)
        delete_url = reverse('expense_delete', args=[self.expense.pk])

        list_response = self.client.get(reverse('expense_list'))
        confirmation_response = self.client.get(delete_url)

        self.assertContains(list_response, delete_url)
        self.assertContains(confirmation_response, '<form method="post">')
        self.assertContains(confirmation_response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(type(self.expense).objects.filter(pk=self.expense.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        self.assertEqual(csrf_client.post(delete_url).status_code, 403)
        self.assertTrue(type(self.expense).objects.filter(pk=self.expense.pk).exists())

    def test_change_projectupdate_permission_can_edit_but_not_publish(self):
        self.client.force_login(create_user_with_permissions(
            'edit-update', 'view_projectupdate', 'change_projectupdate'
        ))

        list_response = self.client.get(reverse('project_update_list'))
        edit_response = self.client.get(reverse('project_update_update', args=[self.project_update.pk]))
        publish_response = self.client.post(reverse('project_update_publish', args=[self.project_update.pk]))

        self.assertContains(
            list_response,
            reverse('project_update_update', args=[self.project_update.pk]),
        )
        self.assertNotContains(
            list_response,
            f'action="{reverse("project_update_publish", args=[self.project_update.pk])}"',
        )
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(publish_response.status_code, 403)
        self.project_update.refresh_from_db()
        self.assertEqual(self.project_update.status, 'draft')

    def test_publish_projectupdate_permission_can_publish_without_editing(self):
        self.client.force_login(create_user_with_permissions(
            'publish-update', 'view_projectupdate', 'publish_projectupdate'
        ))

        publish_url = reverse('project_update_publish', args=[self.project_update.pk])
        list_response = self.client.get(reverse('project_update_list'))
        edit_response = self.client.get(reverse('project_update_update', args=[self.project_update.pk]))
        get_publish_response = self.client.get(publish_url)

        self.project_update.refresh_from_db()
        self.assertContains(list_response, f'action="{publish_url}"')
        self.assertContains(list_response, 'method="post"')
        self.assertContains(list_response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(
            list_response,
            reverse('project_update_update', args=[self.project_update.pk]),
        )
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(get_publish_response.status_code, 405)
        self.assertEqual(self.project_update.status, 'draft')

        publish_response = self.client.post(publish_url)
        published_list_response = self.client.get(reverse('project_update_list'))

        self.assertEqual(publish_response.status_code, 302)
        self.assertNotContains(published_list_response, f'action="{publish_url}"')

    def test_user_without_project_update_mutation_permissions_does_not_see_or_execute_publish(self):
        self.client.force_login(create_user_with_permissions('view-update', 'view_projectupdate'))

        publish_url = reverse('project_update_publish', args=[self.project_update.pk])
        list_response = self.client.get(reverse('project_update_list'))
        publish_response = self.client.post(publish_url)

        self.assertNotContains(list_response, f'action="{publish_url}"')
        self.assertEqual(publish_response.status_code, 403)
        self.project_update.refresh_from_db()
        self.assertEqual(self.project_update.status, 'draft')

    def test_project_update_list_omits_arbitrary_progress_representation(self):
        self.client.force_login(
            create_user_with_permissions('view-update-progress', 'view_projectupdate')
        )

        response = self.client.get(reverse('project_update_list'))

        self.assertNotContains(response, '<th>Progreso</th>')
        self.assertNotContains(response, 'role="progressbar"')
        self.assertNotContains(
            response,
            f'>{self.project_update.progress_percentage}%<',
        )

    def test_project_update_secondary_actions_follow_permissions(self):
        detail_url = reverse('project_update_detail', args=[self.project_update.pk])
        edit_url = reverse('project_update_update', args=[self.project_update.pk])
        delete_url = reverse('project_update_delete', args=[self.project_update.pk])
        viewer = create_user_with_permissions('view-secondary-actions', 'view_projectupdate')
        self.client.force_login(viewer)

        viewer_response = self.client.get(reverse('project_update_list'))

        self.assertContains(viewer_response, detail_url)
        self.assertNotContains(viewer_response, edit_url)
        self.assertNotContains(viewer_response, delete_url)

        deleter = create_user_with_permissions(
            'delete-secondary-action', 'view_projectupdate', 'delete_projectupdate'
        )
        self.client.force_login(deleter)
        deleter_response = self.client.get(reverse('project_update_list'))

        self.assertContains(deleter_response, detail_url)
        self.assertNotContains(deleter_response, edit_url)
        self.assertContains(deleter_response, delete_url)

    def test_project_update_publish_rejects_post_without_csrf_token(self):
        publisher = create_user_with_permissions(
            'publish-update-csrf', 'view_projectupdate', 'publish_projectupdate'
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(publisher)

        response = csrf_client.post(
            reverse('project_update_publish', args=[self.project_update.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.project_update.refresh_from_db()
        self.assertEqual(self.project_update.status, 'draft')

    def test_user_with_view_auditlog_permission_can_access_audit_log_list(self):
        self.client.force_login(create_user_with_permissions('view-auditlog', 'view_auditlog'))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 200)

    def test_user_without_view_auditlog_permission_gets_403_on_audit_log_list(self):
        self.client.force_login(create_user('no-view-auditlog'))

        response = self.client.get(reverse('audit_log_list'))

        self.assertEqual(response.status_code, 403)
