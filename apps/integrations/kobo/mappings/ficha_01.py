from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.common import (
    optional_string,
    parse_attachments,
    parse_geolocation,
    parse_integer,
    parse_optional_date,
    parse_optional_datetime,
    require_non_empty_string,
)


FICHA_01_FORM_ID = "ficha_1_identificacion_territorial_depurada"
FICHA_01_VERSION = "2026-07-12-depurada"

PASTORAL_ZONES = {"catia_la_mar", "centro", "este", "montana", "insular"}
ACCESS_DIFFICULTIES = {"yes", "no", "unknown"}
INITIAL_PRIORITY_PERCEPTIONS = {"low", "medium", "high", "critical", "unknown"}


def _parse_non_negative_integer(
    raw_payload: Mapping[str, object],
    key: str,
) -> int | None:
    # PRE: raw_payload contains optional Kobo integer data under key.
    # POST: returns a non-negative integer/None or raises KoboPayloadError.
    value = parse_integer(raw_payload.get(key), field_name=key)
    if value is not None and value < 0:
        raise KoboPayloadError(f"Field {key!r} must not be negative.")
    return value


def _require_choice(
    raw_payload: Mapping[str, object],
    key: str,
    choices: set[str],
) -> str:
    # PRE: key identifies a required XLSForm select-one value.
    # POST: returns a supported trimmed value or raises KoboPayloadError.
    value = require_non_empty_string(raw_payload, key)
    if value not in choices:
        raise KoboPayloadError(f"Field {key!r} has an unsupported value.")
    return value


def normalize_ficha_01(
    raw_payload: Mapping[str, object],
    *,
    default_timezone: tzinfo,
) -> KoboSubmissionPayload:
    """
    PRE: raw_payload belongs to Ficha 1, contains identification keys and _uuid,
    and default_timezone is supplied explicitly.
    POST: returns an immutable normalized contract without mutation, persistence,
    downloads, or attachment URLs inside normalized_payload.
    """
    if not isinstance(raw_payload, Mapping):
        raise KoboPayloadError("Ficha 1 payload must be an object.")

    external_id = require_non_empty_string(raw_payload, "_uuid")
    nucleo_code = require_non_empty_string(raw_payload, "nucleo_code")
    pastoral_zone = _require_choice(raw_payload, "pastoral_zone", PASTORAL_ZONES)
    parish = require_non_empty_string(raw_payload, "parish")
    community_sector = require_non_empty_string(raw_payload, "community_sector")
    estimated_households = _parse_non_negative_integer(
        raw_payload,
        "estimated_households",
    )

    normalized_payload = {
        "nucleo_code": nucleo_code,
        "location": parse_geolocation(raw_payload, location_key="location"),
        "parish_delegate": optional_string(raw_payload, "parish_delegate"),
        "contact_phone": optional_string(raw_payload, "contact_phone"),
        "main_informant_role": optional_string(raw_payload, "main_informant_role"),
        "communities_covered": optional_string(raw_payload, "communities_covered"),
        "estimated_households": estimated_households,
        "access_difficulties": _require_choice(
            raw_payload, "access_difficulties", ACCESS_DIFFICULTIES
        ),
        "access_difficulties_notes": optional_string(raw_payload, "access_difficulties_notes"),
        "initial_priority_perception": _require_choice(
            raw_payload,
            "initial_priority_perception",
            INITIAL_PRIORITY_PERCEPTIONS,
        ),
        "general_notes": optional_string(raw_payload, "general_notes"),
    }
    return KoboSubmissionPayload(
        external_id=external_id,
        form_id=FICHA_01_FORM_ID,
        form_version=FICHA_01_VERSION,
        pastoral_zone=pastoral_zone,
        parish=parish,
        primary_community=community_sector,
        assessment_date=parse_optional_date(raw_payload.get("today")),
        submitted_at=parse_optional_datetime(
            raw_payload.get("_submission_time"),
            default_timezone=default_timezone,
        ),
        submitted_by=optional_string(raw_payload, "_submitted_by"),
        device_id=optional_string(raw_payload, "deviceid"),
        normalized_payload=normalized_payload,
        attachments=parse_attachments(raw_payload.get("_attachments")),
    )
