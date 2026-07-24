from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

from apps.integrations.kobo.admin import KoboPrioritizedMicroprojectAdmin
from apps.integrations.kobo.import_contracts import ImportOutcome
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboImportRecord,
    KoboPrioritizedMicroproject,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.services import import_kobo_submission
from apps.operations.models import AuditLog, Donation, Expense, FundAllocation, Project


def canonical_microproject_payload(code="NV-MICROPROJECT", **changes):
    # PRE: changes contains intended canonical Ficha 10 field overrides.
    # POST: returns a complete persisted normalized microproject proposal.
    payload = {
        "nucleo_code": code,
        "nucleo_code_normalized": code,
        "microproject_name": "Rehabilitación del centro comunitario",
        "component": "infrastructure",
        "problem_summary": "El techo presenta filtraciones.",
        "specific_objective": "Recuperar un espacio comunitario seguro.",
        "beneficiary_group": ["youth", "women"],
        "main_activities": "Reparar techo y adecuar instalaciones.",
        "estimated_cost_range": "5000_15000",
        "implementation_urgency": "immediate",
        "technical_viability": "high",
        "expected_result": "Centro comunitario operativo y seguro.",
    }
    payload.update(changes)
    return payload


class PrioritizedMicroprojectFixtureMixin:
    def create_domain(self):
        # PRE: the test database is empty enough for unique fixture identifiers.
        # POST: creates an importer, two projects, both form definitions and a Ficha 10 asset.
        self.importer = get_user_model().objects.create_user(username="micro-importer")
        self.importer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="operations",
                codename="change_project",
            )
        )
        self.project = Project.objects.create(
            code="PRJ-KOBO-MICRO",
            name="Núcleo Vital",
            status=Project.Status.ACTIVE,
        )
        self.other_project = Project.objects.create(
            code="PRJ-KOBO-MICRO-OTHER",
            name="Otro Núcleo Vital",
            status=Project.Status.ACTIVE,
        )
        self.ficha_1_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 identity source",
            version=FICHA_01_VERSION,
        )
        self.definition = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 microproject",
            version=FICHA_10_VERSION,
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="prioritized-microproject-asset",
            name="Prioritized microproject",
            form_definition=self.definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
        )

    def create_identity(self, *, code="NV-MICROPROJECT", project=None, status=None):
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
        code="NV-MICROPROJECT",
        project=None,
        payload_changes=None,
        external_id="microproject-candidate",
        status=None,
        routing_status=None,
    ):
        # PRE: inputs describe an intended persisted Ficha 10 import candidate.
        # POST: creates it with canonical normalized data and preserved raw evidence.
        return KoboSubmission.objects.create(
            form_definition=self.definition,
            asset=self.asset,
            project=project or self.project,
            external_id=external_id,
            raw_payload={"_uuid": external_id, "sensitive_notes": "raw only"},
            normalized_payload=canonical_microproject_payload(
                code, **(payload_changes or {})
            ),
            status=status or KoboSubmission.Status.APPROVED_FOR_IMPORT,
            primary_community="Comunidad La Esperanza",
            normalized_at=timezone.now(),
            routing_status=routing_status or KoboSubmission.RoutingStatus.RESOLVED,
            routing_resolved_at=timezone.now(),
            nucleo_code_original=code,
            nucleo_code_normalized=code,
        )

    def build_microproject(self, submission, identity, **changes):
        # PRE: submission and identity are coherent persisted Ficha 10 fixtures.
        # POST: returns an unsaved model populated with valid canonical fields.
        values = {
            "territorial_identity": identity,
            "project": submission.project,
            "source_submission": submission,
            "name": "Microproyecto",
            "component": "training",
            "problem_summary": "Problema confirmado",
            "specific_objective": "Objetivo confirmado",
            "beneficiary_group": ["youth", "parish_volunteers"],
            "main_activities": "Formación y acompañamiento.",
            "estimated_cost_range": "1000_5000",
            "implementation_urgency": "short_term",
            "technical_viability": "medium",
            "expected_result": "Capacidades fortalecidas.",
            "created_by": self.importer,
        }
        values.update(changes)
        return KoboPrioritizedMicroproject(**values)


