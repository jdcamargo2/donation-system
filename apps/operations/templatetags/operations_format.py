from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def money_es(value):
    """
    PRE: value is Decimal, int, numeric text, None, or an arbitrary template value.
    POST: returns Spanish two-decimal money text; invalid values are returned safely without raising.
    """
    if value is None:
        return '0,00'
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if not amount.is_finite():
        return str(value)
    canonical = format(amount, ',.2f')
    return canonical.translate(str.maketrans({',': '.', '.': ','}))


@register.filter
def percentage_es(value):
    """
    PRE: value is Decimal percentage, numeric text, or None.
    POST: returns Spanish percentage text without a %% suffix; None becomes em dash.
    """
    if value is None:
        return '—'
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if not amount.is_finite():
        return str(value)
    quantized = amount.quantize(Decimal('0.1'))
    if quantized == quantized.to_integral_value():
        return format(quantized.quantize(Decimal('1')), 'f')
    return format(quantized, 'f').replace('.', ',')


@register.filter
def widget_has_attr(bound_field, attr_name):
    """
    PRE: bound_field is a BoundField; attr_name is a widget attribute key.
    POST: returns True when the widget attrs define attr_name with a non-empty value.
    """
    try:
        value = bound_field.field.widget.attrs.get(attr_name)
    except AttributeError:
        return False
    return value not in (None, '', False)
