from __future__ import annotations

import os

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from apps.operations.demo_seed import (
    DEFAULT_DEMO_PASSWORD,
    DEMO_USER_DEFINITIONS,
    collect_demo_counts,
    seed_sigedon_demo,
    validate_demo_password,
    verify_sigedon_demo,
)


class Command(BaseCommand):
    help = (
        'Puebla SIGEDON con usuarios, instituciones, proyectos, '
        'donaciones, asignaciones, gastos, solicitudes y avances de demostración.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            default=os.getenv('SIGEDON_DEMO_PASSWORD', DEFAULT_DEMO_PASSWORD),
            help='Contraseña para los usuarios demo (solo local; nunca se imprime).',
        )
        parser.add_argument(
            '--skip-users',
            action='store_true',
            help='No crear ni actualizar usuarios de demostración.',
        )
        parser.add_argument(
            '--verify',
            action='store_true',
            help='Verifica el entorno demo sin realizar escrituras.',
        )

    def handle(self, *args, **options):
        """
        PRE:
        - las migraciones están aplicadas;
        - DEBUG=True para mutación (desarrollo local o efímero);
        - --verify puede ejecutarse en lectura cuando DEBUG=True.

        POST:
        - si DEBUG=False: CommandError antes de cualquier escritura;
        - si --verify: cero escrituras; exit distinto de cero si incompleto;
        - si DEBUG=True: conjunto demo coherente e idempotente;
        - la contraseña demo nunca se imprime.
        """
        if not settings.DEBUG:
            raise CommandError(
                'seed_sigedon_demo is disabled when DEBUG=False.'
            )

        if options['verify']:
            self._verify_only()
            return

        password = validate_demo_password(options['password'])
        result = seed_sigedon_demo(
            password=password,
            skip_users=options['skip_users'],
        )
        self._print_summary(result, skip_users=options['skip_users'])

    def _verify_only(self):
        """
        PRE: DEBUG=True; database is readable.
        POST: prints sanitized verification result; raises CommandError when incomplete.
        """
        problems = verify_sigedon_demo()
        if problems:
            self.stderr.write(self.style.ERROR('La verificación de la demo falló:'))
            for problem in problems:
                self.stderr.write(f'  - {problem}')
            raise CommandError(
                f'La verificación de la demo falló con {len(problems)} problema(s).'
            )

        counts = collect_demo_counts()
        self.stdout.write(self.style.SUCCESS('Verificación de la demo correcta.'))
        for label, value in counts.items():
            self.stdout.write(f'{label}: {value}')

    def _print_summary(self, result, *, skip_users: bool):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Base demo preparada correctamente.'))
        counts = result.counts or collect_demo_counts()
        self.stdout.write(f"Instituciones: {counts.get('institutions', 0)}")
        self.stdout.write(f"Proyectos: {counts.get('projects', 0)}")
        self.stdout.write(f"Donaciones: {counts.get('donations', 0)}")
        self.stdout.write(f"Asignaciones: {counts.get('allocations', 0)}")
        self.stdout.write(f"Gastos: {counts.get('expenses', 0)}")
        self.stdout.write(
            f"Solicitudes de gasto: {counts.get('expense_requests', 0)}"
        )
        self.stdout.write(f"Avances: {counts.get('updates', 0)}")

        if not skip_users:
            self.stdout.write('')
            self.stdout.write('Usuarios demo:')
            for definition in DEMO_USER_DEFINITIONS.values():
                self.stdout.write(f'  {definition["username"]}')
            self.stdout.write('Demo credentials configured.')
