from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProcessingEvent
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.services import receive_api_submission
from apps.integrations.kobo.services import sync_ficha_01_submissions
from apps.integrations.kobo.services import sync_registered_forms
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone as django_timezone
from io import StringIO
from unittest.mock import patch
from types import SimpleNamespace


class StubKoboClient:
    def __init__(self, submissions):
        self.submissions = submissions
        self.calls = []

    def get_submissions(self, asset_uid, *, limit=100):
        # PRE: synchronization supplies its configured asset and positive limit.
        # POST: records the query and returns the configured payloads unchanged.
        self.calls.append((asset_uid, limit))
        return self.submissions


class KoboStagingModelsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha territorial",
            version="20260710",
        )

    def create_submission(self, external_id="submission-001", **overrides):
        # PRE: form_definition exists and overrides contains valid model fields.
        # POST: a persisted submission with a non-null raw payload is returned.
        values = {
            "form_definition": self.form_definition,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def test_form_id_and_version_cannot_be_duplicated(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboFormDefinition.objects.create(
                form_id=self.form_definition.form_id,
                title="Duplicate",
                version=self.form_definition.version,
            )

    def test_external_submission_cannot_be_duplicated_per_form(self):
        self.create_submission()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_submission()

    def test_raw_payload_is_required(self):
        submission = KoboSubmission(
            form_definition=self.form_definition,
            external_id="submission-without-payload",
        )

        with self.assertRaises(ValidationError) as context:
            submission.full_clean()

        self.assertIn("raw_payload", context.exception.message_dict)

    def test_attachment_uses_safe_defaults(self):
        attachment = KoboAttachment(
            submission=self.create_submission(),
            field_name="temple_photo",
        )

        self.assertEqual(
            attachment.privacy_level,
            KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
        )
        self.assertEqual(attachment.status, KoboAttachment.Status.PENDING)

    def test_signature_cannot_be_a_public_candidate(self):
        attachment = KoboAttachment(
            submission=self.create_submission(),
            field_name="beneficiary_signature",
            privacy_level=KoboAttachment.PrivacyLevel.PUBLIC_CANDIDATE,
        )

        with self.assertRaises(ValidationError) as context:
            attachment.full_clean()

        self.assertIn("privacy_level", context.exception.message_dict)

    def test_imported_submission_requires_processed_at(self):
        submission = KoboSubmission(
            form_definition=self.form_definition,
            external_id="imported-without-date",
            raw_payload={"_uuid": "imported-without-date"},
            normalized_payload={"parish": "parroquia_1"},
            status=KoboSubmission.Status.IMPORTED,
        )

        with self.assertRaises(ValidationError) as context:
            submission.full_clean()

        self.assertIn("processed_at", context.exception.message_dict)

        submission.processed_at = django_timezone.now()
        submission.full_clean()

    def test_string_representations_include_identifying_data(self):
        submission = self.create_submission()
        attachment = KoboAttachment(
            submission=submission,
            field_name="temple_photo",
            original_filename="temple.jpg",
        )

        self.assertIn(self.form_definition.form_id, str(self.form_definition))
        self.assertIn(submission.external_id, str(submission))
        self.assertIn("temple.jpg", str(attachment))


class KoboFormSynchronizationTests(TestCase):
    def test_sync_creates_three_form_definitions(self):
        synchronized_count = sync_registered_forms()

        self.assertEqual(synchronized_count, 3)
        self.assertEqual(KoboFormDefinition.objects.count(), 3)

    def test_sync_twice_does_not_duplicate_definitions(self):
        sync_registered_forms()
        sync_registered_forms()

        self.assertEqual(KoboFormDefinition.objects.count(), 3)

    def test_sync_preserves_historical_definition(self):
        KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 histórica",
            version="20260710",
        )

        sync_registered_forms()

        historical = KoboFormDefinition.objects.get(
            form_id="ficha_01_territorio", version="20260710"
        )
        self.assertFalse(historical.is_active)
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_01_FORM_ID, version=FICHA_01_VERSION
            ).exists()
        )
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_10_FORM_ID, version=FICHA_10_VERSION
            ).exists()
        )
        self.assertTrue(
            KoboFormDefinition.objects.filter(
                form_id=FICHA_11_FORM_ID, version=FICHA_11_VERSION
            ).exists()
        )

    def test_register_command_is_idempotent(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("register_kobo_forms", stdout=first_output)
        call_command("register_kobo_forms", stdout=second_output)

        self.assertEqual(KoboFormDefinition.objects.count(), 3)
        self.assertIn("3", first_output.getvalue())
        self.assertIn("3", second_output.getvalue())

    def test_sync_deactivates_unsupported_definition_without_deleting_it(self):
        historical = KoboFormDefinition.objects.create(
            form_id="ficha_02_capacidad_parroquial",
            title="Ficha 02 histórica",
            version="20260710",
        )

        sync_registered_forms()
        historical.refresh_from_db()

        self.assertFalse(historical.is_active)


@override_settings(KOBO_FICHA_01_ASSET_UID="ficha-01-asset")
class KoboSubmissionSynchronizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
    def valid_payload(self, external_id="submission-001", **overrides):
        # PRE: external_id identifies the simulated Kobo submission.
        # POST: returns a valid Ficha 1 API payload with requested overrides.
        payload = {
            "_uuid": external_id,
            "_id": 9876,
            "_xform_id_string": "ficha-01-asset",
            "version": FICHA_01_VERSION,
            "meta/instanceID": f"uuid:{external_id}",
            "contact_phone": "+58-sensitive-phone",
            "gps_coordinates": "sensitive-coordinates",
        }
        payload.update(overrides)
        return payload

    def test_receive_uses_uuid_and_preserves_raw_payload(self):
        raw_payload = self.valid_payload()

        submission, created = receive_api_submission(
            self.form_definition,
            raw_payload,
        )

        self.assertTrue(created)
        self.assertEqual(submission.external_id, raw_payload["_uuid"])
        self.assertNotEqual(submission.external_id, str(raw_payload["_id"]))
        self.assertEqual(submission.raw_payload, raw_payload)
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)

    def test_second_sync_does_not_duplicate_submission(self):
        client = StubKoboClient([self.valid_payload()])

        first_result = sync_ficha_01_submissions(client, "ficha-01-asset")
        second_result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(first_result.created_count, 1)
        self.assertEqual(second_result.existing_count, 1)
        self.assertEqual(KoboSubmission.objects.count(), 1)

    def test_missing_uuid_counts_as_failed_without_inventing_submission(self):
        payload = self.valid_payload()
        payload.pop("_uuid")
        client = StubKoboClient([payload])

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertFalse(KoboProcessingEvent.objects.exists())

    def test_mismatched_asset_counts_as_failed(self):
        client = StubKoboClient(
            [self.valid_payload(_xform_id_string="different-asset")]
        )

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_inconsistent_instance_id_counts_as_failed(self):
        client = StubKoboClient(
            [self.valid_payload(**{"meta/instanceID": "uuid:different"})]
        )

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_invalid_payload_does_not_block_valid_payload(self):
        invalid_payload = self.valid_payload("invalid")
        invalid_payload.pop("_uuid")
        client = StubKoboClient([invalid_payload, self.valid_payload("valid")])

        result = sync_ficha_01_submissions(client, "ficha-01-asset")

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertTrue(KoboSubmission.objects.filter(external_id="valid").exists())

    def test_dry_run_does_not_persist_submissions_or_events(self):
        invalid_payload = self.valid_payload("invalid", _xform_id_string="wrong")
        client = StubKoboClient([self.valid_payload("valid"), invalid_payload])

        result = sync_ficha_01_submissions(
            client,
            "ficha-01-asset",
            dry_run=True,
        )

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertFalse(KoboProcessingEvent.objects.exists())

    def test_invalid_payload_creates_event_only_for_existing_submission(self):
        receive_api_submission(self.form_definition, self.valid_payload())
        invalid_payload = self.valid_payload(_xform_id_string="wrong")

        result = sync_ficha_01_submissions(
            StubKoboClient([invalid_payload]),
            "ficha-01-asset",
        )

        self.assertEqual(result.failed_count, 1)
        event = KoboProcessingEvent.objects.get()
        self.assertEqual(event.code, "invalid_payload")
        self.assertNotIn("sensitive", event.message)

    def test_old_definition_is_rejected(self):
        old_definition = KoboFormDefinition.objects.create(
            form_id="ficha_01_territorio",
            title="Ficha 01 histórica",
            version="20260710",
        )

        with self.assertRaises(KoboPayloadError):
            receive_api_submission(old_definition, self.valid_payload())

    @override_settings(KOBO_FICHA_01_ASSET_UID="")
    def test_missing_configured_asset_uid_uses_configuration_error(self):
        with self.assertRaises(KoboConfigurationError):
            sync_ficha_01_submissions(StubKoboClient([]), "")


