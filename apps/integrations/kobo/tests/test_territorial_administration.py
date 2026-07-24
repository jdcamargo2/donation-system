from queue import Queue
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.integrations.kobo.contracts import (
    TerritorialAdministrationReasonCode as ReasonCode,
    TerritorialAdministrationStatus as ResultStatus,
    TerritorialConflictDecision,
    TerritorialRoutingReasonCode,
)
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import (
    KoboFormDefinition,
    KoboImportRecord,
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboPastoralZoneProjectMapping,
    KoboProcessingEvent,
    KoboSubmission,
    KoboTerritorialAdministrationEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
)
from apps.integrations.kobo.services.territorial_administration import (
    activate_observed_territorial_identity,
    configure_pastoral_zone_project_mapping,
    deactivate_pastoral_zone_project_mapping,
    deactivate_territorial_identity,
    observe_territorial_identity,
    reconcile_territorial_identity_submissions,
    resolve_territorial_identity_conflict,
)
from apps.integrations.kobo.services.automation import IncidentKind, classify_incident
from apps.integrations.kobo.services.territorial_routing import route_ficha_1_submission
from apps.operations.models import AuditLog, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)


class TerritorialAdministrationFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_superuser(
            username="territorial-admin", password="unused", email="admin@example.test"
        )
        cls.project = Project.objects.create(
            code="PRJ-TA-01", name="Centro", status=Project.Status.ACTIVE
        )
        cls.other_project = Project.objects.create(
            code="PRJ-TA-02", name="Este", status=Project.Status.ACTIVE
        )
        cls.ficha_1 = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID, title="Ficha 1", version=FICHA_01_VERSION
        )
        cls.ficha_10 = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID, title="Ficha 10", version=FICHA_10_VERSION
        )
        cls.ficha_11 = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID, title="Ficha 11", version=FICHA_11_VERSION
        )

    def create_submission(self, external_id, *, form=None, code="NV-TA-01", **changes):
        values = {
            "form_definition": form or self.ficha_1,
            "external_id": external_id,
            "raw_payload": {"_uuid": external_id},
            "normalized_payload": {"nucleo_code_normalized": code},
            "status": KoboSubmission.Status.READY_FOR_REVIEW,
            "pastoral_zone": "centro" if (form or self.ficha_1) == self.ficha_1 else "",
            "nucleo_code_original": code,
            "nucleo_code_normalized": code,
        }
        values.update(changes)
        return KoboSubmission.objects.create(**values)

    def create_identity(self, *, status=KoboTerritorialIdentity.Status.ACTIVE):
        source = self.create_submission(
            "identity-source",
            project=self.project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            routing_resolved_at=timezone.now(),
        )
        return KoboTerritorialIdentity.objects.create(
            nucleo_code_original="NV-TA-01",
            nucleo_code_normalized="NV-TA-01",
            pastoral_zone="centro",
            project=self.project,
            source_submission=source,
            status=status,
        )

    def create_conflict(self, identity=None):
        identity = identity or self.create_identity()
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="este", project=self.other_project
        )
        incoming = self.create_submission(
            "incoming-conflict",
            pastoral_zone="este",
            routing_status=KoboSubmission.RoutingStatus.CONFLICT,
            routing_reason_code=TerritorialRoutingReasonCode.TERRITORIAL_IDENTITY_CONFLICT,
        )
        conflict = KoboTerritorialIdentityConflict.objects.create(
            identity=identity,
            incoming_submission=incoming,
            existing_pastoral_zone="centro",
            proposed_pastoral_zone="este",
            existing_project=self.project,
            proposed_project=self.other_project,
        )
        return conflict, incoming


