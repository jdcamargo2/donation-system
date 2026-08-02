"""Presentation helpers for the operator-facing Kobo submission review screen.

Stored codes, payloads and service contracts stay unchanged; this module only
shapes Spanish labels, values and history copy for templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import (
    FICHA_11_FORM_ID,
    FICHA_11_VERSION,
    SCORE_FIELDS,
)
from apps.integrations.kobo.models import KoboAttachment, KoboSubmission
from apps.integrations.kobo.presentation import (
    FORM_ROLE_TITLES,
    KOBO_PRESENTATION_LABELS,
    pastoral_zone_label,
    presentation_label,
)


SENSITIVE_PAYLOAD_KEYS = frozenset(
    {"parish_delegate", "contact_phone", "main_informant_role"}
)

SUBMISSION_STATUS_LABELS = {
    KoboSubmission.Status.READY_FOR_REVIEW: "Incidencia",
    KoboSubmission.Status.APPROVED_FOR_IMPORT: "Aprobado para importar",
    KoboSubmission.Status.IMPORTED: "Importado",
    KoboSubmission.Status.REJECTED: "Rechazado",
    KoboSubmission.Status.PROCESSING_FAILED: "Error de procesamiento",
    KoboSubmission.Status.VALIDATION_FAILED: "Error de validación",
    KoboSubmission.Status.RECEIVED: "Recibido",
}

CHOICE_VALUE_LABELS = {
    "livelihoods": "Medios de vida",
    "infrastructure": "Infraestructura",
    "health_psychosocial": "Salud y atención psicosocial",
    "training": "Formación",
    "communication": "Comunicación",
    "mixed": "Mixto",
    "youth": "Jóvenes",
    "women": "Mujeres",
    "adults": "Adultos",
    "unemployed": "Personas desempleadas",
    "entrepreneurs": "Emprendedores",
    "parish_volunteers": "Voluntariado parroquial",
    "other": "Otro",
    "under_1000": "Menos de 1.000 USD",
    "1000_5000": "Entre 1.000 y 5.000 USD",
    "5000_15000": "Entre 5.000 y 15.000 USD",
    "15000_50000": "Entre 15.000 y 50.000 USD",
    "over_50000": "Más de 50.000 USD",
    "immediate": "Inmediata",
    "short_term": "Corto plazo",
    "medium_term": "Mediano plazo",
    "follow_up": "Seguimiento",
    "high": "Alta",
    "medium": "Media",
    "low": "Baja",
    "critical": "Crítica",
    "requires_design": "Requiere diseño",
    "not_viable": "No viable",
    "yes": "Sí",
    "no": "No",
    "unknown": "Sin determinar",
    "red": "Rojo",
    "yellow": "Amarillo",
    "green": "Verde",
    "gray": "Gris",
}

EVENT_PRESENTATIONS = {
    "webhook_received": (
        "Formulario recibido desde KoboToolbox",
        "El envío llegó correctamente y quedó registrado para procesamiento.",
    ),
    "normalized": (
        "Información procesada correctamente",
        "Los datos del formulario se normalizaron y quedaron listos para importación automática.",
    ),
    "project_assigned": (
        "Formulario asociado al proyecto",
        "Se asignó el proyecto correspondiente según la zona pastoral.",
    ),
    "incomplete": (
        "Devuelto para revisión",
        "Histórico: se solicitó completar o corregir información antes de continuar.",
    ),
    "restored": (
        "Restaurado al pipeline",
        "Histórico: el formulario volvió al estado interno de procesamiento.",
    ),
    "auto_approved": (
        "Aprobado automáticamente",
        "El sistema autorizó la importación en el pipeline automático.",
    ),
    "auto_imported": (
        "Importado automáticamente",
        "La información quedó registrada en el proyecto por el procesamiento automático.",
    ),
    "imported": (
        "Formulario importado",
        "La información quedó registrada en el proyecto.",
    ),
    "remote_update_detected": (
        "Cambio recibido que requiere inspección",
        "KoboToolbox envió una actualización que debe inspeccionarse antes de aplicarse.",
    ),
    "REMOTE_UPDATE_DETECTED": (
        "Cambio recibido que requiere inspección",
        "KoboToolbox envió una actualización que debe inspeccionarse antes de aplicarse.",
    ),
    KoboSubmission.Status.APPROVED_FOR_IMPORT: (
        "Aprobado para importar",
        "Estado transitorio de autorización automática hacia la importación.",
    ),
    KoboSubmission.Status.REJECTED: (
        "Formulario rechazado",
        "Histórico: el formulario quedó rechazado y no se importó.",
    ),
    "test_submission": (
        "Rechazado: envío de prueba",
        "El formulario se marcó como una prueba y no se importará.",
    ),
    "duplicate": (
        "Rechazado: duplicado",
        "El formulario se identificó como duplicado.",
    ),
    "incorrect_data": (
        "Rechazado: datos incorrectos",
        "La información presentada no es válida para importar.",
    ),
    "wrong_project": (
        "Rechazado: proyecto incorrecto",
        "El formulario no corresponde al proyecto esperado.",
    ),
    "other": (
        "Rechazado",
        "Histórico: el formulario se rechazó por un motivo registrado.",
    ),
}

FICHA_01_FIELDS = (
    ("nucleo_code", "Código del núcleo", "text"),
    ("communities_covered", "Comunidades cubiertas", "text"),
    ("estimated_households", "Hogares estimados", "text"),
    ("access_difficulties", "Dificultades de acceso", "choice"),
    ("access_difficulties_notes", "Notas sobre el acceso", "text"),
    ("initial_priority_perception", "Percepción inicial de prioridad", "choice"),
    ("general_notes", "Notas generales", "text"),
)

FICHA_10_FIELDS = (
    ("microproject_name", "Nombre del microproyecto", "text"),
    ("component", "Componente", "choice"),
    ("problem_summary", "Problema identificado", "text"),
    ("specific_objective", "Objetivo específico", "text"),
    ("beneficiary_group", "Grupo beneficiario", "multi_choice"),
    ("main_activities", "Actividades principales", "text"),
    ("estimated_cost_range", "Costo estimado", "choice"),
    ("technical_viability", "Viabilidad técnica", "choice"),
    ("implementation_urgency", "Urgencia de implementación", "choice"),
    ("expected_result", "Resultado esperado", "text"),
)

FICHA_11_FIELDS = (
    ("nucleo_code", "Código del núcleo", "text"),
    ("physical_damage_score", "Nivel de daño físico", "text"),
    ("affected_families_score", "Familias afectadas", "text"),
    ("social_vulnerability_score", "Vulnerabilidad social", "text"),
    ("services_interruption_score", "Interrupción de servicios básicos", "text"),
    ("livelihood_loss_score", "Pérdida de medios de vida", "text"),
    ("parish_capacity_score", "Capacidad parroquial disponible", "text"),
    ("territorial_accessibility_score", "Accesibilidad territorial", "text"),
    ("allies_availability_score", "Existencia de aliados", "text"),
    ("rapid_impact_score", "Potencial de impacto rápido", "text"),
    ("financial_viability_score", "Viabilidad financiera", "text"),
    ("priority_total", "Puntaje total", "text"),
    ("suggested_semaphore", "Semáforo sugerido", "choice"),
    ("final_semaphore", "Semáforo final validado", "choice"),
    ("final_priority", "Prioridad final de intervención", "choice"),
    ("priority_summary", "Síntesis de decisión", "text"),
    ("linked_microprojects", "Microproyectos vinculados", "text"),
)

CONTACT_FIELDS = (
    ("parish_delegate", "Delegado parroquial"),
    ("contact_phone", "Teléfono de contacto"),
    ("main_informant_role", "Rol del informante principal"),
)

EMPTY_DISPLAY = "—"
LOCATION_UNAVAILABLE = "No disponible"


@dataclass(frozen=True)
class PresentedField:
    key: str
    label: str
    value: str
    order: int
    format: str


@dataclass(frozen=True)
class PresentedEvent:
    title: str
    explanation: str
    created_at: Any
    actor: Any | None = None


def submission_status_label(status: str) -> str:
    return SUBMISSION_STATUS_LABELS.get(status, presentation_label(status) or status)


def choice_value_label(value) -> str:
    """
    PRE: value is a stored choice code, boolean, or empty placeholder.
    POST: returns a Spanish label; never exposes raw snake_case when mapped or
    when a safe humanization fallback applies.
    """
    if value is None or value == "":
        return EMPTY_DISPLAY
    if isinstance(value, bool):
        return "Sí" if value else "No"
    text = str(value).strip()
    if not text:
        return EMPTY_DISPLAY
    if text in CHOICE_VALUE_LABELS:
        return CHOICE_VALUE_LABELS[text]
    labeled = KOBO_PRESENTATION_LABELS.get(text)
    if labeled is not None:
        return labeled
    if "_" in text and text.replace("_", "").isalnum():
        return text.replace("_", " ").capitalize()
    return text


def format_presented_value(value, *, format_name: str) -> str:
    if value is None or value == "":
        return EMPTY_DISPLAY
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if format_name == "multi_choice":
        if isinstance(value, (list, tuple)):
            labels = [
                choice_value_label(item) for item in value if item not in (None, "")
            ]
            return ", ".join(labels) if labels else EMPTY_DISPLAY
        return choice_value_label(value)
    if format_name == "choice":
        return choice_value_label(value)
    if isinstance(value, dict):
        return EMPTY_DISPLAY
    if isinstance(value, (list, tuple)):
        labels = [
            format_presented_value(item, format_name="text")
            for item in value
            if item not in (None, "")
        ]
        return ", ".join(labels) if labels else EMPTY_DISPLAY
    return str(value)


def form_identity(submission) -> tuple[str, str]:
    """
    PRE: submission has form_definition and optional asset loaded.
    POST: returns (short ficha title, descriptive subtitle) for the review header.
    """
    form_id = submission.form_definition.form_id
    version = submission.form_definition.version
    if form_id == FICHA_01_FORM_ID and version == FICHA_01_VERSION:
        return ("Ficha 1", "Registro territorial")
    if form_id == FICHA_10_FORM_ID and version == FICHA_10_VERSION:
        return ("Ficha 10", "Microproyecto priorizado")
    if form_id == FICHA_11_FORM_ID and version == FICHA_11_VERSION:
        return ("Ficha 11", "Evaluación de prioridad")
    form_role = getattr(getattr(submission, "asset", None), "form_role", None)
    if form_role:
        titled = FORM_ROLE_TITLES.get(form_role)
        if titled:
            return titled
    return ("Formulario", submission.form_definition.title or form_id)


def _field_specs_for(submission) -> tuple[tuple[str, str, str], ...]:
    form_id = submission.form_definition.form_id
    version = submission.form_definition.version
    if form_id == FICHA_01_FORM_ID and version == FICHA_01_VERSION:
        return FICHA_01_FIELDS
    if form_id == FICHA_10_FORM_ID and version == FICHA_10_VERSION:
        return FICHA_10_FIELDS
    if form_id == FICHA_11_FORM_ID and version == FICHA_11_VERSION:
        return FICHA_11_FIELDS
    return ()


def present_submission_fields(submission) -> list[PresentedField]:
    """
    PRE: submission carries a normalized payload for a supported or unknown ficha.
    POST: returns ordered Spanish-labelled fields without raw internal-only keys.
    """
    payload = submission.normalized_payload or {}
    specs = _field_specs_for(submission)
    rows: list[PresentedField] = []
    for order, (key, label, format_name) in enumerate(specs, start=1):
        rows.append(
            PresentedField(
                key=key,
                label=label,
                value=format_presented_value(payload.get(key), format_name=format_name),
                order=order,
                format=format_name,
            )
        )
    if rows:
        return rows
    # Unsupported definition: show non-sensitive payload with humanized keys only
    # when no ficha presenter applies, still avoiding raw dumps of technical ids.
    order = 1
    for key, value in payload.items():
        if key in SENSITIVE_PAYLOAD_KEYS or key.endswith("_original"):
            continue
        rows.append(
            PresentedField(
                key=key,
                label=key.replace("_", " ").capitalize(),
                value=format_presented_value(value, format_name="text"),
                order=order,
                format="text",
            )
        )
        order += 1
    return rows


def present_contact_fields(submission) -> list[PresentedField]:
    payload = submission.normalized_payload or {}
    rows: list[PresentedField] = []
    for order, (key, label) in enumerate(CONTACT_FIELDS, start=1):
        value = payload.get(key)
        if value in (None, ""):
            continue
        rows.append(
            PresentedField(
                key=key,
                label=label,
                value=str(value),
                order=order,
                format="text",
            )
        )
    return rows


def present_processing_events(events: Iterable) -> list[PresentedEvent]:
    """
    PRE: events are KoboProcessingEvent instances ordered for display.
    POST: returns operator-facing history rows without stage/code as the title.
    """
    presented: list[PresentedEvent] = []
    for event in events:
        code = (event.code or "").strip()
        title, explanation = EVENT_PRESENTATIONS.get(
            code,
            (
                event.message or "Actualización del formulario",
                "Se registró un evento operativo sobre este envío.",
            ),
        )
        if code and code not in EVENT_PRESENTATIONS and event.message:
            explanation = event.message
        presented.append(
            PresentedEvent(
                title=title,
                explanation=explanation,
                created_at=event.created_at,
                actor=None,
            )
        )
    return presented


def attachment_status_label(status: str) -> str:
    labels = {
        KoboAttachment.Status.PENDING: "Pendiente",
        KoboAttachment.Status.DOWNLOADED: "Disponible",
        KoboAttachment.Status.FAILED: "Error",
        KoboAttachment.Status.SKIPPED: "Omitido",
    }
    return labels.get(status, presentation_label(status) or status)


def should_show_retry_normalization(submission) -> bool:
    return submission.status in {
        KoboSubmission.Status.VALIDATION_FAILED,
        KoboSubmission.Status.PROCESSING_FAILED,
        KoboSubmission.Status.RECEIVED,
    }


def should_show_retry_attachments(submission, attachments) -> bool:
    if not attachments:
        return False
    return any(
        attachment.status
        in {
            KoboAttachment.Status.PENDING,
            KoboAttachment.Status.FAILED,
        }
        for attachment in attachments
    )


def territorial_summary_rows(submission) -> list[tuple[str, str]]:
    zone = submission.pastoral_zone
    zone_label = pastoral_zone_label(zone) if zone else EMPTY_DISPLAY
    project = submission.project
    project_label = str(project) if project else ""
    rows = [
        ("Zona pastoral", zone_label),
        ("Parroquia", submission.parish or EMPTY_DISPLAY),
        ("Comunidad", submission.primary_community or EMPTY_DISPLAY),
        ("Código del núcleo", submission.nucleo_code_normalized or EMPTY_DISPLAY),
        ("Fecha de evaluación", str(submission.assessment_date or EMPTY_DISPLAY)),
        ("Recibido", str(submission.received_at)),
    ]
    if project_label:
        rows.append(("Proyecto asociado", project_label))
    return rows


TECHNICAL_METADATA_FIELDS = (
    ("submitted_by", "Enviado por", "_submitted_by"),
    ("device_id", "ID del dispositivo", "deviceid"),
)

FICHA_11_SCORE_LABELS = {
    "physical_damage_score": "Nivel de daño físico",
    "affected_families_score": "Familias afectadas",
    "social_vulnerability_score": "Vulnerabilidad social",
    "services_interruption_score": "Interrupción de servicios básicos",
    "livelihood_loss_score": "Pérdida de medios de vida",
    "parish_capacity_score": "Capacidad parroquial disponible",
    "territorial_accessibility_score": "Accesibilidad territorial",
    "allies_availability_score": "Existencia de aliados",
    "rapid_impact_score": "Potencial de impacto rápido",
    "financial_viability_score": "Viabilidad financiera",
}


def _display_or_empty(value) -> str:
    if value is None or value == "":
        return EMPTY_DISPLAY
    return str(value)


def _presentation_item(
    label: str,
    value: str,
    *,
    value_list: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"label": label, "value": value}
    if value_list is not None:
        item["value_list"] = value_list
    return item


def _compact_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("value") not in (None, "", EMPTY_DISPLAY)
        or item.get("value_list")
    ]


def _is_ficha_01(submission) -> bool:
    form_definition = submission.form_definition
    return (
        form_definition.form_id == FICHA_01_FORM_ID
        and form_definition.version == FICHA_01_VERSION
    )


def _is_ficha_10(submission) -> bool:
    form_definition = submission.form_definition
    return (
        form_definition.form_id == FICHA_10_FORM_ID
        and form_definition.version == FICHA_10_VERSION
    )


def _is_ficha_11(submission) -> bool:
    form_definition = submission.form_definition
    return (
        form_definition.form_id == FICHA_11_FORM_ID
        and form_definition.version == FICHA_11_VERSION
    )


def _nucleo_code_display(submission) -> str:
    payload = submission.normalized_payload or {}
    return _display_or_empty(
        submission.nucleo_code_normalized or payload.get("nucleo_code")
    )


def _normalize_linked_items(value) -> list[str]:
    """
    PRE: value is a linked-microprojects snapshot (string, sequence, or empty).
    POST: returns ordered non-empty display strings without mutating input.
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if item not in (None, "")]
        return [item for item in items if item]
    text = str(value).strip()
    if not text:
        return []
    if any(separator in text for separator in (",", ";", "\n")):
        parts: list[str] = []
        for chunk in text.replace(";", ",").replace("\n", ",").split(","):
            item = chunk.strip()
            if item and item not in parts:
                parts.append(item)
        return parts
    return [text]


