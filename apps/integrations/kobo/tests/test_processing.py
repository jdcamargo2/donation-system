from apps.integrations.kobo.client import DownloadedContent
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProcessingEvent
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import process_pending_submissions
from apps.integrations.kobo.tests.helpers import StubAttachmentClient
from datetime import date
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo


class KoboSubmissionProcessorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.default_timezone = ZoneInfo("America/Caracas")

    def create_submission(self, external_id="processor-submission", **overrides):
        # PRE: external_id is unique within this test and overrides are model fields.
        # POST: returns persisted retryable staging for the active Ficha 1 contract.
        raw_payload = {
            "_uuid": external_id,
            "today": "2026-07-12",
            "nucleo_code": "NV-001",
            "pastoral_zone": "catia_la_mar",
            "parish": "caraballeda",
            "community_sector": "caraballeda_tanaguarena",
            "location": "10 -66",
            "estimated_households": 10000,
            "access_difficulties": "unknown",
            "initial_priority_perception": "medium",
            "_attachments": [],
        }
        values = {
            "form_definition": self.form_definition,
            "external_id": external_id,
            "raw_payload": raw_payload,
            "status": KoboSubmission.Status.RECEIVED,
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def test_received_becomes_ready_with_normalized_staging(self):
        submission = self.create_submission()

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.refresh_from_db()

        self.assertTrue(outcome.processed)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-001")
        self.assertEqual(submission.normalized_payload["estimated_households"], 10000)
        self.assertEqual(submission.pastoral_zone, "catia_la_mar")
        self.assertEqual(submission.parish, "caraballeda")
        self.assertEqual(
            submission.primary_community,
            "caraballeda_tanaguarena",
        )
        self.assertEqual(submission.assessment_date, date(2026, 7, 12))
        self.assertIsNotNone(submission.normalized_at)

    def test_creates_no_attachments_without_attachment_descriptors(self):
        submission = self.create_submission()

        first_outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.status = KoboSubmission.Status.PROCESSING_FAILED
        submission.save(update_fields=("status",))
        second_outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )

        self.assertEqual(first_outcome.attachment_count, 0)
        self.assertEqual(second_outcome.attachment_count, 0)
        self.assertEqual(submission.attachments.count(), 0)
        self.assertFalse(
            submission.attachments.exclude(status=KoboAttachment.Status.PENDING).exists()
        )

    def test_invalid_payload_becomes_validation_failed_with_error_event(self):
        submission = self.create_submission()
        submission.raw_payload.pop("parish")
        submission.save(update_fields=("raw_payload",))

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )
        submission.refresh_from_db()

        self.assertEqual(
            submission.status,
            KoboSubmission.Status.VALIDATION_FAILED,
        )
        self.assertEqual(outcome.error_code, "invalid_payload")
        event = submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.ERROR)
        self.assertEqual(event.stage, "normalization")
        self.assertNotIn("caraballeda", event.message)

    def test_unexpected_exception_becomes_safe_processing_failure(self):
        submission = self.create_submission()
        sensitive_error = "unexpected +58-000 coordinates and URL"

        with patch(
            "apps.integrations.kobo.processors.normalize_submission",
            side_effect=RuntimeError(sensitive_error),
        ):
            outcome = process_submission(
                submission,
                default_timezone=self.default_timezone,
            )
        submission.refresh_from_db()

        self.assertEqual(
            submission.status,
            KoboSubmission.Status.PROCESSING_FAILED,
        )
        self.assertEqual(outcome.error_code, "processing_error")
        self.assertNotIn(sensitive_error, outcome.error_message)
        event = submission.processing_events.get()
        self.assertEqual(event.stage, "processing")
        self.assertNotIn(sensitive_error, event.message)

    def test_success_clears_previous_errors(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.PROCESSING_FAILED,
            error_code="old_error",
            error_message="Old sensitive failure",
        )

        process_submission(submission, default_timezone=self.default_timezone)
        submission.refresh_from_db()

        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_non_processable_status_is_skipped_without_event(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )

        outcome = process_submission(
            submission,
            default_timezone=self.default_timezone,
        )

        self.assertFalse(outcome.processed)
        self.assertEqual(outcome.final_status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertFalse(submission.processing_events.exists())

    def test_batch_isolates_invalid_payload_from_valid_submission(self):
        invalid = self.create_submission("invalid")
        invalid.raw_payload.pop("_uuid")
        invalid.save(update_fields=("raw_payload",))
        valid = self.create_submission("valid")

        result = process_pending_submissions(
            default_timezone=self.default_timezone,
        )
        invalid.refresh_from_db()
        valid.refresh_from_db()

        self.assertEqual(result.selected_count, 2)
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(result.validation_failed_count, 1)
        self.assertEqual(invalid.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertEqual(valid.status, KoboSubmission.Status.READY_FOR_REVIEW)

    def test_batch_respects_limit(self):
        first = self.create_submission("first")
        second = self.create_submission("second")

        result = process_pending_submissions(
            limit=1,
            default_timezone=self.default_timezone,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(result.selected_count, 1)
        self.assertEqual(result.processed_count, 1)
        statuses = {first.status, second.status}
        self.assertEqual(
            statuses,
            {
                KoboSubmission.Status.READY_FOR_REVIEW,
                KoboSubmission.Status.RECEIVED,
            },
        )

    def test_command_submission_id_processes_only_one_and_prints_safe_output(self):
        selected = self.create_submission("selected")
        untouched = self.create_submission("untouched")
        output = StringIO()

        call_command(
            "process_kobo_submissions",
            submission_id=selected.pk,
            stdout=output,
        )
        selected.refresh_from_db()
        untouched.refresh_from_db()

        self.assertEqual(selected.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(untouched.status, KoboSubmission.Status.RECEIVED)
        self.assertIn("selected=1 processed=1 ready=1", output.getvalue())
        sensitive_values = (
            "000000000",
            "PERSONA_PRUEBA",
            "RESPONSABLE_PRUEBA",
            "10.0",
            "https://example.invalid/attachment/",
        )
        for sensitive_value in sensitive_values:
            self.assertNotIn(sensitive_value, output.getvalue())

    def test_command_without_flag_does_not_download_attachments(self):
        submission = self.create_submission("without-download-flag")

        with patch(
            "apps.integrations.kobo.processors.process_pending_attachments"
        ) as download_mock:
            call_command(
                "process_kobo_submissions",
                submission_id=submission.pk,
                stdout=StringIO(),
            )

        download_mock.assert_not_called()
        self.assertEqual(submission.attachments.count(), 0)
        self.assertFalse(
            submission.attachments.exclude(status=KoboAttachment.Status.PENDING).exists()
        )

    def test_command_with_flag_downloads_created_attachments(self):
        submission = self.create_submission("with-download-flag")
        submission.raw_payload["_attachments"] = [
            {
                "question_xpath": "evidence/rear",
                "download_url": "https://kf.example.test/private/rear.jpg",
                "media_file_basename": "rear.jpg",
                "mimetype": "image/jpeg",
            },
            {
                "question_xpath": "evidence/side",
                "download_url": "https://kf.example.test/private/side.png",
                "media_file_basename": "side.png",
                "mimetype": "image/png",
            },
            {
                "question_xpath": "evidence/front",
                "download_url": "https://kf.example.test/private/front.jpg",
                "media_file_basename": "front.jpg",
                "mimetype": "image/jpeg",
            },
        ]
        submission.save(update_fields=("raw_payload",))
        client = StubAttachmentClient(
            [
                DownloadedContent(b"\xff\xd8\xffrear", "image/jpeg", 7),
                DownloadedContent(b"\x89PNG\r\n\x1a\nside", "image/png", 12),
                DownloadedContent(b"\xff\xd8\xfffront", "image/jpeg", 8),
            ]
        )
        storage = InMemoryStorage()

        with (
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client",
                return_value=client,
            ),
            patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.default_storage",
                storage,
            ),
        ):
            call_command(
                "process_kobo_submissions",
                submission_id=submission.pk,
                download_attachments=True,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(
            submission.attachments.filter(
                status=KoboAttachment.Status.DOWNLOADED
            ).count(),
            3,
        )
