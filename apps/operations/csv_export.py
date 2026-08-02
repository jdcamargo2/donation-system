"""Spreadsheet-safe cell escaping for operational CSV exports.

PRE: callers pass cell values as they would to csv.writer, preserving original
     Python types for numbers and dates.
POST: text that would be interpreted as a spreadsheet formula is neutralized;
      numeric, date, boolean, and already-safe values keep their representation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

DANGEROUS_CSV_PREFIXES = ('=', '+', '-', '@')
_LEADING_CONTROL_OR_WHITESPACE = ' \t\r\n'


def escape_csv_cell(value):
    """
    PRE: value is a single CSV cell prior to csv.writer quoting.
    POST: None becomes ''; numbers/dates/bools are unchanged; text whose first
          significant character is a formula prefix is apostrophe-prefixed;
          already apostrophe-prefixed text is left unchanged.
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return value
    if isinstance(value, (Decimal, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value

    text = value if isinstance(value, str) else str(value)
    if text == '' or text.startswith("'"):
        return text

    significant = text.lstrip(_LEADING_CONTROL_OR_WHITESPACE)
    if significant and significant[0] in DANGEROUS_CSV_PREFIXES:
        return f"'{text}"
    return text
