"""
Verificacion de solo lectura tras restaurar un entorno SIGEDON aislado.

PRE: Django apunta a la base y al storage del entorno restaurado (no produccion).
POST: informa conteos y fallos sin imprimir rutas ni nombres sensibles;
      sale con codigo distinto de cero si detecta inconsistencias.
      No repara datos.
"""

from django.apps import apps
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

AUDITLOG_SCHEMA = 'public'
AUDITLOG_TABLE = 'operations_auditlog'
AUDITLOG_QUALIFIED_NAME = f'{AUDITLOG_SCHEMA}.{AUDITLOG_TABLE}'
AUDITLOG_TRIGGER_NAME = 'operations_auditlog_append_only'

# (app_label, model_name, field_name) — FileField con nombre no vacio debe existir.
FILE_FIELD_SPECS = (
    ('operations', 'Institution', 'legal_document'),
    ('operations', 'ProjectDocument', 'file'),
    ('operations', 'ProjectUpdateAttachment', 'file'),
    ('operations', 'ProjectUpdateRemediationAttachment', 'file'),
    ('operations', 'SupportingDocument', 'document'),
    ('kobo', 'KoboAttachment', 'file'),
)

MAIN_TABLE_SPECS = (
    ('operations', 'Institution'),
    ('operations', 'Project'),
    ('operations', 'Donation'),
    ('operations', 'AuditLog'),
    ('operations', 'OperationalCodeSequence'),
    ('kobo', 'KoboAttachment'),
)


class Command(BaseCommand):
    help = (
        'Verificacion de solo lectura de datos restaurados: tablas principales, '
        'AuditLog, trigger append-only, secuencias operativas y existencia de '
        'archivos referenciados por FileField. No imprime rutas ni nombres de '
        'archivo. No repara datos. Codigo distinto de cero si hay inconsistencias.'
    )

    def handle(self, *args, **options):
        """
        PRE: connection and default_storage point at the restored environment.
        POST: prints aggregate findings; raises CommandError when inconsistencies
        are found.
        """
        issues = []
        report = {
            'tables': {},
            'auditlog_count': None,
            'trigger_installed': None,
            'sequences_count': None,
            'file_refs_checked': 0,
            'file_refs_missing': 0,
            'kobo_downloaded_checked': 0,
            'kobo_downloaded_inconsistent': 0,
        }

        issues.extend(self._check_main_tables(report))
        issues.extend(self._check_auditlog(report))
        issues.extend(self._check_append_only_trigger(report))
        issues.extend(self._check_sequences(report))
        issues.extend(self._check_file_fields(report))
        issues.extend(self._check_kobo_downloaded(report))

        self._print_report(report)

        if issues:
            raise CommandError(
                'Inconsistencias en datos restaurados: ' + '; '.join(issues)
            )
        self.stdout.write(self.style.SUCCESS('Datos restaurados consistentes.'))

    def _check_main_tables(self, report):
        # PRE: model registry is loaded.
        # POST: report['tables'] maps label -> count or 'error'; returns issue strings.
        issues = []
        for app_label, model_name in MAIN_TABLE_SPECS:
            label = f'{app_label}.{model_name}'
            try:
                model = apps.get_model(app_label, model_name)
                count = model.objects.count()
                report['tables'][label] = count
            except Exception:  # noqa: BLE001 - report aggregate only
                report['tables'][label] = 'error'
                issues.append(f'tabla principal inaccesible: {label}')
        return issues

    def _check_auditlog(self, report):
        issues = []
        try:
            AuditLog = apps.get_model('operations', 'AuditLog')
            report['auditlog_count'] = AuditLog.objects.count()
        except Exception:  # noqa: BLE001
            report['auditlog_count'] = None
            issues.append('AuditLog inaccesible')
        return issues

    def _check_append_only_trigger(self, report):
        issues = []
        if connection.vendor != 'postgresql':
            report['trigger_installed'] = 'n/a'
            return issues
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT EXISTS ('
                    '  SELECT 1 FROM pg_trigger'
                    '  WHERE tgrelid = %s::regclass'
                    '    AND tgname = %s'
                    '    AND NOT tgisinternal'
                    ');',
                    [AUDITLOG_QUALIFIED_NAME, AUDITLOG_TRIGGER_NAME],
                )
                installed = bool(cursor.fetchone()[0])
            report['trigger_installed'] = installed
            if not installed:
                issues.append('trigger append-only ausente')
        except Exception:  # noqa: BLE001
            report['trigger_installed'] = None
            issues.append('no se pudo verificar el trigger append-only')
        return issues

    def _check_sequences(self, report):
        issues = []
        try:
            Sequence = apps.get_model('operations', 'OperationalCodeSequence')
            count = Sequence.objects.count()
            report['sequences_count'] = count
            if count < 1:
                issues.append('no hay secuencias operativas')
        except Exception:  # noqa: BLE001
            report['sequences_count'] = None
            issues.append('OperationalCodeSequence inaccesible')
        return issues

    def _check_file_fields(self, report):
        # PRE: default_storage is the restored storage root.
        # POST: increments checked/missing counters; never prints file names.
        issues = []
        missing = 0
        checked = 0
        for app_label, model_name, field_name in FILE_FIELD_SPECS:
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                issues.append(f'modelo ausente: {app_label}.{model_name}')
                continue
            queryset = model.objects.exclude(**{f'{field_name}__isnull': True}).exclude(
                **{f'{field_name}': ''}
            )
            for instance in queryset.iterator():
                field = getattr(instance, field_name)
                name = getattr(field, 'name', '') or ''
                if not name:
                    continue
                checked += 1
                if not default_storage.exists(name):
                    missing += 1
        report['file_refs_checked'] = checked
        report['file_refs_missing'] = missing
        if missing:
            issues.append(f'referencias FileField faltantes: {missing}')
        return issues

    def _check_kobo_downloaded(self, report):
        issues = []
        try:
            KoboAttachment = apps.get_model('kobo', 'KoboAttachment')
        except LookupError:
            return issues
        downloaded = KoboAttachment.objects.filter(status='downloaded')
        checked = 0
        inconsistent = 0
        for attachment in downloaded.iterator():
            checked += 1
            name = (attachment.file.name if attachment.file else '') or ''
            if not name or not default_storage.exists(name):
                inconsistent += 1
        report['kobo_downloaded_checked'] = checked
        report['kobo_downloaded_inconsistent'] = inconsistent
        if inconsistent:
            issues.append(
                f'adjuntos Kobo DOWNLOADED sin archivo: {inconsistent}'
            )
        return issues

    def _print_report(self, report):
        self.stdout.write('Verificacion de datos restaurados (agregados):')
        for label, value in sorted(report['tables'].items()):
            self.stdout.write(f'  tabla {label}: {value}')
        self.stdout.write(f"  AuditLog count: {report['auditlog_count']}")
        self.stdout.write(f"  trigger append-only: {report['trigger_installed']}")
        self.stdout.write(f"  secuencias operativas: {report['sequences_count']}")
        self.stdout.write(
            f"  FileField comprobados: {report['file_refs_checked']}; "
            f"faltantes: {report['file_refs_missing']}"
        )
        self.stdout.write(
            f"  Kobo DOWNLOADED comprobados: {report['kobo_downloaded_checked']}; "
            f"inconsistentes: {report['kobo_downloaded_inconsistent']}"
        )
