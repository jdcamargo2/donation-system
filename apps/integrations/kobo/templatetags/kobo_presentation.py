from django import template

from apps.integrations.kobo.services import REJECTION_REASON_LABELS


register = template.Library()

KOBO_PRESENTATION_LABELS = {
    "pending_identity": "Pendiente de identidad",
    "conflict": "Conflicto territorial",
    "error": "Error de routing",
    "ready_for_review": "Pendiente de revisión",
    "imported": "Importada",
    "rejected": "Rechazada",
    "validation_failed": "Error de validación",
    "territorial_profile": "Identificación territorial",
    "prioritized_microproject": "Microproyecto priorizado",
    "prioritization_matrix": "Matriz de priorización",
    "red": "Rojo",
    "yellow": "Amarillo",
    "green": "Verde",
    "gray": "Gris",
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Crítica",
    "unknown": "Sin determinar",
    **REJECTION_REASON_LABELS,
}


@register.filter
def get_item(mapping, key):
    """
    PRE: mapping is a safe template lookup dictionary.
    POST: returns its key value or None without raising for absent contextual links.
    """
    return mapping.get(key) if mapping else None


@register.filter
def kobo_label(value):
    """
    PRE: value is a persisted Kobo code or a safe display value.
    POST: returns its Spanish presentation label without changing stored values.
    """
    return KOBO_PRESENTATION_LABELS.get(value, value)