class PastoralZoneMappingAdministrationTests(TerritorialAdministrationFixtureMixin, TestCase):
    def test_create_and_repeat_mapping_are_audited_once(self):
        first = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=self.project, actor=self.actor
        )
        second = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=self.project, actor=self.actor
        )

        self.assertEqual(first.status, ResultStatus.SUCCESS)
        self.assertEqual(second.status, ResultStatus.ALREADY_APPLIED)
        self.assertEqual(KoboPastoralZoneProjectMapping.objects.filter(is_active=True).count(), 1)
        self.assertEqual(KoboTerritorialAdministrationEvent.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_change_without_identity_deactivates_previous_mapping(self):
        old = KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )

        result = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=self.other_project, actor=self.actor
        )

        old.refresh_from_db()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertFalse(old.is_active)
        self.assertEqual(
            KoboPastoralZoneProjectMapping.objects.get(is_active=True).project,
            self.other_project,
        )

    def test_change_and_deactivation_are_blocked_when_zone_has_identity(self):
        mapping = KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        self.create_identity()

        changed = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=self.other_project, actor=self.actor
        )
        deactivated = deactivate_pastoral_zone_project_mapping(
            pastoral_zone="centro", actor=self.actor, reason="Retiro controlado"
        )

        mapping.refresh_from_db()
        self.assertEqual(changed.reason_code, ReasonCode.ZONE_MAPPING_IN_USE)
        self.assertEqual(deactivated.reason_code, ReasonCode.ZONE_MAPPING_IN_USE)
        self.assertTrue(mapping.is_active)

    def test_deactivation_records_actor_time_and_reason(self):
        mapping = KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )

        result = deactivate_pastoral_zone_project_mapping(
            pastoral_zone="centro", actor=self.actor, reason="Proyecto territorial cerrado"
        )

        mapping.refresh_from_db()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(mapping.deactivated_by, self.actor)
        self.assertIsNotNone(mapping.deactivated_at)
        self.assertEqual(mapping.deactivation_reason, "Proyecto territorial cerrado")

    def test_invalid_zone_unavailable_project_and_unauthorized_actor_are_typed(self):
        closed = Project.objects.create(
            code="PRJ-TA-CLOSED", name="Cerrado", status=Project.Status.CLOSED
        )
        ordinary_user = get_user_model().objects.create_user(username="ordinary")

        invalid_zone = configure_pastoral_zone_project_mapping(
            pastoral_zone="unknown", project=self.project, actor=self.actor
        )
        unavailable = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=closed, actor=self.actor
        )
        denied = configure_pastoral_zone_project_mapping(
            pastoral_zone="centro", project=self.project, actor=ordinary_user
        )

        self.assertEqual(invalid_zone.reason_code, ReasonCode.INVALID_PASTORAL_ZONE)
        self.assertEqual(unavailable.reason_code, ReasonCode.PROJECT_NOT_AVAILABLE)
        self.assertEqual(denied.reason_code, ReasonCode.PERMISSION_DENIED)


