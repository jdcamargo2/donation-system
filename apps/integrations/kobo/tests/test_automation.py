from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.integrations.kobo.contracts import TerritorialRoutingReasonCode
from apps.integrations.kobo.models import (
    KoboPastoralZoneProjectMapping,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboSubmission,
    KoboTerritorialProfile,
)
from apps.integrations.kobo.services.automation import (
    KOBO_SYSTEM_USERNAME,
    AutoImportOutcome,
    IncidentKind,
    auto_import_if_eligible,
    classify_incident,
    get_kobo_system_actor,
    incident_queryset,
)
from apps.integrations.kobo.services.territorial_routing import route_ficha_1_submission
from apps.integrations.kobo.tests.test_prioritization_assessments import (
    PrioritizationAssessmentFixtureMixin,
)
from apps.integrations.kobo.tests.test_prioritized_microprojects import (
    PrioritizedMicroprojectFixtureMixin,
)
from apps.integrations.kobo.tests.test_territorial_profiles import (
    KoboTerritorialProfileTests,
)


class KoboAutomationFicha1Tests(KoboTerritorialProfileTests):
    def setUp(self):
        self.sync_clicker = get_user_model().objects.create_user(
            username="sync-clicker",
            password="unused",
        )
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="catia_la_mar",
            project=self.project,
            is_active=True,
        )

    def create_ready_routed_submission(self, *, external_id="auto-ficha-1", **payload_changes):
        # PRE: mapping exists for catia_la_mar; payload_changes override profile fields.
        # POST: returns READY_FOR_REVIEW Ficha 1 after territorial routing created identity.
        submission = self.create_submission(
            external_id=external_id,
            payload_changes=payload_changes or None,
        )
        submission.status = KoboSubmission.Status.READY_FOR_REVIEW
        submission.save(update_fields=("status",))
        route_ficha_1_submission(submission)
        submission.refresh_from_db()
        return submission

    def test_auto_import_ficha_1_when_routed_and_mapped(self):
        submission = self.create_ready_routed_submission()
        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(submission.project, self.project)

        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()
        system_actor = get_kobo_system_actor()

        self.assertEqual(result.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)
        profile = submission.territorial_profile
        self.assertEqual(profile.created_by, system_actor)
        self.assertEqual(profile.created_by.username, KOBO_SYSTEM_USERNAME)
        self.assertNotEqual(profile.created_by, self.sync_clicker)
        self.assertNotEqual(profile.created_by, self.importer)
        self.assertTrue(
            submission.processing_events.filter(code="auto_imported").exists()
        )

    def test_second_auto_import_is_idempotent(self):
        submission = self.create_ready_routed_submission(external_id="auto-ficha-1-idem")
        first = auto_import_if_eligible(submission)
        second = auto_import_if_eligible(submission)
        submission.refresh_from_db()

        self.assertEqual(first.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, AutoImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)

    def test_system_actor_is_not_the_sync_clicking_user(self):
        submission = self.create_ready_routed_submission(external_id="auto-ficha-1-actor")
        auto_import_if_eligible(submission)
        submission.refresh_from_db()

        self.assertEqual(
            submission.territorial_profile.created_by.username,
            KOBO_SYSTEM_USERNAME,
        )
        self.assertNotEqual(submission.territorial_profile.created_by_id, self.sync_clicker.pk)

    def test_system_actor_is_noninteractive_and_has_only_minimum_permission(self):
        system_actor = get_kobo_system_actor()

        self.assertEqual(system_actor.username, KOBO_SYSTEM_USERNAME)
        self.assertTrue(system_actor.is_active)
        self.assertFalse(system_actor.is_superuser)
        self.assertFalse(system_actor.has_usable_password())
        self.assertFalse(self.client.login(username=KOBO_SYSTEM_USERNAME, password="unused"))
        self.assertEqual(system_actor.groups.count(), 0)
        self.assertEqual(
            set(system_actor.user_permissions.values_list("content_type__app_label", "codename")),
            {("operations", "change_project")},
        )


