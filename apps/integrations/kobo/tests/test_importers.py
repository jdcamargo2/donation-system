from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboAttachment
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.services import get_project_imported_submissions
from apps.integrations.kobo.services import get_project_pending_submissions
from apps.integrations.kobo.services import get_project_submission_history
from apps.integrations.kobo.services import import_kobo_submission
from apps.integrations.kobo.services import reject_kobo_submission
from apps.integrations.kobo.services import restore_kobo_submission_to_review
from apps.integrations.kobo.services.importers import _lock_submission_for_operational_import
from apps.operations.models import AuditLog
from apps.operations.models import Project
from copy import deepcopy
from datetime import date
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.db import transaction
from django.test import TestCase
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone as django_timezone


@override_settings(KOBO_ENABLED=True)
class KoboProjectImportedSubmissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="project-kobo-viewer",
            password="test-password",
        )
        cls.reviewer = user_model.objects.create_user(
            username="project-kobo-reviewer",
            password="test-password",
        )
        cls.unprivileged = user_model.objects.create_user(
            username="project-only-viewer",
            password="test-password",
        )
        permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(
                codename__in=(
                    "view_project",
                    "change_project",
                    "view_kobosubmission",
                    "change_kobosubmission",
                )
            )
        }
        cls.viewer.user_permissions.add(
            permissions["view_project"],
            permissions["view_kobosubmission"],
        )
        cls.reviewer.user_permissions.add(
            permissions["view_project"],
            permissions["change_project"],
            permissions["view_kobosubmission"],
            permissions["change_kobosubmission"],
        )
        cls.unprivileged.user_permissions.add(permissions["view_project"])
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )
        cls.microproject_form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 - Microproyecto priorizado (depurada)",
            version=FICHA_10_VERSION,
        )
        cls.prioritization_form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
            version=FICHA_11_VERSION,
        )

    def setUp(self):
        self.project = Project.objects.create(
            code="PRJ-KOBO-DETAIL",
            name="Proyecto con levantamiento",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-KOBO-OTHER",
            name="Otro proyecto",
            status=Project.Status.ACTIVE,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="project-detail-asset",
            name="Ficha territorial activa",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.inactive_asset = KoboAsset.objects.create(
            asset_uid="inactive-project-detail-asset",
            name="Ficha territorial inactiva",
            form_definition=self.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=False,
        )
        self.microproject_asset = KoboAsset.objects.create(
            asset_uid="project-detail-microproject-asset",
            name="Ficha de microproyectos activa",
            form_definition=self.microproject_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )
        self.prioritization_asset = KoboAsset.objects.create(
            asset_uid="project-detail-prioritization-asset",
            name="Ficha de priorización activa",
            form_definition=self.prioritization_form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        self.binding = KoboProjectBinding.objects.create(
            asset=self.asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="NV-001",
        )
        self.imported = self.create_submission(
            "visible-imported",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.other_imported = self.create_submission(
            "other-project-imported",
            project=self.other_project,
            asset=self.asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.ready = self.create_submission(
            "ready-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.approved = self.create_submission(
            "approved-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.validation_failed = self.create_submission(
            "validation-failed-hidden",
            project=self.project,
            asset=self.asset,
            status=KoboSubmission.Status.VALIDATION_FAILED,
        )
        self.inactive_asset_submission = self.create_submission(
            "inactive-asset-hidden",
            project=self.project,
            asset=self.inactive_asset,
            status=KoboSubmission.Status.IMPORTED,
        )
        self.microproject_imported = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            project=self.project,
            external_id="visible-microproject-imported",
            raw_payload={"_uuid": "visible-microproject-imported"},
            normalized_payload={
                "nucleo_code": "NV-001",
                "microproject_name": "Techo para el centro comunitario",
                "component": "infrastructure",
                "problem_summary": "Filtraciones persistentes.",
                "specific_objective": "Recuperar la cubierta.",
                "beneficiary_group": ["youth", "women"],
                "main_activities": "Reparar el techo.",
                "estimated_cost_range": "5000_15000",
                "implementation_urgency": "immediate",
                "technical_viability": "high",
                "expected_result": "Espacio protegido.",
            },
            status=KoboSubmission.Status.IMPORTED,
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
            processed_at=django_timezone.now(),
        )
        self.prioritization_imported = KoboSubmission.objects.create(
            form_definition=self.prioritization_form_definition,
            asset=self.prioritization_asset,
            project=self.project,
            external_id="visible-prioritization-imported",
            raw_payload={"_uuid": "visible-prioritization-imported"},
            normalized_payload={
                "nucleo_code": "NV-011",
                "physical_damage_score": 4,
                "affected_families_score": 4,
                "social_vulnerability_score": 4,
                "services_interruption_score": 4,
                "livelihood_loss_score": 4,
                "parish_capacity_score": 4,
                "territorial_accessibility_score": 4,
                "allies_availability_score": 4,
                "rapid_impact_score": 4,
                "financial_viability_score": 4,
                "priority_total": 40,
                "suggested_semaphore": "red",
                "final_semaphore": "yellow",
                "final_priority": "high",
                "priority_summary": "Prioridad validada.",
                "linked_microprojects": "MP-01",
            },
            status=KoboSubmission.Status.IMPORTED,
            assessment_date=date(2026, 7, 12),
            imported_at=django_timezone.now(),
            processed_at=django_timezone.now(),
        )
        self.downloaded_attachment = KoboAttachment.objects.create(
            submission=self.imported,
            field_name="territorial_evidence/front",
            source_url="https://kf.example.test/private/source-never-visible",
            content_type="image/jpeg",
            size_bytes=20,
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.DOWNLOADED,
            file="kobo-visible-evidence.jpg",
        )
        self.pending_attachment = KoboAttachment.objects.create(
            submission=self.imported,
            field_name="territorial_evidence/pending",
            source_url="https://kf.example.test/private/pending-never-visible",
            content_type="image/jpeg",
            privacy_level=KoboAttachment.PrivacyLevel.INTERNAL_REVIEW,
            status=KoboAttachment.Status.PENDING,
        )

    def create_submission(self, external_id, *, project, asset, status):
        # PRE: project, asset and status define a staging visibility scenario.
        # POST: returns a persisted Ficha 1 submission with sensitive test data.
        return KoboSubmission.objects.create(
            form_definition=self.form_definition,
            asset=asset,
            project=project,
            external_id=external_id,
            raw_payload={
                "_uuid": external_id,
                "_submitted_by": "Sensitive Submitter",
                "deviceid": "Sensitive Device",
            },
            normalized_payload={
                "nucleo_code": "NV-001",
                "communities_covered": "Comunidades visibles",
                "estimated_households": 300,
                "access_difficulties": "no",
                "access_difficulties_notes": None,
                "initial_priority_perception": "medium",
                "general_notes": "Nota visible",
                "location": {"latitude": 10.0, "longitude": -66.0},
                "parish_delegate": "Sensitive Delegate",
                "contact_phone": "+58-sensitive-phone",
                "main_informant_role": "Sensitive Informant Role",
            },
            status=status,
            pastoral_zone="catia_la_mar",
            parish="visible-parish",
            primary_community="visible-community",
            assessment_date=date(2026, 7, 11),
            imported_at=django_timezone.now() if status == KoboSubmission.Status.IMPORTED else None,
            normalized_at=django_timezone.now(),
            processed_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )

    def test_service_returns_only_imported_exact_project_active_asset(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            )
        )

        self.assertEqual(submissions, [self.imported])
        self.assertEqual(submissions[0].attachment_count, 2)
        self.assertEqual(submissions[0].downloaded_attachment_count, 1)

    def test_service_separates_imported_microprojects_by_role(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            )
        )

        self.assertEqual(submissions, [self.microproject_imported])

    def test_service_separates_imported_prioritization_matrices_by_role(self):
        submissions = list(
            get_project_imported_submissions(
                self.project,
                form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            )
        )

        self.assertEqual(submissions, [self.prioritization_imported])

    def test_pending_service_and_project_detail_show_only_reviewable_submissions(self):
        submissions = list(get_project_pending_submissions(self.project))

        self.assertEqual(submissions, [self.ready])
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))
        queue_response = self.client.get(
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,))
        )

        self.assertContains(response, "Fichas Kobo pendientes de revisión")
        self.assertContains(response, "ready-hidden")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "validation-failed-hidden")
        self.assertNotContains(response, "other-project-imported")
        self.assertNotContains(response, "Revisar")
        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, "ready-hidden")
        self.assertNotContains(queue_response, "approved-hidden")
        self.assertNotContains(queue_response, "validation-failed-hidden")
        self.assertNotContains(queue_response, "other-project-imported")

    def test_project_detail_shows_only_visible_imported_submission(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "operations/project_detail.html")
        self.assertContains(response, "Levantamientos de campo")
        self.assertContains(response, "visible-parish")
        self.assertNotContains(response, "other-project-imported")
        self.assertContains(response, "ready-hidden")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "inactive-asset-hidden")
        self.assertContains(response, "Microproyectos priorizados")
        self.assertContains(response, "Techo para el centro comunitario")
        self.assertContains(response, "Matriz de priorización y semáforo")
        self.assertContains(response, self.project.code)
        for sensitive_value in (
            "Sensitive Delegate",
            "Sensitive Informant Role",
            "+58-sensitive-phone",
            "Sensitive Submitter",
            "Sensitive Device",
        ):
            self.assertNotContains(response, sensitive_value)

    def test_pending_review_is_project_scoped_and_uses_normalized_payload(self):
        review_url = reverse(
            "kobo:project_pending_submission_review",
            args=(self.project.pk, self.ready.pk),
        )
        mismatched_url = reverse(
            "kobo:project_pending_submission_review",
            args=(self.other_project.pk, self.ready.pk),
        )
        self.client.force_login(self.reviewer)

        response = self.client.get(review_url)
        mismatched_response = self.client.get(mismatched_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comunidades visibles")
        self.assertContains(response, "Identificación territorial")
        self.assertContains(response, "Pendiente de revisión")
        self.assertContains(response, "Rechazar ficha")
        self.assertNotContains(response, "Sensitive Submitter")
        self.assertNotContains(response, "raw_payload")
        self.assertEqual(mismatched_response.status_code, 404)

        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.get(review_url).status_code, 403)

    def test_rejection_action_requires_other_comment_and_is_idempotent(self):
        url = reverse(
            "kobo:project_pending_submission_reject",
            args=(self.project.pk, self.ready.pk),
        )
        self.client.force_login(self.reviewer)

        invalid_response = self.client.post(url, {"reason": "other", "comment": ""})
        self.assertEqual(invalid_response.status_code, 400)
        self.ready.refresh_from_db()
        self.assertEqual(self.ready.status, KoboSubmission.Status.READY_FOR_REVIEW)

        first_response = self.client.post(
            url,
            {"reason": "test_submission", "comment": ""},
        )
        second_response = self.client.post(
            url,
            {"reason": "test_submission", "comment": ""},
        )
        self.ready.refresh_from_db()

        self.assertRedirects(
            first_response,
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,)),
        )
        self.assertRedirects(
            second_response,
            reverse("kobo:project_pending_submission_list", args=(self.project.pk,)),
        )
        self.assertEqual(self.ready.status, KoboSubmission.Status.REJECTED)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.REJECTED,
                summary="Ficha Kobo rechazada.",
            ).count(),
            1,
        )

    def test_supported_stub_handlers_never_mark_imported(self):
        microproject_pending = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            project=self.project,
            external_id="pending-microproject",
            raw_payload={"_uuid": "pending-microproject"},
            normalized_payload=self.microproject_imported.normalized_payload,
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            normalized_at=django_timezone.now(),
            processed_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )
        prioritization_pending = KoboSubmission.objects.create(
            form_definition=self.prioritization_form_definition,
            asset=self.prioritization_asset,
            project=self.project,
            external_id="pending-prioritization",
            raw_payload={"_uuid": "pending-prioritization"},
            normalized_payload=self.prioritization_imported.normalized_payload,
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            normalized_at=django_timezone.now(),
            processed_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )
        self.ready.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
        self.ready.save(update_fields=("status",))

        for submission in (self.ready, microproject_pending, prioritization_pending):
            with self.subTest(submission=submission.external_id):
                result = import_kobo_submission(submission, actor=self.reviewer)
                submission.refresh_from_db()
                self.assertFalse(result.imported)
                self.assertEqual(
                    submission.status,
                    KoboSubmission.Status.APPROVED_FOR_IMPORT,
                )
                self.assertEqual(submission.project, self.project)
                self.assertIsNone(submission.imported_at)
                self.assertTrue(
                    submission.processing_events.filter(
                        stage="operational_import",
                        code="MATERIALIZATION_NOT_IMPLEMENTED",
                    ).exists()
                )

        repeated = import_kobo_submission(self.ready, actor=self.reviewer)
        self.assertFalse(repeated.imported)
        self.assertFalse(repeated.already_imported)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.CREATED,
                user=self.reviewer,
            ).count(),
            0,
        )
        self.assertEqual(self.ready.processing_events.count(), 1)

    def test_pending_territorial_submission_is_not_importable_or_in_project_queue(self):
        pending = KoboSubmission.objects.create(
            form_definition=self.microproject_form_definition,
            asset=self.microproject_asset,
            external_id="pending-territorial-identity",
            raw_payload={"_uuid": "pending-territorial-identity"},
            normalized_payload={"nucleo_code_normalized": "NV-PENDING"},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            normalized_at=django_timezone.now(),
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            routing_reason_code="unknown_territorial_identity",
            nucleo_code_original="NV-PENDING",
            nucleo_code_normalized="NV-PENDING",
        )

        result = import_kobo_submission(pending, actor=self.reviewer)
        pending.refresh_from_db()

        self.assertFalse(result.imported)
        self.assertEqual(pending.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertEqual(pending.error_code, "IMPORT_ROUTING_PENDING")
        self.assertNotIn(pending, get_project_pending_submissions(self.project))

    def test_operational_import_lock_query_has_no_nullable_join(self):
        with transaction.atomic():
            with CaptureQueriesContext(connection) as queries:
                locked_submission = _lock_submission_for_operational_import(
                    self.ready.pk
                )

        lock_query = next(
            query["sql"]
            for query in queries.captured_queries
            if "kobo_kobosubmission" in query["sql"].lower()
        ).upper()
        self.assertEqual(locked_submission.pk, self.ready.pk)
        self.assertNotIn(" JOIN ", lock_query)
        if connection.vendor == "postgresql":
            self.assertIn("FOR UPDATE", lock_query)

    def test_import_action_preserves_ready_submission_when_configuration_is_invalid(self):
        self.asset.is_active = False
        self.asset.save(update_fields=("is_active",))
        self.ready.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
        self.ready.save(update_fields=("status",))

        result = import_kobo_submission(self.ready, actor=self.reviewer)
        self.ready.refresh_from_db()

        self.assertFalse(result.imported)
        self.assertFalse(result.already_imported)
        self.assertEqual(self.ready.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertIsNone(self.ready.imported_at)
        self.assertTrue(
            self.ready.processing_events.filter(
                stage="operational_import", code="IMPORT_ASSET_INVALID"
            ).exists()
        )

    def test_import_action_requires_project_change_permission_and_transitions_submission(self):
        url = reverse(
            "kobo:project_pending_submission_import",
            args=(self.project.pk, self.ready.pk),
        )
        self.client.force_login(self.unprivileged)
        self.assertEqual(self.client.post(url).status_code, 403)

        self.client.force_login(self.reviewer)
        response = self.client.post(url)
        self.ready.refresh_from_db()

        self.assertRedirects(response, reverse("project_detail", args=(self.project.pk,)))
        self.assertEqual(
            self.ready.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        self.assertIsNone(self.ready.imported_at)

    def test_rejection_is_auditable_idempotent_and_excluded_from_pending(self):
        original_raw = deepcopy(self.ready.raw_payload)
        original_normalized = deepcopy(self.ready.normalized_payload)

        result = reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="duplicate",
            comment="<b>Repetida</b>",
        )
        self.ready.refresh_from_db()

        self.assertTrue(result.rejected)
        self.assertEqual(self.ready.status, KoboSubmission.Status.REJECTED)
        self.assertEqual(self.ready.raw_payload, original_raw)
        self.assertEqual(self.ready.normalized_payload, original_normalized)
        rejection_event = self.ready.processing_events.get(stage="review", code="duplicate")
        self.assertEqual(rejection_event.message, "Repetida")
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(self.ready.pk),
                action=AuditLog.Action.REJECTED,
                user=self.reviewer,
                summary="Ficha Kobo rechazada.",
            ).count(),
            1,
        )
        self.assertNotIn(self.ready, get_project_pending_submissions(self.project))
        self.assertNotIn(
            self.ready,
            get_project_imported_submissions(self.project),
        )

        repeated = reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="duplicate",
        )
        self.assertTrue(repeated.already_rejected)
        self.assertEqual(
            self.ready.processing_events.filter(stage="review", code="duplicate").count(),
            1,
        )

    def test_rejection_validates_reason_state_and_restoration(self):
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.ready,
                actor=self.reviewer,
                reason="other",
            )
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.ready,
                actor=self.reviewer,
                reason="invalid",
            )
        with self.assertRaises(KoboPayloadError):
            reject_kobo_submission(
                self.imported,
                actor=self.reviewer,
                reason="duplicate",
            )

        reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="other",
            comment="Descartada por revisión.",
        )
        restored = restore_kobo_submission_to_review(self.ready, actor=self.reviewer)
        self.ready.refresh_from_db()

        self.assertTrue(restored.restored)
        self.assertEqual(self.ready.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertTrue(
            self.ready.processing_events.filter(stage="review", code="other").exists()
        )
        self.assertTrue(
            self.ready.processing_events.filter(stage="review", code="restored").exists()
        )
        with self.assertRaises(KoboPayloadError):
            restore_kobo_submission_to_review(self.imported, actor=self.reviewer)

    def test_history_shows_only_imported_and_rejected_submissions(self):
        reject_kobo_submission(
            self.ready,
            actor=self.reviewer,
            reason="test_submission",
        )
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_history", args=(self.project.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importada")
        self.assertContains(response, "Rechazada")
        self.assertContains(response, "Submission de prueba")
        self.assertNotContains(response, "approved-hidden")
        self.assertNotContains(response, "validation-failed-hidden")
        self.assertIn(self.ready, get_project_submission_history(self.project))

    @override_settings(KOBO_ENABLED=False)
    def test_disabled_kobo_hides_section_and_uses_legacy_project_detail(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("project_detail", args=(self.project.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "web/project_detail.html")
        self.assertNotContains(response, "Levantamientos de campo")
        self.assertEqual(response.context["kobo_submissions"], ())

    def test_project_submission_detail_requires_login_and_permission(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))

        anonymous_response = self.client.get(url)
        self.client.force_login(self.unprivileged)
        forbidden_response = self.client.get(url)

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertEqual(forbidden_response.status_code, 403)

    def test_sensitive_detail_requires_elevated_permission(self):
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))
        self.client.force_login(self.viewer)
        viewer_response = self.client.get(url)

        self.assertContains(viewer_response, "NV-001")
        self.assertNotContains(viewer_response, "Datos internos sensibles")
        self.assertNotContains(viewer_response, "+58-sensitive-phone")
        self.assertNotContains(viewer_response, "Sensitive Delegate")
        self.assertNotContains(viewer_response, "Sensitive Informant Role")
        self.assertNotContains(viewer_response, "Nombre del microproyecto")

        self.client.force_login(self.reviewer)
        reviewer_response = self.client.get(url)

        self.assertContains(reviewer_response, "Datos internos sensibles")
        self.assertContains(reviewer_response, "+58-sensitive-phone")
        self.assertContains(reviewer_response, "Sensitive Delegate")
        self.assertContains(reviewer_response, "Sensitive Informant Role")
        self.assertContains(reviewer_response, "Sensitive Device")

    def test_microproject_detail_uses_human_readable_labels(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.microproject_imported.pk,))
        )

        self.assertContains(response, "Microproyecto priorizado")
        self.assertContains(response, "Nombre del microproyecto")
        self.assertNotContains(response, "Salud y atención psicosocial")
        self.assertContains(response, "Infraestructura")
        self.assertContains(response, "Inmediata")
        self.assertContains(response, "Alta")
        self.assertNotContains(response, "Hogares estimados")
        self.assertNotContains(response, "_submitted_by")

    def test_prioritization_detail_distinguishes_suggested_and_final_semaphores(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.prioritization_imported.pk,))
        )

        self.assertContains(response, "Nivel de daño físico")
        self.assertContains(response, "Puntaje total")
        self.assertContains(response, "Semáforo sugerido")
        self.assertContains(response, "Semáforo final validado")
        self.assertNotContains(response, "Nombre del microproyecto")
        self.assertNotContains(response, "Hogares estimados")
        self.assertNotContains(response, "raw_payload")

    def test_detail_hides_sources_and_non_downloaded_attachments(self):
        self.client.force_login(self.viewer)
        url = reverse("kobo:project_submission_detail", args=(self.imported.pk,))

        response = self.client.get(url)

        self.assertContains(response, "kobo-visible-evidence.jpg")
        self.assertNotContains(response, self.downloaded_attachment.source_url)
        self.assertNotContains(response, self.pending_attachment.source_url)
        self.assertNotContains(response, "territorial_evidence/pending")

    def test_project_model_dashboard_and_public_portal_remain_kobo_free(self):
        self.assertFalse(
            any(
                field.name.startswith("kobo_") and not field.auto_created
                for field in Project._meta.get_fields()
            )
        )
        self.client.force_login(self.viewer)
        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertNotContains(dashboard_response, "Levantamientos de campo")

        self.client.logout()
        public_response = self.client.get(
            reverse(
                "public_portal:public_project_detail",
                args=(self.project.pk,),
            )
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertNotContains(public_response, "Levantamientos de campo")
        self.assertNotContains(public_response, "visible-parish")
