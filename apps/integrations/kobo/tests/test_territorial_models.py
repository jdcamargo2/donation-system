from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.models import KoboTerritorialIdentity
from apps.integrations.kobo.models import KoboTerritorialIdentityConflict
from apps.operations.models import Project, ProjectDeletionForbiddenError
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone


class KoboTerritorialModelsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ficha_01 = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 territorial",
            version=FICHA_01_VERSION,
        )
        cls.other_form = KoboFormDefinition.objects.create(
            form_id="unsupported_form",
            title="Unsupported",
            version="1",
        )
        cls.project = Project.objects.create(code="PRJ-TERR-01", name="Configured")
        cls.other_project = Project.objects.create(
            code="PRJ-TERR-02",
            name="Other configured project",
        )

    def create_submission(self, external_id="ficha-01-submission", **overrides):
        # PRE: overrides contain KoboSubmission fields compatible with Ficha 1.
        # POST: returns a persisted Ficha 1 staging submission with safe test payloads.
        values = {
            "form_definition": self.ficha_01,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
            "normalized_payload": {"nucleo_code": "NV-001"},
            "pastoral_zone": "centro",
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def create_identity(self, **overrides):
        # PRE: overrides describe one valid canonical Ficha 1 territorial identity.
        # POST: returns a persisted identity after exercising its shared-normalizer validation.
        values = {
            "nucleo_code_original": " NV-001 ",
            "nucleo_code_normalized": "NV-001",
            "pastoral_zone": "centro",
            "project": self.project,
        }
        values.update(overrides)
        if "source_submission" not in values:
            values["source_submission"] = self.create_submission()
        identity = KoboTerritorialIdentity(**values)
        identity.full_clean()
        identity.save()
        return identity

    def test_identity_creation_preserves_original_and_uses_shared_normalization(self):
        identity = self.create_identity()

        self.assertEqual(identity.nucleo_code_original, " NV-001 ")
        self.assertEqual(identity.nucleo_code_normalized, "NV-001")

    def test_identity_normalized_code_is_unique_and_codes_cannot_be_empty(self):
        self.create_identity()
        duplicate = KoboTerritorialIdentity(
            nucleo_code_original="NV-001",
            nucleo_code_normalized="NV-001",
            pastoral_zone="este",
            project=self.other_project,
            source_submission=self.create_submission("duplicate-source"),
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        empty = KoboTerritorialIdentity(
            nucleo_code_original=" ",
            nucleo_code_normalized="",
            pastoral_zone="centro",
            project=self.project,
            source_submission=self.create_submission("empty-source"),
        )
        with self.assertRaises(ValidationError) as context:
            empty.full_clean()
        self.assertIn("nucleo_code_original", context.exception.message_dict)

    def test_identity_accepts_each_canonical_zone_and_rejects_invalid_zone(self):
        for index, pastoral_zone in enumerate(
            ("catia_la_mar", "centro", "este", "montana", "insular"), start=1
        ):
            identity = self.create_identity(
                nucleo_code_original=f"NV-{index:03d}",
                nucleo_code_normalized=f"NV-{index:03d}",
                pastoral_zone=pastoral_zone,
                source_submission=self.create_submission(f"zone-{index}"),
            )
            self.assertEqual(identity.pastoral_zone, pastoral_zone)

        invalid = KoboTerritorialIdentity(
            nucleo_code_original="NV-999",
            nucleo_code_normalized="NV-999",
            pastoral_zone="unknown",
            project=self.project,
            source_submission=self.create_submission("invalid-zone"),
        )
        with self.assertRaises(ValidationError) as context:
            invalid.full_clean()
        self.assertIn("pastoral_zone", context.exception.message_dict)

    def test_identity_rejects_non_ficha_01_source_and_protects_traceability(self):
        unsupported = self.create_submission(
            "unsupported-source",
            form_definition=self.other_form,
        )
        identity = KoboTerritorialIdentity(
            nucleo_code_original="NV-002",
            nucleo_code_normalized="NV-002",
            pastoral_zone="centro",
            project=self.project,
            source_submission=unsupported,
        )
        with self.assertRaises(ValidationError) as context:
            identity.full_clean()
        self.assertIn("source_submission", context.exception.message_dict)

        persisted = self.create_identity()
        with self.assertRaises(ProjectDeletionForbiddenError):
            persisted.project.delete()
        with self.assertRaises(ProtectedError):
            persisted.source_submission.delete()
        self.assertTrue(Project.objects.filter(pk=persisted.project_id).exists())

    def test_active_zone_mapping_is_explicit_unique_and_protects_project(self):
        mapping = KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro",
            project=self.project,
        )
        self.assertEqual(mapping.project_id, self.project.id)
        self.project.name = "A renamed visible project"
        self.project.save(update_fields=("name",))
        mapping.refresh_from_db()
        self.assertEqual(mapping.project_id, self.project.id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboPastoralZoneProjectMapping.objects.create(
                pastoral_zone="centro",
                project=self.other_project,
            )
        with self.assertRaises(ProjectDeletionForbiddenError):
            self.project.delete()
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_conflict_is_idempotent_and_does_not_change_identity(self):
        identity = self.create_identity()
        incoming_submission = self.create_submission("conflict-source")
        conflict = KoboTerritorialIdentityConflict.objects.create(
            identity=identity,
            incoming_submission=incoming_submission,
            existing_pastoral_zone="centro",
            proposed_pastoral_zone="este",
        )
        identity.refresh_from_db()
        self.assertEqual(identity.pastoral_zone, "centro")
        self.assertEqual(identity.project_id, self.project.id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            KoboTerritorialIdentityConflict.objects.create(
                identity=identity,
                incoming_submission=incoming_submission,
                existing_pastoral_zone="centro",
                proposed_pastoral_zone="este",
            )
        with self.assertRaises(ProtectedError):
            incoming_submission.delete()
        self.assertEqual(conflict.status, KoboTerritorialIdentityConflict.Status.OPEN)

    def test_conflict_resolution_preserves_audit_fields(self):
        identity = self.create_identity()
        conflict = KoboTerritorialIdentityConflict.objects.create(
            identity=identity,
            incoming_submission=self.create_submission("resolution-source"),
            existing_pastoral_zone="centro",
            proposed_pastoral_zone="este",
        )
        reviewer = get_user_model().objects.create_user(username="territorial-reviewer")
        resolved_at = timezone.now()
        conflict.status = KoboTerritorialIdentityConflict.Status.RESOLVED_KEEP_EXISTING
        conflict.resolution = KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING
        conflict.resolved_by = reviewer
        conflict.resolved_at = resolved_at
        conflict.full_clean()
        conflict.save()

        conflict.refresh_from_db()
        self.assertEqual(conflict.resolved_by, reviewer)
        self.assertEqual(conflict.resolution, "keep_existing")
        self.assertEqual(conflict.status, "resolved_keep_existing")

    def test_submission_routing_is_initially_unresolved_and_independent_from_processing(self):
        submission = self.create_submission()
        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.UNRESOLVED)
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)

        submission.routing_status = KoboSubmission.RoutingStatus.RESOLVED
        submission.project = self.project
        submission.routing_reason_code = (
            TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY
        )
        submission.full_clean()
        submission.save()
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)
        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED)

    def test_submission_routing_invariants_and_reason_codes(self):
        resolved_without_project = self.create_submission("resolved-without-project")
        resolved_without_project.routing_status = KoboSubmission.RoutingStatus.RESOLVED
        with self.assertRaises(ValidationError) as context:
            resolved_without_project.full_clean()
        self.assertIn("__all__", context.exception.message_dict)

        pending = self.create_submission("pending-identity")
        pending.routing_status = KoboSubmission.RoutingStatus.PENDING_IDENTITY
        pending.full_clean()

        pending.project = self.project
        with self.assertRaises(ValidationError) as context:
            pending.full_clean()
        self.assertIn("__all__", context.exception.message_dict)

        pending.project = None
        pending.routing_reason_code = "not_a_defined_reason"
        with self.assertRaises(ValidationError) as context:
            pending.full_clean()
        self.assertIn("routing_reason_code", context.exception.message_dict)