class KoboAutomationFicha10Tests(PrioritizedMicroprojectFixtureMixin, TestCase):
    def setUp(self):
        self.create_domain()
        self.identity = self.create_identity()

    def test_auto_import_ficha_10_when_routed(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            external_id="auto-ficha-10",
        )
        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()
        system_actor = get_kobo_system_actor()

        self.assertEqual(result.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        microproject = KoboPrioritizedMicroproject.objects.get()
        self.assertEqual(microproject.created_by, system_actor)
        self.assertEqual(microproject.created_by.username, KOBO_SYSTEM_USERNAME)
        self.assertNotEqual(microproject.created_by, self.importer)

    def test_auto_import_ficha_10_idempotent(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            external_id="auto-ficha-10-idem",
        )
        first = auto_import_if_eligible(submission)
        second = auto_import_if_eligible(submission)

        self.assertEqual(first.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, AutoImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(KoboPrioritizedMicroproject.objects.count(), 1)


class KoboAutomationFicha11Tests(PrioritizationAssessmentFixtureMixin, TestCase):
    def setUp(self):
        self.create_domain()
        self.identity = self.create_identity()

    def test_auto_import_ficha_11_when_routed(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            external_id="auto-ficha-11",
        )
        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()
        system_actor = get_kobo_system_actor()

        self.assertEqual(result.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        assessment = KoboPrioritizationAssessment.objects.get()
        self.assertEqual(assessment.created_by, system_actor)
        self.assertEqual(assessment.created_by.username, KOBO_SYSTEM_USERNAME)
        self.assertNotEqual(assessment.created_by, self.importer)

    def test_auto_import_ficha_11_idempotent(self):
        submission = self.create_submission(
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            external_id="auto-ficha-11-idem",
        )
        first = auto_import_if_eligible(submission)
        second = auto_import_if_eligible(submission)

        self.assertEqual(first.outcome, AutoImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, AutoImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(KoboPrioritizationAssessment.objects.count(), 1)


class KoboAutomationIncidentTests(KoboTerritorialProfileTests):
    def test_invalid_form_becomes_incident_not_rejected(self):
        submission = self.create_submission(external_id="auto-invalid")
        submission.status = KoboSubmission.Status.VALIDATION_FAILED
        submission.error_code = "invalid_payload"
        submission.error_message = "Payload inválido"
        submission.routing_status = KoboSubmission.RoutingStatus.UNRESOLVED
        submission.project = None
        submission.save(
            update_fields=(
                "status",
                "error_code",
                "error_message",
                "routing_status",
                "project",
            )
        )

        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()

        self.assertEqual(result.outcome, AutoImportOutcome.INCIDENT)
        self.assertEqual(result.incident_kind, IncidentKind.INVALID_DATA)
        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertNotEqual(submission.status, KoboSubmission.Status.REJECTED)
        self.assertFalse(
            submission.processing_events.filter(stage="review", code="incomplete").exists()
        )
        self.assertFalse(
            submission.processing_events.filter(
                stage="review",
                code=KoboSubmission.Status.REJECTED,
            ).exists()
        )
        self.assertTrue(incident_queryset().filter(pk=submission.pk).exists())

    def test_no_automatic_rejection_or_request_correction_on_routing_incident(self):
        submission = self.create_submission(external_id="auto-no-reject")
        submission.status = KoboSubmission.Status.READY_FOR_REVIEW
        submission.routing_status = KoboSubmission.RoutingStatus.PENDING_IDENTITY
        submission.project = None
        submission.routing_reason_code = (
            TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY
        )
        submission.save(
            update_fields=(
                "status",
                "routing_status",
                "project",
                "routing_reason_code",
            )
        )

        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()

        self.assertEqual(result.outcome, AutoImportOutcome.INCIDENT)
        self.assertEqual(result.incident_kind, IncidentKind.NUCLEUS_NOT_FOUND)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertNotEqual(submission.status, KoboSubmission.Status.REJECTED)
        self.assertFalse(
            submission.processing_events.filter(code="incomplete").exists()
        )

    def test_ready_resolved_with_project_is_not_an_incident(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="catia_la_mar",
            project=self.project,
            is_active=True,
        )
        submission = self.create_submission(external_id="auto-not-incident")
        submission.status = KoboSubmission.Status.READY_FOR_REVIEW
        submission.save(update_fields=("status",))
        route_ficha_1_submission(submission)
        submission.refresh_from_db()

        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertFalse(incident_queryset().filter(pk=submission.pk).exists())

        result = auto_import_if_eligible(submission)
        submission.refresh_from_db()
        self.assertEqual(result.outcome, AutoImportOutcome.IMPORTED)
        self.assertFalse(incident_queryset().filter(pk=submission.pk).exists())


class KoboClassifyIncidentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
        from apps.integrations.kobo.models import KoboFormDefinition

        cls.form = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Classify",
            version=FICHA_01_VERSION,
        )

    def make_submission(self, **changes):
        values = {
            "form_definition": self.form,
            "external_id": f"classify-{KoboSubmission.objects.count()}",
            "raw_payload": {"_uuid": "x"},
            "normalized_payload": {},
            "status": KoboSubmission.Status.READY_FOR_REVIEW,
            "routing_status": KoboSubmission.RoutingStatus.UNRESOLVED,
        }
        values.update(changes)
        return KoboSubmission.objects.create(**values)

    def test_classify_incident_categories(self):
        cases = (
            (
                IncidentKind.REMOTE_UPDATE_PENDING,
                {"remote_update_pending": True},
            ),
            (
                IncidentKind.INVALID_DATA,
                {"status": KoboSubmission.Status.VALIDATION_FAILED},
            ),
            (
                IncidentKind.TECHNICAL_ERROR,
                {"status": KoboSubmission.Status.PROCESSING_FAILED},
            ),
            (
                IncidentKind.MATERIALIZATION_ERROR,
                {"error_code": "MATERIALIZATION_FAILED"},
            ),
            (
                IncidentKind.ZONE_WITHOUT_PROJECT,
                {
                    "error_code": "IMPORT_PROJECT_MISSING",
                    "routing_reason_code": "",
                },
            ),
            (
                IncidentKind.TERRITORIAL_CONFLICT,
                {
                    "error_code": "IMPORT_ROUTING_CONFLICT",
                },
            ),
            (
                IncidentKind.ZONE_WITHOUT_PROJECT,
                {
                    "routing_reason_code": (
                        TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING
                    ),
                },
            ),
            (
                IncidentKind.NUCLEUS_NOT_FOUND,
                {
                    "routing_reason_code": (
                        TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY
                    ),
                },
            ),
            (
                IncidentKind.TERRITORIAL_CONFLICT,
                {
                    "routing_reason_code": (
                        TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT
                    ),
                },
            ),
            (
                IncidentKind.INVALID_DATA,
                {
                    "routing_reason_code": TerritorialRoutingReasonCode.MISSING_NUCLEO_CODE,
                },
            ),
            (
                IncidentKind.NUCLEUS_NOT_FOUND,
                {
                    "routing_status": KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                    "routing_reason_code": "",
                },
            ),
            (
                IncidentKind.TERRITORIAL_CONFLICT,
                {
                    "routing_status": KoboSubmission.RoutingStatus.CONFLICT,
                    "routing_reason_code": "",
                },
            ),
            (
                IncidentKind.ROUTING_ERROR,
                {
                    "routing_status": KoboSubmission.RoutingStatus.ERROR,
                    "routing_reason_code": "",
                },
            ),
            (
                IncidentKind.TECHNICAL_ERROR,
                {
                    "routing_status": KoboSubmission.RoutingStatus.UNRESOLVED,
                    "routing_reason_code": "",
                    "error_code": "",
                },
            ),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected, changes=changes):
                submission = self.make_submission(**changes)
                self.assertEqual(classify_incident(submission), expected)
