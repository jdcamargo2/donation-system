from apps.integrations.kobo.attachments import build_safe_filename
from apps.integrations.kobo.attachments import download_and_store_attachment
from apps.integrations.kobo.attachments import process_pending_attachments
from apps.integrations.kobo.client import DownloadedContent
from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.tests.helpers import RecordingAttachmentStorage
from apps.integrations.kobo.tests.helpers import StubAttachmentClient
from datetime import timedelta
from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone as django_timezone
from io import StringIO
from unittest.mock import patch
import uuid


class KoboAttachmentProcessorTests(TestCase):
    JPEG_CONTENT = b"\xff\xd8\xffsafe-jpeg"
    PNG_CONTENT = b"\x89PNG\r\n\x1a\nsafe-png"

    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 - Territorio",
            version="20260710",
        )

    def setUp(self):
        self.storage = RecordingAttachmentStorage()
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="attachment-submission",
            raw_payload={"_uuid": "attachment-submission"},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def create_attachment(self, **overrides):
        # PRE: overrides contains valid KoboAttachment model fields.
        # POST: returns a persisted pending JPEG descriptor for this submission.
        values = {
            "submission": self.submission,
            "field_name": "territorial_evidence/temple_photo",
            "external_id": "attachment-uuid",
            "source_url": "https://kf.example.test/api/attachment/1",
            "original_filename": "../../remote/private/photo.jpg",
            "content_type": "image/jpeg",
            "privacy_level": KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            "status": KoboAttachment.Status.PENDING,
        }
        values.update(overrides)
        return KoboAttachment.objects.create(**values)

    def process(self, attachment, outcome, *, max_bytes=1024, storage=None):
        # PRE: attachment is persisted and outcome configures a fake download.
        # POST: runs storage processing without a real network request.
        return download_and_store_attachment(
            attachment,
            client=StubAttachmentClient([outcome]),
            storage=storage or self.storage,
            max_bytes=max_bytes,
        )

    def successful_download(self):
        return DownloadedContent(
            self.JPEG_CONTENT,
            "image/jpeg; charset=binary",
            len(self.JPEG_CONTENT),
        )

    def test_rejects_disallowed_mime_type(self):
        attachment = self.create_attachment(content_type="application/pdf")

        outcome = self.process(
            attachment,
            DownloadedContent(b"%PDF", "application/pdf", 4),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.INVALID)
        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_rejects_false_binary_signature(self):
        attachment = self.create_attachment()

        self.process(
            attachment,
            DownloadedContent(b"not-a-jpeg", "image/jpeg", 10),
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)

    def test_rejects_content_over_size_limit(self):
        attachment = self.create_attachment()

        self.process(
            attachment,
            DownloadedContent(self.JPEG_CONTENT, "image/jpeg", len(self.JPEG_CONTENT)),
            max_bytes=3,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)

    def test_builds_stable_safe_filename_without_remote_path(self):
        attachment = self.create_attachment(external_id="../../unsafe/id")

        filename = build_safe_filename(attachment, "jpg")

        self.assertNotIn("/", filename)
        self.assertNotIn("..", filename)
        self.assertNotIn("remote", filename)
        self.assertTrue(filename.endswith(".jpg"))

    def test_pending_is_claimed_and_processed(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(outcome.previous_status, KoboAttachment.Status.PENDING)
        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertEqual(attachment.error_message, "")

    def test_failed_attachment_can_be_retried(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.FAILED,
            error_message="Attachment download or storage failed.",
        )

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(outcome.previous_status, KoboAttachment.Status.FAILED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.error_message, "")

    def test_downloaded_and_invalid_are_skipped(self):
        downloaded = self.create_attachment(
            external_id="already-downloaded",
            status=KoboAttachment.Status.DOWNLOADED,
        )
        invalid = self.create_attachment(
            external_id="already-invalid",
            status=KoboAttachment.Status.INVALID,
            source_url="https://kf.example.test/api/attachment/invalid",
        )
        client = StubAttachmentClient([])

        downloaded_outcome = download_and_store_attachment(
            downloaded,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        invalid_outcome = download_and_store_attachment(
            invalid,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )

        self.assertFalse(downloaded_outcome.processed)
        self.assertFalse(invalid_outcome.processed)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.storage.saved, [])

    def test_active_processing_is_skipped_without_client_or_storage(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now(),
        )
        client = StubAttachmentClient([self.successful_download()])

        outcome = download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        attachment.refresh_from_db()

        self.assertFalse(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.storage.saved, [])

    @override_settings(KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS=60)
    def test_expired_processing_can_be_recovered(self):
        attachment = self.create_attachment(
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now() - timedelta(seconds=120),
        )

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_download_and_storage_complete_successfully(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])

        download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(self.storage.saved), 1)

    def test_success_clears_processing_token_and_timestamp(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertTrue(self.storage.exists(attachment.file.name))
        self.assertEqual(attachment.size_bytes, len(self.JPEG_CONTENT))
        self.assertEqual(attachment.content_type, "image/jpeg")
        self.assertEqual(attachment.error_message, "")
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)

    def test_success_stores_file_and_marks_downloaded(self):
        attachment = self.create_attachment()

        outcome = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertTrue(attachment.file.name)
        self.assertTrue(self.storage.exists(attachment.file.name))
        self.assertEqual(attachment.size_bytes, len(self.JPEG_CONTENT))
        self.assertEqual(attachment.content_type, "image/jpeg")
        self.assertEqual(attachment.error_message, "")

    def test_network_failure_marks_attachment_failed_safely(self):
        attachment = self.create_attachment()
        sensitive_url = attachment.source_url

        outcome = self.process(
            attachment,
            KoboIntegrationError(f"network failed for {sensitive_url}"),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.FAILED)
        self.assertEqual(attachment.status, KoboAttachment.Status.FAILED)
        self.assertFalse(attachment.file)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertNotIn(sensitive_url, attachment.error_message)
        self.assertEqual(self.storage.saved, [])

    def test_invalid_content_marks_invalid_and_clears_processing(self):
        attachment = self.create_attachment()

        outcome = self.process(
            attachment,
            DownloadedContent(b"not-a-jpeg", "image/jpeg", 10),
        )
        attachment.refresh_from_db()

        self.assertEqual(outcome.final_status, KoboAttachment.Status.INVALID)
        self.assertEqual(attachment.status, KoboAttachment.Status.INVALID)
        self.assertIsNone(attachment.processing_token)
        self.assertIsNone(attachment.processing_started_at)
        self.assertEqual(self.storage.saved, [])

    def test_storage_success_then_db_failure_compensates_new_file(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=RuntimeError("db confirmation failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "db confirmation failed"):
                download_and_store_attachment(
                    attachment,
                    client=client,
                    storage=self.storage,
                    max_bytes=1024,
                )

        attachment.refresh_from_db()
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(len(self.storage.deleted), 1)
        self.assertEqual(self.storage.deleted[0][0], self.storage.saved[0][0])
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertFalse(attachment.file)

    def test_replaced_token_compensates_stale_worker_file(self):
        attachment = self.create_attachment()
        client = StubAttachmentClient([self.successful_download()])
        winner_token = uuid.uuid4()
        from apps.integrations.kobo import attachments as attachments_module

        real_confirm = attachments_module._confirm_download_success

        def steal_then_confirm(claim, **kwargs):
            KoboAttachment.objects.filter(pk=claim.attachment_id).update(
                processing_token=winner_token,
                processing_started_at=django_timezone.now(),
                status=KoboAttachment.Status.PROCESSING,
            )
            return real_confirm(claim, **kwargs)

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=steal_then_confirm,
        ):
            outcome = download_and_store_attachment(
                attachment,
                client=client,
                storage=self.storage,
                max_bytes=1024,
            )

        attachment.refresh_from_db()
        self.assertFalse(outcome.processed)
        self.assertEqual(attachment.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(attachment.processing_token, winner_token)
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(len(self.storage.deleted), 1)
        self.assertEqual(self.storage.deleted[0][0], self.storage.saved[0][0])

    def test_compensation_failure_does_not_replace_original_exception(self):
        attachment = self.create_attachment()
        storage = RecordingAttachmentStorage(fail_delete=True)
        client = StubAttachmentClient([self.successful_download()])

        with patch(
            "apps.integrations.kobo.attachments._confirm_download_success",
            side_effect=RuntimeError("original db failure"),
        ):
            with self.assertRaisesMessage(RuntimeError, "original db failure"):
                download_and_store_attachment(
                    attachment,
                    client=client,
                    storage=storage,
                    max_bytes=1024,
                )

        self.assertEqual(len(storage.deleted), 1)

    def test_one_failed_attachment_does_not_block_another(self):
        first = self.create_attachment(external_id="first")
        second = self.create_attachment(
            external_id="second",
            source_url="https://kf.example.test/api/attachment/2",
        )
        client = StubAttachmentClient(
            [
                OSError("network unavailable"),
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                ),
            ]
        )

        result = process_pending_attachments(
            self.submission,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.downloaded, 1)
        self.assertEqual(first.status, KoboAttachment.Status.FAILED)
        self.assertEqual(second.status, KoboAttachment.Status.DOWNLOADED)

    def test_batch_skips_active_processing_without_counting_failure(self):
        active = self.create_attachment(
            external_id="active",
            status=KoboAttachment.Status.PROCESSING,
            processing_token=uuid.uuid4(),
            processing_started_at=django_timezone.now(),
        )
        pending = self.create_attachment(
            external_id="pending",
            source_url="https://kf.example.test/api/attachment/2",
        )
        client = StubAttachmentClient([self.successful_download()])

        result = process_pending_attachments(
            self.submission,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )
        active.refresh_from_db()
        pending.refresh_from_db()

        self.assertEqual(result.selected, 2)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.downloaded, 1)
        self.assertEqual(active.status, KoboAttachment.Status.PROCESSING)
        self.assertEqual(pending.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(len(client.calls), 1)

    def test_reprocessing_downloaded_attachment_is_skipped(self):
        attachment = self.create_attachment(status=KoboAttachment.Status.DOWNLOADED)
        client = StubAttachmentClient([])

        outcome = download_and_store_attachment(
            attachment,
            client=client,
            storage=self.storage,
            max_bytes=1024,
        )

        self.assertFalse(outcome.processed)
        self.assertEqual(outcome.final_status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(client.calls, [])

    def test_no_duplicate_confirmed_files_or_events_on_repeat(self):
        attachment = self.create_attachment()
        first = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()
        confirmed_name = attachment.file.name
        event_count = self.submission.processing_events.count()

        second = self.process(attachment, self.successful_download())
        attachment.refresh_from_db()

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(attachment.file.name, confirmed_name)
        self.assertEqual(len(self.storage.saved), 1)
        self.assertEqual(self.submission.processing_events.count(), event_count)

    def test_attachment_failure_does_not_change_submission_status(self):
        attachment = self.create_attachment()

        self.process(attachment, OSError("network unavailable"))
        self.submission.refresh_from_db()

        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )

    def test_ready_submission_without_flag_does_not_process_attachments(self):
        attachment = self.create_attachment()
        output = StringIO()

        call_command(
            "process_kobo_submissions",
            submission_id=self.submission.pk,
            stdout=output,
        )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)
        self.assertIn("skipped=1", output.getvalue())
        self.assertIn("attachments_selected=0", output.getvalue())

    def test_ready_submission_with_flag_downloads_without_normalizing(self):
        attachment = self.create_attachment()
        original_normalized_at = self.submission.normalized_at
        client = StubAttachmentClient(
            [
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                )
            ]
        )
        output = StringIO()

        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                self.storage,
            ),
            patch(
                "apps.integrations.kobo.processors.normalize_submission"
            ) as normalize_mock,
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=self.submission.pk,
                download_attachments=True,
                stdout=output,
            )
        attachment.refresh_from_db()
        self.submission.refresh_from_db()

        normalize_mock.assert_not_called()
        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.assertEqual(self.submission.normalized_at, original_normalized_at)
        self.assertFalse(self.submission.processing_events.exists())
        self.assertIn("skipped=1", output.getvalue())
        self.assertIn("attachments_selected=1", output.getvalue())
        self.assertIn("attachments_downloaded=1", output.getvalue())
        self.assertIn("attachments_skipped=0", output.getvalue())

    def test_downloaded_attachment_is_not_downloaded_again_by_command(self):
        attachment = self.create_attachment(status=KoboAttachment.Status.DOWNLOADED)
        client = StubAttachmentClient([])
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client",
            return_value=client,
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=self.submission.pk,
                download_attachments=True,
                stdout=output,
            )

        self.assertEqual(client.calls, [])
        self.assertIn("attachments_skipped=1", output.getvalue())
        self.assertIn("attachments_downloaded=0", output.getvalue())

    def test_batch_includes_ready_with_pending_only_when_flag_is_active(self):
        attachment = self.create_attachment()
        without_flag_output = StringIO()

        call_command("process_kobo_submissions", stdout=without_flag_output)
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)
        self.assertIn("selected=0", without_flag_output.getvalue())

        client = StubAttachmentClient(
            [
                DownloadedContent(
                    self.JPEG_CONTENT,
                    "image/jpeg",
                    len(self.JPEG_CONTENT),
                )
            ]
        )
        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                self.storage,
            ),
        ):
            with_flag_output = StringIO()
            call_command(
                "process_kobo_submissions",
                download_attachments=True,
                stdout=with_flag_output,
            )
        attachment.refresh_from_db()

        self.assertEqual(attachment.status, KoboAttachment.Status.DOWNLOADED)
        self.assertIn("selected=1", with_flag_output.getvalue())
        self.assertIn("skipped=1", with_flag_output.getvalue())
        self.assertIn("attachments_downloaded=1", with_flag_output.getvalue())
