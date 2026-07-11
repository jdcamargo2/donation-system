from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import (
    AttachmentPrivacy,
    KoboAttachmentPayload,
    KoboSubmissionPayload,
)
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.common import (
    optional_string,
    parse_geolocation,
    parse_integer,
    parse_multiselect,
    parse_optional_date,
    parse_optional_datetime,
    require_non_empty_string,
)


FICHA_01_FORM_ID = "ficha_01_territorio"
FICHA_01_VERSION = "20260710"


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


def _parse_attachments(
    raw_attachments: object,
) -> tuple[KoboAttachmentPayload, ...]:
    # PRE: raw_attachments is optional Kobo attachment collection data.
    # POST: returns active internal-review descriptors or raises an indexed error.
    if raw_attachments is None:
        return ()
    if not isinstance(raw_attachments, list):
        raise KoboPayloadError("Field '_attachments' must be a list.")

    attachments = []
    for index, raw_attachment in enumerate(raw_attachments):
        if not isinstance(raw_attachment, dict):
            raise KoboPayloadError(f"Attachment {index} must be an object.")
        if raw_attachment.get("is_deleted") is True:
            continue
        try:
            attachment = KoboAttachmentPayload(
                field_name=require_non_empty_string(
                    raw_attachment,
                    "question_xpath",
                ),
                source_url=require_non_empty_string(raw_attachment, "download_url"),
                filename=optional_string(raw_attachment, "media_file_basename"),
                content_type=optional_string(raw_attachment, "mimetype"),
                privacy_level=AttachmentPrivacy.INTERNAL_REVIEW,
            )
        except KoboPayloadError as exc:
            raise KoboPayloadError(f"Attachment {index} is invalid: {exc}") from exc
        attachments.append(attachment)
    return tuple(attachments)


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
    pastoral_zone = require_non_empty_string(
        raw_payload,
        "identification/pastoral_zone",
    )
    parish = require_non_empty_string(raw_payload, "identification/parish")
    estimated_population = _parse_non_negative_integer(
        raw_payload,
        "territorial_profile/estimated_population",
    )
    estimated_households = _parse_non_negative_integer(
        raw_payload,
        "territorial_profile/estimated_households",
    )

    normalized_payload = {
        "survey_responsible": optional_string(
            raw_payload,
            "identification/survey_responsible",
        ),
        "parish_priest": optional_string(
            raw_payload,
            "identification/parish_priest",
        ),
        "contact_phone": optional_string(
            raw_payload,
            "identification/contact_phone",
        ),
        "official_parish_name": optional_string(
            raw_payload,
            "territorial_profile/official_parish_name",
        ),
        "church_advocation": optional_string(
            raw_payload,
            "territorial_profile/church_advocation",
        ),
        "estimated_population": estimated_population,
        "estimated_households": estimated_households,
        "location": parse_geolocation(raw_payload),
        "main_accessibility": optional_string(
            raw_payload,
            "territorial_profile/main_accessibility",
        ),
        "territory_type": parse_multiselect(
            raw_payload.get("territorial_profile/territory_type")
        ),
    }
    return KoboSubmissionPayload(
        external_id=external_id,
        form_id=FICHA_01_FORM_ID,
        form_version=FICHA_01_VERSION,
        pastoral_zone=pastoral_zone,
        parish=parish,
        primary_community=optional_string(
            raw_payload,
            "identification/primary_community",
        ),
        assessment_date=parse_optional_date(
            raw_payload.get("identification/assessment_date")
        ),
        submitted_at=parse_optional_datetime(
            raw_payload.get("_submission_time"),
            default_timezone=default_timezone,
        ),
        submitted_by=optional_string(raw_payload, "_submitted_by"),
        device_id=optional_string(raw_payload, "deviceid"),
        normalized_payload=normalized_payload,
        attachments=_parse_attachments(raw_payload.get("_attachments")),
    )
