from datetime import date, datetime, tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import AttachmentPrivacy, KoboAttachmentPayload
from apps.integrations.kobo.errors import KoboPayloadError


def require_non_empty_string(payload: Mapping[str, object], key: str) -> str:
    """
    PRE: payload is a mapping and key identifies a required string field.
    POST: returns the trimmed non-empty string or raises KoboPayloadError.
    """
    value = payload.get(key)
    if not isinstance(value, str):
        raise KoboPayloadError(f"Field {key!r} must be a string.")
    cleaned_value = value.strip()
    if not cleaned_value:
        raise KoboPayloadError(f"Field {key!r} must not be empty.")
    return cleaned_value


def optional_string(payload: Mapping[str, object], key: str) -> str | None:
    """
    PRE: payload is a mapping and key identifies an optional string field.
    POST: returns a trimmed string, None for missing/empty, or raises on bad type.
    """
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise KoboPayloadError(f"Field {key!r} must be a string or null.")
    return value.strip() or None


def parse_optional_date(value: object) -> date | None:
    """
    PRE: value is None, empty, a date, or an ISO 8601 date string.
    POST: returns a date or raises KoboPayloadError with the invalid field reason.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise KoboPayloadError("Field 'date' must be an ISO 8601 date or null.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise KoboPayloadError("Field 'date' is not a valid ISO 8601 date.") from exc


def parse_optional_datetime(
    value: object,
    *,
    default_timezone: tzinfo | None = None,
) -> datetime | None:
    """
    PRE: value is optional ISO 8601 data; naive values require default_timezone.
    POST: returns an aware datetime or raises KoboPayloadError without settings reads.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed_value = value
    elif isinstance(value, str):
        try:
            parsed_value = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise KoboPayloadError(
                "Field 'datetime' is not a valid ISO 8601 datetime."
            ) from exc
    else:
        raise KoboPayloadError(
            "Field 'datetime' must be an ISO 8601 datetime or null."
        )

    if parsed_value.tzinfo is None or parsed_value.utcoffset() is None:
        if default_timezone is None:
            raise KoboPayloadError(
                "Field 'datetime' has no offset and requires a default timezone."
            )
        parsed_value = parsed_value.replace(tzinfo=default_timezone)
    return parsed_value


def parse_integer(value: object, *, field_name: str) -> int | None:
    """
    PRE: field_name names an optional integer field.
    POST: returns an integer/None or raises KoboPayloadError without coercing junk.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise KoboPayloadError(f"Field {field_name!r} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned_value = value.strip()
        if not cleaned_value:
            return None
        try:
            return int(cleaned_value)
        except ValueError as exc:
            raise KoboPayloadError(
                f"Field {field_name!r} must be an integer."
            ) from exc
    raise KoboPayloadError(f"Field {field_name!r} must be an integer.")


def parse_multiselect(value: object) -> tuple[str, ...]:
    """
    PRE: value is optional Kobo multiselect text or a sequence of strings.
    POST: returns cleaned choices in input order or raises KoboPayloadError.
    """
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(choice for choice in value.strip().split() if choice)
    if isinstance(value, (list, tuple)):
        choices = []
        for index, choice in enumerate(value):
            if not isinstance(choice, str) or not choice.strip():
                raise KoboPayloadError(
                    f"Field 'multiselect' item {index} must be a non-empty string."
                )
            choices.append(choice.strip())
        return tuple(choices)
    raise KoboPayloadError("Field 'multiselect' must be text or a string sequence.")


def parse_attachments(raw_attachments: object) -> tuple[KoboAttachmentPayload, ...]:
    """
    PRE: raw_attachments is optional Kobo attachment descriptor data.
    POST: returns active INTERNAL_REVIEW descriptors or raises an indexed error.
    """
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
                field_name=require_non_empty_string(raw_attachment, "question_xpath"),
                source_url=require_non_empty_string(raw_attachment, "download_url"),
                filename=optional_string(raw_attachment, "media_file_basename"),
                content_type=optional_string(raw_attachment, "mimetype"),
                privacy_level=AttachmentPrivacy.INTERNAL_REVIEW,
            )
        except KoboPayloadError as exc:
            raise KoboPayloadError(f"Attachment {index} is invalid: {exc}") from exc
        attachments.append(attachment)
    return tuple(attachments)


def _parse_coordinate(value: object, *, field_name: str) -> float | None:
    # PRE: value is optional numeric coordinate data.
    # POST: returns a float/None or raises KoboPayloadError naming the field.
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise KoboPayloadError(f"Field {field_name!r} must be numeric.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise KoboPayloadError(f"Field {field_name!r} must be numeric.") from exc


def parse_geolocation(
    payload: Mapping[str, object],
    *,
    location_key: str = "territorial_profile/location",
) -> dict | None:
    """
    PRE: payload may contain _geolocation or location_key geolocation text.
    POST: returns validated coordinate components, None, or raises KoboPayloadError.
    """
    raw_geolocation = payload.get("_geolocation")
    if raw_geolocation not in (None, [], ""):
        if not isinstance(raw_geolocation, (list, tuple)) or len(raw_geolocation) < 2:
            raise KoboPayloadError(
                "Field '_geolocation' must contain latitude and longitude."
            )
        components = tuple(raw_geolocation[:4])
        field_name = "_geolocation"
    else:
        raw_location = payload.get(location_key)
        if raw_location is None or raw_location == "":
            return None
        if not isinstance(raw_location, str):
            raise KoboPayloadError(
                f"Field {location_key!r} must be geolocation text."
            )
        components = tuple(raw_location.strip().split())
        field_name = location_key
        if len(components) < 2 or len(components) > 4:
            raise KoboPayloadError(
                f"Field {field_name!r} must contain two to four coordinates."
            )

    padded_components = components + (None,) * (4 - len(components))
    latitude = _parse_coordinate(padded_components[0], field_name=f"{field_name}.latitude")
    longitude = _parse_coordinate(
        padded_components[1], field_name=f"{field_name}.longitude"
    )
    altitude = _parse_coordinate(
        padded_components[2], field_name=f"{field_name}.altitude"
    )
    accuracy = _parse_coordinate(
        padded_components[3], field_name=f"{field_name}.accuracy"
    )
    if latitude is None or not -90 <= latitude <= 90:
        raise KoboPayloadError(f"Field {field_name!r} latitude is outside -90..90.")
    if longitude is None or not -180 <= longitude <= 180:
        raise KoboPayloadError(
            f"Field {field_name!r} longitude is outside -180..180."
        )
    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude": altitude,
        "accuracy": accuracy,
    }