class TerritorialConflictAdministrationTests(TerritorialAdministrationFixtureMixin, TestCase):
    def test_keep_existing_rejects_incoming_routing_and_is_idempotent(self):
        conflict, incoming = self.create_conflict()

        first = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING,
            actor=self.actor,
            reason="La evidencia vigente confirma la zona existente.",
        )
        event_count = KoboTerritorialAdministrationEvent.objects.count()
        second = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING,
            actor=self.actor,
            reason="Reintento",
        )

        incoming.refresh_from_db()
        conflict.refresh_from_db()
        self.assertEqual(first.status, ResultStatus.SUCCESS)
        self.assertEqual(second.status, ResultStatus.ALREADY_APPLIED)
        self.assertEqual(incoming.routing_status, KoboSubmission.RoutingStatus.ERROR)
        self.assertEqual(
            incoming.routing_reason_code,
            TerritorialRoutingReasonCode.TERRITORIAL_CONFLICT_REJECTED,
        )
        self.assertIsNone(incoming.project)
        self.assertEqual(KoboTerritorialAdministrationEvent.objects.count(), event_count)
        self.assertFalse(hasattr(incoming, "territorial_profile"))

    def test_different_decision_after_resolution_is_blocked(self):
        conflict, _ = self.create_conflict()
        resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=TerritorialConflictDecision.DISMISS,
            actor=self.actor,
            reason="Conflicto técnico duplicado.",
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING,
            actor=self.actor,
            reason="Decisión distinta.",
        )

        self.assertEqual(result.reason_code, ReasonCode.CONFLICT_DECISION_MISMATCH)

    def test_dismiss_preserves_identity_and_submission(self):
        identity = self.create_identity()
        conflict, incoming = self.create_conflict(identity)
        prior_submission_state = (incoming.status, incoming.routing_status, incoming.project_id)

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.DISMISSED,
            actor=self.actor,
            reason="Duplicado creado por una corrección técnica.",
        )

        identity.refresh_from_db()
        incoming.refresh_from_db()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(identity.project, self.project)
        self.assertEqual(
            (incoming.status, incoming.routing_status, incoming.project_id),
            prior_submission_state,
        )

    def test_accept_proposed_moves_unused_identity_and_reconciles_pending(self):
        identity = self.create_identity()
        conflict, incoming = self.create_conflict(identity)
        pending = self.create_submission(
            "pending-ficha-10",
            form=self.ficha_10,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="La revisión humana confirma la nueva zona.",
        )

        identity.refresh_from_db()
        incoming.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(identity.pastoral_zone, "este")
        self.assertEqual(identity.project, self.other_project)
        self.assertEqual(identity.source_submission, incoming)
        self.assertEqual(incoming.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(pending.project, self.other_project)
        self.assertEqual(pending.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(pending.status, KoboSubmission.Status.PROCESSING_FAILED)
        self.assertIsNone(pending.imported_at)
        self.assertEqual(pending.error_code, "IMPORT_NORMALIZATION_INVALID")
        self.assertEqual(classify_incident(pending), IncidentKind.TECHNICAL_ERROR)
        self.assertFalse(KoboImportRecord.objects.exists())
        self.assertFalse(
            KoboPrioritizedMicroproject.objects.filter(
                source_submission=pending
            ).exists()
        )
        self.assertFalse(
            KoboPrioritizationAssessment.objects.filter(
                source_submission=pending
            ).exists()
        )
        self.assertNotEqual(pending.status, KoboSubmission.Status.REJECTED)

    def test_accept_proposed_is_blocked_by_profile_or_import_record(self):
        identity = self.create_identity()
        conflict, _ = self.create_conflict(identity)
        KoboTerritorialProfile.objects.create(
            territorial_identity=identity,
            project=self.project,
            source_submission=identity.source_submission,
            parish="Parroquia",
            community_sector="Comunidad",
            access_difficulties="no",
            initial_priority_perception="medium",
            created_by=self.actor,
        )

        profile_block = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="Intento bloqueado.",
        )

        self.assertEqual(profile_block.reason_code, ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED)
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, KoboTerritorialIdentityConflict.Status.OPEN)

    def test_accept_proposed_is_blocked_by_prioritized_microproject(self):
        identity = self.create_identity()
        conflict, _ = self.create_conflict(identity)
        submission = self.create_submission(
            "materialized-ficha-10",
            form=self.ficha_10,
            project=self.project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )
        KoboPrioritizedMicroproject.objects.create(
            territorial_identity=identity,
            project=self.project,
            source_submission=submission,
            name="Microproyecto",
            component="infrastructure",
            problem_summary="Problema",
            specific_objective="Objetivo",
            beneficiary_group=["youth"],
            main_activities="Actividades",
            estimated_cost_range="5000_15000",
            implementation_urgency="immediate",
            technical_viability="high",
            expected_result="Resultado",
            created_by=self.actor,
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="Intento bloqueado.",
        )

        self.assertEqual(result.reason_code, ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED)

    def test_accept_proposed_is_blocked_by_prioritization_assessment(self):
        identity = self.create_identity()
        conflict, _ = self.create_conflict(identity)
        submission = self.create_submission(
            "materialized-ficha-11",
            form=self.ficha_11,
            project=self.project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )
        scores = {
            "physical_damage_score": 1,
            "affected_families_score": 1,
            "social_vulnerability_score": 1,
            "services_interruption_score": 1,
            "livelihood_loss_score": 1,
            "parish_capacity_score": 1,
            "territorial_accessibility_score": 1,
            "allies_availability_score": 1,
            "rapid_impact_score": 1,
            "financial_viability_score": 1,
        }
        KoboPrioritizationAssessment.objects.create(
            territorial_identity=identity,
            project=self.project,
            source_submission=submission,
            **scores,
            priority_total_calculated=10,
            suggested_semaphore_calculated="red",
            final_semaphore="red",
            final_priority="high",
            priority_summary="Resumen",
            created_by=self.actor,
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="Intento bloqueado.",
        )

        self.assertEqual(result.reason_code, ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED)

    def test_accept_proposed_is_blocked_by_import_record(self):
        identity = self.create_identity()
        conflict, _ = self.create_conflict(identity)
        identity.source_submission.status = KoboSubmission.Status.IMPORTED
        identity.source_submission.processed_at = timezone.now()
        identity.source_submission.save(update_fields=("status", "processed_at"))
        KoboImportRecord.objects.create(
            submission=identity.source_submission,
            handler_type="territorial_profile",
            target_app_label="kobo",
            target_model="KoboTerritorialProfile",
            target_object_id=999,
            created_by=self.actor,
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="Intento bloqueado.",
        )

        self.assertEqual(result.reason_code, ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED)

    def test_accept_proposed_is_blocked_by_another_resolved_submission(self):
        identity = self.create_identity()
        conflict, _ = self.create_conflict(identity)
        self.create_submission(
            "other-resolved",
            form=self.ficha_10,
            project=self.project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
        )

        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.ACCEPT_PROPOSED,
            actor=self.actor,
            reason="Intento bloqueado.",
        )

        self.assertEqual(result.reason_code, ReasonCode.TERRITORIAL_IDENTITY_ALREADY_USED)

    def test_reason_is_required(self):
        conflict, _ = self.create_conflict()
        result = resolve_territorial_identity_conflict(
            conflict=conflict,
            decision=KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING,
            actor=self.actor,
            reason="   ",
        )
        self.assertEqual(result.reason_code, ReasonCode.REASON_REQUIRED)