def format_linked_collection(
    value,
    *,
    label: str = "Microproyectos vinculados",
) -> dict[str, Any]:
    """
    PRE: value is a stored linked-microprojects snapshot of any supported shape.
    POST: returns a presentation item payload: single text, multi value_list, or —.
    """
    items = _normalize_linked_items(value)
    if not items:
        return _presentation_item(label, EMPTY_DISPLAY)
    if len(items) == 1:
        return _presentation_item(label, items[0])
    return _presentation_item(label, EMPTY_DISPLAY, value_list=items)


def _format_coordinate_component(value) -> str:
    """
    PRE: value is a numeric coordinate component or an unusable placeholder.
    POST: returns a plain decimal string, or LOCATION_UNAVAILABLE when absent.
    """
    if value is None or value == "":
        return LOCATION_UNAVAILABLE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return LOCATION_UNAVAILABLE
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def get_valid_coordinates(location) -> tuple[float, float] | None:
    """
    PRE: location may be a dict, None, or malformed presentation input.
    POST: returns (latitude, longitude) as floats when both are finite and
    in range; otherwise None. Does not mutate input or raise.
    """
    if not isinstance(location, dict):
        return None
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        return None
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    if not isfinite(latitude) or not isfinite(longitude):
        return None
    if not -90 <= latitude <= 90:
        return None
    if not -180 <= longitude <= 180:
        return None
    return (float(latitude), float(longitude))


