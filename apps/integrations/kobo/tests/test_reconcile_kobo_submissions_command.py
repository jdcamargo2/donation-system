"""OPS-COMMAND-SAFETY: reconcile_kobo_submissions exit contract."""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.integrations.kobo.contracts import TerritorialRoutingResult, TerritorialRoutingStatus
from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboProcessingEvent,
    KoboSubmission,
)
from apps.integrations.kobo.processors import ProcessingOutcome


@override_settings(KOBO_ENABLED=True, KOBO_BASE_URL="https://kf.example.test", KOBO_API_TOKEN="test-token")
class ReconcileKoboSubmissionsCommandSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1",
            version=FICHA_01_VERSION,
            is_active=True,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="reconcile-asset-01",
            name="Reconcile asset",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )

    def _client(self, payloads=None, *, error=None):
        client = MagicMock()
        if error is not None:
            client.get_submissions.side_effect = error
        else:
            client.get_submissions.return_value = list(payloads or [])
        return client

    def test_zero_remote_results_no_errors_exits_zero(self):
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
            return_value=self._client([]),
        ):
            call_command("reconcile_kobo_submissions", stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("errors=0", output)
        self.assertIn("created=0", output)
        self.assertIn("Kobo reconciliation summary:", output)

    def test_all_reconciled_successfully_exits_zero(self):
        payload = {
            "_uuid": "remote-ok-1",
            "_xform_id_string": self.asset.asset_uid,
        }
        submission = KoboSubmission(
            form_definition=self.form_definition,
            asset=self.asset,
            external_id="remote-ok-1",
            raw_payload=payload,
            status=KoboSubmission.Status.RECEIVED,
        )
        outcome = ProcessingOutcome(
            submission_id=1,
            previous_status=KoboSubmission.Status.RECEIVED,
            final_status=KoboSubmission.Status.READY_FOR_REVIEW,
            processed=True,
            attachment_count=0,
            error_code="",
            error_message="",
        )
        routing = TerritorialRoutingResult(
            status=TerritorialRoutingStatus.RESOLVED,
            form_type="territorial_profile",
            project_id=None,
            pastoral_zone=None,
            reason_code=None,
            message=None,
        )
        stdout = StringIO()
        with (
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
                return_value=self._client([payload]),
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.receive_webhook_submission",
                return_value=(submission, True),
            ) as receive_mock,
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.process_submission",
                return_value=outcome,
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.route_normalized_submission",
                return_value=routing,
            ),
        ):
            call_command("reconcile_kobo_submissions", stdout=stdout)
        receive_mock.assert_called_once()
        self.assertIn("errors=0", stdout.getvalue())
        self.assertIn("created=1", stdout.getvalue())
        self.assertIn("resolved=1", stdout.getvalue())

    def test_one_remote_fetch_error_exits_non_zero(self):
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
            return_value=self._client(
                error=KoboIntegrationError("remote unavailable token=SECRET"),
            ),
        ):
            with self.assertRaisesMessage(
                CommandError,
                "Reconciliation completed with 1 error(s).",
            ):
                call_command("reconcile_kobo_submissions", stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("errors=1", output)
        self.assertIn("failed_assets=1", output)
        self.assertNotIn("SECRET", output)
        self.assertNotIn("test-token", output)

    def test_one_record_error_among_successes_exits_non_zero(self):
        good_payload = {"_uuid": "good-1", "_xform_id_string": self.asset.asset_uid}
        bad_payload = {"_uuid": "bad-1", "_xform_id_string": self.asset.asset_uid}
        good_submission = KoboSubmission(
            pk=101,
            form_definition=self.form_definition,
            asset=self.asset,
            external_id="good-1",
            raw_payload=good_payload,
            status=KoboSubmission.Status.RECEIVED,
        )
        bad_submission = KoboSubmission(
            pk=102,
            form_definition=self.form_definition,
            asset=self.asset,
            external_id="bad-1",
            raw_payload=bad_payload,
            status=KoboSubmission.Status.RECEIVED,
        )
        ready = ProcessingOutcome(
            submission_id=101,
            previous_status=KoboSubmission.Status.RECEIVED,
            final_status=KoboSubmission.Status.READY_FOR_REVIEW,
            processed=True,
            attachment_count=0,
            error_code="",
            error_message="",
        )
        failed = ProcessingOutcome(
            submission_id=102,
            previous_status=KoboSubmission.Status.RECEIVED,
            final_status=KoboSubmission.Status.VALIDATION_FAILED,
            processed=True,
            attachment_count=0,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        routing = TerritorialRoutingResult(
            status=TerritorialRoutingStatus.RESOLVED,
            form_type="territorial_profile",
            project_id=None,
            pastoral_zone=None,
            reason_code=None,
            message=None,
        )

        def receive(*, asset, raw_payload):
            if raw_payload["_uuid"] == "good-1":
                return good_submission, True
            return bad_submission, True

        def process(submission, *, default_timezone):
            if submission.external_id == "good-1":
                return ready
            return failed

        stdout = StringIO()
        with (
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
                return_value=self._client([good_payload, bad_payload]),
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.receive_webhook_submission",
                side_effect=receive,
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.process_submission",
                side_effect=process,
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.route_normalized_submission",
                return_value=routing,
            ),
        ):
            with self.assertRaises(CommandError):
                call_command("reconcile_kobo_submissions", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("created=2", output)
        self.assertIn("errors=1", output)
        self.assertIn("resolved=1", output)
        self.assertNotIn("invalid_payload details", output)

    def test_successes_remain_committed_after_final_command_error(self):
        # Local path: one retryable failure + empty remote; created remote success
        # is verified by persisting a real staging row before the final raise.
        retryable = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=self.asset,
            external_id="local-retryable",
            raw_payload={"_uuid": "local-retryable", "note": "keep"},
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            project=None,
        )
        KoboProcessingEvent.objects.create(
            submission=retryable,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        persisted = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=self.asset,
            external_id="already-staged",
            raw_payload={"_uuid": "already-staged"},
            status=KoboSubmission.Status.RECEIVED,
        )
        fail_outcome = ProcessingOutcome(
            submission_id=retryable.pk,
            previous_status=KoboSubmission.Status.VALIDATION_FAILED,
            final_status=KoboSubmission.Status.VALIDATION_FAILED,
            processed=True,
            attachment_count=0,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        stdout = StringIO()
        with (
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
                return_value=self._client([]),
            ),
            patch(
                "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.process_submission",
                return_value=fail_outcome,
            ),
        ):
            with self.assertRaises(CommandError):
                call_command("reconcile_kobo_submissions", stdout=stdout)

        self.assertTrue(
            KoboSubmission.objects.filter(pk=persisted.pk).exists()
        )
        self.assertTrue(
            KoboProcessingEvent.objects.filter(
                submission=retryable,
                stage="reconciliation",
                code="local_failed",
            ).exists()
        )

    def test_dry_run_no_errors_exits_zero_without_writes(self):
        payload = {"_uuid": "dry-new", "_xform_id_string": self.asset.asset_uid}
        before = KoboSubmission.objects.count()
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
            return_value=self._client([payload]),
        ):
            call_command("reconcile_kobo_submissions", dry_run=True, stdout=stdout)
        self.assertEqual(KoboSubmission.objects.count(), before)
        self.assertIn("dry_run=True", stdout.getvalue())
        self.assertIn("created=1", stdout.getvalue())
        self.assertIn("errors=0", stdout.getvalue())

    def test_dry_run_with_operational_errors_exits_non_zero_without_writes(self):
        before = KoboSubmission.objects.count()
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
            return_value=self._client(
                error=KoboIntegrationError("page failed"),
            ),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "reconcile_kobo_submissions",
                    dry_run=True,
                    stdout=stdout,
                )
        self.assertEqual(KoboSubmission.objects.count(), before)
        self.assertIn("errors=1", stdout.getvalue())

    def test_configuration_disabled_fails_immediately(self):
        with override_settings(KOBO_ENABLED=False):
            with self.assertRaisesMessage(
                CommandError,
                "Kobo integration is disabled.",
            ):
                call_command("reconcile_kobo_submissions", stdout=StringIO())

    def test_non_positive_limit_fails_immediately(self):
        with self.assertRaisesMessage(CommandError, "--limit must be positive."):
            call_command(
                "reconcile_kobo_submissions",
                limit=0,
                stdout=StringIO(),
            )

    def test_output_contains_no_token_or_raw_response(self):
        payload = {
            "_uuid": "safe-out",
            "_xform_id_string": self.asset.asset_uid,
            "answers": {"name": "NOMBRE_PRIVADO", "phone": "+58-000"},
        }
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.reconcile_kobo_submissions.build_kobo_api_client",
            return_value=self._client([payload]),
        ):
            call_command("reconcile_kobo_submissions", dry_run=True, stdout=stdout)
        output = stdout.getvalue()
        self.assertNotIn("test-token", output)
        self.assertNotIn("NOMBRE_PRIVADO", output)
        self.assertNotIn("+58-000", output)
        self.assertNotIn("Authorization", output)
