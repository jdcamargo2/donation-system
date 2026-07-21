from django import template

from apps.integrations.kobo.presentation import (
    form_role_title,
    pastoral_zone_label,
    presentation_label,
    sync_mode_label,
    sync_status_label,
)


register = template.Library()


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
    return presentation_label(value)


@register.filter
def kobo_zone_label(value):
    """
    PRE: value is a pastoral-zone code or PastoralZone member.
    POST: returns the operator-facing Spanish zone name.
    """
    return pastoral_zone_label(value)


@register.filter
def kobo_sync_status(value):
    return sync_status_label(value)


@register.filter
def kobo_sync_mode(value):
    return sync_mode_label(value)


@register.filter
def kobo_form_role_title(value):
    title, _subtitle = form_role_title(value)
    return title


@register.filter
def kobo_form_role_subtitle(value):
    _title, subtitle = form_role_title(value)
    return subtitle
