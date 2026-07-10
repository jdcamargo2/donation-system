from pathlib import Path

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdate
from apps.operations.services import register_advance, review_project_update
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project, create_user


class PublicPortalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_user()
        self.institution = create_institution()
        self.institution.contact_email = 'privado@example.com'
        self.institution.contact_phone = '+58-000-privado'
        self.institution.save()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.description = 'Descripción pública del proyecto.'
        self.project.save()
        donation = create_donation(donor=self.institution)
        allocation = create_allocation(donation=donation, project=self.project)
        create_expense(allocation=allocation)
        self.approved_update = register_advance(
            project_id=self.project.pk,
            title='Avance aprobado',
            description='Descripción pública aprobada.',
            created_by=self.user,
        )
        review_project_update(
            update_id=self.approved_update.pk,
            reviewer=self.user,
            status=ProjectUpdate.Status.APPROVED,
            notes='Nota interna de revisión.',
        )
        self.pending_update = register_advance(
            project_id=self.project.pk,
            title='Avance pendiente privado',
            description='Pendiente de revisión.',
            created_by=self.user,
        )
        self.rejected_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance rechazado privado',
            description='No debe mostrarse.',
            status=ProjectUpdate.Status.REJECTED,
            reviewed_at=self.approved_update.reviewed_at,
        )
        self.draft_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance borrador privado',
            description='No debe mostrarse.',
            status=ProjectUpdate.Status.DRAFT,
        )

    def assert_public_response_is_sanitized(self, response):
        self.assertNotContains(response, 'project_update_create')
        self.assertNotContains(response, 'project_update_create_for_project')
        self.assertNotContains(response, 'project_update_review')
        self.assertNotContains(response, 'project_update_update')
        self.assertNotContains(response, 'project_update_delete')
        self.assertNotContains(response, '/admin/')
        self.assertNotContains(response, 'privado@example.com')
        self.assertNotContains(response, '+58-000-privado')
        self.assertNotContains(response, self.user.username)
        self.assertNotContains(response, 'Nota interna de revisión.')

    def test_public_home_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, 'SIGEDON Transparencia')
        self.assertContains(response, 'Transparencia pública para seguir proyectos y avances')
        self.assertContains(response, 'public-metrics-overlap')
        self.assertContains(response, 'metric-icon')
        self.assertContains(response, 'public-notice-inline')
        self.assertContains(response, 'public-editorial-section')
        self.assertContains(response, 'public-editorial-grid')
        self.assertContains(response, 'public-link-arrow')
        self.assertContains(response, 'public-card-actions')
        self.assertContains(response, 'id="metodologia"')
        self.assertContains(response, 'id="datos-abiertos"')
        self.assertContains(response, 'No se publican datos personales')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_list_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, self.project.name)
        self.assertContains(response, 'No publica datos personales')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_list_only_shows_active_projects(self):
        planned_project = create_project(code='PRJ-PLAN', name='Proyecto planificado')
        planned_project.status = Project.Status.PLANNED
        planned_project.save()
        suspended_project = create_project(code='PRJ-SUSP', name='Proyecto suspendido')
        suspended_project.status = Project.Status.SUSPENDED
        suspended_project.save()

        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertContains(response, self.project.name)
        self.assertNotContains(response, planned_project.name)
        self.assertNotContains(response, suspended_project.name)

    def test_public_project_detail_for_non_active_project_returns_404(self):
        planned_project = create_project(code='PRJ-PRIVATE', name='Proyecto no publicable')
        planned_project.status = Project.Status.PLANNED
        planned_project.save()

        response = self.client.get(reverse('public_portal:public_project_detail', args=[planned_project.pk]))

        self.assertEqual(response.status_code, 404)

    def test_public_project_detail_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, self.project.name)
        self.assertContains(response, 'public-notice-inline')
        self.assertContains(response, 'public-back-link')
        self.assert_public_response_is_sanitized(response)

    def test_public_updates_feed_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, 'Avances aprobados')
        self.assertContains(response, 'public-link-arrow')
        self.assertContains(response, 'public-card-actions')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_detail_only_shows_approved_updates(self):
        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))

        self.assertContains(response, self.approved_update.title)
        self.assertNotContains(response, self.pending_update.title)
        self.assertNotContains(response, self.rejected_update.title)
        self.assertNotContains(response, self.draft_update.title)

    def test_public_updates_feed_only_shows_approved_updates(self):
        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertContains(response, self.approved_update.title)
        self.assertNotContains(response, self.pending_update.title)
        self.assertNotContains(response, self.rejected_update.title)
        self.assertNotContains(response, self.draft_update.title)

    def test_public_portal_views_do_not_import_operations_views_or_forms(self):
        views_source = Path('apps/public_portal/views.py').read_text()

        self.assertNotIn('apps.operations.views', views_source)
        self.assertNotIn('apps.operations.forms', views_source)
        self.assertNotIn('from apps.operations.views', views_source)
        self.assertNotIn('from apps.operations.forms', views_source)
        self.assertNotIn('import apps.operations.views', views_source)
        self.assertNotIn('import apps.operations.forms', views_source)

    def test_public_portal_selectors_do_not_import_operations_views_or_forms(self):
        selectors_source = Path('apps/public_portal/selectors.py').read_text()

        self.assertNotIn('apps.operations.views', selectors_source)
        self.assertNotIn('apps.operations.forms', selectors_source)

    def test_public_templates_do_not_extend_internal_base(self):
        for template_path in Path('templates/public_portal').glob('*.html'):
            source = template_path.read_text()
            with self.subTest(template=template_path.name):
                if template_path.name == 'public_base.html':
                    continue
                self.assertNotIn('{% extends "base.html" %}', source)
                self.assertIn('{% extends "public_portal/public_base.html" %}', source)

    def test_public_base_uses_independent_public_stylesheet(self):
        source = Path('templates/public_portal/public_base.html').read_text()

        self.assertIn('public-header', source)
        self.assertIn("public_portal/css/public_portal.css", source)
        self.assertNotIn("web/css/sigedon.css", source)

    def test_public_stylesheet_defines_public_visual_system(self):
        source = Path('static/public_portal/css/public_portal.css').read_text()

        self.assertIn(
            '@import url("https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,400..800;1,14..32,400..800&family=Merriweather:opsz,wght@18..144,700..900&display=swap");',
            source,
        )
        self.assertIn('--public-bg', source)
        self.assertIn('--public-surface', source)
        self.assertIn('--public-surface-soft', source)
        self.assertIn('--public-text', source)
        self.assertIn('--public-muted', source)
        self.assertIn('--public-primary', source)
        self.assertIn('--public-border', source)
        self.assertIn('--public-shadow', source)
        self.assertIn('font-family: "Inter"', source)
        self.assertIn('font-family: "Merriweather"', source)
        self.assertIn('prefers-reduced-motion', source)
        self.assertIn('public-hero-visual', source)
        self.assertIn('.metric-card:hover', source)
        self.assertIn('.public-notice-inline', source)
        self.assertIn('.public-editorial-item', source)
        self.assertIn('.public-link-arrow:hover span', source)
        self.assertIn('.public-card-actions', source)
        self.assertIn('.public-action-secondary', source)
        self.assertIn('.public-back-link', source)

    def test_public_home_contains_methodology_data_and_privacy_sections(self):
        response = self.client.get(reverse('public_portal:public_home'))

        self.assertContains(response, 'Metodología')
        self.assertContains(response, 'Datos abiertos')
        self.assertContains(response, 'href="/transparency/#metodologia"')
        self.assertContains(response, 'href="/transparency/#datos-abiertos"')
        self.assertContains(response, 'No se publican datos personales')
        self.assert_public_response_is_sanitized(response)

    def test_public_base_does_not_contain_internal_navigation_or_session_state(self):
        source = Path('templates/public_portal/public_base.html').read_text()
        forbidden_terms = [
            'dashboard',
            'admin',
            'logout',
            'login',
            'request.user',
            'user.is_authenticated',
            'project_update_create',
            'project_update_create_for_project',
            'project_update_review',
            'project_update_update',
            'project_update_delete',
        ]

        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, source)

    def test_public_project_list_is_paginated_by_twenty(self):
        for index in range(21):
            project = create_project(code=f'PRJ-PUB-{index:03d}', name=f'Proyecto público {index}')
            project.status = Project.Status.ACTIVE
            project.save()

        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['projects']), 20)

    def test_public_updates_feed_is_paginated_by_twenty(self):
        for index in range(21):
            project = create_project(code=f'PRJ-UPD-{index:03d}', name=f'Proyecto avance {index}')
            project.status = Project.Status.ACTIVE
            project.save()
            update = register_advance(
                project_id=project.pk,
                title=f'Avance aprobado {index}',
                description='Avance aprobado para paginación.',
            )
            review_project_update(
                update_id=update.pk,
                reviewer=self.user,
                status=ProjectUpdate.Status.APPROVED,
            )

        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['updates']), 20)
