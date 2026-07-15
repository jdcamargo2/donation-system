"""Inspección de solo lectura de secuencias de códigos operativos."""

from dataclasses import dataclass
import re

from .models import (
    Donation,
    Expense,
    FundAllocation,
    OPERATIONAL_CODE_PREFIXES,
    OperationalCodeSequence,
    Project,
)


OPERATIONAL_CODE_MODELS = {
    'project': Project,
    'donation': Donation,
    'fund_allocation': FundAllocation,
    'expense': Expense,
}
UNSAFE_SEQUENCE_STATUSES = frozenset(
    {'MISSING_SEQUENCE', 'LAGGING_SEQUENCE', 'INVALID_SEQUENCE'}
)


@dataclass(frozen=True)
class OperationalCodeSequenceReport:
    namespace: str
    prefix: str
    total: int
    canonical: int
    noncanonical: int
    maximum: int | None
    sequence_exists: bool
    next_value: int | None
    status: str


def inspect_operational_code_sequences(*, using='default'):
    """
    PRE: using identifies a configured database containing the operational tables.
    POST: returns one read-only diagnostic per namespace without changing rows or locks.
    """
    reports = []
    for namespace, model in OPERATIONAL_CODE_MODELS.items():
        prefix = OPERATIONAL_CODE_PREFIXES[namespace]
        pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$')
        codes = model.objects.using(using).values_list('code', flat=True)
        canonical_numbers = []
        total = 0
        for code in codes.iterator():
            total += 1
            match = pattern.fullmatch(code or '')
            if match and int(match.group(1)) > 0:
                canonical_numbers.append(int(match.group(1)))

        sequence = (
            OperationalCodeSequence.objects.using(using)
            .filter(namespace=namespace)
            .values('prefix', 'next_value')
            .first()
        )
        maximum = max(canonical_numbers, default=None)
        reports.append(
            OperationalCodeSequenceReport(
                namespace=namespace,
                prefix=prefix,
                total=total,
                canonical=len(canonical_numbers),
                noncanonical=total - len(canonical_numbers),
                maximum=maximum,
                sequence_exists=sequence is not None,
                next_value=sequence['next_value'] if sequence else None,
                status=_sequence_status(
                    maximum=maximum,
                    sequence=sequence,
                    expected_prefix=prefix,
                ),
            )
        )
    return tuple(reports)


def _sequence_status(*, maximum, sequence, expected_prefix):
    """
    PRE: maximum is None or a positive canonical numeric code; sequence is its row or None.
    POST: classifies sequence safety without modifying sequence state.
    """
    if sequence is not None and (
        sequence['prefix'] != expected_prefix
        or sequence['next_value'] is None
        or sequence['next_value'] <= 0
    ):
        return 'INVALID_SEQUENCE'
    next_value = sequence['next_value'] if sequence else None
    if maximum is None:
        if next_value is None or next_value == 1:
            return 'OK_EMPTY'
        return 'OK'
    if next_value is None:
        return 'MISSING_SEQUENCE'
    if next_value <= maximum:
        return 'LAGGING_SEQUENCE'
    return 'OK'
