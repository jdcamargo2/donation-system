from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project
from apps.operations.services import register_advance
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project


class InternalExperienceTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='operator', password='pass-12345')
        self.client.force_login(self.user)
        self.institution = create_institution(name='Fundación Operativa')
        self.project = create_project(code='PRJ-OPS-001', name='Proyecto operativo')
        self.project.status = Project.Status.ACTIVE
        self.project.save()
        self.donation = create_donation(code='DON-OPS-001', donor=self.institution)
        self.allocation = create_allocation(donation=self.donation, project=self.project)
        self.expense = create_expense(allocation=self.allocation)
        self.project_update = register_advance(
            project_id=self.project.pk,
            title='Avance operativo',
            description='Evidencia interna del avance.',
            created_by=self.user,
        )

    def test_central_list_views_use_specific_templates_and_columns(self):
        cases = [
            (
                reverse('institution_list'),
                'web/institution_list.html',
                ['Nombre', 'Tipo', 'País', 'Estado', 'Acciones', 'Fundación Operativa'],
            ),
            (
                reverse('donation_list'),
                'web/donation_list.html',
                ['Código', 'Donante', 'Monto', 'Asignado', 'Disponible', 'Fecha', 'DON-OPS-001'],
            ),
            (
                reverse('allocation_list'),
                'web/allocation_list.html',
                ['Donación', 'Proyecto', 'Monto asignado', 'Ejecutado', 'Disponible', 'Categoría'],
            ),
            (
                reverse('expense_list'),
                'web/expense_list.html',
                ['Asignación', 'Proyecto', 'Monto', 'Categoría', 'Estado', 'Soporte', 'Fecha'],
            ),
            (
                reverse('project_update_list'),
                'web/project_update_list.html',
                ['Proyecto', 'Título', 'Estado', 'Creado', 'Evidencia', 'Avance operativo'],
            ),
        ]

        for url, template_name, expected_texts in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                self.assertContains(response, 'ops-table-card')
                self.assertContains(response, 'class="table-responsive"')
                for text in expected_texts:
                    self.assertContains(response, text)

    def test_financial_detail_views_show_relationships_and_metrics(self):
        cases = [
            (
                reverse('institution_detail', args=[self.institution.pk]),
                'web/institution_detail.html',
                ['Datos de la institución', 'Donaciones asociadas', 'Fundación Operativa', 'Venezuela'],
            ),
            (
                reverse('donation_detail', args=[self.donation.pk]),
                'web/donation_detail.html',
                ['Monto total', 'Monto asignado', 'Monto disponible', 'Asignaciones vinculadas', 'DON-OPS-001'],
            ),
            (
                reverse('allocation_detail', args=[self.allocation.pk]),
                'web/allocation_detail.html',
                ['Donación origen', 'Proyecto destino', 'Monto asignado', 'Ejecutado', 'Disponible', 'Gastos asociados'],
            ),
        ]

        for url, template_name, expected_texts in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                for text in expected_texts:
                    self.assertContains(response, text)

    def test_user_without_permissions_still_gets_403_on_specific_internal_template_view(self):
        limited_user = get_user_model().objects.create_user(username='limited', password='pass-12345')
        self.client.force_login(limited_user)

        response = self.client.get(reverse('donation_list'))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_still_redirects_to_login_on_specific_internal_template_view(self):
        self.client.logout()

        response = self.client.get(reverse('donation_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