def build_openstreetmap_map_url(location) -> str | None:
    """
    PRE: location may be a dict, None, or malformed presentation input.
    POST: returns a coordinates-only OpenStreetMap URL at zoom 15, or None.
    Makes no network request and includes no project/community identifiers.
    """
    coordinates = get_valid_coordinates(location)
    if coordinates is None:
        return None
    latitude, longitude = coordinates
    lat_text = _format_coordinate_component(latitude)
    lon_text = _format_coordinate_component(longitude)
    return (
        f"https://www.openstreetmap.org/?mlat={lat_text}&mlon={lon_text}"
        f"#map=15/{lat_text}/{lon_text}"
    )


def format_location(location) -> dict[str, str]:
    """
    PRE: location may be a normalized geolocation dict, None, or malformed input.
    POST: returns individually formatted Spanish location values without repr/JSON.
    """
    if not isinstance(location, dict):
        return {
            "latitude": LOCATION_UNAVAILABLE,
            "longitude": LOCATION_UNAVAILABLE,
            "accuracy": LOCATION_UNAVAILABLE,
            "altitude": LOCATION_UNAVAILABLE,
        }
    return {
        "latitude": _format_coordinate_component(location.get("latitude")),
        "longitude": _format_coordinate_component(location.get("longitude")),
        "accuracy": _format_coordinate_component(location.get("accuracy")),
        "altitude": _format_coordinate_component(location.get("altitude")),
    }


