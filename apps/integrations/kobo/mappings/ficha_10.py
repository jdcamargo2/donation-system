from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.common import (
    parse_attachments,
    parse_multiselect,
    parse_optional_date,
    parse_optional_datetime,
    optional_string,
    require_non_empty_string,
)
from apps.integrations.kobo.territorial import normalize_nucleo_code


FICHA_10_FORM_ID = "ficha_10_microproyecto_priorizado_depurada"
FICHA_10_VERSION = "2026-07-12-depurada"

COMPONENTS = {
    "infrastructure", "health_psychosocial", "training", "livelihoods",
    "communication", "mixed",
}
BENEFICIARY_GROUPS = {
    "youth", "women", "adults", "unemployed", "entrepreneurs",
    "parish_volunteers", "mixed", "other",
}
ESTIMATED_COST_RANGES = {
    "under_1000", "1000_5000", "5000_15000", "15000_50000",
    "over_50000", "unknown",
}
IMPLEMENTATION_URGENCIES = {
    "immediate", "short_term", "medium_term", "follow_up", "unknown",
}
TECHNICAL_VIABILITIES = {
    "high", "medium", "low", "requires_design", "not_viable",
}


def _require_choice(raw_payload: Mapping[str, object], key: str, choices: set[str]) -> str:
    # PRE: key identifies a required XLSForm select-one value.
    # POST: returns a supported trimmed choice or raises KoboPayloadError.
    value = require_non_empty_string(raw_payload, key)
    if value not in choices:
        raise KoboPayloadError(f"Field {key!r} has an unsupported value.")
    return value


def _parse_beneficiary_groups(microproject: Mapping[str, object]) -> list[str]:
    # PRE: beneficiary_group is a required Kobo multiselect value.
    # POST: returns ordered, unique supported choices or raises KoboPayloadError.
    choices = parse_multiselect(microproject.get("beneficiary_group"))
    if not choices:
        raise KoboPayloadError("Field 'beneficiary_group' must not be empty.")
    normalized = []
    for choice in choices:
        if choice not in BENEFICIARY_GROUPS:
            raise KoboPayloadError("Field 'beneficiary_group' has an unsupported value.")
        if choice not in normalized:
            normalized.append(choice)
    return normalized


def normalize_ficha_10(
    raw_payload: Mapping[str, object],
    *,
    default_timezone: tzinfo,
) -> KoboSubmissionPayload:
    """
    PRE: raw_payload belongs to the active Ficha 10 contract and timezone is explicit.
    POST: returns an immutable normalized microproject proposal without persistence.
    """
    if not isinstance(raw_payload, Mapping):
        raise KoboPayloadError("Ficha 10 payload must be an object.")
    microproject = raw_payload.get("microproject")
    if not isinstance(microproject, Mapping) or not microproject:
        raise KoboPayloadError("Ficha 10 microproject section is required.")

    nucleo_code_original = raw_payload.get("nucleo_code")
    nucleo_code_normalized = normalize_nucleo_code(nucleo_code_original)
    normalized_payload = {
        "nucleo_code": nucleo_code_normalized,
        "nucleo_code_original": nucleo_code_original,
        "nucleo_code_normalized": nucleo_code_normalized,
        "microproject_name": require_non_empty_string(microproject, "microproject_name"),
        "component": _require_choice(microproject, "component", COMPONENTS),
        "problem_summary": require_non_empty_string(microproject, "problem_summary"),
        "specific_objective": require_non_empty_string(microproject, "specific_objective"),
        "beneficiary_group": _parse_beneficiary_groups(microproject),
        "main_activities": require_non_empty_string(microproject, "main_activities"),
        "estimated_cost_range": _require_choice(
            microproject, "estimated_cost_range", ESTIMATED_COST_RANGES
        ),
        "implementation_urgency": _require_choice(
            microproject, "implementation_urgency", IMPLEMENTATION_URGENCIES
        ),
        "technical_viability": _require_choice(
            microproject, "technical_viability", TECHNICAL_VIABILITIES
        ),
        "expected_result": require_non_empty_string(microproject, "expected_result"),
    }
    return KoboSubmissionPayload(
        external_id=require_non_empty_string(raw_payload, "_uuid"),
        form_id=FICHA_10_FORM_ID,
        form_version=FICHA_10_VERSION,
        pastoral_zone="",
        parish="",
        primary_community="",
        assessment_date=parse_optional_date(raw_payload.get("today")),
        submitted_at=parse_optional_datetime(
            raw_payload.get("_submission_time"), default_timezone=default_timezone
        ),
        submitted_by=optional_string(raw_payload, "_submitted_by"),
        device_id=optional_string(raw_payload, "deviceid"),
        normalized_payload=normalized_payload,
        attachments=parse_attachments(raw_payload.get("_attachments")),
    )
