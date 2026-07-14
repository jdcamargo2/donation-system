from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.common import (
    optional_string,
    parse_attachments,
    parse_optional_date,
    parse_optional_datetime,
    require_non_empty_string,
)


FICHA_11_FORM_ID = "ficha_11_priorizacion_semaforo_depurada"
FICHA_11_VERSION = "2026-07-12-depurada"

SCORE_FIELDS = (
    "physical_damage_score",
    "affected_families_score",
    "social_vulnerability_score",
    "services_interruption_score",
    "livelihood_loss_score",
    "parish_capacity_score",
    "territorial_accessibility_score",
    "allies_availability_score",
    "rapid_impact_score",
    "financial_viability_score",
)
FINAL_SEMAPHORES = {"red", "yellow", "green", "gray"}
FINAL_PRIORITIES = {"low", "medium", "high", "critical", "unknown"}


def _parse_score(scoring: Mapping[str, object], key: str) -> int:
    # PRE: key identifies one required Ficha 11 score in the scoring section.
    # POST: returns an integer from 1 to 5 or raises KoboPayloadError.
    value = scoring.get(key)
    if isinstance(value, bool) or value is None:
        raise KoboPayloadError(f"Field {key!r} must be an integer from 1 to 5.")
    if isinstance(value, int):
        score = value
    elif isinstance(value, str) and value.strip().isdigit():
        score = int(value.strip())
    else:
        raise KoboPayloadError(f"Field {key!r} must be an integer from 1 to 5.")
    if not 1 <= score <= 5:
        raise KoboPayloadError(f"Field {key!r} must be an integer from 1 to 5.")
    return score


def _calculate_suggested_semaphore(priority_total: int) -> str:
    # PRE: priority_total is the exact sum of ten scores from 1 to 5.
    # POST: returns the canonical suggested semaphore for that total.
    if priority_total >= 40:
        return "red"
    if priority_total >= 28:
        return "yellow"
    if priority_total >= 15:
        return "green"
    return "gray"


def _validate_optional_calculation(
    scoring: Mapping[str, object],
    *,
    key: str,
    expected_value: int | str,
) -> None:
    # PRE: expected_value is calculated by SIGEDON from validated scores.
    # POST: accepts absent values or an exact matching Kobo calculation only.
    value = scoring.get(key)
    if value is None or value == "":
        return
    if isinstance(expected_value, int):
        if isinstance(value, bool) or not (
            isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit())
        ):
            raise KoboPayloadError(f"Field {key!r} must match the calculated value.")
        value = int(value)
    if value != expected_value:
        raise KoboPayloadError(f"Field {key!r} must match the calculated value.")


def _require_choice(scoring: Mapping[str, object], key: str, choices: set[str]) -> str:
    # PRE: key identifies a required Ficha 11 select-one field in scoring.
    # POST: returns a supported trimmed value or raises KoboPayloadError.
    value = require_non_empty_string(scoring, key)
    if value not in choices:
        raise KoboPayloadError(f"Field {key!r} has an unsupported value.")
    return value


def normalize_ficha_11(
    raw_payload: Mapping[str, object],
    *,
    default_timezone: tzinfo,
) -> KoboSubmissionPayload:
    """
    PRE: raw_payload belongs to the exact active Ficha 11 contract.
    POST: returns a canonical, immutable prioritization assessment without I/O.
    """
    if not isinstance(raw_payload, Mapping):
        raise KoboPayloadError("Ficha 11 payload must be an object.")
    scoring = raw_payload.get("scoring")
    if not isinstance(scoring, Mapping) or not scoring:
        raise KoboPayloadError("Ficha 11 scoring section is required.")

    scores = {key: _parse_score(scoring, key) for key in SCORE_FIELDS}
    priority_total = sum(scores.values())
    suggested_semaphore = _calculate_suggested_semaphore(priority_total)
    _validate_optional_calculation(
        scoring,
        key="priority_total",
        expected_value=priority_total,
    )
    _validate_optional_calculation(
        scoring,
        key="suggested_semaphore",
        expected_value=suggested_semaphore,
    )
    normalized_payload = {
        "nucleo_code": require_non_empty_string(raw_payload, "nucleo_code"),
        **scores,
        "priority_total": priority_total,
        "suggested_semaphore": suggested_semaphore,
        "final_semaphore": _require_choice(
            scoring, "final_semaphore", FINAL_SEMAPHORES
        ),
        "final_priority": _require_choice(
            scoring, "final_priority", FINAL_PRIORITIES
        ),
        "priority_summary": require_non_empty_string(scoring, "priority_summary"),
        "linked_microprojects": optional_string(scoring, "linked_microprojects")
        or "",
    }
    return KoboSubmissionPayload(
        external_id=require_non_empty_string(raw_payload, "_uuid"),
        form_id=FICHA_11_FORM_ID,
        form_version=FICHA_11_VERSION,
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