def history_submission_detail_title(submission) -> str:
    """
    PRE: submission has its form definition loaded.
    POST: returns a concise history-detail title without technical suffixes.
    """
    if _is_ficha_11(submission):
        return "Ficha 11 · Evaluación de priorización"
    if _is_ficha_10(submission):
        return "Ficha 10 · Microproyecto priorizado"
    if _is_ficha_01(submission):
        return "Ficha 1 · Identificación territorial"
    return "Historial Kobo"


def history_submission_detail_rows(submission) -> tuple[tuple[str, Any], ...]:
    """
    PRE: submission is a historical imported/rejected Kobo record.
    POST: returns labelled Ficha 10/11 fields with Spanish values; Ficha 1 none.
    """
    payload = submission.normalized_payload or {}
    if _is_ficha_11(submission):
        score_rows = tuple(
            (
                FICHA_11_SCORE_LABELS[key],
                format_presented_value(payload.get(key), format_name="text"),
            )
            for key in SCORE_FIELDS
        )
        linked = format_linked_collection(payload.get("linked_microprojects"))
        linked_value = (
            ", ".join(linked["value_list"])
            if linked.get("value_list")
            else linked["value"]
        )
        return score_rows + (
            (
                "Puntaje total",
                format_presented_value(payload.get("priority_total"), format_name="text"),
            ),
            (
                "Semáforo sugerido",
                format_presented_value(
                    payload.get("suggested_semaphore"), format_name="choice"
                ),
            ),
            (
                "Semáforo final",
                format_presented_value(
                    payload.get("final_semaphore"), format_name="choice"
                ),
            ),
            (
                "Prioridad final",
                format_presented_value(
                    payload.get("final_priority"), format_name="choice"
                ),
            ),
            (
                "Resumen de priorización",
                format_presented_value(
                    payload.get("priority_summary"), format_name="text"
                ),
            ),
            ("Microproyectos vinculados", linked_value),
        )
    if not _is_ficha_10(submission):
        return ()
    return (
        ("Código del Núcleo Vital", _nucleo_code_display(submission)),
        (
            "Nombre del microproyecto",
            format_presented_value(payload.get("microproject_name"), format_name="text"),
        ),
        (
            "Componente",
            format_presented_value(payload.get("component"), format_name="choice"),
        ),
        (
            "Resumen del problema",
            format_presented_value(payload.get("problem_summary"), format_name="text"),
        ),
        (
            "Objetivo específico",
            format_presented_value(payload.get("specific_objective"), format_name="text"),
        ),
        (
            "Grupo beneficiario",
            format_presented_value(
                payload.get("beneficiary_group"), format_name="multi_choice"
            ),
        ),
        (
            "Actividades principales",
            format_presented_value(payload.get("main_activities"), format_name="text"),
        ),
        (
            "Rango de costo",
            format_presented_value(
                payload.get("estimated_cost_range"), format_name="choice"
            ),
        ),
        (
            "Urgencia",
            format_presented_value(
                payload.get("implementation_urgency"), format_name="choice"
            ),
        ),
        (
            "Viabilidad técnica",
            format_presented_value(
                payload.get("technical_viability"), format_name="choice"
            ),
        ),
        (
            "Resultado esperado",
            format_presented_value(payload.get("expected_result"), format_name="text"),
        ),
    )





