from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboDiscoveredAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProcessingEvent
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services import associate_submission_with_project
from apps.integrations.kobo.services import configure_discovered_asset
from apps.integrations.kobo.services import review_submission
from apps.integrations.kobo.tests.test_contracts import KoboFicha11NormalizerTests
from apps.operations.models import Project
from apps.operations.models import ProjectUpdate
from copy import deepcopy
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone
from zoneinfo import ZoneInfo


class KoboProjectAssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="association-reviewer",
            password="test-password",
        )
        cls.viewer = user_model.objects.create_user(
            username="association-viewer",
            password="test-password",
        )
        view_permission = Permission.objects.get(codename="view_kobosubmission")
        change_permission = Permission.objects.get(codename="change_kobosubmission")
        cls.reviewer.user_permissions.add(view_permission, change_permission)
        cls.viewer.user_permissions.add(view_permission)
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )

    def setUp(self):
        self.project = Project.objects.create(
            code="PRJ-ASSOCIATION",
            name="Configured exact project",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-BROWSER-CHOICE",
            name="Browser supplied project",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="exact-kobo-asset",
            name="Territorial asset",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="catia_la_mar",
        )
        self.submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="association-submission",
            raw_payload={
                "_uuid": "association-submission",
                "_xform_id_string": self.asset.asset_uid,
                "contact_phone": "+58-secret-phone",
            },
            normalized_payload={"official_parish_name": "Normalized parish"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            pastoral_zone="catia_la_mar",
            error_code="old_error",
            error_message="old message",
        )

    def associate(self):
        # PRE: submission and reviewer belong to the current association fixture.
        # POST: delegates to the exact binding association service.
        return associate_submission_with_project(
            self.submission,
            reviewed_by=self.reviewer,
        )

    def assert_safe_failure(self, expected_code):
        # PRE: one expected association configuration error was prepared.
        # POST: verifies safe warning persistence with no domain association.
        original_raw_payload = deepcopy(self.submission.raw_payload)
        result = self.associate()
        self.submission.refresh_from_db()

        self.assertFalse(result.associated)
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.assertIsNone(self.submission.asset_id)
        self.assertIsNone(self.submission.project_id)
        self.assertIsNone(self.submission.imported_at)
        self.assertEqual(self.submission.error_code, expected_code)
        self.assertEqual(self.submission.raw_payload, original_raw_payload)
        event = self.submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.WARNING)
        self.assertEqual(event.stage, "project_association")
        self.assertNotIn("+58-secret-phone", event.message)

    def test_associates_exact_asset_and_zone_to_configured_project(self):
        original_raw_payload = deepcopy(self.submission.raw_payload)
        original_normalized_payload = deepcopy(self.submission.normalized_payload)

        result = self.associate()
        self.submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(self.submission.asset_id, self.asset.pk)
        self.assertEqual(self.submission.project_id, self.project.pk)
        self.assertEqual(self.submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(self.submission.imported_at)
        self.assertIsNotNone(self.submission.processed_at)
        self.assertEqual(self.submission.error_code, "")
        self.assertEqual(self.submission.error_message, "")
        self.assertEqual(self.submission.raw_payload, original_raw_payload)
        self.assertEqual(self.submission.normalized_payload, original_normalized_payload)
        event = self.submission.processing_events.get()
        self.assertEqual(event.level, KoboProcessingEvent.Level.INFO)
        self.assertEqual(event.stage, "project_association")
        self.assertEqual(event.code, "project_associated")
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_asset_is_taken_only_from_xform_id_string(self):
        decoy_asset = KoboAsset.objects.create(
            asset_uid="decoy-asset",
            name=self.asset.name,
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        KoboProjectBinding.objects.create(
            asset=decoy_asset,
            project=self.other_project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.pastoral_zone",
            source_value="catia_la_mar",
        )

        self.associate()
        self.submission.refresh_from_db()

        self.assertEqual(self.submission.asset_id, self.asset.pk)
        self.assertEqual(self.submission.project_id, self.project.pk)

    def test_missing_asset_keeps_submission_approved(self):
        self.submission.raw_payload["_xform_id_string"] = "missing-asset"
        self.submission.save(update_fields=("raw_payload",))

        self.assert_safe_failure("asset_not_found")

    def test_missing_asset_uid_keeps_submission_approved(self):
        self.submission.raw_payload.pop("_xform_id_string")
        self.submission.save(update_fields=("raw_payload",))

        self.assert_safe_failure("asset_uid_missing")

    def test_inactive_asset_blocks_association(self):
        self.asset.is_active = False
        self.asset.save(update_fields=("is_active",))

        self.assert_safe_failure("asset_inactive")

    def test_incompatible_asset_role_blocks_association(self):
        self.asset.form_role = KoboAsset.FormRole.PRIORITIZED_MICROPROJECT
        self.asset.save(update_fields=("form_role",))

        self.assert_safe_failure("asset_role_incompatible")

    def test_missing_binding_blocks_association(self):
        self.binding.delete()

        self.assert_safe_failure("routing_not_found")

    def test_inactive_binding_blocks_association(self):
        self.binding.is_active = False
        self.binding.save(update_fields=("is_active",))

        self.assert_safe_failure("routing_not_found")

    def test_empty_pastoral_zone_blocks_association(self):
        self.submission.pastoral_zone = ""
        self.submission.save(update_fields=("pastoral_zone",))

        self.assert_safe_failure("routing_value_invalid")

    def test_second_association_does_not_duplicate_events(self):
        first_result = self.associate()
        second_result = self.associate()

        self.assertTrue(first_result.associated)
        self.assertFalse(second_result.associated)
        self.assertEqual(self.submission.processing_events.count(), 1)

    @override_settings(KOBO_ENABLED=True)
    def test_post_requires_change_permission(self):
        self.client.force_login(self.viewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

    @override_settings(KOBO_ENABLED=True)
    def test_get_cannot_associate(self):
        self.client.force_login(self.reviewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 405)
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

    @override_settings(KOBO_ENABLED=True)
    def test_browser_project_id_is_ignored(self):
        self.client.force_login(self.reviewer)
        url = reverse(
            "kobo:submission_associate_project",
            args=(self.submission.pk,),
        )

        response = self.client.post(url, {"project_id": self.other_project.pk})
        self.submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.submission.project_id, self.project.pk)
        self.assertNotEqual(self.submission.project_id, self.other_project.pk)


@override_settings(KOBO_ENABLED=True)
class KoboFicha10AssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="ficha-10-reviewer",
            password="test-password",
        )
        cls.project = Project.objects.create(
            code="PRJ-FICHA-10",
            name="Proyecto para microproyectos",
            status=Project.Status.ACTIVE,
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 - Microproyecto priorizado (depurada)",
            version=FICHA_10_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="ficha-10-asset",
            name="Ficha de microproyectos",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        KoboProjectBinding.objects.create(
            asset=cls.asset,
            project=cls.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-010",
        )

    def valid_payload(self):
        # PRE: Ficha 10 asset and binding are configured for NV-010.
        # POST: returns a complete raw payload whose routing uses normalized data.
        return {
            "_uuid": "ficha-10-association",
            "_xform_id_string": self.asset.asset_uid,
            "today": "2026-07-12",
            "nucleo_code": "NV-010",
            "microproject": {
                "microproject_name": "Rehabilitación del centro comunitario",
                "component": "infrastructure",
                "problem_summary": "Filtraciones persistentes.",
                "specific_objective": "Recuperar la cubierta.",
                "beneficiary_group": "youth women",
                "main_activities": "Reparar el techo.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Espacio protegido.",
            },
        }

    def test_ficha_10_associates_without_creating_operational_records(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-10-association",
            raw_payload=self.valid_payload(),
        )

        process_submission(
            submission,
            default_timezone=ZoneInfo("America/Caracas"),
        )
        submission.refresh_from_db()
        review_submission(
            submission,
            decision=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            reason="",
            reviewed_by=self.reviewer,
        )
        result = associate_submission_with_project(
            submission,
            reviewed_by=self.reviewer,
        )
        submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(submission.project_id, self.project.pk)
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-010")
        self.assertFalse(ProjectUpdate.objects.exists())
        self.assertEqual(Project.objects.count(), 1)

    def test_asset_configuration_rejects_the_territorial_role_for_ficha_10(self):
        discovered_asset = KoboDiscoveredAsset.objects.create(
            asset_uid="ficha-10-incompatible-role",
            name="Ficha 10 - Microproyecto priorizado (depurada)",
            last_seen_at=django_timezone.now(),
        )

        with self.assertRaises(ValidationError):
            configure_discovered_asset(
                discovered_asset,
                name="Configuración incompatible",
                form_definition=self.form_definition,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
                configured_by=self.reviewer,
            )


@override_settings(KOBO_ENABLED=True)
class KoboFicha11AssociationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.reviewer = user_model.objects.create_user(
            username="ficha-11-reviewer",
            password="test-password",
        )
        cls.project = Project.objects.create(
            code="PRJ-FICHA-11",
            name="Proyecto para priorización",
            status=Project.Status.ACTIVE,
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
            version=FICHA_11_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="ficha-11-asset",
            name="Ficha de priorización",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        KoboProjectBinding.objects.create(
            asset=cls.asset,
            project=cls.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-011",
        )

    def valid_payload(self, **overrides):
        # PRE: the Ficha 11 asset has an explicit active routing binding.
        # POST: returns a complete raw priority matrix payload.
        payload = {
            "_uuid": "ficha-11-association",
            "_xform_id_string": self.asset.asset_uid,
            "nucleo_code": "NV-011",
            "scoring": {
                **{field: "4" for field in KoboFicha11NormalizerTests.SCORE_FIELDS},
                "priority_total": "40",
                "suggested_semaphore": "red",
                "final_semaphore": "yellow",
                "final_priority": "high",
                "priority_summary": "Validación técnica independiente.",
            },
        }
        scoring_overrides = {
            key: value for key, value in overrides.items() if key in payload["scoring"]
        }
        payload["scoring"].update(scoring_overrides)
        payload.update(
            {
                key: value
                for key, value in overrides.items()
                if key not in scoring_overrides
            }
        )
        return payload

    def test_ficha_11_processes_reviews_and_associates_without_operations_effects(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-association",
            raw_payload=self.valid_payload(),
        )
        process_submission(submission, default_timezone=ZoneInfo("America/Caracas"))
        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        submission.raw_payload["nucleo_code"] = "RAW-MUST-NOT-ROUTE"
        submission.save(update_fields=("raw_payload",))
        review_submission(
            submission,
            decision=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            reason="",
            reviewed_by=self.reviewer,
        )

        result = associate_submission_with_project(submission, reviewed_by=self.reviewer)
        submission.refresh_from_db()

        self.assertTrue(result.associated)
        self.assertEqual(submission.project_id, self.project.pk)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(submission.normalized_payload["priority_total"], 40)
        self.assertFalse(ProjectUpdate.objects.exists())
        self.assertEqual(Project.objects.count(), 1)

    def test_manipulated_total_fails_processing_before_review(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-invalid-total",
            raw_payload=self.valid_payload(priority_total="39"),
        )

        process_submission(submission, default_timezone=ZoneInfo("America/Caracas"))
        submission.refresh_from_db()

        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_ficha_11_rejects_a_crossed_asset_role(self):
        submission = KoboSubmission.objects.create(
            form_definition=self.form_definition,
            external_id="ficha-11-crossed-role",
            raw_payload={
                "_uuid": "ficha-11-crossed-role",
                "_xform_id_string": self.asset.asset_uid,
            },
            normalized_payload={"nucleo_code": "NV-011"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.asset.form_role = KoboAsset.FormRole.TERRITORIAL_PROFILE
        self.asset.save(update_fields=("form_role",))

        result = associate_submission_with_project(submission, reviewed_by=self.reviewer)
        submission.refresh_from_db()

        self.assertFalse(result.associated)
        self.assertEqual(submission.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertEqual(submission.error_code, "asset_role_incompatible")
