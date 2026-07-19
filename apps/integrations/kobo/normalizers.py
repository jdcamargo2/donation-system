from copy import deepcopy
from datetime import tzinfo
from typing import Mapping

from apps.integrations.kobo.contracts import KoboSubmissionPayload
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.form_registry import KoboFormType, resolve_form_type
from apps.integrations.kobo.mappings.ficha_01 import normalize_ficha_01
from apps.integrations.kobo.mappings.ficha_10 import normalize_ficha_10
from apps.integrations.kobo.mappings.ficha_11 import normalize_ficha_11


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
    form_type = resolve_form_type(form_id, form_version)
    if form_type == KoboFormType.FICHA_1:
        return normalize_ficha_01(
            adapted_payload,
            default_timezone=default_timezone,
        )
    if form_type == KoboFormType.FICHA_10:
        return normalize_ficha_10(adapted_payload, default_timezone=default_timezone)
    if form_type == KoboFormType.FICHA_11:
        return normalize_ficha_11(adapted_payload, default_timezone=default_timezone)
    raise AssertionError("Every registered Kobo form type must have a normalizer.")
