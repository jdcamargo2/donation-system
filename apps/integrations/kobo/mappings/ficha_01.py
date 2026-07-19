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
from apps.integrations.kobo.territorial import normalize_nucleo_code
from apps.integrations.kobo.territorial import normalize_pastoral_zone


FICHA_01_FORM_ID = "ficha_1_identificacion_territorial_depurada"
FICHA_01_VERSION = "2026-07-12-depurada"

ACCESS_DIFFICULTIES = {"yes", "no", "unknown"}
INITIAL_PRIORITY_PERCEPTIONS = {"low", "medium", "high", "critical", "unknown"}


def _canonical_ficha_01_payload(raw_payload: Mapping[str, object]) -> dict[str, object]:
    """
    PRE: raw_payload has already passed the common Kobo shape adapter.
    POST: returns Ficha 1's canonical flat fields without mutating the input and
    rejects duplicate legacy and section values rather than overwriting either.
    """
    canonical = dict(raw_payload)
    sections = {
        "identification": {
            "parish",
            "location",
            "nucleo_code",
            "contact_phone",
            "pastoral_zone",
            "parish_delegate",
            "community_sector",
            "main_informant_role",
        },
        "territorial_summary": {
            "general_notes",
            "access_difficulties",
            "access_difficulties_notes",
            "communities_covered",
            "estimated_households",
            "initial_priority_perception",
        },
    }
    for section_name, field_names in sections.items():
        section = raw_payload.get(section_name)
        if section is None:
            continue
        if not isinstance(section, Mapping):
            raise KoboPayloadError("Ficha 1 section has an invalid structure.")
        for field_name in field_names:
            if field_name not in section:
                continue
            if field_name in canonical:
                raise KoboPayloadError("Ficha 1 field is defined more than once.")
            canonical[field_name] = section[field_name]
    return canonical


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

    raw_payload = _canonical_ficha_01_payload(raw_payload)

    external_id = require_non_empty_string(raw_payload, "_uuid")
    nucleo_code_original = raw_payload.get("nucleo_code")
    pastoral_zone_original = raw_payload.get("pastoral_zone")
    nucleo_code = normalize_nucleo_code(nucleo_code_original)
    pastoral_zone = normalize_pastoral_zone(pastoral_zone_original)
    parish = require_non_empty_string(raw_payload, "parish")
    community_sector = require_non_empty_string(raw_payload, "community_sector")
    estimated_households = _parse_non_negative_integer(
        raw_payload,
        "estimated_households",
    )

    normalized_payload = {
        "nucleo_code": nucleo_code,
        "nucleo_code_original": nucleo_code_original,
        "nucleo_code_normalized": nucleo_code,
        "pastoral_zone_original": pastoral_zone_original,
        "pastoral_zone_normalized": pastoral_zone.value,
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
        pastoral_zone=pastoral_zone.value,
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
