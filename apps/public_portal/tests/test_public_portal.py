from pathlib import Path
from decimal import Decimal
from datetime import date

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdate
from apps.operations.services import (
    finish_project,
    publish_project,
    publish_project_update,
    register_advance,
    unpublish_project,
)
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
        self.project.is_public = True
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

    def create_approved_update_for_project_status(self, code, project_status, *, is_public=True):
        project = create_project(code=code, name=f'Proyecto {project_status}')
        project.status = Project.Status.ACTIVE
        project.is_public = is_public
        project.save(update_fields=['status', 'is_public'])
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
        self.assertContains(response, 'USD')
        self.assertContains(response, 'SIGEDON Transparencia')
        self.assertContains(response, 'Conoce cómo se gestionan los recursos y proyectos comunitarios.')
        self.assertContains(response, 'Portal público de transparencia')
        self.assertContains(response, 'Explorar proyectos')
        self.assertContains(response, 'Ver todos los avances')
        self.assertContains(response, 'id="metodologia"')
        self.assertNotContains(response, 'id="datos-abiertos"')
        self.assertContains(response, 'El portal no publica datos personales, usuarios internos ni archivos privados.')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_list_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, self.project.name)
        self.assertContains(response, 'El portal no publica datos personales, usuarios internos ni archivos privados.')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_list_uses_public_metadata_and_integrated_action(self):
        self.project.location = 'Caracas'
        self.project.start_date = date(2026, 1, 15)
        self.project.end_date = date(2026, 6, 30)
        self.project.save(update_fields=['location', 'start_date', 'end_date'])

        response = self.client.get(reverse('public_portal:public_project_list'))
        content = response.content.decode()

        self.assertContains(response, '1 proyectos activos')
        self.assertContains(response, 'Caracas')
        self.assertContains(response, '15/01/2026 — 30/06/2026')
        self.assertContains(response, 'public-project-card')
        self.assertNotIn('>Activo<', content)
        self.assertNotIn('Ver proyecto', content)
        self.assertNotIn('Ver detalle', content)
        self.assertNotIn('La información financiera detallada está disponible', content)

    def test_public_project_list_omits_missing_public_metadata(self):
        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertNotContains(response, 'Ubicación')
        self.assertNotContains(response, 'Periodo')

    def test_public_project_list_only_shows_active_public_projects(self):
        private_active = create_project(code='PRJ-PRIVATE-ACTIVE', name='Activo no público')
        private_active.status = Project.Status.ACTIVE
        private_active.is_public = False
        private_active.save(update_fields=['status', 'is_public'])
        closed_public = create_project(code='PRJ-CLOSED-PUBLIC', name='Cerrado público')
        closed_public.status = Project.Status.CLOSED
        closed_public.is_public = True
        closed_public.save(update_fields=['status', 'is_public'])

        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertContains(response, self.project.name)
        self.assertNotContains(response, private_active.name)
        self.assertNotContains(response, closed_public.name)

    def test_public_project_detail_for_non_public_project_returns_404(self):
        private_project = create_project(code='PRJ-PRIVATE', name='Proyecto no publicable')
        private_project.status = Project.Status.ACTIVE
        private_project.is_public = False
        private_project.save(update_fields=['status', 'is_public'])

        response = self.client.get(reverse('public_portal:public_project_detail', args=[private_project.pk]))

        self.assertEqual(response.status_code, 404)

    def test_publication_lifecycle_services_control_public_visibility_and_cache(self):
        private_project = create_project(code='PRJ-LIFECYCLE-PUB', name='Ciclo de publicación')
        detail_url = reverse('public_portal:public_project_detail', args=[private_project.pk])
        list_url = reverse('public_portal:public_project_list')

        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertNotContains(self.client.get(list_url), private_project.name)

        publish_project(project_id=private_project.pk, actor=self.user)
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertContains(self.client.get(list_url), private_project.name)

        unpublish_project(project_id=private_project.pk, actor=self.user)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertNotContains(self.client.get(list_url), private_project.name)

        publish_project(project_id=private_project.pk, actor=self.user)
        finish_project(private_project.pk, actor=self.user)
        private_project.refresh_from_db()
        self.assertEqual(private_project.status, Project.Status.CLOSED)
        self.assertFalse(private_project.is_public)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertNotContains(self.client.get(list_url), private_project.name)

    def test_public_project_detail_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'USD')
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, self.project.name)
        self.assertContains(response, 'public-back-link')
        self.assert_public_response_is_sanitized(response)

    def test_public_project_detail_uses_editorial_hierarchy_and_safe_metadata(self):
        self.project.location = 'Caracas'
        self.project.start_date = date(2026, 1, 15)
        self.project.end_date = date(2026, 6, 30)
        self.project.objective = 'Fortalecer la respuesta comunitaria.'
        self.project.save()

        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))
        content = response.content.decode()

        self.assertContains(
            response,
            f'<title>{self.project.name} | Transparencia SIGEDON</title>',
            html=False,
        )
        self.assertContains(response, 'Proyecto comunitario')
        self.assertContains(response, 'Caracas')
        self.assertContains(response, '15/01/2026 — 30/06/2026')
        self.assertContains(response, 'Fondos asignados')
        self.assertContains(response, 'Recursos ejecutados')
        self.assertContains(response, 'Disponible por ejecutar')
        self.assertContains(response, 'Las cifras están expresadas en dólares estadounidenses.')
        self.assertContains(response, 'Fortalecer la respuesta comunitaria.')
        self.assertContains(response, 'href="/transparency/#metodologia"', html=False)
        self.assertEqual(content.count('<h1'), 1)
        self.assertIn('public-financial-summary', content)
        self.assertIn('public-project-information', content)
        self.assertIn('public-project-updates', content)
        self.assertIn('public-methodology-note', content)
        self.assertNotIn('>Activo<', content)
        self.assertNotIn('Presupuesto estimado', content)
        self.assertNotIn('public-notice-inline', content)

    def test_public_project_detail_empty_updates_explains_publication_policy(self):
        self.approved_update.status = ProjectUpdate.Status.DRAFT
        self.approved_update.save(update_fields=['status'])

        response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))

        self.assertContains(response, 'Este proyecto todavía no tiene avances publicados.')
        self.assertContains(response, 'Las actualizaciones aparecerán después de ser revisadas y aprobadas.')

    def test_public_updates_feed_returns_200_without_login(self):
        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_base.html')
        self.assertContains(response, 'Avances publicados')
        self.assertContains(response, 'public-update-card')
        self.assert_public_response_is_sanitized(response)

    def test_public_updates_feed_uses_secondary_heading_without_published_badge(self):
        response = self.client.get(reverse('public_portal:public_updates_feed'))
        content = response.content.decode()

        self.assertEqual(content.count('<h1'), 1)
        self.assertContains(response, '<h1 class="public-page-title">Avances publicados</h1>', html=False)
        self.assertNotIn('>Publicado<', content)
        self.assertNotIn('Ver proyecto', content)

    def test_public_navigation_marks_only_the_current_primary_page(self):
        pages = (
            ('public_home', 'Inicio'),
            ('public_project_list', 'Proyectos'),
            ('public_updates_feed', 'Avances'),
        )

        for url_name, label in pages:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(f'public_portal:{url_name}'))
                content = response.content.decode()

                self.assertIn(f'aria-current="page">{label}</a>', content)
                self.assertEqual(content.count('aria-current="page"'), 1)

    def test_public_list_empty_states_use_the_publication_messages(self):
        self.project.is_public = False
        self.project.save(update_fields=['is_public'])

        projects_response = self.client.get(reverse('public_portal:public_project_list'))
        updates_response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertContains(projects_response, 'No hay proyectos activos publicados en este momento.')
        self.assertContains(updates_response, 'No hay avances publicados en este momento.')

    def test_public_list_cards_are_single_links_to_their_public_details(self):
        project_response = self.client.get(reverse('public_portal:public_project_list'))
        updates_response = self.client.get(reverse('public_portal:public_updates_feed'))
        project_url = reverse('public_portal:public_project_detail', args=[self.project.pk])
        update_url = reverse('public_portal:public_project_update_detail', args=[self.approved_update.pk])

        self.assertContains(project_response, f'class="public-project-card" href="{project_url}"', html=False)
        self.assertContains(updates_response, f'class="public-update-card" href="{update_url}"', html=False)
        self.assertNotContains(project_response, 'public-card-link')
        self.assertNotContains(updates_response, 'public-link-arrow')
        self.assertNotContains(updates_response, 'public-card-actions')

    def test_public_project_update_detail_is_available_only_for_published_updates_of_active_projects(self):
        public_url = reverse('public_portal:public_project_update_detail', args=[self.approved_update.pk])

        response = self.client.get(public_url)
        draft_response = self.client.get(
            reverse('public_portal:public_project_update_detail', args=[self.pending_update.pk])
        )
        missing_response = self.client.get(reverse('public_portal:public_project_update_detail', args=[999999]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'public_portal/public_project_update_detail.html')
        self.assertEqual(draft_response.status_code, 404)
        self.assertEqual(missing_response.status_code, 404)

        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=['status'])
        inactive_project_response = self.client.get(public_url)

        self.assertEqual(inactive_project_response.status_code, 404)

    def test_public_project_update_detail_shows_only_authorized_content(self):
        self.approved_update.update_date = date(2026, 7, 17)
        self.approved_update.save(update_fields=['update_date'])
        response = self.client.get(
            reverse('public_portal:public_project_update_detail', args=[self.approved_update.pk])
        )
        content = response.content.decode()
        project_url = reverse('public_portal:public_project_detail', args=[self.project.pk])

        self.assertContains(response, f'<title>{self.approved_update.title} | Transparencia SIGEDON</title>', html=False)
        self.assertContains(response, self.approved_update.title)
        self.assertContains(response, '17 de julio de 2026')
        self.assertContains(response, self.approved_update.description)
        self.assertContains(response, self.project.name)
        self.assertContains(response, self.project.code)
        self.assertContains(response, f'href="{project_url}"', html=False)
        self.assertContains(response, 'Volver a avances')
        self.assertContains(response, 'Este avance fue revisado y aprobado para su publicación en el portal.')
        self.assertEqual(content.count('<h1'), 1)
        for private_term in (
            'Publicado',
            'Porcentaje de progreso',
            'Responsable institucional',
            'Comité',
            '/media/',
            'Kobo',
            'panel interno',
            self.user.username,
            self.reporter.username,
        ):
            with self.subTest(private_term=private_term):
                self.assertNotIn(private_term, content)

    def test_home_and_project_timeline_link_each_update_to_its_public_detail(self):
        update_url = reverse('public_portal:public_project_update_detail', args=[self.approved_update.pk])
        home_response = self.client.get(reverse('public_portal:public_home'))
        project_response = self.client.get(reverse('public_portal:public_project_detail', args=[self.project.pk]))

        self.assertContains(home_response, f'class="public-update-card" href="{update_url}"', html=False)
        self.assertContains(project_response, f'class="public-detail-update" href="{update_url}"', html=False)
        self.assertNotContains(project_response, '<a class="public-detail-update"><a', html=False)

    def test_public_card_extracts_are_clamped_without_backend_truncation(self):
        project_source = Path('templates/public_portal/public_project_list.html').read_text()
        updates_source = Path('templates/public_portal/public_updates_feed.html').read_text()
        home_source = Path('templates/public_portal/public_home.html').read_text()
        stylesheet = Path('static/public_portal/css/public_portal.css').read_text()

        self.assertNotIn('truncatewords', project_source)
        self.assertNotIn('truncatewords', updates_source)
        self.assertNotIn('truncatewords', home_source)
        self.assertIn('class="public-card-excerpt"', project_source)
        self.assertIn('class="public-card-excerpt"', updates_source)
        self.assertIn('class="public-card-excerpt"', home_source)
        self.assertIn('-webkit-line-clamp: 3;', stylesheet)

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

    def test_public_portal_does_not_show_approved_evidence_from_non_public_project(self):
        private_project = create_project(code='PRJ-NO-PUBLIC', name='Proyecto privado con evidencia')
        private_project.status = Project.Status.ACTIVE
        private_project.is_public = True
        private_project.save(update_fields=['status', 'is_public'])
        private_update = register_advance(
            project_id=private_project.pk,
            title='Avance aprobado que dejo de ser público',
            description='No debe aparecer cuando el proyecto deja de estar público.',
            created_by=self.user,
            reported_by=self.reporter,
        )
        publish_project_update(private_update.pk, self.user)
        private_project.is_public = False
        private_project.save(update_fields=['is_public'])

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

    def test_published_update_count_excludes_private_and_closed_projects(self):
        self.create_approved_update_for_project_status(
            'PRJ-PRIVATE-COUNT',
            Project.Status.ACTIVE,
            is_public=False,
        )
        self.create_approved_update_for_project_status('PRJ-CLOSED-COUNT', Project.Status.CLOSED)

        response = self.client.get(reverse('public_portal:public_home'))

        self.assertEqual(response.context['summary']['published_update_count'], 1)

    def test_every_update_in_public_feed_links_to_an_available_public_detail(self):
        private_project, private_update = self.create_approved_update_for_project_status(
            'PRJ-PRIVATE-LINK',
            Project.Status.ACTIVE,
            is_public=False,
        )
        closed_project, closed_update = self.create_approved_update_for_project_status(
            'PRJ-CLOSED-LINK',
            Project.Status.CLOSED,
        )

        response = self.client.get(reverse('public_portal:public_updates_feed'))

        self.assertNotContains(response, reverse('public_portal:public_project_detail', args=[private_project.pk]))
        self.assertNotContains(response, reverse('public_portal:public_project_detail', args=[closed_project.pk]))
        self.assertNotContains(response, private_update.title)
        self.assertNotContains(response, closed_update.title)
        for update in response.context['updates']:
            detail_url = reverse('public_portal:public_project_update_detail', args=[update.pk])
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
        self.assertIn('.public-skip-link', source)
        self.assertIn('.public-primary-metrics', source)
        self.assertIn('.public-financial-context', source)
        self.assertIn('.public-information-section', source)
        self.assertIn('.public-footer-links', source)

    def test_public_home_contains_methodology_and_privacy_sections_without_json_promotion(self):
        response = self.client.get(reverse('public_portal:public_home'))

        self.assertContains(response, 'Metodología')
        self.assertContains(response, 'href="/transparency/#metodologia"')
        self.assertContains(response, 'solo proyectos ACTIVE y avances PUBLISHED')
        self.assertContains(response, 'TTL de caché')
        self.assertNotContains(response, 'Datos abiertos')
        self.assertNotContains(response, '#datos-abiertos')
        self.assertNotContains(response, reverse('public_portal:public_projects_json'))
        self.assertNotContains(response, reverse('public_portal:public_metrics_json'))
        self.assert_public_response_is_sanitized(response)

    def test_public_home_has_accessible_layout_and_four_primary_metrics(self):
        response = self.client.get(reverse('public_portal:public_home'))
        content = response.content.decode()

        self.assertContains(response, '<meta name="description"', html=False)
        self.assertContains(response, 'Saltar al contenido principal')
        self.assertContains(response, '<main class="public-main" id="main-content"', html=False)
        self.assertContains(response, 'aria-label="Navegación pública"')
        self.assertEqual(content.count('<h1'), 1)
        self.assertEqual(content.count('class="public-metric"'), 4)
        self.assertNotIn('public-hero-visual', content)
        self.assertNotIn('public-data-card', content)
        self.assertNotIn('panel interno', content)

    def test_public_home_keeps_currency_and_value_together(self):
        source = Path('templates/public_portal/public_home.html').read_text()
        stylesheet = Path('static/public_portal/css/public_portal.css').read_text()

        self.assertEqual(source.count('class="public-money-value"'), 4)
        self.assertIn('{{ summary.total_received|money_es }} USD</span>', source)
        self.assertIn('{{ summary.total_executed|money_es }} USD</span>', source)
        self.assertIn('white-space: nowrap;', stylesheet)
        self.assertIn('font-size: clamp(1.2rem, 2.4vw, 2rem);', stylesheet)

    def test_public_home_shows_at_most_five_recent_updates(self):
        for index in range(5):
            update = register_advance(
                project_id=self.project.pk,
                title=f'Avance adicional {index}',
                description='Avance público adicional para comprobar el límite de la portada.',
                created_by=self.user,
                reported_by=self.reporter,
            )
            publish_project_update(update.pk, self.user)

        response = self.client.get(reverse('public_portal:public_home'))

        self.assertEqual(response.content.decode().count('class="public-update-card"'), 5)
        self.assertNotContains(response, self.approved_update.title)

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
            project.is_public = True
            project.save(update_fields=['status', 'is_public'])

        response = self.client.get(reverse('public_portal:public_project_list'))

        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['projects']), 20)

    def test_public_updates_feed_is_paginated_by_twenty(self):
        for index in range(21):
            project = create_project(code=f'PRJ-UPD-{index:03d}', name=f'Proyecto avance {index}')
            project.status = Project.Status.ACTIVE
            project.is_public = True
            project.save(update_fields=['status', 'is_public'])
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

    def test_public_pagination_uses_clear_non_linked_boundary_controls(self):
        for index in range(21):
            project = create_project(code=f'PRJ-PAGE-{index:03d}', name=f'Proyecto paginado {index}')
            project.status = Project.Status.ACTIVE
            project.is_public = True
            project.save(update_fields=['status', 'is_public'])

        response = self.client.get(reverse('public_portal:public_project_list'))
        content = response.content.decode()

        self.assertIn('<span aria-disabled="true">Anterior</span>', content)
        self.assertIn('aria-current="page">Página 1 de 2</span>', content)
        self.assertIn('href="?page=2">Siguiente</a>', content)

    def test_public_pagination_preserves_existing_query_parameters(self):
        for index in range(21):
            project = create_project(code=f'PRJ-QUERY-{index:03d}', name=f'Proyecto consulta {index}')
            project.status = Project.Status.ACTIVE
            project.is_public = True
            project.save(update_fields=['status', 'is_public'])

        response = self.client.get(f"{reverse('public_portal:public_project_list')}?source=home")

        self.assertContains(response, 'href="?source=home&amp;page=2">Siguiente</a>', html=False)

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
