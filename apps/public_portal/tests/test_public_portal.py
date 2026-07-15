from pathlib import Path
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdate
from apps.operations.services import register_advance, publish_project_update
from apps.operations.tests.helpers import create_allocation, create_donation, create_expense, create_institution, create_project, create_user


class PublicPortalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_user()
        self.reporter = create_user('public-update-reporter')
        self.institution = create_institution()
        self.institution.contact_email = 'privado@example.com'
        self.institution.contact_phone = '+58-000-privado'
        self.institution.save()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.description = 'Descripción pública del proyecto.'
        self.project.save()
        self.donation = create_donation(donor=self.institution)
        self.allocation = create_allocation(donation=self.donation, project=self.project)
        self.expense = create_expense(allocation=self.allocation)
        self.approved_update = register_advance(
            project_id=self.project.pk,
            title='Avance aprobado',
            description='Descripción pública aprobada.',
            created_by=self.user,
            reported_by=self.reporter,
        )
        publish_project_update(self.approved_update.pk, self.user)
        self.pending_update = register_advance(
            project_id=self.project.pk,
            title='Avance pendiente privado',
            description='Pendiente de revisión.',
            created_by=self.user,
            reported_by=self.reporter,
        )
        self.rejected_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance rechazado privado',
            description='No debe mostrarse.',
            status=ProjectUpdate.Status.DRAFT,
        )
        self.draft_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance borrador privado',
            description='No debe mostrarse.',
            status=ProjectUpdate.Status.DRAFT,
        )

    def create_approved_update_for_project_status(self, code, project_status):
        project = create_project(code=code, name=f'Proyecto {project_status}')
        project.status = Project.Status.ACTIVE
        project.save(update_fields=['status'])
        update = register_advance(
            project_id=project.pk,
            title=f'Avance aprobado {project_status}',
            description='Avance que no debe permanecer publicado.',
            created_by=self.user,
            reported_by=self.reporter,
        )
        publish_project_update(update.pk, self.user)
        project.status = project_status
        project.save(update_fields=['status'])
        return project, update

    def assert_public_response_is_sanitized(self, response):
        self.assertNotContains(response, 'project_update_create')
        self.assertNotContains(response, 'project_update_create_for_project')
        self.assertNotContains(response, 'project_update_publish')
        self.assertNotContains(response, 'project_update_update')
        self.assertNotContains(response, 'project_update_delete')
        self.assertNotContains(response, '/admin/')
        self.assertNotContains(response, 'privado@example.com')
        self.assertNotContains(response, '+58-000-privado')
        self.assertNotContains(response, self.user.username)
        self.assertNotContains(response, self.reporter.username)
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
        self.assertContains(response, 'Avances publicados')
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

    def test_public_project_detail_does_not_expose_operational_file_urls(self):
        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))
        self.assertNotContains(response, '/media/')

    def test_public_portal_does_not_show_draft_update(self):
        detail_response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))
        feed_response = self.client.get(reverse('public_portal:public_updates_feed'))
        self.assertNotContains(detail_response, self.pending_update.title)
        self.assertNotContains(feed_response, self.pending_update.title)

    def test_public_portal_does_not_show_approved_evidence_from_non_active_project(self):
        private_project = create_project(code='PRJ-NO-PUBLIC', name='Proyecto suspendido con evidencia')
        private_project.status = Project.Status.ACTIVE
        private_project.save()
        private_update = register_advance(
            project_id=private_project.pk,
            title='Avance aprobado que dejo de ser público',
            description='No debe aparecer cuando el proyecto deja de estar activo.',
            created_by=self.user,
            reported_by=self.reporter,
        )
        publish_project_update(private_update.pk, self.user)
        private_project.status = Project.Status.SUSPENDED
        private_project.save(update_fields=['status'])

        feed_response = self.client.get(reverse('public_portal:public_updates_feed'))
        home_response = self.client.get(reverse('public_portal:public_home'))

        self.assertNotContains(feed_response, private_update.title)
        self.assertNotContains(feed_response, '/media/')
        self.assertEqual(home_response.context['summary']['published_update_count'], 1)

    def test_approved_update_from_closed_project_is_not_public(self):
        closed_project, closed_update = self.create_approved_update_for_project_status(
            'PRJ-CLOSED-UPD',
            Project.Status.CLOSED,
        )

        feed_response = self.client.get(reverse('public_portal:public_updates_feed'))
        detail_response = self.client.get(
            reverse('public_portal:public_project_detail', args=[closed_project.pk])
        )

        self.assertNotContains(feed_response, closed_update.title)
        self.assertEqual(detail_response.status_code, 404)

    def test_published_update_count_excludes_suspended_and_closed_projects(self):
        self.create_approved_update_for_project_status('PRJ-SUSP-COUNT', Project.Status.SUSPENDED)
        self.create_approved_update_for_project_status('PRJ-CLOSED-COUNT', Project.Status.CLOSED)

        response = self.client.get(reverse('public_portal:public_home'))

        self.assertEqual(response.context['summary']['published_update_count'], 1)

    def test_every_update_in_public_feed_links_to_an_available_public_detail(self):
        suspended_project, suspended_update = self.create_approved_update_for_project_status(
            'PRJ-SUSP-LINK',
            Project.Status.SUSPENDED,
        )
        closed_project, closed_update = self.create_approved_update_for_project_status(
            'PRJ-CLOSED-LINK',
            Project.Status.CLOSED,
        )

        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertNotContains(response, reverse('public_portal:public_project_detail', args=[suspended_project.pk]))
        self.assertNotContains(response, reverse('public_portal:public_project_detail', args=[closed_project.pk]))
        self.assertNotContains(response, suspended_update.title)
        self.assertNotContains(response, closed_update.title)
        for update in response.context['updates']:
            detail_url = reverse('public_portal:public_project_detail', args=[update.project_id])
            self.assertEqual(self.client.get(detail_url).status_code, 200)

    def test_public_metrics_only_include_finances_linked_to_active_projects(self):
        private_project = create_project(code='PRJ-CLOSED-FIN', name='Proyecto financiero cerrado')
        private_project.status = Project.Status.CLOSED
        private_project.save(update_fields=['status'])
        private_donation = create_donation(
            code='DON-PRIVATE-FIN',
            donor=self.institution,
            amount=Decimal('500.00'),
        )
        private_allocation = create_allocation(
            donation=private_donation,
            project=private_project,
            amount=Decimal('300.00'),
        )
        create_expense(
            allocation=private_allocation,
            amount=Decimal('100.00'),
            reason='Gasto no público',
        )

        response = self.client.get(reverse('public_portal:public_home'))
        summary = response.context['summary']

        self.assertEqual(summary['total_received'], self.donation.amount)
        self.assertEqual(summary['total_assigned'], self.allocation.amount)
        self.assertEqual(summary['total_executed'], self.expense.amount)
        self.assertEqual(summary['available_balance'], self.allocation.amount - self.expense.amount)

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
        self.assertNotIn("web/js/ops_forms.js", source)
        self.assertNotIn("vendor/flatpickr", source)
        self.assertNotIn("vendor/autonumeric", source)

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
            'project_update_publish',
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
                created_by=self.user,
                reported_by=self.reporter,
            )
            publish_project_update(update.pk, self.user)

        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['updates']), 20)

    def test_public_projects_json_does_not_expose_private_data(self):
        response = self.client.get(reverse('public_portal:public_projects_json'))
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('privado@example.com', content)
        self.assertNotIn('contact_email', content)
        self.assertNotIn('/media/', content)
        self.assertNotIn('uploaded_by', content)

    def test_public_json_endpoints_return_valid_structures(self):
        projects_response = self.client.get(reverse('public_portal:public_projects_json'))
        metrics_response = self.client.get(reverse('public_portal:public_metrics_json'))

        self.assertIsInstance(projects_response.json()['projects'], list)
        self.assertIn('code', projects_response.json()['projects'][0])
        self.assertIn('metrics', metrics_response.json())
        self.assertIn('project_count', metrics_response.json()['metrics'])