def _imported_page_title(submission) -> str:
    if _is_ficha_01(submission):
        return "Ficha 1 · Identificación territorial"
    if _is_ficha_10(submission):
        return "Ficha 10 · Microproyecto priorizado"
    if _is_ficha_11(submission):
        return "Ficha 11 · Evaluación de priorización"
    return "Levantamiento importado"


def _imported_page_subtitle(submission) -> str:
    project = submission.project
    parts: list[str] = []
    if project is not None and getattr(project, "code", None):
        parts.append(str(project.code))

    if _is_ficha_10(submission):
        payload = submission.normalized_payload or {}
        nucleo = submission.nucleo_code_normalized or payload.get("nucleo_code")
        if nucleo:
            parts.append(str(nucleo))
        name = payload.get("microproject_name")
        if name:
            parts.append(str(name))
        return " · ".join(parts) if parts else EMPTY_DISPLAY

    if _is_ficha_11(submission):
        payload = submission.normalized_payload or {}
        nucleo = submission.nucleo_code_normalized or payload.get("nucleo_code")
        if nucleo:
            parts.append(str(nucleo))
        if submission.assessment_date:
            parts.append(str(submission.assessment_date))
        return " · ".join(parts) if parts else EMPTY_DISPLAY

    zone = submission.pastoral_zone
    if zone:
        parts.append(pastoral_zone_label(zone))
    subtitle = " · ".join(parts) if parts else EMPTY_DISPLAY
    extras: list[str] = []
    if submission.parish:
        extras.append(str(submission.parish))
    if submission.primary_community:
        extras.append(str(submission.primary_community))
    if extras and len(subtitle) + len(" · ".join(extras)) <= 90:
        subtitle = f"{subtitle} · {' · '.join(extras)}"
    return subtitle


