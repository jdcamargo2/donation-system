import ast
from dataclasses import dataclass
import inspect
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.kobo.form_registry import KoboFormType
from apps.integrations.kobo.import_contracts import (
    ImportOutcome,
    ImportWarning,
    KoboMaterializationResult,
)
from apps.integrations.kobo.import_handlers import (
    KOBO_IMPORT_HANDLERS,
    get_import_handler,
)
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboImportRecord,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialProfile,
)
from apps.integrations.kobo.services import import_kobo_submission
from apps.integrations.kobo.services import associate_submission_with_project
from apps.integrations.kobo.services.importers import (
    _import_kobo_submission_with_handlers,
)
from apps.operations.models import AuditLog, Project


FORM_CASES = (
    (
        KoboFormType.FICHA_1,
        FICHA_01_FORM_ID,
        FICHA_01_VERSION,
        KoboAsset.FormRole.TERRITORIAL_PROFILE,
    ),
    (
        KoboFormType.FICHA_10,
        FICHA_10_FORM_ID,
        FICHA_10_VERSION,
        KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
    ),
    (
        KoboFormType.FICHA_11,
        FICHA_11_FORM_ID,
        FICHA_11_VERSION,
        KoboAsset.FormRole.PRIORITIZATION_MATRIX,
    ),
)


@dataclass
class SuccessfulProjectHandler:
    form_type: KoboFormType
    calls: int = 0

    def validate_for_import(self, *, submission):
        # PRE: the common service validated the locked submission.
        # POST: returns one controlled warning for traceability tests.
        return (ImportWarning(code="REVIEWED_WARNING", message="Reviewed warning."),)

    def materialize(self, *, submission, actor):
        # PRE: the caller owns the transaction and submission has a project.
        # POST: returns the existing project as a deterministic test target.
        self.calls += 1
        return KoboMaterializationResult(
            materialization_type="test_project_reference",
            target_app_label="operations",
            target_model="project",
            target_object_id=submission.project_id,
            created=False,
        )


@dataclass
class FailingProjectHandler:
    form_type: KoboFormType
    calls: int = 0

    def validate_for_import(self, *, submission):
        # PRE: the common service validated the locked submission.
        # POST: returns no warnings before the forced technical failure.
        return ()

    def materialize(self, *, submission, actor):
        # PRE: materialization runs in the common transaction.
        # POST: creates a rollback probe and raises a controlled test exception.
        self.calls += 1
        submission.processing_events.create(
            stage="materialization_probe",
            level="info",
            code="must_rollback",
            message="Must roll back.",
        )
        raise RuntimeError("forced materialization failure")


class KoboImportContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.importer = user_model.objects.create_user(
            username="contract-importer",
            password="test-password",
        )
        cls.importer.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="operations",
                codename__in=("view_project", "change_project"),
            )
        )
        cls.project = Project.objects.create(
            code="PRJ-KOBO-CONTRACT",
            name="Kobo contract project",
            status=Project.Status.ACTIVE,
        )
        cls.form_assets = {}
        for form_type, form_id, version, form_role in FORM_CASES:
            definition = KoboFormDefinition.objects.create(
                form_id=form_id,
                title=form_type.value,
                version=version,
            )
            asset = KoboAsset.objects.create(
                asset_uid=f"contract-{form_type.value}",
                name=form_type.value,
                form_definition=definition,
                form_role=form_role,
            )
            cls.form_assets[form_type] = (definition, asset)

    def create_submission(
        self,
        form_type=KoboFormType.FICHA_1,
        *,
        status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        project=True,
        normalized_payload=None,
    ):
        # PRE: form fixtures exist and requested routing/project values are coherent.
        # POST: returns one persisted import candidate without an import record.
        definition, asset = self.form_assets[form_type]
        payload = normalized_payload or {"nucleo_code": "NV-CONTRACT"}
        return KoboSubmission.objects.create(
            form_definition=definition,
            asset=asset,
            project=self.project if project else None,
            external_id=f"contract-{form_type.value}-{KoboSubmission.objects.count()}",
            raw_payload={"_uuid": f"raw-{KoboSubmission.objects.count()}"},
            normalized_payload=payload,
            status=status,
            routing_status=routing_status,
            normalized_at=timezone.now(),
            processed_at=timezone.now(),
        )

    def test_dispatcher_is_closed_and_selects_each_supported_handler(self):
        self.assertEqual(set(KOBO_IMPORT_HANDLERS), {case[0] for case in FORM_CASES})
        for form_type, *_ in FORM_CASES:
            with self.subTest(form_type=form_type):
                self.assertEqual(get_import_handler(form_type).form_type, form_type)

    def test_public_import_routes_delegate_without_direct_imported_assignment(self):
        association_source = inspect.getsource(associate_submission_with_project)
        self.assertIn("import_kobo_submission", association_source)
        self.assertNotIn("Status.IMPORTED", association_source)

        views_path = Path(__file__).parents[1] / "views.py"
        views_source = views_path.read_text()
        views_tree = ast.parse(views_source)
        expected_delegates = {
            "associate_project_action": "associate_submission_with_project",
            "project_pending_submission_import": "import_kobo_submission",
        }
        view_functions = {
            node.name: ast.get_source_segment(views_source, node)
            for node in views_tree.body
            if isinstance(node, ast.FunctionDef) and node.name in expected_delegates
        }
        self.assertEqual(set(view_functions), set(expected_delegates))
        for function_name, expected_delegate in expected_delegates.items():
            with self.subTest(callable=function_name):
                source = view_functions[function_name]
                self.assertIn(expected_delegate, source)
                self.assertNotIn("Status.IMPORTED", source)

    def test_ficha_11_handler_rejects_incomplete_normalized_assessment(self):
        submission = self.create_submission(
            KoboFormType.FICHA_11,
            normalized_payload={"nucleo_code": "NV-11"},
        )

        result = import_kobo_submission(submission, actor=self.importer)
        submission.refresh_from_db()

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(result.reason_code, "FICHA_11_IDENTITY_MISMATCH")
        self.assertEqual(submission.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertIsNone(submission.imported_at)
        self.assertFalse(KoboImportRecord.objects.filter(submission=submission).exists())

    def test_common_preconditions_block_invalid_state_routing_project_and_rejection(self):
        cases = (
            (
                "review",
                self.create_submission(status=KoboSubmission.Status.READY_FOR_REVIEW),
                "IMPORT_REVIEW_NOT_APPROVED",
            ),
            (
                "pending",
                self.create_submission(
                    routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                    project=False,
                ),
                "IMPORT_ROUTING_PENDING",
            ),
            (
                "conflict",
                self.create_submission(
                    routing_status=KoboSubmission.RoutingStatus.CONFLICT,
                ),
                "IMPORT_ROUTING_CONFLICT",
            ),
            (
                "error",
                self.create_submission(routing_status=KoboSubmission.RoutingStatus.ERROR),
                "IMPORT_ROUTING_ERROR",
            ),
            (
                "project",
                self.create_submission(
                    routing_status=KoboSubmission.RoutingStatus.UNRESOLVED,
                    project=False,
                ),
                "IMPORT_ROUTING_UNRESOLVED",
            ),
            (
                "rejected",
                self.create_submission(status=KoboSubmission.Status.REJECTED),
                "IMPORT_REVIEW_NOT_APPROVED",
            ),
        )
        for label, submission, reason_code in cases:
            with self.subTest(label=label):
                result = import_kobo_submission(submission, actor=self.importer)
                submission.refresh_from_db()
                self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
                self.assertEqual(result.reason_code, reason_code)
                self.assertNotEqual(submission.status, KoboSubmission.Status.IMPORTED)
                self.assertIsNone(submission.imported_at)
                self.assertFalse(hasattr(submission, "import_record"))

    def test_unknown_form_is_explicitly_unsupported(self):
        definition = KoboFormDefinition.objects.create(
            form_id="unknown-form",
            title="Unknown",
            version="1",
        )
        asset = KoboAsset.objects.create(
            asset_uid="unknown-form-asset",
            name="Unknown",
            form_definition=definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        submission = KoboSubmission.objects.create(
            form_definition=definition,
            asset=asset,
            project=self.project,
            external_id="unknown-form",
            raw_payload={"_uuid": "unknown-form"},
            normalized_payload={"value": "normalized"},
            normalized_at=timezone.now(),
            processed_at=timezone.now(),
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )

        result = import_kobo_submission(submission, actor=self.importer)

        self.assertEqual(result.outcome, ImportOutcome.BLOCKED)
        self.assertEqual(result.reason_code, "UNSUPPORTED_FORM")

    def test_failure_rolls_back_materialization_and_retry_can_succeed(self):
        submission = self.create_submission()
        failing_handler = FailingProjectHandler(KoboFormType.FICHA_1)

        failed = _import_kobo_submission_with_handlers(
            submission,
            actor=self.importer,
            handler_registry={KoboFormType.FICHA_1: failing_handler},
        )
        submission.refresh_from_db()

        self.assertEqual(failed.outcome, ImportOutcome.FAILED)
        self.assertEqual(submission.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertIsNone(submission.imported_at)
        self.assertFalse(KoboImportRecord.objects.filter(submission=submission).exists())
        self.assertFalse(
            submission.processing_events.filter(code="must_rollback").exists()
        )

        successful_handler = SuccessfulProjectHandler(KoboFormType.FICHA_1)
        retried = _import_kobo_submission_with_handlers(
            submission,
            actor=self.importer,
            handler_registry={KoboFormType.FICHA_1: successful_handler},
        )
        submission.refresh_from_db()

        self.assertEqual(retried.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.imported_at)
        self.assertEqual(submission.import_record.target_object_id, self.project.pk)

    def test_sequential_retry_returns_original_record_without_duplicates(self):
        submission = self.create_submission()
        handler = SuccessfulProjectHandler(KoboFormType.FICHA_1)
        registry = {KoboFormType.FICHA_1: handler}

        first = _import_kobo_submission_with_handlers(
            submission,
            actor=self.importer,
            handler_registry=registry,
        )
        second = _import_kobo_submission_with_handlers(
            submission,
            actor=self.importer,
            handler_registry=registry,
        )

        self.assertEqual(first.outcome, ImportOutcome.IMPORTED)
        self.assertEqual(second.outcome, ImportOutcome.ALREADY_IMPORTED)
        self.assertEqual(second.materialization_id, first.materialization_id)
        self.assertEqual(handler.calls, 1)
        self.assertEqual(KoboImportRecord.objects.filter(submission=submission).count(), 1)
        self.assertEqual(
            submission.processing_events.filter(
                stage="operational_import", code="imported"
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(submission.pk),
                summary="Ficha Kobo materializada e importada.",
            ).count(),
            1,
        )

    @override_settings(KOBO_ENABLED=True)
    def test_project_ui_explicitly_approves_then_calls_ficha_11_handler(self):
        submission = self.create_submission(
            KoboFormType.FICHA_11,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        self.client.force_login(self.importer)

        response = self.client.post(
            reverse(
                "kobo:project_pending_submission_import",
                args=(self.project.pk, submission.pk),
            )
        )
        submission.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(submission.status, KoboSubmission.Status.APPROVED_FOR_IMPORT)
        self.assertIsNone(submission.imported_at)
        self.assertTrue(
            submission.processing_events.filter(
                stage="review",
                code=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            ).exists()
        )
        self.assertTrue(
            submission.processing_events.filter(
                stage="operational_import",
                code="FICHA_11_IDENTITY_MISMATCH",
            ).exists()
        )


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
class KoboImportConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.importer = user_model.objects.create_user(username="concurrent-importer")
        self.importer.user_permissions.add(
            Permission.objects.get(codename="change_project")
        )
        self.project = Project.objects.create(
            code="PRJ-KOBO-CONCURRENT-IMPORT",
            name="Concurrent Kobo import",
            status=Project.Status.ACTIVE,
        )
        definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Concurrent Ficha 1",
            version=FICHA_01_VERSION,
        )
        asset = KoboAsset.objects.create(
            asset_uid="concurrent-import-asset",
            name="Concurrent import",
            form_definition=definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        self.submission = KoboSubmission.objects.create(
            form_definition=definition,
            asset=asset,
            project=self.project,
            external_id="concurrent-import",
            raw_payload={"_uuid": "concurrent-import"},
            normalized_payload={
                "nucleo_code": "NV-CONCURRENT",
                "nucleo_code_normalized": "NV-CONCURRENT",
                "pastoral_zone_normalized": "catia_la_mar",
                "location": None,
                "parish_delegate": None,
                "contact_phone": None,
                "main_informant_role": None,
                "communities_covered": "Comunidad concurrente",
                "estimated_households": 20,
                "access_difficulties": "no",
                "access_difficulties_notes": None,
                "initial_priority_perception": "medium",
                "general_notes": None,
            },
            normalized_at=timezone.now(),
            processed_at=timezone.now(),
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            pastoral_zone="catia_la_mar",
            parish="Parroquia concurrente",
            primary_community="Sector concurrente",
            nucleo_code_original="NV-CONCURRENT",
            nucleo_code_normalized="NV-CONCURRENT",
        )
        self.identity = KoboTerritorialIdentity.objects.create(
            nucleo_code_original="NV-CONCURRENT",
            nucleo_code_normalized="NV-CONCURRENT",
            pastoral_zone="catia_la_mar",
            project=self.project,
            source_submission=self.submission,
        )

    def test_two_workers_materialize_once(self):
        barrier = Barrier(2)
        results = Queue()

        def worker():
            # PRE: both workers use independent PostgreSQL connections.
            # POST: stores one import outcome and closes its thread connection.
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.importer.pk)
                candidate = KoboSubmission.objects.get(pk=self.submission.pk)
                barrier.wait(timeout=10)
                results.put(import_kobo_submission(candidate, actor=actor))
            finally:
                connections.close_all()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        outcomes = [results.get_nowait().outcome for _ in threads]
        self.submission.refresh_from_db()
        self.assertCountEqual(
            outcomes,
            [ImportOutcome.IMPORTED, ImportOutcome.ALREADY_IMPORTED],
        )
        self.assertEqual(KoboTerritorialProfile.objects.count(), 1)
        self.assertEqual(KoboImportRecord.objects.count(), 1)
        self.assertEqual(self.submission.status, KoboSubmission.Status.IMPORTED)
        self.identity.refresh_from_db()
        self.assertEqual(
            self.identity.status,
            KoboTerritorialIdentity.Status.ACTIVE,
        )
        self.assertEqual(
            self.submission.processing_events.filter(code="imported").count(),
            1,
        )
        self.assertEqual(
            self.submission.processing_events.filter(
                code="territorial_profile_created"
            ).count(),
            1,
        )
