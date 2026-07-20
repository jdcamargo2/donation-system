from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from django.core.exceptions import ValidationError

from apps.integrations.kobo.form_registry import KoboFormType
from apps.integrations.kobo.import_contracts import (
    ImportWarning,
    KoboImportBlocked,
    KoboImportHandler,
    KoboMaterializationResult,
)
from apps.integrations.kobo.models import (
    KoboPrioritizationAssessment,
    KoboPrioritizedMicroproject,
    KoboProcessingEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
    TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES,
    TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS,
    validate_prioritization_calculation_warnings,
    validate_territorial_profile_location,
)
from apps.integrations.kobo.mappings.ficha_10 import (
    BENEFICIARY_GROUPS,
    COMPONENTS,
    ESTIMATED_COST_RANGES,
    IMPLEMENTATION_URGENCIES,
    TECHNICAL_VIABILITIES,
)
from apps.integrations.kobo.mappings.ficha_11 import (
    FINAL_PRIORITIES,
    FINAL_SEMAPHORES,
    SCORE_FIELDS,
    SCORE_MAX,
    SCORE_MIN,
    calculate_ficha_11_suggested_semaphore,
)


FICHA_1_PROFILE_INVALID = "FICHA_1_PROFILE_INVALID"
FICHA_1_IDENTITY_MISSING = "FICHA_1_IDENTITY_MISSING"
FICHA_1_IDENTITY_MISMATCH = "FICHA_1_IDENTITY_MISMATCH"
FICHA_1_IDENTITY_INACTIVE = "FICHA_1_IDENTITY_INACTIVE"
FICHA_1_TERRITORIAL_CONFLICT = "FICHA_1_TERRITORIAL_CONFLICT"
FICHA_1_PROFILE_STATE_CONFLICT = "FICHA_1_PROFILE_STATE_CONFLICT"
FICHA_10_MICROPROJECT_INVALID = "FICHA_10_MICROPROJECT_INVALID"
FICHA_10_IDENTITY_MISSING = "FICHA_10_IDENTITY_MISSING"
FICHA_10_IDENTITY_MISMATCH = "FICHA_10_IDENTITY_MISMATCH"
FICHA_10_IDENTITY_INACTIVE = "FICHA_10_IDENTITY_INACTIVE"
FICHA_10_TERRITORIAL_CONFLICT = "FICHA_10_TERRITORIAL_CONFLICT"
FICHA_10_MICROPROJECT_STATE_CONFLICT = "FICHA_10_MICROPROJECT_STATE_CONFLICT"
FICHA_11_ASSESSMENT_INVALID = "FICHA_11_ASSESSMENT_INVALID"
FICHA_11_IDENTITY_MISSING = "FICHA_11_IDENTITY_MISSING"
FICHA_11_IDENTITY_MISMATCH = "FICHA_11_IDENTITY_MISMATCH"
FICHA_11_IDENTITY_INACTIVE = "FICHA_11_IDENTITY_INACTIVE"
FICHA_11_TERRITORIAL_CONFLICT = "FICHA_11_TERRITORIAL_CONFLICT"
FICHA_11_ASSESSMENT_STATE_CONFLICT = "FICHA_11_ASSESSMENT_STATE_CONFLICT"


def _optional_profile_text(payload, key) -> str:
    # PRE: payload is the persisted normalized Ficha 1 mapping.
    # POST: returns an optional canonical string or blocks malformed stored data.
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    return value


def _validated_ficha_1_profile_data(submission) -> dict[str, object]:
    """
    PRE: common import checks accepted the locked Ficha 1 submission.
    POST: returns model-ready data exclusively from persisted normalized fields,
    or raises a safe domain blocker without reading the raw payload.
    """
    payload = submission.normalized_payload
    if not isinstance(payload, dict):
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    if (
        not isinstance(submission.parish, str)
        or not submission.parish.strip()
        or not isinstance(submission.primary_community, str)
        or not submission.primary_community.strip()
    ):
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    if payload.get("nucleo_code_normalized") != submission.nucleo_code_normalized:
        raise KoboImportBlocked(FICHA_1_IDENTITY_MISMATCH)
    if payload.get("pastoral_zone_normalized") != submission.pastoral_zone:
        raise KoboImportBlocked(FICHA_1_IDENTITY_MISMATCH)

    estimated_households = payload.get("estimated_households")
    if estimated_households is not None and (
        isinstance(estimated_households, bool)
        or not isinstance(estimated_households, int)
        or estimated_households < 0
    ):
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    access_difficulties = payload.get("access_difficulties")
    if access_difficulties not in TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES:
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    priority = payload.get("initial_priority_perception")
    if priority not in TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS:
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID)
    location = payload.get("location")
    try:
        validate_territorial_profile_location(location)
    except ValidationError as exc:
        raise KoboImportBlocked(FICHA_1_PROFILE_INVALID) from exc

    return {
        "parish": submission.parish.strip(),
        "community_sector": submission.primary_community.strip(),
        "location": location,
        "parish_delegate": _optional_profile_text(payload, "parish_delegate"),
        "contact_phone": _optional_profile_text(payload, "contact_phone"),
        "main_informant_role": _optional_profile_text(payload, "main_informant_role"),
        "communities_covered": _optional_profile_text(payload, "communities_covered"),
        "estimated_households": estimated_households,
        "access_difficulties": access_difficulties,
        "access_difficulties_notes": _optional_profile_text(
            payload, "access_difficulties_notes"
        ),
        "initial_priority_perception": priority,
        "general_notes": _optional_profile_text(payload, "general_notes"),
    }


