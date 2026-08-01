from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.forms import (
    InstitutionForm,
    ProjectUpdateRemediationAttachmentForm,
)
from apps.operations.models import Institution
from apps.operations.tests.helpers import create_institution, create_user


class InstitutionLegalDocumentPreviewTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user(username='institution-legal-admin')
        self.client.force_login(self.user)

    def institution_form_data(self, institution=None, **overrides):
        source = institution
        data = {
            'name': source.name if source else 'Institución legal',
            'institution_type': source.institution_type if source else 'foundation',
            'role': source.role if source else Institution.Role.DONOR,
            'country': source.country.code if source else 'VE',
            'contact_email': source.contact_email if source else '',
            'contact_phone': source.contact_phone if source else '',
            'responsible_person': source.responsible_person if source else '',
            'status': source.status if source else Institution.Status.ACTIVE,
        }
        data.update(overrides)
        return data

    def create_institution_with_legal_document(self, filename='legal.pdf', content=b'legal-data'):
        institution = create_institution(name='Institución con documento')
        institution.legal_document = SimpleUploadedFile(
            filename, content, content_type='application/pdf',
        )
        institution.save(update_fields=('legal_document', 'updated_at'))
        return institution

    def assert_single_file_input_without_multiple(self, content):
        self.assertEqual(content.count('type="file"'), 1)
        self.assertIn('name="legal_document"', content)
        self.assertNotIn('multiple', content.split('type="file"')[1].split('>')[0])

    def assert_preview_mounts_present(self, response):
        self.assertContains(response, 'data-file-upload-preview')
        self.assertContains(response, 'class="ops-file-upload"')
        self.assertContains(response, 'data-file-upload-list')
        self.assertContains(response, 'data-file-upload-summary')

    def test_create_page_renders_preview_contract_without_clearable_controls(self):
        response = self.client.get(reverse('institution_create'))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/object_form.html')
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'type="submit"')
        self.assertNotContains(response, 'Actualmente:')
        self.assertNotContains(response, 'name="legal_document-clear"')
        self.assertNotContains(response, 'id="legal_document-clear_id"')

    def test_edit_page_without_legal_document_renders_preview_without_clearable_controls(self):
        institution = create_institution(name='Institución sin documento')
        response = self.client.get(reverse('institution_update', args=[institution.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)
        self.assertNotContains(response, 'Actualmente:')
        self.assertNotContains(response, 'name="legal_document-clear"')

    def test_edit_page_with_legal_document_keeps_clearable_controls_and_preview_mounts(self):
        institution = self.create_institution_with_legal_document()
        filename = Path(institution.legal_document.name).name
        response = self.client.get(reverse('institution_update', args=[institution.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Actualmente:')
        self.assertContains(response, institution.legal_document.url)
        self.assertContains(response, 'name="legal_document-clear"')
        self.assertContains(response, 'id="legal_document-clear_id"')
        self.assertContains(response, 'for="legal_document-clear_id"')
        self.assertContains(response, 'Limpiar')
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)

        preview_list_chunk = content.split('data-file-upload-list', 1)[1].split('</div>', 1)[0]
        self.assertNotIn(filename, preview_list_chunk)
        self.assertNotIn(institution.legal_document.name, preview_list_chunk)

    def test_edit_unchanged_preserves_existing_legal_document(self):
        institution = self.create_institution_with_legal_document()
        original_name = institution.legal_document.name

        response = self.client.post(
            reverse('institution_update', args=[institution.pk]),
            data=self.institution_form_data(institution),
        )

        institution.refresh_from_db()
        self.assertRedirects(response, reverse('institution_list'))
        self.assertEqual(institution.legal_document.name, original_name)
        self.assertTrue(institution.legal_document)

    def test_edit_replace_stores_new_legal_document(self):
        institution = self.create_institution_with_legal_document()
        original_name = institution.legal_document.name
        replacement = SimpleUploadedFile(
            'replacement.pdf', b'replacement-data', content_type='application/pdf',
        )

        response = self.client.post(
            reverse('institution_update', args=[institution.pk]),
            data={
                **self.institution_form_data(institution),
                'legal_document': replacement,
            },
        )

        institution.refresh_from_db()
        self.assertRedirects(response, reverse('institution_list'))
        self.assertTrue(institution.legal_document)
        self.assertNotEqual(institution.legal_document.name, original_name)
        self.assertIn('replacement', institution.legal_document.name)

    def test_edit_clear_only_empties_legal_document(self):
        institution = self.create_institution_with_legal_document()

        response = self.client.post(
            reverse('institution_update', args=[institution.pk]),
            data=self.institution_form_data(institution, **{'legal_document-clear': 'on'}),
        )

        institution.refresh_from_db()
        self.assertRedirects(response, reverse('institution_list'))
        self.assertFalse(institution.legal_document)

    def test_edit_clear_plus_replacement_is_form_invalid_and_preserves_file(self):
        institution = self.create_institution_with_legal_document()
        original_name = institution.legal_document.name
        replacement = SimpleUploadedFile(
            'contradiction.pdf', b'contradiction-data', content_type='application/pdf',
        )

        response = self.client.post(
            reverse('institution_update', args=[institution.pk]),
            data={
                **self.institution_form_data(institution, **{'legal_document-clear': 'on'}),
                'legal_document': replacement,
            },
        )

        institution.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.context['form'].has_error('legal_document', code='contradiction'),
        )
        self.assertFormError(
            response.context['form'],
            'legal_document',
            'Por favor envíe un fichero o marque la casilla de limpiar, pero no ambos.',
        )
        self.assertEqual(institution.legal_document.name, original_name)

    def test_validation_redisplay_keeps_preview_and_clearable_controls(self):
        institution = self.create_institution_with_legal_document()

        response = self.client.post(
            reverse('institution_update', args=[institution.pk]),
            data=self.institution_form_data(institution, name=''),
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'name', 'Este campo es obligatorio.')
        self.assert_preview_mounts_present(response)
        self.assert_single_file_input_without_multiple(content)
        self.assertContains(response, 'name="legal_document-clear"')
        self.assertContains(response, 'Actualmente:')

    def test_authorized_legal_document_download_succeeds(self):
        institution = self.create_institution_with_legal_document(
            filename='downloadable.pdf', content=b'download-bytes',
        )

        response = self.client.get(
            reverse('institution_legal_document_download', args=[institution.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'download-bytes')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('downloadable.pdf', response['Content-Disposition'])

    def test_user_without_permission_cannot_download_legal_document(self):
        institution = self.create_institution_with_legal_document()
        limited = get_user_model().objects.create_user(
            username='no-institution-download', password='pass-12345',
        )
        self.client.force_login(limited)

        response = self.client.get(
            reverse('institution_legal_document_download', args=[institution.pk]),
        )

        self.assertEqual(response.status_code, 403)

    def test_missing_stored_legal_document_download_returns_404(self):
        institution = self.create_institution_with_legal_document()
        stored_path = Path(institution.legal_document.path)
        stored_path.unlink()

        response = self.client.get(
            reverse('institution_legal_document_download', args=[institution.pk]),
        )

        self.assertEqual(response.status_code, 404)

    def test_detail_does_not_expose_direct_storage_url(self):
        institution = self.create_institution_with_legal_document()
        viewer = get_user_model().objects.create_user(
            username='institution-legal-viewer', password='pass-12345',
        )
        viewer.user_permissions.add(Permission.objects.get(codename='view_institution'))
        self.client.force_login(viewer)

        response = self.client.get(reverse('institution_detail', args=[institution.pk]))

        self.assertContains(
            response,
            reverse('institution_legal_document_download', args=[institution.pk]),
        )
        self.assertNotContains(response, institution.legal_document.url)
        self.assertNotContains(response, institution.legal_document.name)

    def test_preview_opt_in_is_limited_to_institution_legal_document(self):
        institution_form = InstitutionForm()
        remediation_form = ProjectUpdateRemediationAttachmentForm()

        self.assertEqual(
            institution_form.fields['legal_document'].widget.attrs.get(
                'data-file-upload-preview'
            ),
            'true',
        )
        for field_name in institution_form.fields:
            if field_name == 'legal_document':
                continue
            self.assertNotIn(
                'data-file-upload-preview',
                institution_form.fields[field_name].widget.attrs,
            )
        self.assertNotIn(
            'data-file-upload-preview',
            remediation_form.fields['file'].widget.attrs,
        )
