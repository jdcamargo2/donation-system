from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.operations.models import Donation, Institution
from apps.operations.tests.helpers import create_donation, create_institution


class InstitutionDetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='institution-detail-admin', password='pass-12345',
        )
        self.client.force_login(self.user)

    def test_detail_compacts_identity_and_omits_empty_sections(self):
        institution = create_institution(name='Institución compacta')
        response = self.client.get(reverse('institution_detail', args=[institution.pk]))
        content = response.content.decode()

        self.assertEqual(content.count(institution.get_role_display()), 1)
        self.assertEqual(content.count(institution.country.name), 1)
        self.assertEqual(content.count(institution.get_institution_type_display()), 1)
        self.assertEqual(content.count(institution.get_status_display()), 1)
        self.assertNotContains(response, 'Contacto')
        self.assertNotContains(response, 'Información institucional')
        self.assertNotContains(response, '>-<')
        self.assertContains(response, 'Información de registro')
        self.assertContains(response, 'Creada')
        self.assertContains(response, 'Actualizada')

    def test_detail_shows_legal_document_only_when_it_exists(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        institution = create_institution(name='Institución documentada')
        institution.legal_document = SimpleUploadedFile('legal.pdf', b'legal-data')
        institution.save(update_fields=('legal_document', 'updated_at'))

        response = self.client.get(reverse('institution_detail', args=[institution.pk]))

        self.assertContains(response, 'Información institucional')
        self.assertContains(response, 'Documento legal')
        self.assertContains(response, 'Descargar')
        self.assertContains(
            response,
            reverse('institution_legal_document_download', args=[institution.pk]),
        )
        self.assertNotContains(response, institution.legal_document.name)

    def test_detail_limits_donations_in_stable_order_and_links_to_existing_filter(self):
        institution = create_institution(name='Institución con donaciones')
        donations = []
        for index in range(6):
            donation = create_donation(
                code=f'DON-INST-{index}', donor=institution,
            )
            donation.received_date = date(2026, 7, index + 1)
            donation.save(update_fields=('received_date', 'updated_at'))
            donations.append(donation)

        response = self.client.get(reverse('institution_detail', args=[institution.pk]))

        self.assertEqual(response.context['institution_donation_count'], 6)
        self.assertTrue(response.context['has_more_institution_donations'])
        self.assertEqual(
            [donation.pk for donation in response.context['recent_institution_donations']],
            [donation.pk for donation in reversed(donations[1:])],
        )
        self.assertContains(response, 'Mostrando 5 de 6')
        self.assertContains(
            response,
            f'{reverse("donation_list")}?institution={institution.pk}',
        )
        self.assertNotContains(response, donations[0].code)

    def test_detail_donations_do_not_add_queries_per_row(self):
        one_donation_institution = create_institution(name='Una donación')
        many_donations_institution = create_institution(name='Seis donaciones')
        create_donation(code='DON-ONE', donor=one_donation_institution)
        for index in range(6):
            create_donation(code=f'DON-MANY-{index}', donor=many_donations_institution)

        self.client.get(reverse('institution_detail', args=[one_donation_institution.pk]))
        with CaptureQueriesContext(connection) as one_donation_queries:
            self.client.get(reverse('institution_detail', args=[one_donation_institution.pk]))
        with CaptureQueriesContext(connection) as many_donation_queries:
            self.client.get(reverse('institution_detail', args=[many_donations_institution.pk]))

        self.assertEqual(len(one_donation_queries), len(many_donation_queries))

    def test_delete_action_keeps_sweetalert_post_fallback_and_permissions(self):
        institution = create_institution(name='Institución eliminable')
        delete_url = reverse('institution_delete', args=[institution.pk])
        response = self.client.get(reverse('institution_detail', args=[institution.pk]))
        content = response.content.decode()

        self.assertContains(response, 'aria-label="Más acciones para Institución eliminable"')
        self.assertIn(f'href="{delete_url}"', content)
        self.assertIn(
            f'id="institution-delete-form" method="post" action="{delete_url}"', content,
        )
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'data-confirm-title="¿Eliminar esta institución?"')
        self.assertContains(response, 'data-confirm-variant="danger"')
        self.assertContains(response, 'web/js/confirm_actions.js')

        viewer = get_user_model().objects.create_user(
            username='institution-detail-viewer', password='pass-12345',
        )
        viewer.user_permissions.add(Permission.objects.get(codename='view_institution'))
        self.client.force_login(viewer)
        viewer_response = self.client.get(reverse('institution_detail', args=[institution.pk]))
        self.assertNotContains(viewer_response, delete_url)
        self.assertNotContains(viewer_response, '>Más<')

    def test_list_and_detail_render_monteluz_name_not_venezuela_or_code(self):
        institution = Institution.objects.create(
            name='Institución Monteluz',
            role=Institution.Role.DONOR,
            institution_type='foundation',
            country='ZZ',
        )

        list_response = self.client.get(reverse('institution_list'))
        detail_response = self.client.get(reverse('institution_detail', args=[institution.pk]))

        self.assertEqual(institution.country.name, 'República de Monteluz')
        for response in (list_response, detail_response):
            with self.subTest(path=response.request['PATH_INFO']):
                self.assertContains(response, 'República de Monteluz')
                self.assertNotContains(response, 'Venezuela')
                self.assertNotContains(response, '>ZZ<')


class InstitutionCountryDisplayAuditTests(SimpleTestCase):
    def test_operator_templates_use_country_name_not_code(self):
        templates = (
            Path('templates/web/institution_list.html'),
            Path('templates/web/institution_detail.html'),
        )
        forbidden = (
            '{{ institution.country }}',
            '{{ object.country }}',
        )
        for path in templates:
            source = path.read_text()
            with self.subTest(template=str(path)):
                self.assertIn('.country.name', source)
                for snippet in forbidden:
                    self.assertNotIn(snippet, source)

