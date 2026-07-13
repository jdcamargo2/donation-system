from dataclasses import dataclass

from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION


INITIAL_FORM_VERSION = "20260710"
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
        form_id="ficha_02_capacidad_parroquial",
        title="Ficha 02 - Capacidad parroquial",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_02",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_03_danos_seguridad_infraestructura",
        title="Ficha 03 - Daños, seguridad e infraestructura",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_03",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_04_servicios_infraestructura_abasto",
        title="Ficha 04 - Servicios, infraestructura y abasto",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_04",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_05_afectacion_humana_social",
        title="Ficha 05 - Afectación humana y social",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_05",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_06_salud_integral_psicosocial",
        title="Ficha 06 - Salud integral y psicosocial",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_06",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_07_formacion_tecnica_oficios",
        title="Ficha 07 - Formación técnica y oficios",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_07",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_08_emprendimiento_medios_vida",
        title="Ficha 08 - Emprendimiento y medios de vida",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_08",
        mapping_version=INITIAL_MAPPING_VERSION,
    ),
    KoboRegisteredForm(
        form_id="ficha_09_redes_informacion_transparencia",
        title="Ficha 09 - Redes, información y transparencia",
        version=INITIAL_FORM_VERSION,
        normalizer_name="normalize_ficha_09",
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
