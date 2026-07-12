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