class TerritorialIdentityStateTests(TerritorialAdministrationFixtureMixin, TestCase):
    def test_observe_activate_and_deactivate_are_explicit_and_audited(self):
        identity = self.create_identity()

        observed = observe_territorial_identity(
            identity=identity, actor=self.actor, reason="Revisión requerida"
        )
        activated = activate_observed_territorial_identity(
            identity=identity, actor=self.actor, reason="Revisión completada"
        )
        KoboTerritorialProfile.objects.create(
            territorial_identity=identity,
            project=self.project,
            source_submission=identity.source_submission,
            parish="Parroquia",
            community_sector="Comunidad",
            access_difficulties="no",
            initial_priority_perception="medium",
            created_by=self.actor,
        )
        inactive = deactivate_territorial_identity(
            identity=identity, actor=self.actor, reason="Cierre administrativo"
        )

        identity.refresh_from_db()
        self.assertEqual(observed.status, ResultStatus.SUCCESS)
        self.assertEqual(activated.status, ResultStatus.SUCCESS)
        self.assertEqual(inactive.status, ResultStatus.SUCCESS)
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.INACTIVE)
        self.assertTrue(
            KoboTerritorialProfile.objects.filter(territorial_identity=identity).exists()
        )
        self.assertEqual(KoboTerritorialAdministrationEvent.objects.count(), 3)

    def test_pending_identity_can_be_observed(self):
        identity = self.create_identity(
            status=KoboTerritorialIdentity.Status.PENDING_REVIEW
        )

        result = observe_territorial_identity(
            identity=identity,
            actor=self.actor,
            reason="Validación humana pendiente",
        )

        identity.refresh_from_db()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.OBSERVED)

    def test_new_ficha_1_does_not_reactivate_inactive_identity(self):
        identity = self.create_identity(status=KoboTerritorialIdentity.Status.INACTIVE)
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro", project=self.project
        )
        incoming = self.create_submission("inactive-confirmation")

        route_ficha_1_submission(incoming)

        identity.refresh_from_db()
        incoming.refresh_from_db()
        self.assertEqual(identity.status, KoboTerritorialIdentity.Status.INACTIVE)
        self.assertEqual(
            incoming.routing_status,
            KoboSubmission.RoutingStatus.RESOLVED,
        )

    def test_inactive_identity_cannot_be_observed_or_activated(self):
        identity = self.create_identity(status=KoboTerritorialIdentity.Status.INACTIVE)

        observed = observe_territorial_identity(
            identity=identity, actor=self.actor, reason="No permitido"
        )
        activated = activate_observed_territorial_identity(
            identity=identity, actor=self.actor, reason="No permitido"
        )

        self.assertEqual(observed.reason_code, ReasonCode.INVALID_IDENTITY_TRANSITION)
        self.assertEqual(activated.reason_code, ReasonCode.INVALID_IDENTITY_TRANSITION)