@dataclass(frozen=True)
class Ficha1TerritorialProfileImportHandler:
    form_type: KoboFormType = KoboFormType.FICHA_1

    def validate_for_import(self, *, submission) -> tuple[ImportWarning, ...]:
        """
        PRE: common checks accepted a locked, approved, routed Ficha 1 submission.
        POST: validates its persisted normalized profile data without side effects.
        """
        _validated_ficha_1_profile_data(submission)
        return ()

    def materialize(self, *, submission, actor) -> KoboMaterializationResult:
        """
        PRE: validation passed and the caller owns the import transaction and row lock.
        POST: creates exactly one immutable territorial profile, safely activates a
        pending identity, and emits only profile-specific transactional audit events.
        """
        profile_data = _validated_ficha_1_profile_data(submission)
        try:
            identity = KoboTerritorialIdentity.objects.select_for_update().get(
                nucleo_code_normalized=submission.nucleo_code_normalized
            )
        except KoboTerritorialIdentity.DoesNotExist as exc:
            raise KoboImportBlocked(FICHA_1_IDENTITY_MISSING) from exc

        if identity.status == KoboTerritorialIdentity.Status.INACTIVE:
            raise KoboImportBlocked(FICHA_1_IDENTITY_INACTIVE)
        if (
            identity.project_id != submission.project_id
            or identity.pastoral_zone != submission.pastoral_zone
        ):
            raise KoboImportBlocked(FICHA_1_IDENTITY_MISMATCH)
        if KoboTerritorialIdentityConflict.objects.filter(
            identity=identity,
            status=KoboTerritorialIdentityConflict.Status.OPEN,
        ).exists():
            raise KoboImportBlocked(FICHA_1_TERRITORIAL_CONFLICT)
        if KoboTerritorialProfile.objects.filter(source_submission=submission).exists():
            raise KoboImportBlocked(FICHA_1_PROFILE_STATE_CONFLICT)

        profile = KoboTerritorialProfile(
            territorial_identity=identity,
            project=submission.project,
            source_submission=submission,
            created_by=actor,
            **profile_data,
        )
        try:
            profile.full_clean()
        except ValidationError as exc:
            raise KoboImportBlocked(FICHA_1_PROFILE_INVALID) from exc
        profile.save()

        from apps.operations.models import AuditLog
        from apps.operations.services import log_action

        event_metadata = {
            "profile_id": profile.pk,
            "identity_id": identity.pk,
            "project_id": submission.project_id,
            "nucleo_code_normalized": identity.nucleo_code_normalized,
        }
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="operational_import",
            level=KoboProcessingEvent.Level.INFO,
            code="territorial_profile_created",
            message="Approved Ficha 1 territorial profile created.",
            metadata=event_metadata,
        )
        log_action(
            actor,
            AuditLog.Action.CREATED,
            profile,
            "Perfil territorial Kobo creado desde una Ficha 1 aprobada.",
        )

        if identity.status == KoboTerritorialIdentity.Status.PENDING_REVIEW:
            identity.status = KoboTerritorialIdentity.Status.ACTIVE
            identity.save(update_fields=("status", "updated_at"))
            KoboProcessingEvent.objects.create(
                submission=submission,
                stage="operational_import",
                level=KoboProcessingEvent.Level.INFO,
                code="territorial_identity_activated",
                message="Territorial identity activated after approved profile import.",
                metadata=event_metadata,
            )
            log_action(
                actor,
                AuditLog.Action.UPDATED,
                identity,
                "Identidad territorial Kobo activada tras materializar Ficha 1.",
            )

        return KoboMaterializationResult(
            materialization_type="territorial_profile",
            target_app_label="kobo",
            target_model="KoboTerritorialProfile",
            target_object_id=profile.pk,
            created=True,
        )


