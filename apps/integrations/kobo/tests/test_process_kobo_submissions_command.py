"""OPS-COMMAND-SAFETY: process_kobo_submissions exit contract."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import KoboFormDefinition, KoboProcessingEvent, KoboSubmission


@override_settings(KOBO_ENABLED=True)
class ProcessKoboSubmissionsCommandSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial",
            version=FICHA_01_VERSION,
        )
        cls.default_timezone = ZoneInfo("America/Caracas")

    def create_submission(self, external_id, **overrides):
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

    def test_no_eligible_records_exits_zero(self):
        stdout = StringIO()
        call_command("process_kobo_submissions", stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("selected=0", output)
        self.assertIn("failed=0", output)

    def test_one_successful_record_exits_zero(self):
        submission = self.create_submission("one-success")
        stdout = StringIO()
        call_command("process_kobo_submissions", stdout=stdout)
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIn("failed=0", stdout.getvalue())
        self.assertIn("succeeded=1", stdout.getvalue())

    def test_multiple_successful_records_exits_zero(self):
        first = self.create_submission("multi-a")
        second = self.create_submission("multi-b")
        stdout = StringIO()
        call_command("process_kobo_submissions", stdout=stdout)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(second.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIn("failed=0", stdout.getvalue())
        self.assertIn("succeeded=2", stdout.getvalue())

    def test_one_failed_record_raises_command_error(self):
        submission = self.create_submission("one-fail")
        submission.raw_payload.pop("parish")
        submission.save(update_fields=("raw_payload",))
        stdout = StringIO()

        with self.assertRaisesMessage(
            CommandError,
            "Processing completed with 1 error(s).",
        ):
            call_command("process_kobo_submissions", stdout=stdout)

        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertTrue(
            submission.processing_events.filter(stage="normalization").exists()
        )
        output = stdout.getvalue()
        self.assertIn("failed=1", output)
        self.assertIn("Kobo processing summary:", output)
        self.assertNotIn("caraballeda", output)
        self.assertNotIn("fake-token", output)
        self.assertNotIn("password", output.lower())

    def test_mixed_success_failure_raises_and_keeps_success_committed(self):
        invalid = self.create_submission("mixed-invalid")
        invalid.raw_payload.pop("_uuid")
        invalid.save(update_fields=("raw_payload",))
        valid = self.create_submission("mixed-valid")
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command("process_kobo_submissions", stdout=stdout)

        invalid.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(invalid.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertEqual(valid.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIn("failed=1", stdout.getvalue())
        self.assertIn("succeeded=1", stdout.getvalue())

    def test_summary_printed_before_command_error(self):
        submission = self.create_submission("summary-order")
        submission.raw_payload.pop("parish")
        submission.save(update_fields=("raw_payload",))
        stdout = StringIO()

        with self.assertRaises(CommandError) as raised:
            call_command("process_kobo_submissions", stdout=stdout)

        self.assertIn("Kobo processing summary:", stdout.getvalue())
        self.assertIn("Processing completed with 1 error(s).", str(raised.exception))

    def test_limit_option_respected(self):
        first = self.create_submission("limit-first")
        second = self.create_submission("limit-second")
        stdout = StringIO()
        call_command("process_kobo_submissions", limit=1, stdout=stdout)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIn("selected=1", stdout.getvalue())
        statuses = {first.status, second.status}
        self.assertEqual(
            statuses,
            {
                KoboSubmission.Status.READY_FOR_REVIEW,
                KoboSubmission.Status.RECEIVED,
            },
        )

    def test_rerun_remains_idempotent_after_success(self):
        submission = self.create_submission("idempotent")
        call_command("process_kobo_submissions", stdout=StringIO())
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        events_after_first = KoboProcessingEvent.objects.filter(
            submission=submission
        ).count()

        call_command("process_kobo_submissions", stdout=StringIO())
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(
            KoboProcessingEvent.objects.filter(submission=submission).count(),
            events_after_first,
        )

    def test_fatal_missing_submission_id_exits_immediately(self):
        with self.assertRaisesMessage(CommandError, "Kobo submission does not exist."):
            call_command(
                "process_kobo_submissions",
                submission_id=999999,
                stdout=StringIO(),
            )

    def test_fatal_non_positive_limit_with_attachments_exits_immediately(self):
        with self.assertRaisesMessage(CommandError, "Limit must be positive."):
            call_command(
                "process_kobo_submissions",
                limit=0,
                download_attachments=True,
                stdout=StringIO(),
            )

    def test_raw_payload_marker_absent_from_output(self):
        submission = self.create_submission("safe-output")
        submission.raw_payload["beneficiary_name"] = "PERSONA_SENSIBLE_XYZ"
        submission.raw_payload["token"] = "kobo-token-SECRET-VALUE"
        submission.save(update_fields=("raw_payload",))
        stdout = StringIO()
        call_command("process_kobo_submissions", stdout=stdout)
        output = stdout.getvalue()
        self.assertNotIn("PERSONA_SENSIBLE_XYZ", output)
        self.assertNotIn("kobo-token-SECRET-VALUE", output)
        self.assertNotIn(str(submission.raw_payload), output)

    def test_unexpected_per_record_failure_still_non_zero_and_continues(self):
        failing = self.create_submission("unexpected-fail")
        succeeding = self.create_submission("unexpected-ok")
        original = failing.pk
        from apps.integrations.kobo.processors import process_submission as real_process

        def side_effect(submission, *, default_timezone):
            if submission.pk == original:
                raise RuntimeError("boom with token=SECRET and phone=+58000")
            return real_process(submission, default_timezone=default_timezone)

        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.services.processing.process_submission",
            side_effect=side_effect,
        ):
            with self.assertRaises(CommandError):
                call_command("process_kobo_submissions", stdout=stdout)

        succeeding.refresh_from_db()
        self.assertEqual(succeeding.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIn("failed=1", stdout.getvalue())
        self.assertNotIn("SECRET", stdout.getvalue())
        self.assertNotIn("+58000", stdout.getvalue())

    def test_disabled_integration_raises_command_error_before_client(self):
        with override_settings(KOBO_ENABLED=False):
            with patch(
                "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client"
            ) as client_factory:
                with self.assertRaisesMessage(
                    CommandError,
                    "Kobo integration is disabled.",
                ):
                    call_command("process_kobo_submissions", stdout=StringIO())
        client_factory.assert_not_called()
