from copy import deepcopy
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


def adapt_kobo_payload(raw_payload: Mapping[str, object]) -> dict[str, object]:
    """
    PRE: raw_payload is a JSON-object-like Kobo submission.
    POST: returns an independent nested representation of slash-separated keys,
    preserving root metadata and rejecting structural collisions safely.
    """
    if not isinstance(raw_payload, Mapping):
        raise KoboPayloadError("Kobo payload must be an object.")

    adapted: dict[str, object] = {}
    for raw_key, value in raw_payload.items():
        if not isinstance(raw_key, str):
            raise KoboPayloadError("Kobo payload keys must be strings.")
        path = raw_key.split("/")
        if not raw_key or any(not part for part in path):
            raise KoboPayloadError("Kobo payload contains an invalid field path.")

        target = adapted
        for part in path[:-1]:
            existing = target.get(part)
            if existing is None and part not in target:
                target[part] = {}
            elif not isinstance(existing, dict):
                raise KoboPayloadError("Kobo payload contains conflicting field paths.")
            target = target[part]

        leaf = path[-1]
        if leaf in target:
            raise KoboPayloadError("Kobo payload contains conflicting field paths.")
        target[leaf] = deepcopy(value)
    return adapted


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
    adapted_payload = adapt_kobo_payload(raw_payload)
    if form_id == FICHA_01_FORM_ID and form_version == FICHA_01_VERSION:
        return normalize_ficha_01(
            adapted_payload,
            default_timezone=default_timezone,
        )
    if form_id == FICHA_10_FORM_ID and form_version == FICHA_10_VERSION:
        return normalize_ficha_10(adapted_payload, default_timezone=default_timezone)
    if form_id == FICHA_11_FORM_ID and form_version == FICHA_11_VERSION:
        return normalize_ficha_11(adapted_payload, default_timezone=default_timezone)
    raise KoboPayloadError(
        f"No Kobo normalizer for form_id={form_id!r}, version={form_version!r}."
    )
