from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import BaseCommand, CommandError, call_command
from django.db import transaction
from django.utils import timezone

from apps.operations.models import (
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
)


DEFAULT_DEMO_PASSWORD = "DemoSigedon2026!"


def first_choice_value(choices: Any) -> str:
    """
    PRE: choices contiene al menos una opción Django válida.
    POST: retorna el valor almacenado de la primera opción.
    """
    if not choices:
        raise CommandError("Uno de los catálogos de opciones está vacío.")

    choice = choices[0]
    return str(choice[0])


def save_validated(instance):
    """
    PRE: instance contiene un estado de dominio coherente.
    POST: valida y persiste la instancia.
    """
    instance.full_clean()
    instance.save()
    return instance


class Command(BaseCommand):
    help = (
        "Puebla SIGEDON con usuarios, instituciones, proyectos, "
        "donaciones, asignaciones, gastos y avances de demostración."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=os.getenv("SIGEDON_DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD),
            help="Contraseña para los usuarios demo.",
        )
        parser.add_argument(
            "--skip-users",
            action="store_true",
            help="No crear ni actualizar usuarios de demostración.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        """
        PRE:
        - las migraciones están aplicadas;
        - PostgreSQL está disponible;
        - los modelos operations están cargados.

        POST:
        - existe un conjunto demo coherente e idempotente;
        - no se exceden saldos;
        - los cinco proyectos de Kobo conservan sus códigos;
        - las ejecuciones posteriores actualizan sin duplicar.
        """
        password = options["password"]
        skip_users = options["skip_users"]

        self.stdout.write("Sincronizando roles de SIGEDON...")
        call_command("sync_sigedon_roles", verbosity=0)

        users = {}
        if not skip_users:
            users = self._create_users(password)

        institutions = self._create_institutions()
        projects = self._create_projects()
        donation = self._create_donation(institutions["donor"])
        allocations = self._create_allocations(donation, projects)
        expenses = self._create_expenses(allocations)
        updates = self._create_project_updates(
            projects=projects,
            operator=users.get("operator"),
            reviewer=users.get("admin"),
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Base demo preparada correctamente."))
        self.stdout.write(f"Instituciones: {len(institutions)}")
        self.stdout.write(f"Proyectos: {len(projects)}")
        self.stdout.write("Donaciones: 1")
        self.stdout.write(f"Asignaciones: {len(allocations)}")
        self.stdout.write(f"Gastos: {len(expenses)}")
        self.stdout.write(f"Avances: {len(updates)}")

        if not skip_users:
            self.stdout.write("")
            self.stdout.write("Usuarios demo:")
            self.stdout.write("  admin_demo")
            self.stdout.write("  operador_demo")
            self.stdout.write("  auditor_demo")
            self.stdout.write(
                self.style.WARNING(
                    "Todos usan la contraseña indicada mediante "
                    "--password o SIGEDON_DEMO_PASSWORD."
                )
            )

    def _create_users(self, password: str) -> dict[str, Any]:
        """
        PRE: los grupos operativos ya fueron sincronizados.
        POST: crea o actualiza tres usuarios demo y sus grupos.
        """
        User = get_user_model()

        definitions = {
            "admin": {
                "username": "admin_demo",
                "email": "admin.demo@sigedon.local",
                "first_name": "Administrador",
                "last_name": "SIGEDON",
                "group": "Administrador SIGEDON",
                "is_staff": True,
            },
            "operator": {
                "username": "operador_demo",
                "email": "operador.demo@sigedon.local",
                "first_name": "Operador",
                "last_name": "de campo",
                "group": "Operador de campo",
                "is_staff": False,
            },
            "auditor": {
                "username": "auditor_demo",
                "email": "auditor.demo@sigedon.local",
                "first_name": "Auditor",
                "last_name": "externo",
                "group": "Auditor externo",
                "is_staff": False,
            },
        }

        result = {}

        for key, definition in definitions.items():
            try:
                group = Group.objects.get(name=definition["group"])
            except Group.DoesNotExist as exc:
                raise CommandError(
                    f"No existe el grupo {definition['group']!r}."
                ) from exc

            user, _ = User.objects.update_or_create(
                username=definition["username"],
                defaults={
                    "email": definition["email"],
                    "first_name": definition["first_name"],
                    "last_name": definition["last_name"],
                    "is_active": True,
                    "is_staff": definition["is_staff"],
                },
            )

            user.set_password(password)
            user.save(update_fields=["password"])
            user.groups.set([group])

            result[key] = user

        return result

    def _create_institutions(self) -> dict[str, Institution]:
        """
        PRE: los choices de Institution están disponibles.
        POST: retorna instituciones activas de demostración.
        """
        institution_type = first_choice_value(
            Institution._meta.get_field("institution_type").choices
        )

        definitions = {
            "donor": {
                "name": "Fondo Humanitario Internacional",
                "role": Institution.Role.DONOR,
                "country": "ES",
                "contact_email": "cooperacion@fondo-demo.org",
                "contact_phone": "+34 000 000 000",
                "responsible_person": "María González",
            },
            "receiver": {
                "name": "Diócesis de La Guaira",
                "role": Institution.Role.RECEIVER,
                "country": "VE",
                "contact_email": "proyectos@diocesis-demo.org",
                "contact_phone": "+58 000 000 0000",
                "responsible_person": "Coordinación de proyectos",
            },
            "executor": {
                "name": "Equipo Territorial SIGEDON",
                "role": Institution.Role.EXECUTOR,
                "country": "VE",
                "contact_email": "territorio@sigedon.local",
                "contact_phone": "+58 000 000 0001",
                "responsible_person": "Coordinación territorial",
            },
        }

        result = {}

        for key, data in definitions.items():
            institution, _ = Institution.objects.update_or_create(
                name=data["name"],
                defaults={
                    "institution_type": institution_type,
                    "role": data["role"],
                    "country": data["country"],
                    "contact_email": data["contact_email"],
                    "contact_phone": data["contact_phone"],
                    "responsible_person": data["responsible_person"],
                    "status": Institution.Status.ACTIVE,
                },
            )

            save_validated(institution)
            result[key] = institution

        return result

    def _create_projects(self) -> dict[str, Project]:
        """
        PRE: no existe otro proyecto con los códigos reservados.
        POST: crea los cinco proyectos territoriales activos de Kobo.
        """
        today = date.today()

        definitions = [
            (
                "catia_la_mar",
                "PRJ-000002",
                "Núcleo Vital Catia la Mar",
                "Zona Pastoral Catia la Mar",
                Decimal("50000.00"),
            ),
            (
                "centro",
                "PRJ-000003",
                "Núcleo Vital Centro",
                "Zona Pastoral Centro",
                Decimal("45000.00"),
            ),
            (
                "este",
                "PRJ-000004",
                "Núcleo Vital Este",
                "Zona Pastoral Este",
                Decimal("40000.00"),
            ),
            (
                "montana",
                "PRJ-000005",
                "Núcleo Vital La Montaña",
                "Zona Pastoral La Montaña",
                Decimal("35000.00"),
            ),
            (
                "insular",
                "PRJ-000006",
                "Núcleo Vital Insular",
                "Zona Pastoral Insular",
                Decimal("30000.00"),
            ),
        ]

        projects = {}

        for key, code, name, location, budget in definitions:
            project, _ = Project.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": (
                        "Proyecto territorial para diagnóstico, priorización "
                        "y seguimiento de acciones comunitarias."
                    ),
                    "objective": (
                        "Fortalecer la respuesta comunitaria y la gestión "
                        "transparente de recursos."
                    ),
                    "responsible_unit": "Coordinación territorial SIGEDON",
                    "location": location,
                    "estimated_budget": budget,
                    "start_date": today - timedelta(days=30),
                    "end_date": today + timedelta(days=335),
                    "status": Project.Status.ACTIVE,
                },
            )

            save_validated(project)
            projects[key] = project

        return projects

    def _create_donation(self, donor: Institution) -> Donation:
        """
        PRE: donor es una institución donante activa.
        POST: crea una donación recibida con saldo suficiente.
        """
        today = date.today()
        donation_type = first_choice_value(
            Donation._meta.get_field("donation_type").choices
        )

        donation, _ = Donation.objects.update_or_create(
            code="DON-000001",
            defaults={
                "donor": donor,
                "donation_type": donation_type,
                "amount": Decimal("200000.00"),
                "currency": "USD",
                "objective": (
                    "Financiar actividades comunitarias de los cinco "
                    "Núcleos Vitales de La Guaira."
                ),
                "restrictions": (
                    "Uso exclusivo en actividades aprobadas y documentadas."
                ),
                "commitment_date": today - timedelta(days=50),
                "received_date": today - timedelta(days=40),
                "status": Donation.Status.PARTIALLY_ALLOCATED,
                "support_reference": "CONVENIO-DEMO-2026-001",
            },
        )

        return save_validated(donation)

    def _create_allocations(
        self,
        donation: Donation,
        projects: dict[str, Project],
    ) -> dict[str, FundAllocation]:
        """
        PRE: donation posee saldo suficiente para todas las asignaciones.
        POST: crea cinco asignaciones cuya suma no excede la donación.
        """
        today = date.today()
        budget_category = first_choice_value(
            FundAllocation._meta.get_field("budget_category").choices
        )

        amounts = {
            "catia_la_mar": Decimal("40000.00"),
            "centro": Decimal("35000.00"),
            "este": Decimal("30000.00"),
            "montana": Decimal("25000.00"),
            "insular": Decimal("20000.00"),
        }

        allocations = {}

        for key, amount in amounts.items():
            project = projects[key]

            allocation, _ = FundAllocation.objects.update_or_create(
                donation=donation,
                project=project,
                budget_category=budget_category,
                defaults={
                    "amount": amount,
                    "responsible_person": "Coordinación financiera SIGEDON",
                    "allocation_date": today - timedelta(days=25),
                    "status": FundAllocation.Status.ACTIVE,
                    "notes": "Asignación inicial del escenario demostrativo.",
                },
            )

            allocations[key] = save_validated(allocation)

        return allocations

    def _create_expenses(
        self,
        allocations: dict[str, FundAllocation],
    ) -> list[Expense]:
        """
        PRE: las asignaciones tienen saldo disponible.
        POST: crea gastos operativos sin exceder ninguna asignación.
        """
        today = date.today()

        category = first_choice_value(
            Expense._meta.get_field("category").choices
        )
        payment_method = first_choice_value(
            Expense._meta.get_field("payment_method").choices
        )

        definitions = [
            (
                allocations["catia_la_mar"],
                "Compra de materiales comunitarios",
                Decimal("3500.00"),
                "Proveedor comunitario Catia la Mar",
                15,
            ),
            (
                allocations["centro"],
                "Jornada de diagnóstico territorial",
                Decimal("2200.00"),
                "Equipo técnico territorial",
                12,
            ),
            (
                allocations["este"],
                "Logística para asamblea comunitaria",
                Decimal("1800.00"),
                "Proveedor logístico local",
                9,
            ),
            (
                allocations["montana"],
                "Traslado de equipo de campo",
                Decimal("950.00"),
                "Servicio de transporte",
                6,
            ),
        ]

        expenses = []

        for allocation, reason, amount, recipient, days_ago in definitions:
            expense, _ = Expense.objects.update_or_create(
                allocation=allocation,
                reason=reason,
                defaults={
                    "expense_date": today - timedelta(days=days_ago),
                    "category": category,
                    "amount": amount,
                    "currency": "USD",
                    "provider_or_recipient": recipient,
                    "payment_method": payment_method,
                    "description": "Registro demostrativo para pruebas operativas.",
                    "observations": "",
                    "status": Expense.Status.REGISTERED,
                    "validated_by": None,
                    "validated_at": None,
                },
            )

            expenses.append(save_validated(expense))

        return expenses

    def _create_project_updates(
        self,
        *,
        projects: dict[str, Project],
        operator,
        reviewer,
    ) -> list[ProjectUpdate]:
        """
        PRE: los proyectos están activos.
        POST: crea avances demo en estados coherentes.
        """
        definitions = [
            {
                "project": projects["catia_la_mar"],
                "title": "Levantamiento territorial inicial",
                "description": (
                    "Se completó el primer recorrido territorial y la "
                    "identificación preliminar de comunidades."
                ),
                "status": ProjectUpdate.Status.APPROVED if reviewer else ProjectUpdate.Status.PENDING_REVIEW,
                "review_notes": "Avance validado para demostración." if reviewer else "",
            },
            {
                "project": projects["centro"],
                "title": "Organización de mesas comunitarias",
                "description": (
                    "Se iniciaron reuniones con actores comunitarios y "
                    "representantes parroquiales."
                ),
                "status": ProjectUpdate.Status.PENDING_REVIEW,
                "review_notes": "",
            },
            {
                "project": projects["este"],
                "title": "Planificación del diagnóstico",
                "description": (
                    "Se encuentra en preparación el cronograma de visitas "
                    "y levantamiento de información."
                ),
                "status": ProjectUpdate.Status.DRAFT,
                "review_notes": "",
            },
        ]

        updates = []

        for data in definitions:
            update, _ = ProjectUpdate.objects.update_or_create(
                project=data["project"],
                title=data["title"],
                defaults={
                    "description": data["description"],
                    "status": data["status"],
                    "created_by": operator,
                    "reviewed_by": (
                        reviewer
                        if data["status"] == ProjectUpdate.Status.APPROVED
                        else None
                    ),
                    "reviewed_at": (
                        timezone.now()
                        if data["status"] == ProjectUpdate.Status.APPROVED
                        else None
                    ),
                    "review_notes": data["review_notes"],
                },
            )

            updates.append(save_validated(update))

        return updates