def _required_microproject_text(payload, key) -> str:
    # PRE: payload is the persisted normalized Ficha 10 mapping.
    # POST: returns its required text unchanged or blocks malformed stored data.
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KoboImportBlocked(FICHA_10_MICROPROJECT_INVALID)
    return value


def _required_microproject_choice(payload, key, choices) -> str:
    # PRE: key names one persisted normalized Ficha 10 select-one field.
    # POST: returns its canonical code or blocks unknown stored data.
    value = payload.get(key)
    if value not in choices:
        raise KoboImportBlocked(FICHA_10_MICROPROJECT_INVALID)
    return value


def _validated_ficha_10_microproject_data(submission) -> dict[str, object]:
    """
    PRE: common checks accepted the locked, approved, routed Ficha 10 submission.
    POST: returns model-ready canonical data solely from normalized_payload.
    """
    payload = submission.normalized_payload
    if not isinstance(payload, dict):
        raise KoboImportBlocked(FICHA_10_MICROPROJECT_INVALID)
    if (
        payload.get("nucleo_code") != submission.nucleo_code_normalized
        or payload.get("nucleo_code_normalized") != submission.nucleo_code_normalized
    ):
        raise KoboImportBlocked(FICHA_10_IDENTITY_MISMATCH)
    beneficiary_group = payload.get("beneficiary_group")
    if (
        not isinstance(beneficiary_group, list)
        or not beneficiary_group
        or len(beneficiary_group) != len(set(beneficiary_group))
        or any(
            not isinstance(value, str) or value not in BENEFICIARY_GROUPS
            for value in beneficiary_group
        )
    ):
        raise KoboImportBlocked(FICHA_10_MICROPROJECT_INVALID)
    return {
        "name": _required_microproject_text(payload, "microproject_name"),
        "component": _required_microproject_choice(payload, "component", COMPONENTS),
        "problem_summary": _required_microproject_text(payload, "problem_summary"),
        "specific_objective": _required_microproject_text(payload, "specific_objective"),
        "beneficiary_group": beneficiary_group,
        "main_activities": _required_microproject_text(payload, "main_activities"),
        "estimated_cost_range": _required_microproject_choice(
            payload, "estimated_cost_range", ESTIMATED_COST_RANGES
        ),
        "implementation_urgency": _required_microproject_choice(
            payload, "implementation_urgency", IMPLEMENTATION_URGENCIES
        ),
        "technical_viability": _required_microproject_choice(
            payload, "technical_viability", TECHNICAL_VIABILITIES
        ),
        "expected_result": _required_microproject_text(payload, "expected_result"),
    }


@dataclass(frozen=True)
class Ficha10PrioritizedMicroprojectImportHandler:
    form_type: KoboFormType = KoboFormType.FICHA_10

    def validate_for_import(self, *, submission) -> tuple[ImportWarning, ...]:
        """
        PRE: common checks accepted a locked, approved, routed Ficha 10 submission.
        POST: validates every persisted normalized proposal field without side effects.
        """
        _validated_ficha_10_microproject_data(submission)
        return ()

    def materialize(self, *, submission, actor) -> KoboMaterializationResult:
        """
        PRE: validation passed and the caller owns the import transaction and row lock.
        POST: creates exactly one immutable prioritized microproject and its safe audit events.
        """
        microproject_data = _validated_ficha_10_microproject_data(submission)
        try:
            identity = KoboTerritorialIdentity.objects.select_for_update().get(
                nucleo_code_normalized=submission.nucleo_code_normalized
            )
        except KoboTerritorialIdentity.DoesNotExist as exc:
            raise KoboImportBlocked(FICHA_10_IDENTITY_MISSING) from exc
        if identity.status == KoboTerritorialIdentity.Status.INACTIVE:
            raise KoboImportBlocked(FICHA_10_IDENTITY_INACTIVE)
        if identity.project_id != submission.project_id:
            raise KoboImportBlocked(FICHA_10_IDENTITY_MISMATCH)
        if KoboTerritorialIdentityConflict.objects.filter(
            identity=identity,
            status=KoboTerritorialIdentityConflict.Status.OPEN,
        ).exists():
            raise KoboImportBlocked(FICHA_10_TERRITORIAL_CONFLICT)
        if KoboPrioritizedMicroproject.objects.filter(
            source_submission=submission
        ).exists():
            raise KoboImportBlocked(FICHA_10_MICROPROJECT_STATE_CONFLICT)

        microproject = KoboPrioritizedMicroproject(
            territorial_identity=identity,
            project=submission.project,
            source_submission=submission,
            created_by=actor,
            **microproject_data,
        )
        try:
            microproject.full_clean()
        except ValidationError as exc:
            raise KoboImportBlocked(FICHA_10_MICROPROJECT_INVALID) from exc
        microproject.save()

        from apps.operations.models import AuditLog
        from apps.operations.services import log_action

        event_metadata = {
            "microproject_id": microproject.pk,
            "identity_id": identity.pk,
            "project_id": submission.project_id,
            "nucleo_code_normalized": identity.nucleo_code_normalized,
            "component": microproject.component,
        }
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="operational_import",
            level=KoboProcessingEvent.Level.INFO,
            code="prioritized_microproject_created",
            message="Approved Ficha 10 prioritized microproject created.",
            metadata=event_metadata,
        )
        log_action(
            actor,
            AuditLog.Action.CREATED,
            microproject,
            "Microproyecto priorizado Kobo creado desde una Ficha 10 aprobada.",
        )
        return KoboMaterializationResult(
            materialization_type="prioritized_microproject",
            target_app_label="kobo",
            target_model="KoboPrioritizedMicroproject",
            target_object_id=microproject.pk,
            created=True,
        )


