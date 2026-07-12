from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project
from apps.operations.tests.helpers import create_project, create_user


class SearchAndExportTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.active = create_project(code='PRJ-SEARCH-001', name='Proyecto Alfa')
        self.active.status = Project.Status.ACTIVE
        self.active.save(update_fields=('status',))
        self.closed = create_project(code='PRJ-SEARCH-002', name='Proyecto Beta')
        self.closed.status = Project.Status.CLOSED
        self.closed.save(update_fields=('status',))

    def test_search_by_code(self):
        response = self.client.get(reverse('project_list'), {'q': 'SEARCH-001'})

        self.assertContains(response, self.active.code)
        self.assertNotContains(response, self.closed.code)

    def test_filter_by_status(self):
        response = self.client.get(reverse('project_list'), {'status': Project.Status.CLOSED})

        self.assertContains(response, self.closed.code)
        self.assertNotContains(response, self.active.code)

    def test_combines_search_and_status_filters(self):
        response = self.client.get(
            reverse('project_list'), {'q': 'Proyecto', 'status': Project.Status.ACTIVE}
        )

        self.assertContains(response, self.active.code)
        self.assertNotContains(response, self.closed.code)

    def test_csv_respects_list_filters_and_uses_canonical_amount(self):
        response = self.client.get(
            reverse('project_export_csv'), {'status': Project.Status.ACTIVE}
        )
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Código,Nombre,Estado,Presupuesto USD', content)
        self.assertIn(self.active.code, content)
        self.assertNotIn(self.closed.code, content)
        self.assertIn(str(self.active.estimated_budget), content)

    def test_csv_requires_view_permission(self):
        limited_user = get_user_model().objects.create_user('no-export', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('project_export_csv'))

        self.assertEqual(response.status_code, 403)
