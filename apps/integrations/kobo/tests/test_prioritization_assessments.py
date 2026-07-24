from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

from apps.integrations.kobo.admin import KoboPrioritizationAssessmentAdmin
from apps.integrations.kobo.import_contracts import ImportOutcome
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_11 import (
    FICHA_11_FORM_ID,
    FICHA_11_VERSION,
    SCORE_FIELDS,
    calculate_ficha_11_suggested_semaphore,
)
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboImportRecord,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.services import import_kobo_submission
from apps.operations.models import Donation, Expense, FundAllocation, Project


def canonical_assessment_payload(code="NV-ASSESSMENT", **changes):
    # PRE: changes contains intended canonical Ficha 11 normalized-field overrides.
    # POST: returns a complete persisted prioritization assessment payload.
    scores = {field_name: 4 for field_name in SCORE_FIELDS}
    total = sum(scores.values())
    semaphore = calculate_ficha_11_suggested_semaphore(total)
    payload = {
        "nucleo_code": code,
        "nucleo_code_normalized": code,
        **scores,
        "priority_total": total,
        "priority_total_original": str(total),
        "priority_total_calculated": total,
        "suggested_semaphore": semaphore,
        "suggested_semaphore_original": semaphore,
        "suggested_semaphore_calculated": semaphore,
        "final_semaphore": "yellow",
        "final_priority": "high",
        "priority_summary": "Prioridad territorial validada por el equipo.",
        "calculation_warnings": [],
        "linked_microprojects": "MP-01, MP-02",
    }
    payload.update(changes)
    return payload


class PrioritizationAssessmentFixtureMixin:
    def create_domain(self):
        # PRE: fixture identifiers are free in the current test database.
        # POST: creates an importer, projects, form definitions, and a Ficha 11 asset.
        self.importer = get_user_model().objects.create_user(
            username="assessment-importer"
        )
        self.importer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="operations",
                codename="change_project",
            )
        )
        self.project = Project.objects.create(
            code="PRJ-KOBO-ASSESSMENT",
            name="Núcleo Vital",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-KOBO-ASSESSMENT-OTHER",
            name="Otro Núcleo Vital",
            status=Project.Status.ACTIVE,
        )
        self.ficha_1_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 identity source",
            version=FICHA_01_VERSION,
        )
        self.definition = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 prioritization assessment",
            version=FICHA_11_VERSION,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="prioritization-assessment-asset",
            name="Prioritization assessment",
            form_definition=self.definition,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )

    def create_identity(self, *, code="NV-ASSESSMENT", project=None, status=None):
        # PRE: code is canonical and project is the intended Núcleo Vital project.
        # POST: creates a valid identity sourced from a distinct Ficha 1 submission.
        project = project or self.project
        source = KoboSubmission.objects.create(
            form_definition=self.ficha_1_definition,
            project=project,
            external_id=f"identity-source-{code}",
            raw_payload={"_uuid": f"identity-source-{code}"},
            normalized_payload={"nucleo_code_normalized": code},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            pastoral_zone="centro",
            nucleo_code_original=code,
            nucleo_code_normalized=code,
        )
        return KoboTerritorialIdentity.objects.create(
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            pastoral_zone="centro",
            project=project,
            source_submission=source,
            status=status or KoboTerritorialIdentity.Status.ACTIVE,
        )

    def create_submission(
        self,
        *,
        code="NV-ASSESSMENT",
        project=None,
        payload_changes=None,
        external_id="assessment-candidate",
        status=None,
        routing_status=None,
    ):
        # PRE: inputs describe an intended persisted Ficha 11 import candidate.
        # POST: creates it with canonical normalized data and preserved raw evidence.
        resolved_project = project or self.project
        effective_routing_status = (
            routing_status or KoboSubmission.RoutingStatus.RESOLVED
        )
        if effective_routing_status == KoboSubmission.RoutingStatus.PENDING_IDENTITY:
            resolved_project = None
        return KoboSubmission.objects.create(
            form_definition=self.definition,
            asset=self.asset,
            project=resolved_project,
            external_id=external_id,
            raw_payload={"_uuid": external_id, "private_notes": "raw only"},
            normalized_payload=canonical_assessment_payload(
                code, **(payload_changes or {})
            ),
            status=status or KoboSubmission.Status.APPROVED_FOR_IMPORT,
            normalized_at=timezone.now(),
            routing_status=effective_routing_status,
            routing_resolved_at=timezone.now(),
            nucleo_code_original=code,
            nucleo_code_normalized=code,
        )

    def build_assessment(self, submission, identity, **changes):
        # PRE: submission and identity are coherent persisted Ficha 11 fixtures.
        # POST: returns an unsaved model populated with canonical assessment fields.
        values = {
            "territorial_identity": identity,
            "project": submission.project,
            "source_submission": submission,
            **{field_name: 4 for field_name in SCORE_FIELDS},
            "priority_total_original": 40,
            "priority_total_calculated": 40,
            "suggested_semaphore_original": "red",
            "suggested_semaphore_calculated": "red",
            "final_semaphore": "yellow",
            "final_priority": "high",
            "priority_summary": "Prioridad validada.",
            "calculation_warnings": [],
            "linked_microprojects_snapshot": "MP-01, MP-02",
            "created_by": self.importer,
        }
        values.update(changes)
        return KoboPrioritizationAssessment(**values)


