import os
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import capfirst

from apps.operations.choices import OPERATING_CURRENCY
from apps.operations.models import (
    AuditLog,
    Donation,
    Expense,
    FundAllocation,
    Institution,
    Project,
    ProjectUpdate,
    SupportingDocument,
)
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR, ROLE_SIGEDON_ADMIN
from apps.operations.services import log_action, review_project_update, validate_expense


DEMO_DATE = date(2026, 7, 8)


class Command(BaseCommand):
    help = 'Crea datos demo idempotentes para probar SIGEDON localmente.'

    def handle(self, *args, **options):
        groups = sync_operation_roles()
        user = self.create_demo_user()
        role_users = self.create_role_demo_users(groups)
        donor, executor = self.create_institutions()
        project_active, project_planned = self.create_projects()
        donation = self.create_donation(donor)
        allocation = self.create_allocation(donation, project_active)
        expense = self.create_expense(allocation, user)
        self.create_supporting_document(expense)
        expense = validate_expense(expense.pk, user)
        self.create_project_updates(project_active, project_planned, user)
        self.create_demo_audit(user, donation, allocation)

        self.stdout.write(self.style.SUCCESS('Datos demo de SIGEDON creados o actualizados correctamente.'))
        self.stdout.write(f'Usuario demo: {user.username}')
        for role_name, role_user in role_users.items():
            self.stdout.write(f'{role_name}: {role_user.username}')

    def create_demo_user(self):
        username = os.environ.get('SIGEDON_DEMO_USERNAME', 'sigedon_demo')
        email = os.environ.get('SIGEDON_DEMO_EMAIL', 'demo@sigedon.local')
        password = os.environ.get('SIGEDON_DEMO_PASSWORD', 'sigedon-demo-12345')
        user, created = get_user_model().objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created:
            user.set_password(password)
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.save()
        return user

    def create_role_demo_users(self, groups):
        role_defaults = {
            ROLE_SIGEDON_ADMIN: (
                os.environ.get('SIGEDON_DEMO_ADMIN_USERNAME', 'admin_sigedon'),
                os.environ.get('SIGEDON_DEMO_ADMIN_EMAIL', 'admin@sigedon.local'),
                os.environ.get('SIGEDON_DEMO_ADMIN_PASSWORD', 'admin-sigedon-12345'),
            ),
            ROLE_FIELD_OPERATOR: (
                os.environ.get('SIGEDON_DEMO_FIELD_USERNAME', 'campo_sigedon'),
                os.environ.get('SIGEDON_DEMO_FIELD_EMAIL', 'campo@sigedon.local'),
                os.environ.get('SIGEDON_DEMO_FIELD_PASSWORD', 'campo-sigedon-12345'),
            ),
            ROLE_EXTERNAL_AUDITOR: (
                os.environ.get('SIGEDON_DEMO_AUDITOR_USERNAME', 'auditor_sigedon'),
                os.environ.get('SIGEDON_DEMO_AUDITOR_EMAIL', 'auditor@sigedon.local'),
                os.environ.get('SIGEDON_DEMO_AUDITOR_PASSWORD', 'auditor-sigedon-12345'),
            ),
        }
        role_users = {}
        for role_name, (username, email, password) in role_defaults.items():
            user, created = get_user_model().objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'is_staff': role_name == ROLE_SIGEDON_ADMIN,
                },
            )
            if created:
                user.set_password(password)
            user.email = email
            user.is_staff = role_name == ROLE_SIGEDON_ADMIN
            user.is_superuser = False
            user.save()
            user.groups.add(groups[role_name])
            role_users[role_name] = user
        return role_users

    def create_institutions(self):
        donor, _ = Institution.objects.get_or_create(
            name='Fundación Demo SIGEDON',
            defaults={
                'institution_type': 'foundation',
                'role': Institution.Role.DONOR,
                'country': 'VE',
                'contact_email': 'donante.demo@sigedon.local',
                'responsible_person': 'Coordinación Demo',
                'status': Institution.Status.ACTIVE,
            },
        )
        executor, _ = Institution.objects.get_or_create(
            name='Parroquia Ejecutora Demo',
            defaults={
                'institution_type': 'parish',
                'role': Institution.Role.EXECUTOR,
                'country': 'VE',
                'responsible_person': 'Equipo Operativo Demo',
                'status': Institution.Status.ACTIVE,
            },
        )
        return donor, executor

    def create_projects(self):
        active, _ = Project.objects.get_or_create(
            code='PRJ-DEMO-001',
            defaults={
                'name': 'Atención alimentaria y psicosocial Demo',
                'description': 'Proyecto demo visible en el portal público.',
                'objective': 'Apoyar familias con alimentos y acompañamiento psicosocial.',
                'responsible_unit': 'Pastoral Social',
                'location': 'Caracas',
                'estimated_budget': Decimal('5000.00'),
                'start_date': DEMO_DATE,
                'status': Project.Status.ACTIVE,
            },
        )
        planned, _ = Project.objects.get_or_create(
            code='PRJ-DEMO-002',
            defaults={
                'name': 'Proyecto planificado no público Demo',
                'description': 'Proyecto demo para mostrar estados internos.',
                'objective': 'Preparar una nueva iniciativa operativa.',
                'responsible_unit': 'Equipo SIGEDON',
                'location': 'Vargas',
                'estimated_budget': Decimal('2500.00'),
                'start_date': DEMO_DATE,
                'status': Project.Status.PLANNED,
            },
        )
        return active, planned

    def create_donation(self, donor):
        donation, _ = Donation.objects.get_or_create(
            code='DON-DEMO-001',
            defaults={
                'donor': donor,
                'donation_type': 'food',
                'amount': Decimal('3000.00'),
                'currency': OPERATING_CURRENCY,
                'objective': 'Financiar atención alimentaria del proyecto demo.',
                'commitment_date': DEMO_DATE,
                'received_date': DEMO_DATE,
                'status': Donation.Status.RECEIVED,
                'support_reference': 'REF-DEMO-001',
            },
        )
        return donation

    def create_allocation(self, donation, project):
        allocation, _ = FundAllocation.objects.get_or_create(
            donation=donation,
            project=project,
            budget_category='health_psychosocial',
            defaults={
                'amount': Decimal('1800.00'),
                'responsible_person': 'Administración Demo',
                'allocation_date': DEMO_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': 'Asignación demo para salud y apoyo psicosocial.',
            },
        )
        return allocation

    def create_expense(self, allocation, user):
        expense, created = Expense.objects.get_or_create(
            allocation=allocation,
            reason='Compra demo de alimentos',
            defaults={
                'expense_date': DEMO_DATE,
                'category': 'food',
                'amount': Decimal('450.00'),
                'currency': OPERATING_CURRENCY,
                'provider_or_recipient': 'Proveedor Demo',
                'payment_method': 'bank_transfer',
                'description': 'Gasto demo con documento soporte.',
                'status': Expense.Status.REGISTERED,
            },
        )
        if created:
            log_action(user, AuditLog.Action.EXECUTED, expense, 'Gasto demo registrado.')
        return expense

    def create_supporting_document(self, expense):
        document, created = SupportingDocument.objects.get_or_create(
            expense=expense,
            title='Factura demo de alimentos',
            defaults={'notes': 'Documento demo generado por seed_sigedon_demo.'},
        )
        if created:
            document.document.save('factura-demo.txt', ContentFile(b'Soporte demo SIGEDON'), save=True)
        return document

    def create_project_updates(self, project_active, _project_planned, user):
        approved, _ = ProjectUpdate.objects.get_or_create(
            project=project_active,
            title='Entrega alimentaria aprobada Demo',
            defaults={
                'description': 'Se completo una entrega demo aprobada para consulta publica.',
                'status': ProjectUpdate.Status.PENDING_REVIEW,
                'created_by': user,
            },
        )
        if approved.status == ProjectUpdate.Status.PENDING_REVIEW:
            review_project_update(approved.pk, user, ProjectUpdate.Status.APPROVED, 'Aprobado para demo publica.')

        ProjectUpdate.objects.get_or_create(
            project=project_active,
            title='Compra pendiente de revisión Demo',
            defaults={
                'description': 'Avance demo pendiente de revisión interna.',
                'status': ProjectUpdate.Status.PENDING_REVIEW,
                'created_by': user,
            },
        )
        ProjectUpdate.objects.get_or_create(
            project=project_active,
            title='Evidencia rechazada Demo',
            defaults={
                'description': 'Avance demo rechazado para mostrar estados internos.',
                'status': ProjectUpdate.Status.REJECTED,
                'created_by': user,
                'reviewed_by': user,
                'reviewed_at': timezone.now(),
                'review_notes': 'Evidencia insuficiente para demo.',
            },
        )
    def create_demo_audit(self, user, donation, allocation):
        events = [
            (AuditLog.Action.CREATED, donation, 'Donación demo creada.'),
            (AuditLog.Action.ASSIGNED, allocation, 'Asignación demo registrada.'),
        ]
        for action, instance, summary in events:
            AuditLog.objects.get_or_create(
                action=action,
                model_name=capfirst(instance._meta.verbose_name),
                entity_id=str(instance.pk),
                summary=summary,
                defaults={
                    'user': user,
                    'entity_label': str(instance),
                },
            )