@override_settings(
    KOBO_BASE_URL="https://kf.example.test",
    KOBO_API_TOKEN="command-secret-token",
    KOBO_REQUEST_TIMEOUT_SECONDS=15,
    KOBO_FICHA_01_ASSET_UID="ficha-01-asset",
)
class KoboFicha01SyncCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="ficha-01-asset",
            name="Ficha 1",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )

    def test_command_delegates_incremental_sync_and_prints_safe_summary(self):
        output = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.sync_kobo_ficha_01.sync_asset_submissions",
            return_value=SimpleNamespace(mode="incremental", status="succeeded", pages_fetched=1, created=1, updated=0, unchanged=0, remote_updates_detected=0, failed=0, partial=False, cursor_before=None, cursor_after=None, watermark_before=None, watermark_after=None),
        ) as synchronize:
            call_command("sync_kobo_ficha_01", asset_uid=self.asset.asset_uid, max_pages=25, stdout=output)

        self.assertEqual(synchronize.call_args.kwargs["asset"], self.asset)
        self.assertEqual(synchronize.call_args.kwargs["max_pages"], 25)
        self.assertIn("status=succeeded", output.getvalue())
        self.assertNotIn("command-secret-token", output.getvalue())

    def test_command_rejects_invalid_page_limit_before_client_or_service(self):
        with patch(
            "apps.integrations.kobo.management.commands.sync_kobo_ficha_01.sync_asset_submissions",
        ):
            with self.assertRaisesMessage(Exception, "--max-pages must be a positive integer"):
                call_command("sync_kobo_ficha_01", asset_uid=self.asset.asset_uid, max_pages=0)