class KoboPrioritizedMicroprojectTests(PrioritizedMicroprojectFixtureMixin, TestCase):
    def setUp(self):
        self.create_domain()

    def test_model_accepts_canonical_catalogs_and_text_activities(self):
        identity = self.create_identity()
        submission = self.create_submission()
        microproject = self.build_microproject(submission, identity)

        microproject.full_clean()
        microproject.save()

        self.assertEqual(microproject.main_activities, "Formación y acompañamiento.")
        self.assertEqual(
            microproject.beneficiary_group, ["youth", "parish_volunteers"]
        )

    def test_model_rejects_unknown_catalogs_and_malformed_multiselect(self):
        invalid_values = (
            {"component": "other_component"},
            {"estimated_cost_range": "exact_1234"},
            {"implementation_urgency": "whenever"},
            {"technical_viability": "guaranteed"},
            {"beneficiary_group": "youth women"},
            {"beneficiary_group": ["youth", "youth"]},
            {"beneficiary_group": ["unknown_group"]},
        )
        for index, changes in enumerate(invalid_values):
            with self.subTest(changes=changes):
                code = f"NV-MODEL-INVALID-{index}"
                identity = self.create_identity(code=code)
                submission = self.create_submission(
                    code=code, external_id=f"model-invalid-{index}"
                )
                with self.assertRaises(ValidationError):
                    self.build_microproject(submission, identity, **changes).full_clean()

    def test_model_requires_every_business_field_and_correct_source_form(self):
        required_fields = (
            "name",
            "problem_summary",
            "specific_objective",
            "main_activities",
            "expected_result",
        )
        for index, field_name in enumerate(required_fields):
            with self.subTest(field_name=field_name):
                code = f"NV-REQUIRED-{index}"
                identity = self.create_identity(code=code)
                submission = self.create_submission(
                    code=code, external_id=f"required-{index}"
                )
                with self.assertRaises(ValidationError):
                    self.build_microproject(
                        submission, identity, **{field_name: ""}
                    ).full_clean()

        identity = self.create_identity(code="NV-WRONG-SOURCE")
        with self.assertRaises(ValidationError):
            self.build_microproject(identity.source_submission, identity).full_clean()

    def test_source_is_unique_relations_are_protected_and_rows_are_immutable(self):
        identity = self.create_identity()
        submission = self.create_submission()
        microproject = self.build_microproject(submission, identity)
        microproject.full_clean()
        microproject.save()

        duplicate = self.build_microproject(submission, identity, name="Otro")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        for protected_object in (identity, self.project, submission, self.importer):
            with self.subTest(protected_object=protected_object):
                with self.assertRaises(ProtectedError):
                    protected_object.delete()
        microproject.name = "Mutación prohibida"
        with self.assertRaises(ValidationError):
            microproject.save()

    def test_identity_accepts_multiple_same_named_historical_microprojects(self):
        identity = self.create_identity()
        for index in range(2):
            submission = self.create_submission(
                external_id=f"same-name-{index}"
            )
            microproject = self.build_microproject(
                submission, identity, name="Mismo nombre"
            )
            microproject.full_clean()
            microproject.save()

        self.assertEqual(identity.prioritized_microprojects.count(), 2)

    def test_approved_ficha_10_creates_exactly_one_target_and_import_record(self):
        identity = self.create_identity(status=KoboTerritorialIdentity.Status.OBSERVED)
        original_identity = (
            identity.status,
            identity.project_id,
            identity.pastoral_zone,
            identity.updated_at,
        )
        submission = self.create_submission()
        project_count = Project.objects.count()

        result = import_kobo_submission(submission, actor=self.importer)
        submission.refresh_from_db()
        identity.refresh_from_db()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.imported_at)
        self.assertEqual(submission.processed_at, submission.imported_at)
        self.assertEqual(KoboPrioritizedMicroproject.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        microproject = submission.prioritized_microproject
        record = submission.import_record
        self.assertEqual(record.target_app_label, "kobo")
        self.assertEqual(record.target_model, "KoboPrioritizedMicroproject")
        self.assertEqual(record.target_object_id, microproject.pk)
        self.assertEqual(result.materialization_id, microproject.pk)
        self.assertEqual(
            (identity.status, identity.project_id, identity.pastoral_zone, identity.updated_at),
            original_identity,
        )
        self.assertEqual(Project.objects.count(), project_count)
        self.assertFalse(Donation.objects.exists())
        self.assertFalse(FundAllocation.objects.exists())
        self.assertFalse(Expense.objects.exists())

    def test_pending_review_identity_accepts_microproject_without_state_change(self):
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
        self.assertEqual(identity.prioritized_microprojects.count(), 1)

    def test_materialization_preserves_normalized_texts_codes_and_activity_type(self):
        self.create_identity()
        submission = self.create_submission(
            payload_changes={
                "microproject_name": "<b>Nombre confirmado</b>",
                "problem_summary": "Problema normalizado original.",
                "main_activities": "Actividad uno; actividad dos.",
                "estimated_cost_range": "over_50000",
                "beneficiary_group": ["women", "entrepreneurs", "other"],
            }
        )

        result = import_kobo_submission(submission, actor=self.importer)
        microproject = KoboPrioritizedMicroproject.objects.get()

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(microproject.name, "<b>Nombre confirmado</b>")
        self.assertEqual(microproject.problem_summary, "Problema normalizado original.")
        self.assertEqual(microproject.main_activities, "Actividad uno; actividad dos.")
        self.assertEqual(microproject.estimated_cost_range, "over_50000")
        self.assertEqual(
            microproject.beneficiary_group, ["women", "entrepreneurs", "other"]
        )

    def test_missing_required_unknown_catalog_and_non_text_activities_block(self):
        invalid_payloads = (
            {"expected_result": None},
            {"component": "unsupported"},
            {"estimated_cost_range": "1234"},
            {"implementation_urgency": "later"},
            {"technical_viability": "certain"},
            {"beneficiary_group": ["unknown"]},
            {"main_activities": ["one", "two"]},
        )
        for index, changes in enumerate(invalid_payloads):
            with self.subTest(changes=changes):
                code = f"NV-DATA-{index}"
                self.create_identity(code=code)
                submission = self.create_submission(
                    code=code,
                    payload_changes=changes,
                    external_id=f"invalid-data-{index}",
                )
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, "FICHA_10_MICROPROJECT_INVALID")
        self.assertFalse(KoboPrioritizedMicroproject.objects.exists())

    def test_identity_project_code_missing_inactive_and_open_conflict_block(self):
        cases = []
        project_mismatch = self.create_submission(
            code="NV-PROJECT-MISMATCH", external_id="project-mismatch"
        )
        self.create_identity(
            code="NV-PROJECT-MISMATCH", project=self.other_project
        )
        cases.append((project_mismatch, "FICHA_10_IDENTITY_MISMATCH"))

        code_mismatch = self.create_submission(
            code="NV-CODE-MISMATCH",
            external_id="code-mismatch",
            payload_changes={"nucleo_code": "NV-OTHER"},
        )
        self.create_identity(code="NV-CODE-MISMATCH")
        cases.append((code_mismatch, "FICHA_10_IDENTITY_MISMATCH"))

        missing = self.create_submission(code="NV-MISSING", external_id="missing")
        cases.append((missing, "FICHA_10_IDENTITY_MISSING"))

        inactive = self.create_submission(code="NV-INACTIVE", external_id="inactive")
        self.create_identity(
            code="NV-INACTIVE", status=KoboTerritorialIdentity.Status.INACTIVE
        )
        cases.append((inactive, "FICHA_10_IDENTITY_INACTIVE"))

        conflict_submission = self.create_submission(
            code="NV-CONFLICT", external_id="conflict"
        )
        conflict_identity = self.create_identity(code="NV-CONFLICT")
        KoboTerritorialIdentityConflict.objects.create(
            identity=conflict_identity,
            incoming_submission=conflict_submission,
            existing_pastoral_zone="centro",
            proposed_pastoral_zone="este",
            existing_project=self.project,
            proposed_project=self.other_project,
        )
        cases.append((conflict_submission, "FICHA_10_TERRITORIAL_CONFLICT"))

        for submission, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, reason_code)
        self.assertFalse(KoboPrioritizedMicroproject.objects.exists())

    def test_pending_conflicting_routing_and_wrong_review_state_block_common_flow(self):
        identity = self.create_identity()
        cases = (
            self.create_submission(
                external_id="routing-pending",
                routing_status=KoboSubmission.RoutingStatus.UNRESOLVED,
            ),
            self.create_submission(
                external_id="routing-conflict",
                routing_status=KoboSubmission.RoutingStatus.CONFLICT,
            ),
            self.create_submission(
                external_id="not-approved",
                status=KoboSubmission.Status.READY_FOR_REVIEW,
            ),
        )
        expected = (
            "IMPORT_ROUTING_UNRESOLVED",
            "IMPORT_ROUTING_CONFLICT",
            "IMPORT_REVIEW_NOT_APPROVED",
        )
        for submission, reason_code in zip(cases, expected, strict=True):
            with self.subTest(reason_code=reason_code):
                result = import_kobo_submission(submission, actor=self.importer)
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, reason_code)
        identity.refresh_from_db()
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)
        self.assertFalse(KoboPrioritizedMicroproject.objects.exists())

    def test_existing_microproject_without_import_record_is_explicitly_blocked(self):
        identity = self.create_identity()
        submission = self.create_submission()
        microproject = self.build_microproject(submission, identity)
        microproject.full_clean()
        microproject.save()

        result = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(
            result.reason_code, "FICHA_10_MICROPROJECT_STATE_CONFLICT"
        )
        self.assertEqual(KoboPrioritizedMicroproject.objects.count(), 1)
        self.assertFalse(KoboImportRecord.objects.exists())

    def test_imported_corruption_without_record_is_not_treated_as_legacy_success(self):
        identity = self.create_identity()
        submission = self.create_submission(status=KoboSubmission.Status.IMPORTED)
        submission.imported_at = timezone.now()
        submission.processed_at = submission.imported_at
        submission.save(update_fields=("imported_at", "processed_at"))
        microproject = self.build_microproject(submission, identity)
        microproject.full_clean()
        microproject.save()

        result = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(
            result.reason_code, "FICHA_10_MICROPROJECT_STATE_CONFLICT"
        )
        self.assertFalse(KoboImportRecord.objects.exists())

    def test_retry_does_not_duplicate_targets_records_events_or_audits(self):
        identity = self.create_identity()
        submission = self.create_submission()

        first = import_kobo_submission(submission, actor=self.importer)
        second = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(first.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, ImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(KoboPrioritizedMicroproject.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(submission.processing_events.filter(code="imported").count(), 1)
        self.assertEqual(
            submission.processing_events.filter(
                code="prioritized_microproject_created"
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(submission.prioritized_microproject.pk),
                model_name=KoboPrioritizedMicroproject._meta.verbose_name.capitalize(),
            ).count(),
            1,
        )
        identity.refresh_from_db()
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.ACTIVE)

    def test_safe_event_metadata_excludes_payload_and_sensitive_text(self):
        self.create_identity()
        submission = self.create_submission(
            payload_changes={
                "problem_summary": "Sensitive long problem text",
                "beneficiary_group": ["women"],
            }
        )

        result = import_kobo_submission(submission, actor=self.importer)
        event = submission.processing_events.get(
            code="prioritized_microproject_created"
        )

        self.assertEqual(result.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(
            set(event.metadata),
            {
                "microproject_id",
                "identity_id",
                "project_id",
                "nucleo_code_normalized",
                "component",
            },
        )
        serialized = str(event.metadata)
        for forbidden in (
            "raw_payload",
            "Sensitive long problem text",
            "women",
            "raw only",
            "Comunidad La Esperanza",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_import_is_atomic_when_target_record_event_or_audit_fails(self):
        failure_targets = (
            "apps.integrations.kobo.models.KoboPrioritizedMicroproject.save",
            "apps.integrations.kobo.models.KoboImportRecord.objects.create",
            "apps.integrations.kobo.models.KoboProcessingEvent.objects.create",
            "apps.operations.services.log_action",
        )
        for index, target in enumerate(failure_targets):
            with self.subTest(target=target):
                code = f"NV-ROLLBACK-MICRO-{index}"
                identity = self.create_identity(code=code)
                submission = self.create_submission(
                    code=code, external_id=f"rollback-micro-{index}"
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
                    KoboPrioritizedMicroproject.objects.filter(
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

    def test_admin_is_readonly_searchable_and_has_no_mutating_actions(self):
        identity = self.create_identity()
        submission = self.create_submission()
        result = import_kobo_submission(submission, actor=self.importer)
        microproject = KoboPrioritizedMicroproject.objects.get(
            pk=result.materialization_id
        )
        model_admin = KoboPrioritizedMicroprojectAdmin(
            KoboPrioritizedMicroproject, admin.site
        )
        request = RequestFactory().post("/admin/kobo/prioritized-microproject/")
        request.user = self.importer

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, microproject))
        self.assertFalse(model_admin.has_delete_permission(request, microproject))
        self.assertEqual(model_admin.actions, ())
        self.assertIn("source_submission__primary_community", model_admin.search_fields)
        self.assertIn("created_by", model_admin.readonly_fields)
        self.assertEqual(identity.prioritized_microprojects.get(), microproject)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
class KoboPrioritizedMicroprojectConcurrencyTests(
    PrioritizedMicroprojectFixtureMixin, TransactionTestCase
):
    reset_sequences = True

    def setUp(self):
        self.create_domain()
        self.identity = self.create_identity()
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
            close_old_connections()

    def test_two_workers_create_one_microproject_and_one_import_record(self):
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
        self.assertEqual(KoboPrioritizedMicroproject.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(
            KoboProcessingEvent.objects.filter(
                submission=self.submission, code="prioritized_microproject_created"
            ).count(),
            1,
        )
        self.assertEqual(
            KoboProcessingEvent.objects.filter(
                submission=self.submission, code="imported"
            ).count(),
            1,
        )
