from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.integrations.kobo.admin import KoboTerritorialProfileAdmin
from apps.integrations.kobo.import_contracts import ImportOutcome
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboImportRecord,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
)
from apps.integrations.kobo.services import import_kobo_submission
from apps.operations.models import AuditLog, Project, ProjectDeletionForbiddenError


class KoboTerritorialProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.importer = get_user_model().objects.create_user(username="profile-importer")
        cls.importer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="operations",
                codename="change_project",
            )
        )
        cls.project = Project.objects.create(
            code="PRJ-KOBO-PROFILE",
            name="Territorial profile project",
            status=Project.Status.ACTIVE,
        )
        cls.other_project = Project.objects.create(
            code="PRJ-KOBO-PROFILE-OTHER",
            name="Other territorial project",
            status=Project.Status.ACTIVE,
        )
        cls.definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 profile",
            version=FICHA_01_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="territorial-profile-asset",
            name="Territorial profile",
            form_definition=cls.definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )

    def profile_payload(self, *, code="NV-PROFILE", zone="catia_la_mar", **changes):
        # PRE: changes contains normalized Ficha 1 field overrides.
        # POST: returns a complete canonical persisted profile payload.
        payload = {
            "nucleo_code": code,
            "nucleo_code_normalized": code,
            "pastoral_zone_normalized": zone,
            "location": {
                "latitude": 10.5,
                "longitude": -66.5,
                "altitude": 12.0,
                "accuracy": 3.0,
            },
            "parish_delegate": "Delegada parroquial",
            "contact_phone": "+58 000 0000000",
            "main_informant_role": "Vocería comunitaria",
            "communities_covered": "Comunidad A y Comunidad B",
            "estimated_households": 120,
            "access_difficulties": "yes",
            "access_difficulties_notes": "Acceso estacional.",
            "initial_priority_perception": "high",
            "general_notes": "Diagnóstico revisado.",
        }
        payload.update(changes)
        return payload

    def create_submission(
        self,
        *,
        code="NV-PROFILE",
        zone="catia_la_mar",
        project=None,
        payload_changes=None,
        external_id=None,
    ):
        # PRE: code, zone and project describe one approved routed Ficha 1.
        # POST: persists an import candidate using only canonical normalized data.
        project = project or self.project
        payload = self.profile_payload(code=code, zone=zone, **(payload_changes or {}))
        return KoboSubmission.objects.create(
            form_definition=self.definition,
            asset=self.asset,
            project=project,
            external_id=external_id or f"profile-{KoboSubmission.objects.count()}",
            raw_payload={"_uuid": external_id or "trace-only"},
            normalized_payload=payload,
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            pastoral_zone=zone,
            parish="Parroquia revisada",
            primary_community="Sector revisado",
            normalized_at=timezone.now(),
            processed_at=None,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            nucleo_code_original=code,
            nucleo_code_normalized=code,
        )

    def create_identity(self, submission, *, status=KoboTerritorialIdentity.Status.PENDING_REVIEW):
        # PRE: submission contains canonical territorial routing fields.
        # POST: persists the one identity expected by its materialization handler.
        return KoboTerritorialIdentity.objects.create(
            nucleo_code_original=submission.nucleo_code_original,
            nucleo_code_normalized=submission.nucleo_code_normalized,
            pastoral_zone=submission.pastoral_zone,
            project=submission.project,
            source_submission=submission,
            status=status,
        )

    def import_candidate(self, *, identity_status=KoboTerritorialIdentity.Status.PENDING_REVIEW):
        # PRE: identity_status is an explicit territorial lifecycle state.
        # POST: returns the submission, identity, and common import result.
        submission = self.create_submission()
        identity = self.create_identity(submission, status=identity_status)
        result = import_kobo_submission(submission, actor=self.importer)
        submission.refresh_from_db()
        identity.refresh_from_db()
        return submission, identity, result

    def test_approved_ficha_1_creates_exactly_one_profile_and_import_record(self):
        submission, identity, result = self.import_candidate()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.imported_at)
        self.assertEqual(submission.processed_at, submission.imported_at)
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        profile = submission.territorial_profile
        record = submission.import_record
        self.assertEqual(profile.territorial_identity, identity)
        self.assertEqual(profile.project, self.project)
        self.assertEqual(profile.created_by, self.importer)
        self.assertEqual(record.target_app_label, "kobo")
        self.assertEqual(record.target_model, "KoboTerritorialProfile")
        self.assertEqual(record.target_object_id, profile.pk)
        self.assertEqual(result.materialization_id, profile.pk)
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)

    def test_profile_preserves_canonical_location_text_fields_and_optional_values(self):
        submission = self.create_submission(
            payload_changes={
                "location": None,
                "parish_delegate": None,
                "contact_phone": None,
                "main_informant_role": None,
                "communities_covered": None,
                "estimated_households": None,
                "access_difficulties_notes": None,
                "general_notes": None,
            }
        )
        self.create_identity(submission)

        result = import_kobo_submission(submission, actor=self.importer)
        profile = KoboTerritorialProfile.objects.get()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertIsNone(profile.location)
        self.assertIsNone(profile.estimated_households)
        self.assertEqual(profile.communities_covered, "")
        self.assertEqual(profile.contact_phone, "")

    def test_profile_location_and_closed_choices_are_model_validated(self):
        submission = self.create_submission()
        identity = self.create_identity(submission)
        profile = KoboTerritorialProfile(
            territorial_identity=identity,
            project=self.project,
            source_submission=submission,
            parish=submission.parish,
            community_sector=submission.primary_community,
            location={
                "latitude": 91,
                "longitude": -66,
                "altitude": None,
                "accuracy": None,
            },
            access_difficulties="sometimes",
            initial_priority_perception="urgent",
            created_by=self.importer,
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_source_is_unique_identity_has_history_and_latest_profile_is_derived(self):
        first_submission = self.create_submission(external_id="profile-history-1")
        identity = self.create_identity(first_submission)
        first_result = import_kobo_submission(first_submission, actor=self.importer)
        first_profile = KoboTerritorialProfile.objects.get(pk=first_result.materialization_id)
        second_submission = self.create_submission(external_id="profile-history-2")

        second_result = import_kobo_submission(second_submission, actor=self.importer)
        second_profile = KoboTerritorialProfile.objects.get(pk=second_result.materialization_id)

        self.assertEqual(identity.territorial_profiles.count(), 2)
        self.assertEqual(identity.latest_profile(), second_profile)
        first_profile.refresh_from_db()
        self.assertEqual(first_profile.source_submission, first_submission)
        duplicate = KoboTerritorialProfile(
            territorial_identity=identity,
            project=self.project,
            source_submission=first_submission,
            parish="Otra",
            community_sector="Otra",
            access_difficulties="no",
            initial_priority_perception="low",
            created_by=self.importer,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_profile_relations_are_protected_and_profile_is_immutable(self):
        submission, identity, _ = self.import_candidate()
        profile = submission.territorial_profile

        for protected_object in (submission, identity, self.project, self.importer):
            with self.subTest(protected_object=protected_object):
                if isinstance(protected_object, Project):
                    with self.assertRaises(ProjectDeletionForbiddenError):
                        protected_object.delete()
                    self.assertTrue(
                        Project.objects.filter(pk=protected_object.pk).exists()
                    )
                    self.assertTrue(
                        KoboTerritorialProfile.objects.filter(pk=profile.pk).exists()
                    )
                else:
                    with self.assertRaises(ProtectedError):
                        protected_object.delete()
        profile.general_notes = "Mutación no permitida"
        with self.assertRaises(ValidationError):
            profile.save()

    def test_observed_identity_remains_observed_and_inactive_identity_blocks(self):
        observed_submission, observed_identity, observed_result = self.import_candidate(
            identity_status=KoboTerritorialIdentity.Status.OBSERVED
        )
        self.assertEqual(observed_result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(observed_identity.status, KoboTerritorialIdentity.Status.OBSERVED)
        self.assertFalse(
            observed_submission.processing_events.filter(
                code="territorial_identity_activated"
            ).exists()
        )

        inactive_submission = self.create_submission(
            code="NV-INACTIVE", external_id="profile-inactive"
        )
        inactive_identity = self.create_identity(
            inactive_submission,
            status=KoboTerritorialIdentity.Status.INACTIVE,
        )
        blocked = import_kobo_submission(inactive_submission, actor=self.importer)
        inactive_identity.refresh_from_db()

        self.assertEqual(blocked.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(blocked.reason_code, "FICHA_1_IDENTITY_INACTIVE")
        self.assertEqual(inactive_identity.status, KoboTerritorialIdentity.Status.INACTIVE)
        self.assertFalse(hasattr(inactive_submission, "territorial_profile"))

    def test_open_conflict_blocks_without_importing(self):
        submission = self.create_submission()
        identity = self.create_identity(submission)
        KoboTerritorialIdentityConflict.objects.create(
            identity=identity,
            incoming_submission=submission,
            existing_pastoral_zone=identity.pastoral_zone,
            proposed_pastoral_zone="centro",
            existing_project=self.project,
            proposed_project=self.other_project,
        )

        result = import_kobo_submission(submission, actor=self.importer)
        submission.refresh_from_db()

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(result.reason_code, "FICHA_1_TERRITORIAL_CONFLICT")
        self.assertEqual(submission.status, KoboSubmission.Status.PROCESSING_FAILED)
        self.assertFalse(KoboTerritorialProfile.objects.exists())

    def test_identity_project_or_zone_incoherence_blocks(self):
        cases = (
            ("project", "NV-PROJECT", "catia_la_mar", self.other_project),
            ("zone", "NV-ZONE", "centro", self.project),
        )
        for label, code, identity_zone, identity_project in cases:
            with self.subTest(label=label):
                submission = self.create_submission(
                    code=code,
                    external_id=f"mismatch-{label}",
                )
                KoboTerritorialIdentity.objects.create(
                    nucleo_code_original=code,
                    nucleo_code_normalized=code,
                    pastoral_zone=identity_zone,
                    project=identity_project,
                    source_submission=submission,
                )
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, "FICHA_1_IDENTITY_MISMATCH")
        self.assertFalse(KoboTerritorialProfile.objects.exists())

    def test_normalized_code_or_zone_incoherence_blocks(self):
        cases = (
            {"nucleo_code_normalized": "NV-DIFFERENT"},
            {"pastoral_zone_normalized": "centro"},
        )
        for index, changes in enumerate(cases):
            with self.subTest(changes=changes):
                code = f"NV-PAYLOAD-MISMATCH-{index}"
                submission = self.create_submission(
                    code=code,
                    payload_changes=changes,
                    external_id=f"payload-mismatch-{index}",
                )
                self.create_identity(submission)
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, "FICHA_1_IDENTITY_MISMATCH")
        self.assertFalse(KoboTerritorialProfile.objects.exists())

    def test_invalid_normalized_data_blocks_without_reading_raw_payload(self):
        cases = (
            {"estimated_households": "many"},
            {"access_difficulties": "sometimes"},
            {"initial_priority_perception": "urgent"},
            {
                "location": {
                    "latitude": 10,
                    "longitude": 181,
                    "altitude": None,
                    "accuracy": None,
                }
            },
        )
        for index, changes in enumerate(cases):
            with self.subTest(changes=changes):
                code = f"NV-INVALID-{index}"
                submission = self.create_submission(
                    code=code,
                    payload_changes=changes,
                    external_id=f"invalid-{index}",
                )
                submission.raw_payload = {"valid_looking_but_ignored": True}
                submission.save(update_fields=("raw_payload",))
                self.create_identity(submission)
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, "FICHA_1_PROFILE_INVALID")
        self.assertFalse(KoboTerritorialProfile.objects.exists())

    def test_existing_profile_without_import_record_is_explicitly_blocked(self):
        submission = self.create_submission()
        identity = self.create_identity(submission)
        profile = KoboTerritorialProfile(
            territorial_identity=identity,
            project=self.project,
            source_submission=submission,
            parish=submission.parish,
            community_sector=submission.primary_community,
            access_difficulties="no",
            initial_priority_perception="medium",
            created_by=self.importer,
        )
        profile.full_clean()
        profile.save()

        result = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(result.reason_code, "FICHA_1_PROFILE_STATE_CONFLICT")
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)
        self.assertFalse(KoboImportRecord.objects.exists())

    def test_retry_is_idempotent_and_safe_events_contain_no_sensitive_values(self):
        submission, identity, first = self.import_candidate()
        second = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(first.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, ImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(submission.processing_events.filter(code="imported").count(), 1)
        self.assertEqual(
            submission.processing_events.filter(code="territorial_profile_created").count(),
            1,
        )
        self.assertEqual(
            submission.processing_events.filter(code="territorial_identity_activated").count(),
            1,
        )
        safe_keys = {
            "profile_id",
            "identity_id",
            "project_id",
            "nucleo_code_normalized",
        }
        for event in submission.processing_events.exclude(metadata={}):
            self.assertEqual(set(event.metadata), safe_keys)
            serialized = str(event.metadata)
            for sensitive_value in (
                "+58 000 0000000",
                "10.5",
                "Diagnóstico revisado",
                "raw_payload",
            ):
                self.assertNotIn(sensitive_value, serialized)
        self.assertEqual(
            AuditLog.objects.filter(
                model_name=KoboTerritorialProfile._meta.verbose_name.capitalize()
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                model_name=KoboTerritorialIdentity._meta.verbose_name.capitalize(),
                action=AuditLog.Action.UPDATED,
            ).count(),
            1,
        )
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)

    def test_profile_import_is_atomic_when_profile_record_event_or_audit_fails(self):
        failure_targets = (
            "apps.integrations.kobo.models.KoboTerritorialProfile.save",
            "apps.integrations.kobo.models.KoboImportRecord.objects.create",
            "apps.integrations.kobo.models.KoboProcessingEvent.objects.create",
            "apps.operations.services.log_action",
        )
        for index, target in enumerate(failure_targets):
            with self.subTest(target=target):
                code = f"NV-ROLLBACK-{index}"
                submission = self.create_submission(
                    code=code,
                    external_id=f"rollback-{index}",
                )
                identity = self.create_identity(submission)
                with patch(target, side_effect=RuntimeError("forced atomic failure")):
                    result = import_kobo_submission(submission, actor=self.importer)
                submission.refresh_from_db()
                identity.refresh_from_db()
                self.assertEqual(result.outcome, ImportOutcome.FAILED)
                self.assertEqual(submission.status, KoboSubmission.Status.PROCESSING_FAILED)
                self.assertIsNone(submission.imported_at)
                self.assertEqual(identity.status, KoboTerritorialIdentity.Status.PENDING_REVIEW)
                self.assertFalse(
                    KoboTerritorialProfile.objects.filter(source_submission=submission).exists()
                )
                self.assertFalse(
                    KoboImportRecord.objects.filter(submission=submission).exists()
                )

    def test_admin_is_readonly_and_has_no_mutating_actions(self):
        submission, _, _ = self.import_candidate()
        profile = submission.territorial_profile
        model_admin = KoboTerritorialProfileAdmin(
            KoboTerritorialProfile, admin.site
        )
        request = RequestFactory().post("/admin/kobo/profile/")
        request.user = self.importer

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, profile))
        self.assertFalse(model_admin.has_delete_permission(request, profile))
        self.assertEqual(model_admin.actions, ())
        self.assertIn("source_submission", model_admin.readonly_fields)
        self.assertIn("created_by", model_admin.readonly_fields)