class TerritorialReconciliationAdministrationTests(TerritorialAdministrationFixtureMixin, TestCase):
    def test_reconciliation_batches_one_hundred_and_records_incomplete_as_incidents(self):
        identity = self.create_identity()
        for index in range(101):
            self.create_submission(
                f"pending-{index}",
                form=self.ficha_10 if index % 2 else self.ficha_11,
                routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            )

        first = reconcile_territorial_identity_submissions(identity=identity, actor=self.actor)
        second = reconcile_territorial_identity_submissions(identity=identity, actor=self.actor)
        processing_events_before_retry = KoboProcessingEvent.objects.filter(
            submission__nucleo_code_normalized=identity.nucleo_code_normalized
        ).count()
        third = reconcile_territorial_identity_submissions(identity=identity, actor=self.actor)

        self.assertEqual(
            (first.scanned, first.resolved, first.routed, first.imported),
            (100, 100, 100, 0),
        )
        self.assertEqual((first.incidents, first.failed, first.skipped), (100, 0, 0))
        self.assertEqual((first.still_pending, first.remaining), (1, 1))
        self.assertTrue(first.has_more)
        self.assertEqual(
            (second.scanned, second.resolved, second.routed, second.imported),
            (1, 1, 1, 0),
        )
        self.assertEqual((second.incidents, second.failed, second.skipped), (1, 0, 0))
        self.assertEqual((second.still_pending, second.remaining), (0, 0))
        self.assertFalse(second.has_more)
        self.assertEqual(
            (third.scanned, third.resolved, third.routed, third.imported),
            (0, 0, 0, 0),
        )
        self.assertEqual((third.incidents, third.failed, third.skipped), (0, 0, 0))
        self.assertEqual(
            KoboProcessingEvent.objects.filter(
                submission__nucleo_code_normalized=identity.nucleo_code_normalized
            ).count(),
            processing_events_before_retry,
        )
        self.assertEqual(KoboTerritorialAdministrationEvent.objects.count(), 2)
        self.assertEqual(first.incidents + second.incidents, 101)
        self.assertEqual(first.failed + second.failed, 0)
        self.assertFalse(KoboImportRecord.objects.exists())
        self.assertFalse(
            KoboSubmission.objects.filter(status=KoboSubmission.Status.IMPORTED).exists()
        )
        self.assertFalse(
            KoboSubmission.objects.filter(status=KoboSubmission.Status.REJECTED).exists()
        )
        self.assertEqual(
            KoboSubmission.objects.filter(
                status=KoboSubmission.Status.PROCESSING_FAILED,
                imported_at__isnull=True,
            ).count(),
            101,
        )
        self.assertFalse(
            KoboSubmission.objects.filter(
                status=KoboSubmission.Status.APPROVED_FOR_IMPORT
            ).exists()
        )
        self.assertFalse(KoboPrioritizedMicroproject.objects.exists())
        self.assertFalse(KoboPrioritizationAssessment.objects.exists())

    def test_reconciliation_does_not_touch_imported_submission(self):
        identity = self.create_identity()
        imported = self.create_submission(
            "imported",
            form=self.ficha_10,
            status=KoboSubmission.Status.IMPORTED,
            project=self.project,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            processed_at=timezone.now(),
        )

        result = reconcile_territorial_identity_submissions(identity=identity, actor=self.actor)

        imported.refresh_from_db()
        self.assertEqual(result.resolved, 0)
        self.assertEqual(imported.status, KoboSubmission.Status.IMPORTED)
        self.assertEqual(imported.project, self.project)


