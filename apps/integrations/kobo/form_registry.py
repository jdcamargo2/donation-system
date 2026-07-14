from dataclasses import dataclass

from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION


INITIAL_MAPPING_VERSION = "1"


@dataclass(frozen=True)
class KoboRegisteredForm:
    form_id: str
    title: str
    version: str
    normalizer_name: str
    mapping_version: str


_REGISTERED_FORMS = (
    KoboRegisteredForm(
        form_id=FICHA_01_FORM_ID,
        title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
        version=FICHA_01_VERSION,
        normalizer_name="normalize_ficha_01",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id=FICHA_10_FORM_ID,
        title="Ficha 10 - Microproyecto priorizado (depurada)",
        version=FICHA_10_VERSION,
        normalizer_name="normalize_ficha_10",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id=FICHA_11_FORM_ID,
        title="Ficha 11 - Matriz de priorización y semáforo (depurada)",
        version=FICHA_11_VERSION,
        normalizer_name="normalize_ficha_11",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
)


def get_registered_form(form_id: str, version: str) -> KoboRegisteredForm:
    """
    PRE: form_id and version are non-empty strings.
    POST: returns the exact registered definition without database access or
    state changes; raises KoboPayloadError when it does not exist.
    """
    for registered_form in _REGISTERED_FORMS:
        if (
            registered_form.form_id == form_id
            and registered_form.version == version
        ):
            return registered_form

    raise KoboPayloadError(
        f"Kobo form is not registered: form_id={form_id!r}, version={version!r}."
    )


def list_registered_forms() -> tuple[KoboRegisteredForm, ...]:
    """
    PRE: none.
    POST: returns every registered definition as an immutable tuple without
    database access.
    """
    return _REGISTERED_FORMS
