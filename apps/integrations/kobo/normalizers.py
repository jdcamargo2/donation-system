from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import (
    FICHA_01_FORM_ID,
    FICHA_01_VERSION,
    normalize_ficha_01,
)
from apps.integrations.kobo.mappings.ficha_10 import (
    FICHA_10_FORM_ID,
    FICHA_10_VERSION,
    normalize_ficha_10,
)
from apps.integrations.kobo.mappings.ficha_11 import (
    FICHA_11_FORM_ID,
    FICHA_11_VERSION,
    normalize_ficha_11,
)


def normalize_submission(
    raw_payload: Mapping[str, object],
    *,
    form_id: str,
    form_version: str,
    default_timezone: tzinfo,
) -> KoboSubmissionPayload:
    """
    PRE: raw_payload is Kobo data and routing metadata is supplied explicitly.
    POST: returns the supported immutable contract or raises KoboPayloadError.
    """
    if form_id == FICHA_01_FORM_ID and form_version == FICHA_01_VERSION:
        return normalize_ficha_01(
            raw_payload,
            default_timezone=default_timezone,
        )
    if form_id == FICHA_10_FORM_ID and form_version == FICHA_10_VERSION:
        return normalize_ficha_10(raw_payload, default_timezone=default_timezone)
    if form_id == FICHA_11_FORM_ID and form_version == FICHA_11_VERSION:
        return normalize_ficha_11(raw_payload, default_timezone=default_timezone)
    raise KoboPayloadError(
        f"No Kobo normalizer for form_id={form_id!r}, version={form_version!r}."
    )
