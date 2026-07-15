from django.core.management.base import BaseCommand, CommandError

from apps.operations.operational_code_sequences import (
    UNSAFE_SEQUENCE_STATUSES,
    inspect_operational_code_sequences,
)


class Command(BaseCommand):
    help = (
        'Diagnostica secuencias de códigos operativos sin repararlas ni modificar datos.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        """
        PRE: database names a configured database whose operational codes can be read.
        POST: prints one diagnostic per namespace and raises CommandError only for unsafe sequences.
        """
        reports = inspect_operational_code_sequences(using=options['database'])
        unsafe_reports = []
        for report in reports:
            self.stdout.write(
                f'{report.namespace}: {report.status} | max={report.maximum} | '
                f'next={report.next_value} | canonical={report.canonical} | '
                f'noncanonical={report.noncanonical}'
            )
            if report.status in UNSAFE_SEQUENCE_STATUSES:
                unsafe_reports.append(report)

        if unsafe_reports:
            details = ', '.join(
                f'{report.namespace}: {report.status}' for report in unsafe_reports
            )
            raise CommandError(f'Secuencias operativas inseguras: {details}')
