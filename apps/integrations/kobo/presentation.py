"""Operator-facing presentation labels for the Kobo operations hub.

Technical codes remain unchanged in models, enums and services; this module
only translates values shown in templates and flash messages.
"""

from __future__ import annotations

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.models import KoboAsset, KoboSyncRun
from apps.integrations.kobo.services import REJECTION_REASON_LABELS


PASTORAL_ZONE_LABELS = {
    PastoralZone.CATIA_LA_MAR.value: "Catia La Mar",
    PastoralZone.CENTRO.value: "Centro",
    PastoralZone.ESTE.value: "Este",
    PastoralZone.MONTANA.value: "Montaña",
    PastoralZone.INSULAR.value: "Insular",
}

PASTORAL_ZONE_TOTAL = len(PastoralZone)

FORM_ROLE_TITLES = {
    KoboAsset.FormRole.TERRITORIAL_PROFILE: ("Ficha 1", "Registro territorial"),
    KoboAsset.FormRole.PRIORITIZED_MICROPROJECT: ("Ficha 10", "Microproyectos priorizados"),
    KoboAsset.FormRole.PRIORITIZATION_MATRIX: ("Ficha 11", "Evaluación de prioridad"),
}

SYNC_STATUS_LABELS = {
    KoboSyncRun.Status.SUCCEEDED: "Completada",
    KoboSyncRun.Status.PARTIAL: "Completada con observaciones",
    KoboSyncRun.Status.FAILED: "Fallida",
    KoboSyncRun.Status.RUNNING: "En curso",
    KoboSyncRun.Status.ABANDONED: "Interrumpida",
}

SYNC_MODE_LABELS = {
    KoboSyncRun.Mode.FULL: "Sincronización completa",
    KoboSyncRun.Mode.INCREMENTAL: "Actualización",
}

KOBO_PRESENTATION_LABELS = {
    "pending_identity": "Sin núcleo registrado",
    "conflict": "Conflicto de asignación",
    "error": "Error de asignación",
    "ready_for_review": "Pendiente de revisión",
    "imported": "Importado",
    "rejected": "Rechazado",
    "validation_failed": "Error de validación",
    "processing_failed": "Error de procesamiento",
    "territorial_profile": "Registro territorial",
    "prioritized_microproject": "Microproyecto priorizado",
    "prioritization_matrix": "Evaluación de prioridad",
    "pending_review": "Pendiente de revisión",
    "active": "Activo",
    "observed": "En observación",
    "inactive": "Inactivo",
    "open": "Abierto",
    "resolved_keep_existing": "Resuelto: conservar actual",
    "resolved_accept_proposed": "Resuelto: aceptar propuesta",
    "dismissed": "Descartado",
    "red": "Rojo",
    "yellow": "Amarillo",
    "green": "Verde",
    "gray": "Gris",
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Crítica",
    "unknown": "Sin determinar",
    **PASTORAL_ZONE_LABELS,
    **{status.value: label for status, label in SYNC_STATUS_LABELS.items()},
    **{mode.value: label for mode, label in SYNC_MODE_LABELS.items()},
    **REJECTION_REASON_LABELS,
}


def pastoral_zone_label(zone) -> str:
    """
    PRE: zone is a PastoralZone member, its value, or another safe code.
    POST: returns the Spanish pastoral-zone label used in operator UI.
    """
    value = getattr(zone, "value", zone)
    return PASTORAL_ZONE_LABELS.get(str(value), str(value).replace("_", " ").title())


def spanish_join(items: list[str]) -> str:
    """
    PRE: items is an ordered list of non-empty display labels.
    POST: joins them with Spanish commas and y/e before the last item.
    """
    labels = [item for item in items if item]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    last = labels[-1]
    conjunction = "e" if last[:1].lower() in {"i", "í"} else "y"
    if len(labels) == 2:
        return f"{labels[0]} {conjunction} {last}"
    return f"{', '.join(labels[:-1])} {conjunction} {last}"


def form_role_title(form_role: str) -> tuple[str, str]:
    """
    PRE: form_role is a KoboAsset.FormRole value.
    POST: returns (short title, descriptive subtitle) for sync rows.
    """
    return FORM_ROLE_TITLES.get(form_role, ("Formulario", form_role.replace("_", " ").title()))


def sync_status_label(status: str) -> str:
    return SYNC_STATUS_LABELS.get(status, status)


def sync_mode_label(mode: str) -> str:
    return SYNC_MODE_LABELS.get(mode, mode)


def presentation_label(value) -> str:
    """
    PRE: value is a persisted Kobo code or a safe display value.
    POST: returns its Spanish presentation label without changing stored values.
    """
    if value is None:
        return ""
    return KOBO_PRESENTATION_LABELS.get(value, value)
