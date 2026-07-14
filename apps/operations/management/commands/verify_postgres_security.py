from django.core.management.base import BaseCommand, CommandError
from django.db import connection

AUDITLOG_SCHEMA = 'public'
AUDITLOG_TABLE = 'operations_auditlog'
AUDITLOG_QUALIFIED_NAME = f'{AUDITLOG_SCHEMA}.{AUDITLOG_TABLE}'
AUDITLOG_TRIGGER_NAME = 'operations_auditlog_append_only'

REPORTED_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER')
REQUIRED_PRIVILEGES = ('SELECT', 'INSERT')
DANGEROUS_PRIVILEGES = ('UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER')


class Command(BaseCommand):
    help = (
        'Verificacion de solo lectura de la separacion de roles PostgreSQL de SIGEDON. '
        'Informa el usuario runtime actual, si es superusuario, si es propietario de '
        f'{AUDITLOG_TABLE}, sus privilegios SELECT/INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER '
        'sobre esa tabla, y si el trigger append-only esta instalado. '
        'No imprime contrasenas ni cadenas de conexion. '
        'Codigo de salida 0 indica configuracion runtime segura; un codigo distinto de 0 '
        'indica que el usuario es superusuario, es propietario de la tabla, carece de '
        'SELECT o INSERT, posee un privilegio peligroso, o falta el trigger. '
        'Pensado para uso en despliegue o CI, '
        'por ejemplo como paso previo a habilitar trafico: '
        'python manage.py verify_postgres_security.'
    )

    def handle(self, *args, **options):
        """
        PRE: connection points to the database that should be verified.
        POST: prints a plain-language report and raises CommandError (non-zero
        exit) when the runtime role is unsafe; returns normally (exit 0)
        otherwise, including when the backend is not PostgreSQL.
        """
        if connection.vendor != 'postgresql':
            self.stdout.write(
                self.style.WARNING(
                    'La verificacion de seguridad de PostgreSQL no aplica: '
                    f'el motor activo es "{connection.vendor}".'
                )
            )
            return

        report = self._collect_report()
        self._print_report(report)

        risks = self._risks(report)
        if risks:
            raise CommandError(
                'Configuracion runtime insegura: ' + '; '.join(risks)
            )
        self.stdout.write(self.style.SUCCESS('Configuracion runtime de PostgreSQL segura.'))

    def _collect_report(self):
        # PRE: connection.vendor == 'postgresql'.
        # POST: returns a read-only snapshot of the current role's exposure
        # to operations_auditlog, without touching credentials or the DSN.
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_user;')
            current_user = cursor.fetchone()[0]

            cursor.execute(
                'SELECT rolsuper FROM pg_roles WHERE rolname = %s;',
                [current_user],
            )
            row = cursor.fetchone()
            is_superuser = bool(row[0]) if row else False

            cursor.execute(
                'SELECT pg_get_userbyid(relowner) FROM pg_class '
                'WHERE relname = %s AND relnamespace = %s::regnamespace;',
                [AUDITLOG_TABLE, AUDITLOG_SCHEMA],
            )
            owner_row = cursor.fetchone()
            table_owner = owner_row[0] if owner_row else None
            is_owner = table_owner is not None and table_owner == current_user

            privileges = {}
            for privilege in REPORTED_PRIVILEGES:
                cursor.execute(
                    'SELECT has_table_privilege(%s, %s, %s);',
                    [current_user, AUDITLOG_QUALIFIED_NAME, privilege],
                )
                privileges[privilege] = bool(cursor.fetchone()[0])

            cursor.execute(
                'SELECT EXISTS ('
                '  SELECT 1 FROM pg_trigger'
                '  WHERE tgrelid = %s::regclass'
                '    AND tgname = %s'
                '    AND NOT tgisinternal'
                ');',
                [AUDITLOG_QUALIFIED_NAME, AUDITLOG_TRIGGER_NAME],
            )
            trigger_installed = bool(cursor.fetchone()[0])

        return {
            'current_user': current_user,
            'is_superuser': is_superuser,
            'is_owner': is_owner,
            'table_owner': table_owner,
            'privileges': privileges,
            'trigger_installed': trigger_installed,
        }

    def _print_report(self, report):
        yes_no = {True: 'si', False: 'no'}
        self.stdout.write(f"Usuario runtime: {report['current_user']}")
        self.stdout.write(f"Es superusuario: {yes_no[report['is_superuser']]}")
        self.stdout.write(
            f"Es propietario de {AUDITLOG_TABLE}: {yes_no[report['is_owner']]}"
        )
        for privilege in REPORTED_PRIVILEGES:
            granted = report['privileges'].get(privilege, False)
            self.stdout.write(f"Privilegio {privilege} sobre {AUDITLOG_TABLE}: {yes_no[granted]}")
        self.stdout.write(
            f"Trigger append-only ({AUDITLOG_TRIGGER_NAME}) instalado: "
            f"{yes_no[report['trigger_installed']]}"
        )

    def _risks(self, report):
        # PRE: report was produced by _collect_report for the current role.
        # POST: returns a list of human-readable reasons the runtime role is unsafe.
        risks = []
        if report['is_superuser']:
            risks.append('el usuario runtime es superusuario')
        if report['is_owner']:
            risks.append(f'el usuario runtime es propietario de {AUDITLOG_TABLE}')
        for privilege in REQUIRED_PRIVILEGES:
            if not report['privileges'].get(privilege):
                risks.append(
                    f'el usuario runtime no posee el privilegio {privilege} '
                    f'sobre {AUDITLOG_TABLE}'
                )
        for privilege in DANGEROUS_PRIVILEGES:
            if report['privileges'].get(privilege):
                risks.append(
                    f'el usuario runtime posee el privilegio {privilege} sobre {AUDITLOG_TABLE}'
                )
        if not report['trigger_installed']:
            risks.append('el trigger append-only no esta instalado')
        return risks
