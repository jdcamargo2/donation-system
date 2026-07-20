from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.services import sync_registered_forms
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone as django_timezone
from io import StringIO


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