def _optional_original_total(payload) -> int | None:
    # PRE: payload is the persisted normalized Ficha 11 mapping.
    # POST: returns its optional non-negative integer total or blocks malformed data.
    value = payload.get("priority_total_original")
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    if isinstance(value, int):
        total = value
    elif isinstance(value, str) and value.strip().isdigit():
        total = int(value.strip())
    else:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    if total < 0:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    return total


def _optional_original_semaphore(payload) -> str:
    # PRE: payload is the persisted normalized Ficha 11 mapping.
    # POST: returns an optional canonical semaphore or blocks an unknown code.
    value = payload.get("suggested_semaphore_original")
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value not in FINAL_SEMAPHORES:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    return value


def _validated_ficha_11_assessment_data(
    submission,
) -> tuple[dict[str, object], tuple[ImportWarning, ...]]:
    """
    PRE: common checks accepted the locked, approved, routed Ficha 11 submission.
    POST: returns model-ready normalized data and safe calculation warnings only.
    """
    payload = submission.normalized_payload
    if not isinstance(payload, dict):
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    if (
        payload.get("nucleo_code") != submission.nucleo_code_normalized
        or payload.get("nucleo_code_normalized")
        != submission.nucleo_code_normalized
    ):
        raise KoboImportBlocked(FICHA_11_IDENTITY_MISMATCH)

    scores = {}
    for field_name in SCORE_FIELDS:
        score = payload.get(field_name)
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or not SCORE_MIN <= score <= SCORE_MAX
        ):
            raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
        scores[field_name] = score
    calculated_total = sum(scores.values())
    calculated_semaphore = calculate_ficha_11_suggested_semaphore(calculated_total)
    if (
        payload.get("priority_total_calculated") != calculated_total
        or payload.get("priority_total") != calculated_total
        or payload.get("suggested_semaphore_calculated") != calculated_semaphore
        or payload.get("suggested_semaphore") != calculated_semaphore
    ):
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)

    original_total = _optional_original_total(payload)
    original_semaphore = _optional_original_semaphore(payload)
    final_semaphore = payload.get("final_semaphore")
    final_priority = payload.get("final_priority")
    if final_semaphore not in FINAL_SEMAPHORES or final_priority not in FINAL_PRIORITIES:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    priority_summary = payload.get("priority_summary")
    linked_microprojects = payload.get("linked_microprojects")
    if not isinstance(priority_summary, str) or not priority_summary.strip():
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    if not isinstance(linked_microprojects, str):
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)

    expected_warnings = []
    if original_total is not None and original_total != calculated_total:
        expected_warnings.append(
            {
                "code": "PRIORITY_TOTAL_MISMATCH",
                "message": "Kobo priority_total differs from the SIGEDON calculation.",
                "original_value": payload.get("priority_total_original"),
                "calculated_value": calculated_total,
            }
        )
    if original_semaphore and original_semaphore != calculated_semaphore:
        expected_warnings.append(
            {
                "code": "SUGGESTED_SEMAPHORE_MISMATCH",
                "message": (
                    "Kobo suggested_semaphore differs from the SIGEDON calculation."
                ),
                "original_value": original_semaphore,
                "calculated_value": calculated_semaphore,
            }
        )
    raw_warnings = payload.get("calculation_warnings")
    try:
        validate_prioritization_calculation_warnings(raw_warnings)
    except ValidationError as exc:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID) from exc
    if raw_warnings != expected_warnings:
        raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID)
    import_warnings = tuple(
        ImportWarning(code=warning["code"], message=warning["message"])
        for warning in raw_warnings
    )
    return (
        {
            **scores,
            "priority_total_original": original_total,
            "priority_total_calculated": calculated_total,
            "suggested_semaphore_original": original_semaphore,
            "suggested_semaphore_calculated": calculated_semaphore,
            "final_semaphore": final_semaphore,
            "final_priority": final_priority,
            "priority_summary": priority_summary,
            "calculation_warnings": raw_warnings,
            "linked_microprojects_snapshot": linked_microprojects,
        },
        import_warnings,
    )