class TerritorialAdministrationPermissionTests(TerritorialAdministrationFixtureMixin, TestCase):
    def test_role_matrix_grants_read_only_except_for_sigedon_admin(self):
        groups = sync_operation_roles()
        users = {}
        for role_name in (
            ROLE_SIGEDON_ADMIN,
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
        ):
            user = get_user_model().objects.create_user(username=f"role-{role_name}")
            user.groups.add(groups[role_name])
            users[role_name] = user

        self.assertTrue(
            users[ROLE_SIGEDON_ADMIN].has_perm("kobo.manage_pastoral_zone_mappings")
        )
        self.assertTrue(
            users[ROLE_SIGEDON_ADMIN].has_perm("kobo.resolve_territorial_conflicts")
        )
        for role_name in (ROLE_FIELD_OPERATOR, ROLE_EXTERNAL_AUDITOR, ROLE_PROJECT_COMMITTEE):
            with self.subTest(role=role_name):
                self.assertTrue(
                    users[role_name].has_perm("kobo.view_territorial_administration")
                )
                self.assertFalse(
                    users[role_name].has_perm("kobo.manage_pastoral_zone_mappings")
                )
                self.assertFalse(
                    users[role_name].has_perm("kobo.resolve_territorial_conflicts")
                )


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking required")
class TerritorialMappingConcurrencyTests(TerritorialAdministrationFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # PRE: TransactionTestCase starts with an empty migrated PostgreSQL database.
        # POST: creates thread-visible actor, projects, and form definitions.
        type(self).setUpTestData()

    def test_concurrent_initial_configuration_preserves_one_active_mapping(self):
        barrier = Barrier(2)
        results = Queue()

        def worker(project_id):
            # PRE: each worker has a separate PostgreSQL connection and a valid project id.
            # POST: reports one typed service result and closes its connection.
            close_old_connections()
            try:
                barrier.wait()
                results.put(
                    configure_pastoral_zone_project_mapping(
                        pastoral_zone="centro",
                        project=Project.objects.get(pk=project_id),
                        actor=get_user_model().objects.get(pk=self.actor.pk),
                    )
                )
            finally:
                close_old_connections()

        threads = [
            Thread(target=worker, args=(self.project.pk,)),
            Thread(target=worker, args=(self.other_project.pk,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        outcomes = {results.get_nowait().status for _ in range(2)}
        self.assertIn(ResultStatus.SUCCESS, outcomes)
        self.assertEqual(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro", is_active=True
            ).count(),
            1,
        )


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking required")
class TerritorialResolutionConcurrencyTests(TerritorialAdministrationFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # PRE: TransactionTestCase starts with an empty migrated PostgreSQL database.
        # POST: creates a thread-visible unused identity, mapping, and conflict.
        type(self).setUpTestData()
        self.conflict, self.incoming = self.create_conflict()

    def test_concurrent_same_conflict_resolution_writes_one_decision(self):
        barrier = Barrier(2)
        results = Queue()

        def worker():
            # PRE: the shared conflict remains open when both workers start.
            # POST: reports SUCCESS or ALREADY_APPLIED from a separate connection.
            close_old_connections()
            try:
                barrier.wait()
                results.put(
                    resolve_territorial_identity_conflict(
                        conflict=KoboTerritorialIdentityConflict.objects.get(pk=self.conflict.pk),
                        decision=KoboTerritorialIdentityConflict.Resolution.KEEP_EXISTING,
                        actor=get_user_model().objects.get(pk=self.actor.pk),
                        reason="Misma decisión concurrente",
                    )
                )
            finally:
                close_old_connections()

        threads = [Thread(target=worker), Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        outcomes = [results.get_nowait().status for _ in range(2)]
        self.assertIn(ResultStatus.SUCCESS, outcomes)
        self.assertIn(ResultStatus.ALREADY_APPLIED, outcomes)
        self.assertEqual(
            KoboTerritorialAdministrationEvent.objects.filter(
                action="territorial_conflict_resolved"
            ).count(),
            1,
        )

    def test_concurrent_reconciliation_resolves_each_submission_once(self):
        pending = self.create_submission(
            "concurrent-pending",
            form=self.ficha_10,
            routing_status=KoboSubmission.RoutingStatus.PENDING_IDENTITY,
        )
        barrier = Barrier(2)
        results = Queue()

        def worker():
            # PRE: the same pending row is visible to both PostgreSQL connections.
            # POST: reports an idempotent reconciliation result and closes its connection.
            close_old_connections()
            try:
                barrier.wait()
                results.put(
                    reconcile_territorial_identity_submissions(
                        identity=KoboTerritorialIdentity.objects.get(pk=self.conflict.identity_id),
                        actor=get_user_model().objects.get(pk=self.actor.pk),
                    )
                )
            finally:
                close_old_connections()

        threads = [Thread(target=worker), Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        resolved_counts = sorted(results.get_nowait().resolved for _ in range(2))
        pending.refresh_from_db()
        self.assertEqual(resolved_counts, [0, 1])
        self.assertEqual(pending.routing_status, KoboSubmission.RoutingStatus.RESOLVED)
        self.assertEqual(
            KoboTerritorialAdministrationEvent.objects.filter(
                action="territorial_submissions_reconciled"
            ).count(),
            1,
        )