class KoboPrioritizationAssessmentTests(
    PrioritizationAssessmentFixtureMixin, TestCase
):
    def setUp(self):
        self.create_domain()

    def test_model_accepts_valid_scores_and_protects_unique_immutable_evidence(self):
        identity = self.create_identity()
        submission = self.create_submission()
        assessment = self.build_assessment(submission, identity)

        assessment.full_clean()
        assessment.save()

        duplicate = self.build_assessment(submission, identity)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        for protected_object in (identity, self.project, submission, self.importer):
            with self.subTest(protected_object=protected_object):
                with self.assertRaises(ProtectedError):
                    protected_object.delete()
        assessment.final_priority = "critical"
        with self.assertRaises(ValidationError):
            assessment.save()

    def test_model_rejects_invalid_scores_calculations_catalogs_and_warnings(self):
        invalid_values = (
            {"physical_damage_score": 0},
            {"physical_damage_score": 6},
            {"priority_total_calculated": 39},
            {"suggested_semaphore_calculated": "green"},
            {"suggested_semaphore_original": "blue"},
            {"final_semaphore": "blue"},
            {"final_priority": "urgent"},
            {"priority_summary": ""},
            {"calculation_warnings": {"code": "PRIORITY_TOTAL_MISMATCH"}},
        )
        for index, changes in enumerate(invalid_values):
            with self.subTest(changes=changes):
                code = f"NV-ASSESSMENT-INVALID-{index}"
                identity = self.create_identity(code=code)
                submission = self.create_submission(
                    code=code, external_id=f"assessment-invalid-{index}"
                )
                with self.assertRaises(ValidationError):
                    self.build_assessment(
                        submission, identity, **changes
                    ).full_clean()

    def test_model_requires_ficha_11_source_and_coherent_relations(self):
        identity = self.create_identity()
        valid_submission = self.create_submission()
        with self.assertRaises(ValidationError):
            self.build_assessment(identity.source_submission, identity).full_clean()
        with self.assertRaises(ValidationError):
            self.build_assessment(
                valid_submission, identity, project=self.other_project
            ).full_clean()

    def test_identity_keeps_multiple_historical_assessments_and_returns_latest(self):
        identity = self.create_identity()
        assessments = []
        for index in range(2):
            submission = self.create_submission(external_id=f"historical-{index}")
            assessment = self.build_assessment(submission, identity)
            assessment.full_clean()
            assessment.save()
            assessments.append(assessment)

        self.assertEqual(identity.prioritization_assessments.count(), 2)
        self.assertEqual(identity.latest_prioritization_assessment(), assessments[-1])

    def test_approved_ficha_11_creates_exactly_one_assessment_and_import_record(self):
        identity = self.create_identity(status=KoboTerritorialIdentity.Status.OBSERVED)
        original_identity = (
            identity.status,
            identity.project_id,
            identity.pastoral_zone,
            identity.updated_at,
        )
        original_project = (self.project.status, self.project.name)
        submission = self.create_submission()

        result = import_kobo_submission(submission, actor=self.importer)
        submission.refresh_from_db()
        identity.refresh_from_db()
        self.project.refresh_from_db()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.imported_at)
        self.assertEqual(submission.processed_at, submission.imported_at)
        self.assertEqual(KoboPrioritizationAssessment.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        assessment = submission.prioritization_assessment
        record = submission.import_record
        self.assertEqual(record.target_app_label, "kobo")
        self.assertEqual(record.target_model, "KoboPrioritizationAssessment")
        self.assertEqual(record.target_object_id, assessment.pk)
        self.assertEqual(result.materialization_id, assessment.pk)
        self.assertEqual(
            (identity.status, identity.project_id, identity.pastoral_zone, identity.updated_at),
            original_identity,
        )
        self.assertEqual((self.project.status, self.project.name), original_project)
        self.assertFalse(KoboPrioritizedMicroproject.objects.exists())
        self.assertFalse(Donation.objects.exists())
        self.assertFalse(FundAllocation.objects.exists())
        self.assertFalse(Expense.objects.exists())

    def test_pending_review_identity_accepts_assessment_without_state_change(self):
        identity = self.create_identity(
            status=KoboTerritorialIdentity.Status.PENDING_REVIEW
        )
        submission = self.create_submission()

        result = import_kobo_submission(submission, actor=self.importer)
        identity.refresh_from_db()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(
            identity.status, KoboTerritorialIdentity.Status.PENDING_REVIEW
        )

    def test_calculation_mismatches_persist_and_do_not_overwrite_human_decisions(self):
        self.create_identity()
        warnings = [
            {
                "code": "PRIORITY_TOTAL_MISMATCH",
                "message": "Kobo priority_total differs from the SIGEDON calculation.",
                "original_value": "41",
                "calculated_value": 40,
            },
            {
                "code": "SUGGESTED_SEMAPHORE_MISMATCH",
                "message": (
                    "Kobo suggested_semaphore differs from the SIGEDON calculation."
                ),
                "original_value": "yellow",
                "calculated_value": "red",
            },
        ]
        submission = self.create_submission(
            payload_changes={
                "priority_total_original": "41",
                "suggested_semaphore_original": "yellow",
                "final_semaphore": "green",
                "final_priority": "low",
                "calculation_warnings": warnings,
                "linked_microprojects": "Techo comunitario; Agua segura",
            }
        )

        result = import_kobo_submission(submission, actor=self.importer)
        assessment = KoboPrioritizationAssessment.objects.get()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["PRIORITY_TOTAL_MISMATCH", "SUGGESTED_SEMAPHORE_MISMATCH"],
        )
        self.assertEqual(assessment.priority_total_original, 41)
        self.assertEqual(assessment.priority_total_calculated, 40)
        self.assertEqual(assessment.suggested_semaphore_calculated, "red")
        self.assertEqual(assessment.final_semaphore, "green")
        self.assertEqual(assessment.final_priority, "low")
        self.assertEqual(assessment.calculation_warnings, warnings)
        self.assertEqual(
            assessment.linked_microprojects_snapshot,
            "Techo comunitario; Agua segura",
        )

    def test_invalid_normalized_scores_calculations_decisions_and_snapshot_block(self):
        invalid_payloads = (
            {"physical_damage_score": 0},
            {"priority_total_calculated": 41},
            {"suggested_semaphore_calculated": "green"},
            {"suggested_semaphore_original": "blue"},
            {"final_semaphore": "blue"},
            {"final_priority": "urgent"},
            {"priority_summary": ""},
            {"linked_microprojects": ["MP-01"]},
            {"calculation_warnings": [] , "priority_total_original": "41"},
        )
        for index, changes in enumerate(invalid_payloads):
            with self.subTest(changes=changes):
                code = f"NV-ASSESSMENT-DATA-{index}"
                self.create_identity(code=code)
                submission = self.create_submission(
                    code=code,
                    payload_changes=changes,
                    external_id=f"invalid-assessment-data-{index}",
                )
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, "FICHA_11_ASSESSMENT_INVALID")
        self.assertFalse(KoboPrioritizationAssessment.objects.exists())

    def test_identity_project_code_missing_inactive_and_open_conflict_block(self):
        cases = []
        project_mismatch = self.create_submission(
            code="NV-ASSESSMENT-PROJECT", external_id="assessment-project-mismatch"
        )
        self.create_identity(
            code="NV-ASSESSMENT-PROJECT", project=self.other_project
        )
        cases.append((project_mismatch, "FICHA_11_IDENTITY_MISMATCH"))

        code_mismatch = self.create_submission(
            code="NV-ASSESSMENT-CODE",
            external_id="assessment-code-mismatch",
            payload_changes={"nucleo_code": "NV-OTHER"},
        )
        self.create_identity(code="NV-ASSESSMENT-CODE")
        cases.append((code_mismatch, "FICHA_11_IDENTITY_MISMATCH"))

        missing = self.create_submission(
            code="NV-ASSESSMENT-MISSING", external_id="assessment-missing"
        )
        cases.append((missing, "FICHA_11_IDENTITY_MISSING"))

        inactive = self.create_submission(
            code="NV-ASSESSMENT-INACTIVE", external_id="assessment-inactive"
        )
        self.create_identity(
            code="NV-ASSESSMENT-INACTIVE",
            status=KoboTerritorialIdentity.Status.INACTIVE,
        )
        cases.append((inactive, "FICHA_11_IDENTITY_INACTIVE"))

        conflict_submission = self.create_submission(
            code="NV-ASSESSMENT-CONFLICT", external_id="assessment-conflict"
        )
        conflict_identity = self.create_identity(code="NV-ASSESSMENT-CONFLICT")
        KoboTerritorialIdentityConflict.objects.create(
            identity=conflict_identity,
            incoming_submission=conflict_submission,
            existing_pastoral_zone="centro",
            proposed_pastoral_zone="este",
            existing_project=self.project,
            proposed_project=self.other_project,
        )
        cases.append((conflict_submission, "FICHA_11_TERRITORIAL_CONFLICT"))

        for submission, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, reason_code)
        self.assertFalse(KoboPrioritizationAssessment.objects.exists())

    def test_unresolved_conflicting_routing_and_wrong_review_state_block_common_flow(self):
        self.create_identity()
        cases = (
            self.create_submission(
                external_id="assessment-routing-pending",
                routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            ),
            self.create_submission(
                external_id="assessment-routing-conflict",
                routing_status=KoboSubmission.RoutingStatus.CONFLICT,
            ),
            self.create_submission(
                external_id="assessment-not-approved",
                status=KoboSubmission.Status.READY_FOR_REVIEW,
            ),
        )
        expected = (
            "IMPORT_ROUTING_PENDING",
            "IMPORT_ROUTING_CONFLICT",
            "IMPORT_REVIEW_NOT_APPROVED",
        )
        for submission, reason_code in zip(cases, expected, strict=True):
            with self.subTest(reason_code=reason_code):
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, reason_code)

    def test_retry_and_corruption_do_not_duplicate_state_or_events(self):
        identity = self.create_identity()
        submission = self.create_submission()

        first = import_kobo_submission(submission, actor=self.importer)
        second = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(first.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, ImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(KoboPrioritizationAssessment.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(submission.processing_events.filter(code="imported").count(), 1)
        self.assertEqual(
            submission.processing_events.filter(
                code="prioritization_assessment_created"
            ).count(),
            1,
        )

        corrupt_submission = self.create_submission(
            code="NV-ASSESSMENT-CORRUPT", external_id="assessment-corrupt"
        )
        corrupt_identity = self.create_identity(code="NV-ASSESSMENT-CORRUPT")
        corrupt_assessment = self.build_assessment(
            corrupt_submission, corrupt_identity
        )
        corrupt_assessment.full_clean()
        corrupt_assessment.save()
        corrupt_result = import_kobo_submission(
            corrupt_submission, actor=self.importer
        )
        self.assertEqual(corrupt_result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(
            corrupt_result.reason_code, "FICHA_11_ASSESSMENT_STATE_CONFLICT"
        )
        self.assertEqual(KoboPrioritizationAssessment.objects.count(), 2)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)

    def test_import_is_atomic_when_target_record_event_or_audit_fails(self):
        failure_targets = (
            "apps.integrations.kobo.models.KoboPrioritizationAssessment.save",
            "apps.integrations.kobo.models.KoboImportRecord.objects.create",
            "apps.integrations.kobo.models.KoboProcessingEvent.objects.create",
            "apps.operations.services.log_action",
        )
        for index, target in enumerate(failure_targets):
            with self.subTest(target=target):
                code = f"NV-ASSESSMENT-ROLLBACK-{index}"
                identity = self.create_identity(code=code)
                submission = self.create_submission(
                    code=code, external_id=f"assessment-rollback-{index}"
                )
                raw_payload = submission.raw_payload.copy()
                normalized_payload = submission.normalized_payload.copy()
                with patch(target, side_effect=RuntimeError("forced failure")):
                    result = import_kobo_submission(submission, actor=self.importer)
                submission.refresh_from_db()
                identity.refresh_from_db()
                self.assertEqual(result.outcome, ImportOutcome.FAILED)
                self.assertEqual(submission.status, KoboSubmission.Status.PROCESSING_FAILED)
                self.assertIsNone(submission.imported_at)
                self.assertEqual(submission.error_code, "MATERIALIZATION_FAILED")
                self.assertNotIn("forced failure", submission.error_message)
                self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)
                self.assertEqual(submission.project, self.project)
                self.assertEqual(
                    submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED
                )
                self.assertEqual(submission.raw_payload, raw_payload)
                self.assertEqual(submission.normalized_payload, normalized_payload)
                self.assertFalse(
                    KoboPrioritizationAssessment.objects.filter(
                        source_submission=submission
                    ).exists()
                )
                self.assertFalse(
                    KoboImportRecord.objects.filter(submission=submission).exists()
                )
                self.assertFalse(
                    submission.processing_events.filter(code="imported").exists()
                )
                self.assertLessEqual(
                    submission.processing_events.filter(
                        stage="operational_import",
                        code="MATERIALIZATION_FAILED",
                    ).count(),
                    1,
                )

    def test_event_metadata_and_admin_are_safe_readonly_and_searchable(self):
        self.create_identity()
        submission = self.create_submission(
            payload_changes={
                "priority_summary": "Sensitive assessment narrative",
                "linked_microprojects": "Sensitive free names",
            }
        )

        result = import_kobo_submission(submission, actor=self.importer)
        assessment = KoboPrioritizationAssessment.objects.get(
            pk=result.materialization_id
        )
        event = submission.processing_events.get(
            code="prioritization_assessment_created"
        )
        self.assertEqual(
            set(event.metadata),
            {
                "assessment_id",
                "identity_id",
                "project_id",
                "nucleo_code_normalized",
                "priority_total_calculated",
                "final_semaphore",
                "final_priority",
                "warning_codes",
            },
        )
        serialized = str(event.metadata)
        for forbidden in (
            "raw_payload",
            "Sensitive assessment narrative",
            "Sensitive free names",
            "raw only",
        ):
            self.assertNotIn(forbidden, serialized)

        model_admin = KoboPrioritizationAssessmentAdmin(
            KoboPrioritizationAssessment, admin.site
        )
        request = RequestFactory().post("/admin/kobo/prioritization-assessment/")
        request.user = self.importer
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, assessment))
        self.assertFalse(model_admin.has_delete_permission(request, assessment))
        self.assertEqual(model_admin.actions, ())
        self.assertIn("priority_summary", model_admin.search_fields)
        self.assertIn("calculation_warnings", model_admin.readonly_fields)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
