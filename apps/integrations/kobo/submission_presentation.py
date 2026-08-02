"""Presentation helpers for the operator-facing Kobo submission review screen.

Stored codes, payloads and service contracts stay unchanged; this module only
shapes Spanish labels, values and history copy for templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAttachment, KoboSubmission
from apps.integrations.kobo.presentation import FORM_ROLE_TITLES, pastoral_zone_label, presentation_label


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
        "Se solicitó completar o corregir información antes de continuar.",
    ),
    "restored": (
        "Restaurado a revisión",
        "El formulario volvió a la cola de revisión humana.",
    ),
    "auto_approved": (
        "Aprobado automáticamente",
        "El sistema autorizó la importación sin revisión humana.",
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
        "Cambio recibido que requiere revisión",
        "KoboToolbox envió una actualización que debe revisarse antes de aplicarse.",
    ),
    "REMOTE_UPDATE_DETECTED": (
        "Cambio recibido que requiere revisión",
        "KoboToolbox envió una actualización que debe revisarse antes de aplicarse.",
    ),
    KoboSubmission.Status.APPROVED_FOR_IMPORT: (
        "Aprobado para importar",
        "La revisión humana autorizó la importación al proyecto.",
    ),
    KoboSubmission.Status.REJECTED: (
        "Formulario rechazado",
        "La revisión humana rechazó el formulario.",
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
        "El formulario se rechazó por un motivo registrado en la revisión.",
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
    if value is None or value == "":
        return "—"
    text = str(value)
    return CHOICE_VALUE_LABELS.get(text, presentation_label(text) or text)


def format_presented_value(value, *, format_name: str) -> str:
    if value is None or value == "":
        return "—"
    if format_name == "multi_choice":
        if isinstance(value, (list, tuple)):
            labels = [choice_value_label(item) for item in value if item not in (None, "")]
            return ", ".join(labels) if labels else "—"
        return choice_value_label(value)
    if format_name == "choice":
        return choice_value_label(value)
    if isinstance(value, (list, tuple, dict)):
        return str(value)
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
    zone_label = pastoral_zone_label(zone) if zone else "—"
    project = submission.project
    project_label = str(project) if project else ""
    rows = [
        ("Zona pastoral", zone_label),
        ("Parroquia", submission.parish or "—"),
        ("Comunidad", submission.primary_community or "—"),
        ("Código del núcleo", submission.nucleo_code_normalized or "—"),
        ("Fecha de evaluación", str(submission.assessment_date or "—")),
        ("Recibido", str(submission.received_at)),
    ]
    if project_label:
        rows.append(("Proyecto asociado", project_label))
    return rows


EMPTY_DISPLAY = "—"
LOCATION_UNAVAILABLE = "No disponible"

# Choice labels retained for Ficha 10 project-detail compatibility until KD2.
# Prefer CHOICE_VALUE_LABELS for new presentation paths.
_MICROPROJECT_DETAIL_CHOICE_LABELS = {
    "component": {
        "infrastructure": "Infraestructura",
        "health_psychosocial": "Salud y atención psicosocial",
        "training": "Formación",
        "livelihoods": "Medios de vida",
        "communication": "Comunicación",
        "mixed": "Mixto",
    },
    "beneficiary_group": {
        "youth": "Jóvenes",
        "women": "Mujeres",
        "adults": "Adultos",
        "unemployed": "Personas desempleadas",
        "entrepreneurs": "Emprendedores",
        "parish_volunteers": "Voluntariado parroquial",
        "mixed": "Mixto",
        "other": "Otro",
    },
    "estimated_cost_range": {
        "under_1000": "Menos de USD 1.000",
        "1000_5000": "USD 1.000 a 5.000",
        "5000_15000": "USD 5.000 a 15.000",
        "15000_50000": "USD 15.000 a 50.000",
        "over_50000": "Más de USD 50.000",
        "unknown": "Por determinar",
    },
    "implementation_urgency": {
        "immediate": "Inmediata",
        "short_term": "Corto plazo",
        "medium_term": "Mediano plazo",
        "follow_up": "Seguimiento",
        "unknown": "Por determinar",
    },
    "technical_viability": {
        "high": "Alta",
        "medium": "Media",
        "low": "Baja",
        "requires_design": "Requiere diseño",
        "not_viable": "No viable",
    },
}

TECHNICAL_METADATA_FIELDS = (
    ("submitted_by", "Enviado por", "_submitted_by"),
    ("device_id", "ID del dispositivo", "deviceid"),
)


def _display_or_empty(value) -> str:
    if value is None or value == "":
        return EMPTY_DISPLAY
    return str(value)


def _presentation_item(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


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


def project_submission_detail_title(submission) -> str:
    """
    PRE: submission has its form definition loaded.
    POST: returns a form-specific internal detail title without exposing metadata.
    """
    if _is_ficha_11(submission):
        return "Matriz de priorización y semáforo"
    if _is_ficha_10(submission):
        return "Microproyecto priorizado"
    if _is_ficha_01(submission):
        return "Ficha 1 · Identificación territorial"
    return "Proyecto y territorio"


def project_submission_detail_rows(submission) -> tuple[tuple[str, Any], ...]:
    """
    PRE: submission is an imported Kobo record with normalized payload data.
    POST: returns labelled Ficha 10/11 fields only; territorial forms return none.
    """
    payload = submission.normalized_payload or {}
    if _is_ficha_11(submission):
        return (
            ("Código del Núcleo Vital / comunidad", payload.get("nucleo_code")),
            ("Nivel de daño físico", payload.get("physical_damage_score")),
            ("Familias afectadas", payload.get("affected_families_score")),
            ("Vulnerabilidad social", payload.get("social_vulnerability_score")),
            ("Interrupción de servicios básicos", payload.get("services_interruption_score")),
            ("Pérdida de medios de vida", payload.get("livelihood_loss_score")),
            ("Capacidad parroquial disponible", payload.get("parish_capacity_score")),
            ("Accesibilidad territorial", payload.get("territorial_accessibility_score")),
            ("Existencia de aliados", payload.get("allies_availability_score")),
            ("Potencial de impacto rápido", payload.get("rapid_impact_score")),
            ("Viabilidad financiera", payload.get("financial_viability_score")),
            ("Puntaje total", payload.get("priority_total")),
            ("Semáforo sugerido", payload.get("suggested_semaphore")),
            ("Semáforo final validado", payload.get("final_semaphore")),
            ("Prioridad final de intervención", payload.get("final_priority")),
            ("Síntesis de decisión", payload.get("priority_summary")),
            ("Microproyectos vinculados", payload.get("linked_microprojects")),
        )
    if not _is_ficha_10(submission):
        return ()
    beneficiary_groups = payload.get("beneficiary_group", ())
    beneficiary_labels = ", ".join(
        _MICROPROJECT_DETAIL_CHOICE_LABELS["beneficiary_group"].get(value, value)
        for value in beneficiary_groups
    )
    return (
        ("Código del Núcleo Vital", payload.get("nucleo_code")),
        ("Nombre del microproyecto", payload.get("microproject_name")),
        (
            "Componente principal",
            _MICROPROJECT_DETAIL_CHOICE_LABELS["component"].get(
                payload.get("component"), payload.get("component")
            ),
        ),
        ("Problema que atiende", payload.get("problem_summary")),
        ("Objetivo específico", payload.get("specific_objective")),
        ("Población beneficiaria principal", beneficiary_labels),
        ("Actividades principales", payload.get("main_activities")),
        (
            "Rango de costo estimado",
            _MICROPROJECT_DETAIL_CHOICE_LABELS["estimated_cost_range"].get(
                payload.get("estimated_cost_range"),
                payload.get("estimated_cost_range"),
            ),
        ),
        (
            "Urgencia de implementación",
            _MICROPROJECT_DETAIL_CHOICE_LABELS["implementation_urgency"].get(
                payload.get("implementation_urgency"),
                payload.get("implementation_urgency"),
            ),
        ),
        (
            "Viabilidad técnica inicial",
            _MICROPROJECT_DETAIL_CHOICE_LABELS["technical_viability"].get(
                payload.get("technical_viability"), payload.get("technical_viability")
            ),
        ),
        ("Resultado esperado verificable", payload.get("expected_result")),
    )


def _imported_page_subtitle(submission) -> str:
    project = submission.project
    parts: list[str] = []
    if project is not None and getattr(project, "code", None):
        parts.append(str(project.code))
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


def present_imported_submission_summary(submission) -> list[dict[str, str]]:
    """
    PRE: submission is an imported Ficha 1 (or compatible) with payload loaded.
    POST: returns at most five compact summary items with Spanish values.
    """
    if not _is_ficha_01(submission):
        return []
    payload = submission.normalized_payload or {}
    nucleo = (
        submission.nucleo_code_normalized
        or payload.get("nucleo_code")
        or EMPTY_DISPLAY
    )
    zone = pastoral_zone_label(submission.pastoral_zone) if submission.pastoral_zone else EMPTY_DISPLAY
    return [
        _presentation_item("Código del Núcleo Vital", _display_or_empty(nucleo)),
        _presentation_item("Zona pastoral", zone),
        _presentation_item(
            "Hogares estimados",
            format_presented_value(payload.get("estimated_households"), format_name="text"),
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


def present_imported_submission_sections(submission) -> list[dict[str, Any]]:
    """
    PRE: submission carries normalized payload for an imported project detail.
    POST: returns operational field sections; Ficha 1 is grouped, others legacy.
    """
    payload = submission.normalized_payload or {}
    if _is_ficha_01(submission):
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
    legacy_rows = project_submission_detail_rows(submission)
    if not legacy_rows:
        return []
    return [
        {
            "title": project_submission_detail_title(submission),
            "fields": [
                _presentation_item(label, _display_or_empty(value))
                for label, value in legacy_rows
            ],
        }
    ]


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


def build_imported_submission_detail_context(
    submission,
    *,
    can_view_sensitive: bool,
) -> dict[str, Any]:
    """
    PRE: submission is an imported, project-scoped Kobo record for detail UI.
    POST: returns a template-ready presentation contract without mutating payload.
    """
    is_redesigned = _is_ficha_01(submission)
    payload = submission.normalized_payload or {}
    location = None
    if is_redesigned:
        location = format_location(payload.get("location"))
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
        "is_redesigned": is_redesigned,
        "page_kicker": "Levantamiento Kobo importado",
        "page_title": project_submission_detail_title(submission),
        "page_subtitle": _imported_page_subtitle(submission),
        "summary_items": present_imported_submission_summary(submission),
        "sections": present_imported_submission_sections(submission),
        "location": location,
        "sensitive_fields": sensitive_fields,
        "technical_fields": technical_fields,
        "registration_fields": present_imported_submission_registration(submission),
        "show_sensitive_block": bool(sensitive_fields or technical_fields),
    }
