from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboSubmission
from datetime import date
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse


class KoboReviewPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="kobo-viewer",
            password="test-password",
        )
        cls.reviewer = user_model.objects.create_user(
            username="kobo-reviewer",
            password="test-password",
        )
        cls.unprivileged = user_model.objects.create_user(
            username="no-kobo-permission",
            password="test-password",
        )
        view_permission = Permission.objects.get(codename="view_kobosubmission")
        change_permission = Permission.objects.get(codename="change_kobosubmission")
        cls.viewer.user_permissions.add(view_permission)
        cls.reviewer.user_permissions.add(view_permission, change_permission)
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
        )

    def setUp(self):
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="long-external-identifier-123456789",
            raw_payload={
                "_uuid": "raw-secret-marker",
                "_submitted_by": "internal-submitter",
                "deviceid": "private-device-id",
            },
            normalized_payload={
                "parish_delegate": "Sensitive Delegate",
                "contact_phone": "+58-secret-phone",
                "main_informant_role": "Sensitive Informant Role",
                "nucleo_code": "NV-001",
            },
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            pastoral_zone="zone-one",
            parish="parish-one",
            primary_community="community-one",
            assessment_date=date(2026, 7, 11),
        )
        self.attachment = KoboAttachment.objects.create(
            submission=self.submission,
            field_name="territorial_evidence/temple_photo",
            source_url="https://kf.example.test/private/source-secret",
            original_filename="remote-personal-name.jpg",
            content_type="image/jpeg",
            size_bytes=123,
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.DOWNLOADED,
            file="kobo-safe-attachment.jpg",
        )
        self.list_url = reverse("kobo:submission_list")
        self.detail_url = reverse(
            "kobo:submission_detail",
            args=(self.submission.pk,),
        )
        self.review_url = reverse(
            "kobo:submission_review",
            args=(self.submission.pk,),
        )

    def test_login_is_required(self):
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.url)

    def test_view_permission_is_required(self):
        self.client.force_login(self.unprivileged)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_list_hides_sensitive_data(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "+58-secret-phone")
        self.assertNotContains(response, "private-device-id")
        self.assertContains(response, "parish-one")

    def test_detail_separates_sensitive_data(self):
        self.client.force_login(self.viewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "Datos internos sensibles")
        self.assertContains(response, "Sensitive Delegate")
        self.assertContains(response, "Sensitive Informant Role")
        self.assertContains(response, "+58-secret-phone")
        self.assertContains(response, "internal-submitter")
        self.assertContains(response, "private-device-id")
        self.assertContains(response, "NV-001")

    def test_raw_payload_requires_existing_elevated_permission(self):
        self.client.force_login(self.viewer)
        viewer_response = self.client.get(self.detail_url)

        self.assertNotContains(viewer_response, "Raw payload")
        self.assertNotContains(viewer_response, "raw-secret-marker")

        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(self.detail_url)

        self.assertContains(reviewer_response, "Raw payload")
        self.assertContains(reviewer_response, "raw-secret-marker")

    def test_approval_changes_status_and_creates_event(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            self.review_url,
            {
                "decision": KoboSubmission.Status.APPROVED_FOR_IMPORT,
                "reason": "",
            },
        )
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        event = self.submission.processing_events.get()
        self.assertEqual(event.stage, "review")
        self.assertEqual(event.code, KoboSubmission.Status.APPROVED_FOR_IMPORT)

    def test_rejection_requires_reason(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            self.review_url,
            {"decision": KoboSubmission.Status.REJECTED, "reason": "   "},
        )
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "La razón es obligatoria", status_code=400)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.assertFalse(self.submission.processing_events.exists())

    def test_submission_cannot_be_reviewed_twice(self):
        self.client.force_login(self.reviewer)
        self.client.post(
            self.review_url,
            {
                "decision": KoboSubmission.Status.APPROVED_FOR_IMPORT,
                "reason": "",
            },
        )

        second_response = self.client.post(
            self.review_url,
            {"decision": KoboSubmission.Status.REJECTED, "reason": "Second"},
        )

        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(self.submission.processing_events.count(), 1)

    def test_get_cannot_execute_review(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(self.review_url)
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def test_detail_does_not_expose_attachment_source_or_private_link(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(self.detail_url)

        self.assertContains(response, "kobo-safe-attachment.jpg")
        self.assertNotContains(response, self.attachment.source_url)
        self.assertNotContains(response, "remote-personal-name.jpg")
        self.assertNotContains(response, "href=\"/media/")