class KoboPrioritizationAssessmentConcurrencyTests(
    PrioritizationAssessmentFixtureMixin, TransactionTestCase
):
    reset_sequences = True

    def setUp(self):
        self.create_domain()
        self.create_identity()
        self.submission = self.create_submission()

    def _import_worker(self, barrier, outcomes):
        # PRE: both workers target one approved submission using separate DB connections.
        # POST: publishes one import outcome and closes its thread-local connection.
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=self.importer.pk)
            submission = KoboSubmission.objects.get(pk=self.submission.pk)
            barrier.wait(timeout=10)
            result = import_kobo_submission(submission, actor=actor)
            outcomes.put((result.outcome, result.reason_code))
        except Exception as exc:
            outcomes.put(exc)
        finally:
            connections.close_all()

    def test_two_workers_create_one_assessment_and_one_import_record(self):
        barrier = Barrier(2)
        outcomes = Queue()
        workers = [
            Thread(target=self._import_worker, args=(barrier, outcomes))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)

        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        for result in results:
            if isinstance(result, Exception):
                raise result
        self.assertCountEqual(
            [result[0] for result in results],
            [ImportOutcome.IMPORTED, ImportOutcome.ALREADY_IMPORTED],
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(KoboPrioritizationAssessment.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(
            KoboProcessingEvent.objects.filter(
                submission=self.submission,
                code="prioritization_assessment_created",
            ).count(),
            1,
        )
        self.assertEqual(
            KoboProcessingEvent.objects.filter(
                submission=self.submission, code="imported"
            ).count(),
            1,
        )
