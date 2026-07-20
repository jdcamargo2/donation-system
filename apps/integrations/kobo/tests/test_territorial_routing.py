from apps.integrations.kobo.contracts import (
    TerritorialRoutingReasonCode,
    TerritorialRoutingStatus,
)
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboFormDefinition,
    KoboPastoralZoneProjectMapping,
    KoboProcessingEvent,
    KoboProjectBinding,
    KoboSubmission,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
)
from apps.integrations.kobo.services.territorial_routing import (
    route_dependent_territorial_submission,
    route_ficha_1_submission,
    route_normalized_submission,
)
from apps.operations.models import Project
from django.db import connection, connections
from django.test import TestCase
from django.test import TransactionTestCase
from django.utils import timezone
from queue import Queue
from types import SimpleNamespace
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch


class TerritorialRoutingServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ficha_01 = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID, title="Ficha 1", version=FICHA_01_VERSION
        )
        cls.ficha_10 = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID, title="Ficha 10", version=FICHA_10_VERSION
        )
        cls.ficha_11 = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID, title="Ficha 11", version=FICHA_11_VERSION
        )
        cls.project = Project.objects.create(code="PRJ-TR-01", name="Centro")
        cls.other_project = Project.objects.create(code="PRJ-TR-02", name="Este")

    def create_submission(self, external_id="ficha-01", **overrides):
        # PRE: overrides keep a normalized Ficha 1 staging submission coherent.
        # POST: returns a persisted submission ready for territorial routing.
        values = {
            "form_definition": self.ficha_01,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
            "normalized_payload": {"nucleo_code": "NV-001"},
            "status": KoboSubmission.Status.READY_FOR_REVIEW,
            "pastoral_zone": "centro",
            "nucleo_code_original": " NV-001 ",
            "nucleo_code_normalized": "NV-001",
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def create_pending_submission(self, form_definition, external_id, code="NV-001"):
        # PRE: form_definition is Ficha 10 or Ficha 11 and code is canonical.
        # POST: returns an unresolved dependent submission without import state.
        return KoboSubmission.objects.create(
            form_definition=form_definition,
            external_id=external_id,
            raw_payload={"_uuid": external_id},
            normalized_payload={"nucleo_code": code},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
        )

    def create_dependent_submission(self, form_definition, external_id, code="NV-001", **overrides):
        # PRE: form_definition is Ficha 10 or Ficha 11 and overrides are model fields.
        # POST: returns normalized review staging ready for dependent territorial routing.
        values = {
            "form_definition": form_definition,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
            "normalized_payload": {
                "nucleo_code_original": code,
                "nucleo_code_normalized": code,
            },
            "status": KoboSubmission.Status.READY_FOR_REVIEW,
            "nucleo_code_original": code,
            "nucleo_code_normalized": code,
        }
        values.update(overrides)
        return KoboSubmission.objects.create(**values)

    def create_identity(self, code="NV-001", *, project=None, status=None):
        # PRE: code is canonical and project is a valid territorial destination.
        # POST: returns an explicit identity sourced by synthetic normalized Ficha 1 staging.
        project = project or self.project
        source = self.create_submission(
            f"identity-source-{code}-{KoboSubmission.objects.count()}",
            normalized_payload={"nucleo_code": code},
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            project=project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            routing_resolved_at=timezone.now(),
        )
        return KoboTerritorialIdentity.objects.create(
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            pastoral_zone="centro",
            project=project,
            source_submission=source,
            status=status or KoboTerritorialIdentity.Status.PENDING_REVIEW,
        )

    def test_new_ficha_1_creates_pending_identity_and_resolves_explicit_mapping(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        submission = self.create_submission()

        result = route_ficha_1_submission(submission)

        identity = KoboTerritorialIdentity.objects.get(nucleo_code_normalized="NV-001")
        submission.refresh_from_db()
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.PENDING_REVIEW)
        self.assertEqual(identity.nucleo_code_original, " NV-001 ")
        self.assertEqual(identity.project, self.project)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertIsNotNone(submission.routing_resolved_at)
        self.assertEqual(result.project_id, self.project.pk)
        self.assertNotEqual(submission.status, KoboSubmission.Status.IMPORTED)

    def test_repeated_ficha_1_is_idempotent(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        submission = self.create_submission()
        route_ficha_1_submission(submission)
        event_count = KoboProcessingEvent.objects.filter(submission=submission).count()

        route_ficha_1_submission(submission)

        self.assertEqual(KoboTerritorialIdentity.objects.count(), 1)
        self.assertEqual(
            KoboProcessingEvent.objects.filter(submission=submission).count(), event_count
        )
        submission.refresh_from_db()
        self.assertEqual(submission.project, self.project)

    def test_missing_mapping_records_routing_error_without_identity(self):
        submission = self.create_submission()

        result = route_ficha_1_submission(submission)

        submission.refresh_from_db()
        self.assertFalse(KoboTerritorialIdentity.objects.exists())
        self.assertIsNone(submission.project)
        self.assertEqual(submission.routing_status, KoboSubmission.RoutingStatus.ERROR)
        self.assertEqual(
            submission.routing_reason_code,
            TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING,
        )
        self.assertEqual(result.reason_code, TerritorialRoutingReasonCode.MISSING_ZONE_PROJECT_MAPPING)

    def test_conflict_preserves_identity_and_is_idempotent(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="este", project=self.other_project
        )
        original = self.create_submission()
        route_ficha_1_submission(original)
        incoming = self.create_submission(
            "different-zone", pastoral_zone="este", nucleo_code_original="NV-001"
        )

        route_ficha_1_submission(incoming)
        route_ficha_1_submission(incoming)

        identity = KoboTerritorialIdentity.objects.get(nucleo_code_normalized="NV-001")
        incoming.refresh_from_db()
        self.assertEqual(identity.pastoral_zone, "centro")
        self.assertEqual(identity.project, self.project)
        self.assertEqual(KoboTerritorialIdentityConflict.objects.count(), 1)
        self.assertEqual(incoming.routing_status, KoboSubmission.RoutingStatus.CONFLICT)
        self.assertIsNone(incoming.project)

    def test_project_mapping_change_with_same_zone_creates_auditable_conflict(self):
        mapping = KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        route_ficha_1_submission(self.create_submission())
        mapping.project = self.other_project
        mapping.save(update_fields=("project",))

        route_ficha_1_submission(self.create_submission("changed-project"))

        conflict = KoboTerritorialIdentityConflict.objects.get()
        self.assertEqual(conflict.existing_project, self.project)
        self.assertEqual(conflict.proposed_project, self.other_project)
        self.assertEqual(conflict.existing_pastoral_zone, conflict.proposed_pastoral_zone)

    def test_reconciles_only_pending_ficha_10_and_11_with_same_code(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        ficha_10 = self.create_pending_submission(self.ficha_10, "ficha-10")
        ficha_11 = self.create_pending_submission(self.ficha_11, "ficha-11")
        different = self.create_pending_submission(self.ficha_10, "different", "NV-999")
        already_resolved = self.create_submission(
            "already-resolved",
            form_definition=self.ficha_10,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.other_project,
        )

        route_ficha_1_submission(self.create_submission())
        route_ficha_1_submission(self.create_submission("repeat"))

        ficha_10.refresh_from_db()
        ficha_11.refresh_from_db()
        different.refresh_from_db()
        already_resolved.refresh_from_db()
        self.assertEqual(ficha_10.project, self.project)
        self.assertEqual(ficha_11.project, self.project)
        self.assertEqual(ficha_10.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(ficha_11.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(different.routing_status, KoboSubmission.RoutingStatus.PENDING_IDENTITY)
        self.assertEqual(already_resolved.project, self.other_project)
        self.assertNotEqual(ficha_10.status, KoboSubmission.Status.IMPORTED)
        self.assertNotEqual(ficha_11.status, KoboSubmission.Status.IMPORTED)

    def test_dependent_fichas_resolve_from_identity_for_every_identity_status(self):
        for index, identity_status in enumerate(KoboTerritorialIdentity.Status.values):
            with self.subTest(identity_status=identity_status):
                code = f"NV-STATUS-{index}"
                identity = self.create_identity(code, status=identity_status)
                form_definition = self.ficha_10 if index % 2 == 0 else self.ficha_11
                submission = self.create_dependent_submission(
                    form_definition, f"dependent-{index}", code
                )

                result = route_dependent_territorial_submission(submission)

                submission.refresh_from_db()
                resolved_at = submission.routing_resolved_at
                event_count = submission.processing_events.count()
                repeated = route_dependent_territorial_submission(submission)
                submission.refresh_from_db()
                identity.refresh_from_db()
                self.assertEqual(submission.project, self.project)
                self.assertEqual(
                    submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED
                )
                self.assertEqual(result.project_id, self.project.pk)
                self.assertEqual(repeated.status, TerritorialRoutingStatus.RESOLVED)
                self.assertEqual(submission.routing_resolved_at, resolved_at)
                self.assertEqual(submission.processing_events.count(), event_count)
                self.assertEqual(identity.status, identity_status)
                self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
                self.assertIsNone(submission.processed_at)
                self.assertNotEqual(submission.status, KoboSubmission.Status.IMPORTED)

    def test_dependent_fichas_remain_pending_without_identity_idempotently(self):
        for index, form_definition in enumerate((self.ficha_10, self.ficha_11)):
            with self.subTest(form_definition=form_definition.form_id):
                submission = self.create_dependent_submission(
                    form_definition, f"pending-dependent-{index}", f"NV-PENDING-{index}"
                )

                first = route_normalized_submission(submission)
                event_count = submission.processing_events.count()
                second = route_normalized_submission(submission)

                submission.refresh_from_db()
                self.assertEqual(first.status, TerritorialRoutingStatus.PENDING_IDENTITY)
                self.assertEqual(second.status, TerritorialRoutingStatus.PENDING_IDENTITY)
                self.assertEqual(submission.processing_events.count(), event_count)
                self.assertEqual(event_count, 1)
                self.assertIsNone(submission.project)
                self.assertEqual(
                    submission.routing_reason_code,
                    TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY,
                )
                self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)

    def test_pending_dependent_resolves_when_identity_appears(self):
        submission = self.create_dependent_submission(
            self.ficha_10, "pending-then-resolved", "NV-LATER"
        )
        route_dependent_territorial_submission(submission)
        self.create_identity("NV-LATER")

        result = route_dependent_territorial_submission(submission)

        submission.refresh_from_db()
        self.assertEqual(result.status, TerritorialRoutingStatus.RESOLVED)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.processing_events.filter(
                code="territorial_dependent_routing_resolved"
            ).count(),
            1,
        )

    def test_dependent_routing_never_uses_direct_binding_as_fallback(self):
        asset = KoboAsset.objects.create(
            asset_uid="dependent-binding",
            name="Dependent binding",
            form_definition=self.ficha_11,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
        )
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.other_project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )
        submission = self.create_dependent_submission(
            self.ficha_11, "binding-must-not-win", "NV-UNKNOWN", asset=asset
        )

        route_normalized_submission(submission)

        submission.refresh_from_db()
        self.assertIsNone(submission.project)
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.PENDING_IDENTITY
        )
        self.assertFalse(
            submission.processing_events.filter(code="project_assigned").exists()
        )

    def test_dependent_routing_records_missing_and_invalid_codes_safely(self):
        cases = (
            ("missing", "", "", TerritorialRoutingReasonCode.MISSING_NUCLEO_CODE),
            ("invalid", "NV-RAW", "", TerritorialRoutingReasonCode.INVALID_NUCLEO_CODE),
        )
        for label, original, normalized, reason_code in cases:
            with self.subTest(label=label):
                submission = self.create_dependent_submission(
                    self.ficha_10,
                    f"dependent-{label}",
                    original,
                    nucleo_code_normalized=normalized,
                )

                result = route_dependent_territorial_submission(submission)

                submission.refresh_from_db()
                self.assertEqual(result.status, TerritorialRoutingStatus.ERROR)
                self.assertEqual(submission.routing_reason_code, reason_code)
                self.assertIsNone(submission.project)
                self.assertIsNone(submission.routing_resolved_at)

    def test_dependent_routing_rejects_unsupported_form_without_binding(self):
        submission = self.create_submission("unsupported-dependent")

        result = route_dependent_territorial_submission(submission)

        submission.refresh_from_db()
        self.assertEqual(result.status, TerritorialRoutingStatus.ERROR)
        self.assertEqual(
            submission.routing_reason_code,
            TerritorialRoutingReasonCode.UNSUPPORTED_FORM,
        )

    def test_invalid_identity_is_not_repaired_or_used(self):
        submission = self.create_dependent_submission(
            self.ficha_11, "invalid-identity", "NV-INVALID-IDENTITY"
        )
        invalid_identity = SimpleNamespace(
            project_id=None,
            project=None,
            pastoral_zone="",
        )
        with patch.object(
            KoboTerritorialIdentity.objects, "select_for_update"
        ) as select_for_update:
            select_for_update.return_value.select_related.return_value.get.return_value = (
                invalid_identity
            )
            result = route_dependent_territorial_submission(submission)

        submission.refresh_from_db()
        self.assertEqual(result.status, TerritorialRoutingStatus.ERROR)
        self.assertEqual(
            submission.routing_reason_code,
            TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_INVALID,
        )
        self.assertIsNone(submission.project)

    def test_changed_code_never_moves_a_resolved_submission_between_projects(self):
        self.create_identity("NV-FIRST", project=self.project)
        self.create_identity("NV-SECOND", project=self.other_project)
        submission = self.create_dependent_submission(
            self.ficha_10, "changed-code", "NV-FIRST"
        )
        route_dependent_territorial_submission(submission)
        submission.nucleo_code_original = "NV-SECOND"
        submission.nucleo_code_normalized = "NV-SECOND"
        submission.normalized_payload["nucleo_code_normalized"] = "NV-SECOND"
        submission.save(
            update_fields=(
                "nucleo_code_original",
                "nucleo_code_normalized",
                "normalized_payload",
            )
        )

        result = route_dependent_territorial_submission(submission)
        event_count = submission.processing_events.count()
        repeated = route_dependent_territorial_submission(submission)

        submission.refresh_from_db()
        self.assertEqual(result.status, TerritorialRoutingStatus.CONFLICT)
        self.assertEqual(repeated.status, TerritorialRoutingStatus.CONFLICT)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.routing_reason_code,
            TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT,
        )
        self.assertIsNone(submission.routing_resolved_at)
        self.assertEqual(submission.processing_events.count(), event_count)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-level locking")
class TerritorialRoutingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.form = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID, title="Ficha 1", version=FICHA_01_VERSION
        )
        self.dependent_form = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID, title="Ficha 10", version=FICHA_10_VERSION
        )
        self.centro = Project.objects.create(code="PRJ-CON-01", name="Centro")
        self.este = Project.objects.create(code="PRJ-CON-02", name="Este")
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.centro
        )
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="este", project=self.este
        )

    def create_submission(self, external_id, pastoral_zone):
        # PRE: pastoral_zone has an explicit active test mapping.
        # POST: returns one independently routeable normalized Ficha 1 submission.
        return KoboSubmission.objects.create(
            form_definition=self.form,
            external_id=external_id,
            raw_payload={"_uuid": external_id},
            normalized_payload={"nucleo_code": "NV-CONCURRENT"},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            pastoral_zone=pastoral_zone,
            nucleo_code_original=" NV-CONCURRENT ",
            nucleo_code_normalized="NV-CONCURRENT",
        )

    def route_concurrently(self, *submissions):
        # PRE: submissions share one normalized territorial code and are persisted.
        # POST: routes each submission in a separate PostgreSQL connection and returns errors.
        barrier = Barrier(len(submissions))
        outcomes = Queue()

        def worker(submission_id):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                route_ficha_1_submission(KoboSubmission.objects.get(pk=submission_id))
                outcomes.put(None)
            except BaseException as exc:
                outcomes.put(exc)
            finally:
                connections.close_all()

        threads = [Thread(target=worker, args=(submission.pk,)) for submission in submissions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        errors = [outcomes.get_nowait() for _ in threads]
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [None, None])

    def test_same_zone_creates_one_identity_without_conflicts(self):
        first = self.create_submission("same-zone-1", "centro")
        second = self.create_submission("same-zone-2", "centro")

        self.route_concurrently(first, second)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(KoboTerritorialIdentity.objects.count(), 1)
        self.assertFalse(KoboTerritorialIdentityConflict.objects.exists())
        self.assertEqual(first.project, self.centro)
        self.assertEqual(second.project, self.centro)

    def test_different_zones_preserve_one_identity_and_create_conflict(self):
        first = self.create_submission("different-zone-1", "centro")
        second = self.create_submission("different-zone-2", "este")

        self.route_concurrently(first, second)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(KoboTerritorialIdentity.objects.count(), 1)
        self.assertTrue(KoboTerritorialIdentityConflict.objects.filter(status="open").exists())
        self.assertEqual(
            {first.routing_status, second.routing_status},
            {KoboSubmission.RoutingStatus.RESOLVED, KoboSubmission.RoutingStatus.CONFLICT},
        )

    def test_two_workers_resolve_one_pending_dependent_submission_once(self):
        source = self.create_submission("dependent-source", "centro")
        route_ficha_1_submission(source)
        dependent = KoboSubmission.objects.create(
            form_definition=self.dependent_form,
            external_id="dependent-concurrent",
            raw_payload={"_uuid": "dependent-concurrent"},
            normalized_payload={"nucleo_code_normalized": "NV-CONCURRENT"},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            nucleo_code_original="NV-CONCURRENT",
            nucleo_code_normalized="NV-CONCURRENT",
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            routing_reason_code=TerritorialRoutingReasonCode.UNKNOWN_TERRITORIAL_IDENTITY,
        )
        barrier = Barrier(2)
        outcomes = Queue()

        def worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                route_dependent_territorial_submission(
                    KoboSubmission.objects.get(pk=dependent.pk)
                )
                outcomes.put(None)
            except BaseException as exc:
                outcomes.put(exc)
            finally:
                connections.close_all()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([outcomes.get_nowait() for _ in threads], [None, None])
        dependent.refresh_from_db()
        self.assertEqual(dependent.project, self.centro)
        self.assertEqual(
            dependent.processing_events.filter(
                code="territorial_dependent_routing_resolved"
            ).count(),
            1,
        )