def _ficha_1_summary(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    zone = (
        pastoral_zone_label(submission.pastoral_zone)
        if submission.pastoral_zone
        else EMPTY_DISPLAY
    )
    return _compact_summary(
        [
            _presentation_item("Código del Núcleo Vital", _nucleo_code_display(submission)),
            _presentation_item("Zona pastoral", zone),
            _presentation_item(
                "Hogares estimados",
                format_presented_value(
                    payload.get("estimated_households"), format_name="text"
                ),
            ),
            _presentation_item(
                "Prioridad inicial",
                format_presented_value(
                    payload.get("initial_priority_perception"),
                    format_name="choice",
                ),
            ),
            _presentation_item(
                "Fecha de evaluación",
                _display_or_empty(submission.assessment_date),
            ),
        ]
    )


def _ficha_10_summary(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    return _compact_summary(
        [
            _presentation_item(
                "Nombre del microproyecto",
                format_presented_value(
                    payload.get("microproject_name"), format_name="text"
                ),
            ),
            _presentation_item(
                "Componente",
                format_presented_value(payload.get("component"), format_name="choice"),
            ),
            _presentation_item(
                "Urgencia",
                format_presented_value(
                    payload.get("implementation_urgency"), format_name="choice"
                ),
            ),
            _presentation_item(
                "Viabilidad técnica",
                format_presented_value(
                    payload.get("technical_viability"), format_name="choice"
                ),
            ),
            _presentation_item(
                "Rango de costo",
                format_presented_value(
                    payload.get("estimated_cost_range"), format_name="choice"
                ),
            ),
        ]
    )


def _ficha_11_summary(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    return _compact_summary(
        [
            _presentation_item(
                "Puntaje total",
                format_presented_value(
                    payload.get("priority_total"), format_name="text"
                ),
            ),
            _presentation_item(
                "Semáforo final",
                format_presented_value(
                    payload.get("final_semaphore"), format_name="choice"
                ),
            ),
            _presentation_item(
                "Prioridad final",
                format_presented_value(
                    payload.get("final_priority"), format_name="choice"
                ),
            ),
            _presentation_item("Código del Núcleo Vital", _nucleo_code_display(submission)),
            _presentation_item(
                "Fecha de evaluación",
                _display_or_empty(submission.assessment_date),
            ),
        ]
    )


def present_imported_submission_summary(submission) -> list[dict[str, Any]]:
    """
    PRE: submission is an imported project-scoped Kobo record.
    POST: returns at most five compact summary items with Spanish values.
    """
    if _is_ficha_01(submission):
        return _ficha_1_summary(submission)
    if _is_ficha_10(submission):
        return _ficha_10_summary(submission)
    if _is_ficha_11(submission):
        return _ficha_11_summary(submission)
    return []


def _ficha_1_sections(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    return [
        {
            "title": "Territorio y población",
            "fields": [
                _presentation_item(
                    "Proyecto",
                    _display_or_empty(submission.project),
                ),
                _presentation_item(
                    "Parroquia",
                    _display_or_empty(submission.parish),
                ),
                _presentation_item(
                    "Comunidad",
                    _display_or_empty(submission.primary_community),
                ),
                _presentation_item(
                    "Comunidades cubiertas",
                    format_presented_value(
                        payload.get("communities_covered"),
                        format_name="text",
                    ),
                ),
                _presentation_item(
                    "Hogares estimados",
                    format_presented_value(
                        payload.get("estimated_households"),
                        format_name="text",
                    ),
                ),
            ],
        },
        {
            "title": "Acceso y evaluación",
            "fields": [
                _presentation_item(
                    "Dificultades de acceso",
                    format_presented_value(
                        payload.get("access_difficulties"),
                        format_name="choice",
                    ),
                ),
                _presentation_item(
                    "Notas de acceso",
                    format_presented_value(
                        payload.get("access_difficulties_notes"),
                        format_name="text",
                    ),
                ),
                _presentation_item(
                    "Percepción inicial de prioridad",
                    format_presented_value(
                        payload.get("initial_priority_perception"),
                        format_name="choice",
                    ),
                ),
                _presentation_item(
                    "Notas generales",
                    format_presented_value(
                        payload.get("general_notes"),
                        format_name="text",
                    ),
                ),
            ],
        },
    ]


def _ficha_10_sections(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    context_fields = [
        _presentation_item("Proyecto", _display_or_empty(submission.project)),
        _presentation_item("Código del Núcleo Vital", _nucleo_code_display(submission)),
    ]
    if submission.pastoral_zone:
        context_fields.append(
            _presentation_item(
                "Zona pastoral",
                pastoral_zone_label(submission.pastoral_zone),
            )
        )
    if submission.parish:
        context_fields.append(
            _presentation_item("Parroquia", _display_or_empty(submission.parish))
        )
    if submission.primary_community:
        context_fields.append(
            _presentation_item(
                "Comunidad",
                _display_or_empty(submission.primary_community),
            )
        )
    return [
        {
            "title": "Diagnóstico y objetivo",
            "fields": [
                _presentation_item(
                    "Resumen del problema",
                    format_presented_value(
                        payload.get("problem_summary"), format_name="text"
                    ),
                ),
                _presentation_item(
                    "Objetivo específico",
                    format_presented_value(
                        payload.get("specific_objective"), format_name="text"
                    ),
                ),
                _presentation_item(
                    "Resultado esperado",
                    format_presented_value(
                        payload.get("expected_result"), format_name="text"
                    ),
                ),
            ],
        },
        {
            "title": "Población y actividades",
            "fields": [
                _presentation_item(
                    "Grupo beneficiario",
                    format_presented_value(
                        payload.get("beneficiary_group"), format_name="multi_choice"
                    ),
                ),
                _presentation_item(
                    "Actividades principales",
                    format_presented_value(
                        payload.get("main_activities"), format_name="text"
                    ),
                ),
            ],
        },
        {
            "title": "Contexto territorial",
            "fields": context_fields,
        },
    ]


def _ficha_11_sections(submission) -> list[dict[str, Any]]:
    payload = submission.normalized_payload or {}
    score_fields = [
        _presentation_item(
            FICHA_11_SCORE_LABELS[key],
            format_presented_value(payload.get(key), format_name="text"),
        )
        for key in SCORE_FIELDS
    ]
    linked = format_linked_collection(
        payload.get("linked_microprojects"),
        label="Referencias",
    )
    return [
        {
            "title": "Puntajes de evaluación",
            "fields": score_fields,
        },
        {
            "title": "Decisión de priorización",
            "fields": [
                _presentation_item(
                    "Resumen de priorización",
                    format_presented_value(
                        payload.get("priority_summary"), format_name="text"
                    ),
                ),
                _presentation_item(
                    "Semáforo sugerido",
                    format_presented_value(
                        payload.get("suggested_semaphore"), format_name="choice"
                    ),
                ),
                _presentation_item(
                    "Semáforo final",
                    format_presented_value(
                        payload.get("final_semaphore"), format_name="choice"
                    ),
                ),
                _presentation_item(
                    "Prioridad final",
                    format_presented_value(
                        payload.get("final_priority"), format_name="choice"
                    ),
                ),
            ],
        },
        {
            "title": "Microproyectos vinculados",
            "fields": [linked],
        },
    ]


def present_imported_submission_sections(submission) -> list[dict[str, Any]]:
    """
    PRE: submission carries normalized payload for an imported project detail.
    POST: returns domain-specific operational field sections for Ficha 1/10/11.
    """
    if _is_ficha_01(submission):
        return _ficha_1_sections(submission)
    if _is_ficha_10(submission):
        return _ficha_10_sections(submission)
    if _is_ficha_11(submission):
        return _ficha_11_sections(submission)
    return []


def present_imported_submission_sensitive_fields(submission) -> list[dict[str, str]]:
    """
    PRE: submission normalized_payload may contain contact keys.
    POST: returns human-labelled contact fields, omitting empty values.
    """
    payload = submission.normalized_payload or {}
    rows: list[dict[str, str]] = []
    for key, label in CONTACT_FIELDS:
        value = payload.get(key)
        if value in (None, ""):
            continue
        rows.append(_presentation_item(label, str(value)))
    return rows


def present_imported_submission_technical_fields(submission) -> list[dict[str, str]]:
    """
    PRE: submission raw_payload may contain submitter/device metadata.
    POST: returns human-labelled technical metadata, omitting empty values.
    """
    raw_payload = submission.raw_payload or {}
    rows: list[dict[str, str]] = []
    for _key, label, raw_key in TECHNICAL_METADATA_FIELDS:
        value = raw_payload.get(raw_key)
        if value in (None, ""):
            continue
        rows.append(_presentation_item(label, str(value)))
    return rows


def present_imported_submission_registration(submission) -> list[dict[str, str]]:
    """
    PRE: submission has form_definition and lifecycle timestamps loaded.
    POST: returns Registro Kobo fields with technical IDs kept secondary.
    """
    form_definition = submission.form_definition
    return [
        _presentation_item(
            "Formulario técnico",
            _display_or_empty(form_definition.form_id),
        ),
        _presentation_item(
            "Versión del formulario",
            _display_or_empty(form_definition.version),
        ),
        _presentation_item(
            "Identificador externo",
            _display_or_empty(submission.external_id),
        ),
        _presentation_item("Recibido", _display_or_empty(submission.received_at)),
        _presentation_item("Importado", _display_or_empty(submission.imported_at)),
    ]


def _imported_location(submission) -> dict[str, Any] | None:
    if not _is_ficha_01(submission):
        return None
    payload = submission.normalized_payload or {}
    raw_location = payload.get("location")
    location = format_location(raw_location)
    location["map_url"] = build_openstreetmap_map_url(raw_location)
    return location


def _build_ficha_imported_detail(
    submission,
    *,
    can_view_sensitive: bool,
) -> dict[str, Any]:
    sensitive_fields = (
        present_imported_submission_sensitive_fields(submission)
        if can_view_sensitive
        else []
    )
    technical_fields = (
        present_imported_submission_technical_fields(submission)
        if can_view_sensitive
        else []
    )
    return {
        "is_redesigned": True,
        "page_kicker": "Levantamiento Kobo importado",
        "page_title": _imported_page_title(submission),
        "page_subtitle": _imported_page_subtitle(submission),
        "summary_items": present_imported_submission_summary(submission),
        "sections": present_imported_submission_sections(submission),
        "location": _imported_location(submission),
        "sensitive_fields": sensitive_fields,
        "technical_fields": technical_fields,
        "registration_fields": present_imported_submission_registration(submission),
        "show_sensitive_block": bool(sensitive_fields or technical_fields),
    }


def _build_ficha_1_imported_detail(submission, *, can_view_sensitive: bool) -> dict[str, Any]:
    return _build_ficha_imported_detail(
        submission, can_view_sensitive=can_view_sensitive
    )


def _build_ficha_10_imported_detail(submission, *, can_view_sensitive: bool) -> dict[str, Any]:
    return _build_ficha_imported_detail(
        submission, can_view_sensitive=can_view_sensitive
    )


def _build_ficha_11_imported_detail(submission, *, can_view_sensitive: bool) -> dict[str, Any]:
    return _build_ficha_imported_detail(
        submission, can_view_sensitive=can_view_sensitive
    )


def build_imported_submission_detail_context(
    submission,
    *,
    can_view_sensitive: bool,
) -> dict[str, Any]:
    """
    PRE: submission is an imported, project-scoped Kobo record for detail UI.
    POST: returns a template-ready presentation contract without mutating payload.
    """
    if _is_ficha_01(submission):
        return _build_ficha_1_imported_detail(
            submission, can_view_sensitive=can_view_sensitive
        )
    if _is_ficha_10(submission):
        return _build_ficha_10_imported_detail(
            submission, can_view_sensitive=can_view_sensitive
        )
    if _is_ficha_11(submission):
        return _build_ficha_11_imported_detail(
            submission, can_view_sensitive=can_view_sensitive
        )
    return _build_ficha_imported_detail(
        submission, can_view_sensitive=can_view_sensitive
    )