@dataclass(frozen=True)
class Ficha11PrioritizationAssessmentImportHandler:
    form_type: KoboFormType = KoboFormType.FICHA_11

    def validate_for_import(self, *, submission) -> tuple[ImportWarning, ...]:
        """
        PRE: common checks accepted a locked, approved, routed Ficha 11 submission.
        POST: validates its scores, calculations, decisions, snapshot, and warnings.
        """
        _, warnings = _validated_ficha_11_assessment_data(submission)
        return warnings

    def materialize(self, *, submission, actor) -> KoboMaterializationResult:
        """
        PRE: validation passed and the caller owns the import transaction and row lock.
        POST: creates exactly one immutable assessment and its safe audit events.
        """
        assessment_data, warnings = _validated_ficha_11_assessment_data(submission)
        try:
            identity = KoboTerritorialIdentity.objects.select_for_update().get(
                nucleo_code_normalized=submission.nucleo_code_normalized
            )
        except KoboTerritorialIdentity.DoesNotExist as exc:
            raise KoboImportBlocked(FICHA_11_IDENTITY_MISSING) from exc
        if identity.status == KoboTerritorialIdentity.Status.INACTIVE:
            raise KoboImportBlocked(FICHA_11_IDENTITY_INACTIVE)
        if identity.project_id != submission.project_id:
            raise KoboImportBlocked(FICHA_11_IDENTITY_MISMATCH)
        if KoboTerritorialIdentityConflict.objects.filter(
            identity=identity,
            status=KoboTerritorialIdentityConflict.Status.OPEN,
        ).exists():
            raise KoboImportBlocked(FICHA_11_TERRITORIAL_CONFLICT)
        if KoboPrioritizationAssessment.objects.filter(
            source_submission=submission
        ).exists():
            raise KoboImportBlocked(FICHA_11_ASSESSMENT_STATE_CONFLICT)

        assessment = KoboPrioritizationAssessment(
            territorial_identity=identity,
            project=submission.project,
            source_submission=submission,
            created_by=actor,
            **assessment_data,
        )
        try:
            assessment.full_clean()
        except ValidationError as exc:
            raise KoboImportBlocked(FICHA_11_ASSESSMENT_INVALID) from exc
        assessment.save()

        from apps.operations.models import AuditLog
        from apps.operations.services import log_action

        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="operational_import",
            level=KoboProcessingEvent.Level.INFO,
            code="prioritization_assessment_created",
            message="Approved Ficha 11 prioritization assessment created.",
            metadata={
                "assessment_id": assessment.pk,
                "identity_id": identity.pk,
                "project_id": submission.project_id,
                "nucleo_code_normalized": identity.nucleo_code_normalized,
                "priority_total_calculated": assessment.priority_total_calculated,
                "final_semaphore": assessment.final_semaphore,
                "final_priority": assessment.final_priority,
                "warning_codes": [warning.code for warning in warnings],
            },
        )
        log_action(
            actor,
            AuditLog.Action.CREATED,
            assessment,
            "Evaluación de priorización Kobo creada desde una Ficha 11 aprobada.",
        )
        return KoboMaterializationResult(
            materialization_type="prioritization_assessment",
            target_app_label="kobo",
            target_model="KoboPrioritizationAssessment",
            target_object_id=assessment.pk,
            created=True,
            warnings=warnings,
        )


KOBO_IMPORT_HANDLERS: Mapping[KoboFormType, KoboImportHandler] = MappingProxyType(
    {
        KoboFormType.FICHA_1: Ficha1TerritorialProfileImportHandler(),
        KoboFormType.FICHA_10: Ficha10PrioritizedMicroprojectImportHandler(),
        KoboFormType.FICHA_11: Ficha11PrioritizationAssessmentImportHandler(),
    }
)


def get_import_handler(form_type: KoboFormType) -> KoboImportHandler:
    """
    PRE: form_type came from the closed registered-form dispatcher.
    POST: returns the exact supported handler; no generic fallback is available.
    """
    return KOBO_IMPORT_HANDLERS[form_type]
