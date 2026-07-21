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
    KoboSubmission.Status.READY_FOR_REVIEW: "Pendiente de revisión",
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
        "Los datos del formulario se normalizaron y quedaron listos para revisión.",
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
