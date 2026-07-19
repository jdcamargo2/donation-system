"""Pure territorial identifiers shared by the three supported Kobo fichas."""

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.errors import KoboNormalizationError


def normalize_nucleo_code(value: object) -> str:
    """
    PRE: value is Kobo-provided candidate identity data.
    POST: returns its trimmed uppercase string without changing internal symbols,
    or raises KoboNormalizationError without persistence or I/O.
    """
    if not isinstance(value, str):
        raise KoboNormalizationError("Nucleo code must be a string.")
    normalized_value = value.strip().upper()
    if not normalized_value:
        raise KoboNormalizationError("Nucleo code must not be empty.")
    return normalized_value


def normalize_pastoral_zone(value: object) -> PastoralZone:
    """
    PRE: value is a Kobo pastoral-zone code, never a presentation label.
    POST: returns one configured canonical PastoralZone or raises
    KoboNormalizationError without settings, queries, or persistence.
    """
    if not isinstance(value, str):
        raise KoboNormalizationError("Pastoral zone must be a string.")
    normalized_value = value.strip().lower()
    if not normalized_value:
        raise KoboNormalizationError("Pastoral zone must not be empty.")
    try:
        return PastoralZone(normalized_value)
    except ValueError as exc:
        raise KoboNormalizationError("Pastoral zone is not supported.") from exc
