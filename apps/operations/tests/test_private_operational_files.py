import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import AuditLog, Institution, Project, ProjectUpdate


class PrivateOperationalFileDownloadTests(TestCase):
    def setUp(self):
        # PRE: Django test storage may be redirected to a temporary directory.
        # POST: creates isolated media storage and representative protected objects.
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.institution = Institution.objects.create(
            name='Institución privada',
            institution_type='foundation',
            role=Institution.Role.DONOR,
        )
        self.project = Project.objects.create(
            code='PRJ-PRIVATE-FILES',
            name='Proyecto privado',
            status=Project.Status.ACTIVE,
        )
        self.project_update = ProjectUpdate.objects.create(
            project=self.project,
            title='Avance privado',
            description='Evidencia protegida.',
            status=ProjectUpdate.Status.PENDING_REVIEW,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def create_user_with_permission(self, username, codename):
        # PRE: codename identifies one generated operations permission.
        # POST: returns a persisted authenticated-capable user with that permission.
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        permission = Permission.objects.get(
            codename=codename,
            content_type__app_label='operations',
        )
        user.user_permissions.add(permission)
        return user

    def store_legal_document(self, name='nested/legal report.pdf', content=b'legal'):
        # PRE: institution exists and name is a relative storage name.
        # POST: stores content and associates its generated path with the institution.
        self.institution.legal_document.save(name, ContentFile(content), save=True)

    def store_evidence(self, name='nested/evidence report.pdf', content=b'evidence'):
        # PRE: project update exists and name is a relative storage name.
        # POST: stores content and associates its generated path with the update.
        self.project_update.evidence.save(name, ContentFile(content), save=True)

    def test_legal_document_download_requires_permission_and_uses_safe_basename(self):
        self.store_legal_document()
        user = self.create_user_with_permission('institution-viewer', 'view_institution')
        self.client.force_login(user)

        response = self.client.get(
            reverse('institution_legal_document_download', args=(self.institution.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'legal')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('legal_report.pdf', response['Content-Disposition'])
        self.assertNotIn('institution_documents/', response['Content-Disposition'])

    def test_evidence_download_requires_permission_and_uses_safe_basename(self):
        self.store_evidence()
        user = self.create_user_with_permission('update-viewer', 'view_projectupdate')
        self.client.force_login(user)

        response = self.client.get(
            reverse('project_update_evidence_download', args=(self.project_update.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'evidence')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('evidence_report.pdf', response['Content-Disposition'])
        self.assertNotIn('project_updates/', response['Content-Disposition'])

    def test_anonymous_access_redirects_to_login(self):
        self.store_legal_document()
        self.store_evidence()
        urls = (
            reverse('institution_legal_document_download', args=(self.institution.pk,)),
            reverse('project_update_evidence_download', args=(self.project_update.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_authenticated_users_without_permissions_receive_403(self):
        self.store_legal_document()
        self.store_evidence()
        user = get_user_model().objects.create_user('private-file-denied')
        self.client.force_login(user)
        urls = (
            reverse('institution_legal_document_download', args=(self.institution.pk,)),
            reverse('project_update_evidence_download', args=(self.project_update.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_missing_objects_and_empty_file_fields_return_404(self):
        institution_user = self.create_user_with_permission('missing-institution', 'view_institution')
        update_user = self.create_user_with_permission('missing-update', 'view_projectupdate')
        scenarios = (
            (
                institution_user,
                reverse('institution_legal_document_download', args=(999999,)),
            ),
            (
                institution_user,
                reverse('institution_legal_document_download', args=(self.institution.pk,)),
            ),
            (
                update_user,
                reverse('project_update_evidence_download', args=(999999,)),
            ),
            (
                update_user,
                reverse('project_update_evidence_download', args=(self.project_update.pk,)),
            ),
        )
        for user, url in scenarios:
            with self.subTest(url=url):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_file_missing_from_storage_returns_404(self):
        self.institution.legal_document = 'institution_documents/missing.pdf'
        self.institution.save(update_fields=('legal_document',))
        self.project_update.evidence = 'project_updates/missing.pdf'
        self.project_update.save(update_fields=('evidence',))
        scenarios = (
            (
                self.create_user_with_permission('missing-legal-storage', 'view_institution'),
                reverse('institution_legal_document_download', args=(self.institution.pk,)),
            ),
            (
                self.create_user_with_permission('missing-evidence-storage', 'view_projectupdate'),
                reverse('project_update_evidence_download', args=(self.project_update.pk,)),
            ),
        )
        for user, url in scenarios:
            with self.subTest(url=url):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_detail_templates_never_render_storage_urls(self):
        self.store_legal_document(content=b'private-legal')
        self.store_evidence(content=b'private-evidence')
        user = get_user_model().objects.create_superuser('private-template-admin')
        self.client.force_login(user)
        responses = (
            self.client.get(reverse('institution_detail', args=(self.institution.pk,))),
            self.client.get(reverse('project_detail', args=(self.project.pk,))),
            self.client.get(reverse('project_update_detail', args=(self.project_update.pk,))),
            self.client.get(reverse('project_update_review', args=(self.project_update.pk,))),
        )
        private_urls = (
            self.institution.legal_document.url,
            self.project_update.evidence.url,
        )
        for response in responses:
            with self.subTest(template=response.templates[0].name if response.templates else ''):
                self.assertEqual(response.status_code, 200)
                for private_url in private_urls:
                    self.assertNotContains(response, private_url)
                self.assertNotContains(response, settings.MEDIA_URL)

        template_paths = (
            'institution_detail.html',
            'project_detail.html',
            'project_update_detail.html',
            'project_update_review.html',
        )
        for template_name in template_paths:
            source = Path('templates/web', template_name).read_text()
            self.assertNotIn('.url', source)
            self.assertNotIn('MEDIA_URL', source)

    def test_download_get_does_not_modify_models_or_create_audit(self):
        self.store_legal_document()
        self.store_evidence()
        user = get_user_model().objects.create_superuser('private-file-readonly')
        self.client.force_login(user)
        before = {
            'institution': Institution.objects.values().get(pk=self.institution.pk),
            'update': ProjectUpdate.objects.values().get(pk=self.project_update.pk),
            'audit_count': AuditLog.objects.count(),
        }

        legal_response = self.client.get(
            reverse('institution_legal_document_download', args=(self.institution.pk,))
        )
        evidence_response = self.client.get(
            reverse('project_update_evidence_download', args=(self.project_update.pk,))
        )
        b''.join(legal_response.streaming_content)
        b''.join(evidence_response.streaming_content)

        self.assertEqual(
            Institution.objects.values().get(pk=self.institution.pk),
            before['institution'],
        )
        self.assertEqual(
            ProjectUpdate.objects.values().get(pk=self.project_update.pk),
            before['update'],
        )
        self.assertEqual(AuditLog.objects.count(), before['audit_count'])

    def test_querystring_or_post_cannot_select_another_storage_path(self):
        self.store_legal_document(content=b'authorized')
        Path(self.media_root, 'secret.txt').write_bytes(b'not-authorized')
        user = self.create_user_with_permission('querystring-viewer', 'view_institution')
        self.client.force_login(user)
        url = reverse('institution_legal_document_download', args=(self.institution.pk,))

        response = self.client.get(url, {'path': 'secret.txt'})

        self.assertEqual(b''.join(response.streaming_content), b'authorized')
        self.assertEqual(self.client.post(url, {'path': 'secret.txt'}).status_code, 405)
