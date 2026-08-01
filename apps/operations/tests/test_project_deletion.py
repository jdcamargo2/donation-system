from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from apps.operations.admin import InstitutionAdmin, ProjectAdmin
from apps.operations.models import (
    AuditLog,
    Institution,
    Project,
    ProjectDeletionForbiddenError,
    ProjectMilestone,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_PROJECT_UPDATE_DECIDER,
    ROLE_PROJECT_UPDATE_REVIEWER,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import finish_project, publish_project, unpublish_project
from apps.operations.tests.helpers import create_institution, create_project


def create_user_with_permissions(username, *permission_codenames):
    user = get_user_model().objects.create_user(username=username, password='pass-12345')
    permissions = Permission.objects.filter(
        content_type__app_label='operations',
        codename__in=permission_codenames,
    )
    user.user_permissions.add(*permissions)
    return user


class ProjectDeletionGuardTests(TestCase):
    def setUp(self):
        self.project = create_project(code='PRJ-DEL-GUARD', name='Proyecto inmutable')

    def test_instance_delete_raises_and_preserves_project(self):
        with self.assertRaises(ProjectDeletionForbiddenError):
            self.project.delete()

        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_queryset_filter_delete_raises_and_preserves_project(self):
        with self.assertRaises(ProjectDeletionForbiddenError):
            Project.objects.filter(pk=self.project.pk).delete()

        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_queryset_all_delete_raises_and_preserves_projects(self):
        other = create_project(code='PRJ-DEL-OTHER', name='Otro proyecto')

        with self.assertRaises(ProjectDeletionForbiddenError):
            Project.objects.all().delete()

        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(Project.objects.filter(pk=other.pk).exists())

    def test_rejected_deletion_creates_no_audit_log(self):
        audit_count = AuditLog.objects.count()

        with self.assertRaises(ProjectDeletionForbiddenError):
            self.project.delete()
        with self.assertRaises(ProjectDeletionForbiddenError):
            Project.objects.filter(pk=self.project.pk).delete()

        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_ordinary_create_filter_update_finish_and_publication_still_work(self):
        created = Project.objects.create(code='PRJ-DEL-OK', name='Proyecto operable')
        actor = get_user_model().objects.create_user(username='project-deleter-actor')

        self.assertTrue(Project.objects.filter(code='PRJ-DEL-OK').exists())
        created.name = 'Proyecto operable editado'
        created.save(update_fields=('name', 'updated_at'))
        created.refresh_from_db()
        self.assertEqual(created.name, 'Proyecto operable editado')

        finish_project(created.pk, actor=actor)
        created.refresh_from_db()
        self.assertEqual(created.status, Project.Status.CLOSED)

        active = create_project(code='PRJ-DEL-PUB', name='Proyecto publicable')
        publish_project(project_id=active.pk, actor=actor)
        active.refresh_from_db()
        self.assertTrue(active.is_public)
        unpublish_project(project_id=active.pk, actor=actor)
        active.refresh_from_db()
        self.assertFalse(active.is_public)


class ProjectDeletionWebTests(TestCase):
    def setUp(self):
        self.project = create_project(code='PRJ-DEL-WEB', name='Proyecto web')
        self.institution = create_institution(name='Institución con borrado')
        self.admin = get_user_model().objects.create_superuser(
            username='project-delete-web-admin',
            password='pass-12345',
        )
        self.client.force_login(self.admin)

    def test_project_delete_named_route_is_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse('project_delete', args=[self.project.pk])

    def test_old_literal_project_delete_path_returns_404(self):
        response = self.client.get(f'/projects/{self.project.pk}/delete/')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_project_list_never_renders_eliminar_even_with_builtin_permission(self):
        with_perm = create_user_with_permissions(
            'project-list-with-delete-perm',
            'view_project',
            'delete_project',
        )
        without_perm = create_user_with_permissions(
            'project-list-without-delete-perm',
            'view_project',
        )
        for user in (with_perm, without_perm, self.admin):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('project_list'))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, '>Eliminar</a>')
                self.assertNotContains(response, f'/projects/{self.project.pk}/delete/')

    def test_project_detail_does_not_expose_project_deletion(self):
        response = self.client.get(reverse('project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'/projects/{self.project.pk}/delete/')
        self.assertNotContains(response, 'project_delete')

    def test_institution_delete_link_remains_available_with_permission(self):
        user = create_user_with_permissions(
            'institution-deleter',
            'view_institution',
            'delete_institution',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('institution_list'))
        self.assertContains(response, reverse('institution_delete', args=[self.institution.pk]))
        self.assertContains(response, 'Eliminar')


class ProjectDeletionAdminTests(TestCase):
    def setUp(self):
        self.project = create_project(code='PRJ-DEL-ADMIN', name='Proyecto admin')
        self.superuser = get_user_model().objects.create_superuser(
            username='project-delete-superuser',
            password='pass-12345',
        )
        self.client.force_login(self.superuser)
        self.factory = RequestFactory()
        self.model_admin = ProjectAdmin(Project, AdminSite())

    def test_has_delete_permission_is_false_for_superuser(self):
        request = self.factory.get('/')
        request.user = self.superuser
        self.assertFalse(self.model_admin.has_delete_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.project))

    def test_changelist_has_no_delete_selected_action(self):
        request = self.factory.get('/')
        request.user = self.superuser
        actions = self.model_admin.get_actions(request)
        self.assertNotIn('delete_selected', actions)

    def test_change_page_exposes_no_deletion_action(self):
        response = self.client.get(
            reverse('admin:operations_project_change', args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 200)
        delete_url = reverse('admin:operations_project_delete', args=[self.project.pk])
        self.assertNotContains(response, delete_url)

    def test_admin_delete_route_is_denied_and_preserves_project(self):
        delete_url = reverse('admin:operations_project_delete', args=[self.project.pk])
        get_response = self.client.get(delete_url)
        post_response = self.client.post(delete_url, {'post': 'yes'})

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_institution_admin_delete_permission_remains_available(self):
        institution = create_institution(name='Institución admin borrable')
        request = self.factory.get('/')
        request.user = self.superuser
        institution_admin = InstitutionAdmin(Institution, AdminSite())
        self.assertTrue(institution_admin.has_delete_permission(request, institution))


class ProjectDeletionRoleTests(TestCase):
    def setUp(self):
        sync_operation_roles()

    def test_operational_roles_do_not_receive_delete_project(self):
        role_names = (
            ROLE_SIGEDON_ADMIN,
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
            ROLE_PROJECT_UPDATE_REVIEWER,
            ROLE_PROJECT_UPDATE_DECIDER,
        )
        for role_name in role_names:
            with self.subTest(role=role_name):
                group = Group.objects.get(name=role_name)
                self.assertFalse(
                    group.permissions.filter(codename='delete_project').exists(),
                    role_name,
                )

    def test_resync_removes_delete_project_from_administrador_sigedon(self):
        group = Group.objects.get(name=ROLE_SIGEDON_ADMIN)
        permission = Permission.objects.get(
            content_type__app_label='operations',
            codename='delete_project',
        )
        group.permissions.add(permission)
        self.assertTrue(group.permissions.filter(codename='delete_project').exists())

        sync_operation_roles()

        self.assertFalse(group.permissions.filter(codename='delete_project').exists())
        sync_operation_roles()
        self.assertFalse(group.permissions.filter(codename='delete_project').exists())


class ProjectDeletionMilestoneTests(TestCase):
    def test_blocked_project_deletion_preserves_milestones(self):
        project = create_project(code='PRJ-DEL-MILE', name='Proyecto con hitos')
        milestone = ProjectMilestone.objects.create(
            project=project, title='Hito conservado', position=1
        )

        with self.assertRaises(ProjectDeletionForbiddenError):
            project.delete()

        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
        self.assertTrue(ProjectMilestone.objects.filter(pk=milestone.pk).exists())
