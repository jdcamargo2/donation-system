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
    KoboProcessingEvent,
    KoboTerritorialIdentity,
    KoboTerritorialIdentityConflict,
    KoboTerritorialProfile,
    TERRITORIAL_PROFILE_ACCESS_DIFFICULTIES,
    TERRITORIAL_PROFILE_PRIORITY_PERCEPTIONS,
    validate_territorial_profile_location,
)


MATERIALIZATION_NOT_IMPLEMENTED = "MATERIALIZATION_NOT_IMPLEMENTED"
FICHA_1_PROFILE_INVALID = "FICHA_1_PROFILE_INVALID"
FICHA_1_IDENTITY_MISSING = "FICHA_1_IDENTITY_MISSING"
FICHA_1_IDENTITY_MISMATCH = "FICHA_1_IDENTITY_MISMATCH"
FICHA_1_IDENTITY_INACTIVE = "FICHA_1_IDENTITY_INACTIVE"
FICHA_1_TERRITORIAL_CONFLICT = "FICHA_1_TERRITORIAL_CONFLICT"
FICHA_1_PROFILE_STATE_CONFLICT = "FICHA_1_PROFILE_STATE_CONFLICT"


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


def _calculation_warnings(submission) -> tuple[ImportWarning, ...]:
    """
    PRE: submission has passed common normalized-payload validation.
    POST: returns only safe structured calculation warnings from normalized data.
    """
    raw_warnings = submission.normalized_payload.get("calculation_warnings", ())
    if not isinstance(raw_warnings, (list, tuple)):
        return ()
    warnings = []
    for raw_warning in raw_warnings:
        if not isinstance(raw_warning, dict):
            continue
        code = raw_warning.get("code")
        message = raw_warning.get("message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            warnings.append(ImportWarning(code=code, message=message))
    return tuple(warnings)


@dataclass(frozen=True)
class StubKoboImportHandler:
    form_type: KoboFormType

    def validate_for_import(self, *, submission) -> tuple[ImportWarning, ...]:
        """
        PRE: common import preconditions passed for the handler's form type.
        POST: exposes safe Ficha 11 warnings without treating them as blockers.
        """
        if self.form_type == KoboFormType.FICHA_11:
            return _calculation_warnings(submission)
        return ()

    def materialize(self, *, submission, actor) -> KoboMaterializationResult:
        """
        PRE: the supported form reached its explicit but unfinished handler.
        POST: blocks deterministically and never creates a target entity.
        """
        raise KoboImportBlocked(
            MATERIALIZATION_NOT_IMPLEMENTED,
            warnings=self.validate_for_import(submission=submission),
        )


KOBO_IMPORT_HANDLERS: Mapping[KoboFormType, KoboImportHandler] = MappingProxyType(
    {
        KoboFormType.FICHA_1: Ficha1TerritorialProfileImportHandler(),
        KoboFormType.FICHA_10: StubKoboImportHandler(KoboFormType.FICHA_10),
        KoboFormType.FICHA_11: StubKoboImportHandler(KoboFormType.FICHA_11),
    }
)


def get_import_handler(form_type: KoboFormType) -> KoboImportHandler:
    """
    PRE: form_type came from the closed registered-form dispatcher.
    POST: returns the exact supported handler; no generic fallback is available.
    """
    return KOBO_IMPORT_HANDLERS[form_type]
