"""
Local SIGEDON demo seed helpers (DEBUG=True only).

PRE: callers enforce DEBUG=True before mutation; PostgreSQL/SQLite is disposable.
POST: idempotent demo entities under stable DEMO codes / @sigedon.local users;
      lifecycle transitions go through domain services when they exist;
      passwords and private storage paths are never returned to callers for display.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models, transaction

from apps.operations.expense_request_services import (
    add_expense_request_attachments,
    annul_expense_request,
    approve_expense_request,
    create_expense_request,
    deny_expense_request,
    fulfill_expense_request,
    withdraw_expense_request,
)
from apps.operations.models import (
    Donation,
    Expense,
    ExpenseRequest,
    ExpenseRequestAttachment,
    ExpenseRequestEvent,
    FundAllocation,
    Institution,
    Project,
    ProjectDocument,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
    ProjectUpdateReview,
    ProjectUpdateReviewDecision,
    SupportingDocument,
)
from apps.operations.role_services import get_user_functional_role, set_user_functional_role
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.services import (
    add_project_update_attachment,
    add_project_update_remediation_attachment,
    create_expense_legacy,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    finish_fund_allocation,
    finish_project,
    publish_project,
    publish_project_update,
    publish_project_update_attachment,
    register_advance,
    transition_donation_status,
    unpublish_project_update_attachment,
)


DEMO_USER_EMAIL_DOMAIN = 'sigedon.local'
DEMO_INSTITUTION_COUNTRY = 'ZZ'
DEMO_TINY_PDF = b'%PDF-1.4\n%SIGEDON-DEMO\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'

# Visible demo copy must stay inside the fictional Monteluz universe.
DEMO_FORBIDDEN_SUBSTRINGS = (
    'la guaira',
    'catia',
    'caraballeda',
    'diócesis',
    'diocesis',
    'núcleo vital',
    'nucleo vital',
    'zona pastoral',
)

DEMO_ER_PURPOSE = {
    'pending': '[DEMO-ER:pending] Solicitud pendiente de decisión del comité',
    'approved': '[DEMO-ER:approved] Solicitud aprobada pendiente de cumplimiento',
    'denied': '[DEMO-ER:denied] Solicitud denegada por el comité',
    'withdrawn': '[DEMO-ER:withdrawn] Solicitud retirada por el operador',
    'fulfilled': '[DEMO-ER:fulfilled] Solicitud cumplida con gasto registrado',
    'annulled': '[DEMO-ER:annulled] Solicitud anulada administrativamente',
}

DEMO_UPDATE_TITLE = {
    'draft': '[DEMO-UPD:draft] Borrador no publicado',
    'published_public': '[DEMO-UPD:published] Avance publicado en portal',
    'reviewed_ok': '[DEMO-UPD:conforming] Avance publicado conforme',
    'observed': '[DEMO-UPD:observed] Avance publicado con observación',
    'no_attachment': '[DEMO-UPD:plain] Avance publicado sin adjunto',
}

DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE = 'Evidencia DEMO pública'
DEMO_UPDATE_PRIVATE_ATTACHMENT_TITLE = 'Evidencia DEMO interna'

DEMO_USER_DEFINITIONS = {
    'admin': {
        'username': 'admin_demo',
        'email': f'admin.demo@{DEMO_USER_EMAIL_DOMAIN}',
        'first_name': 'Elena',
        'last_name': 'Vargas',
        'group': ROLE_SIGEDON_ADMIN,
        'is_staff': True,
    },
    'operator': {
        'username': 'operador_demo',
        'email': f'operador.demo@{DEMO_USER_EMAIL_DOMAIN}',
        'first_name': 'Mateo',
        'last_name': 'Solano',
        'group': ROLE_FIELD_OPERATOR,
        'is_staff': False,
    },
    'auditor': {
        'username': 'auditor_demo',
        'email': f'auditor.demo@{DEMO_USER_EMAIL_DOMAIN}',
        'first_name': 'Nuria',
        'last_name': 'Belmonte',
        'group': ROLE_EXTERNAL_AUDITOR,
        'is_staff': False,
    },
    'committee': {
        'username': 'comite_demo',
        'email': f'comite.demo@{DEMO_USER_EMAIL_DOMAIN}',
        'first_name': 'Paula',
        'last_name': 'Herrera',
        'group': ROLE_PROJECT_COMMITTEE,
        'is_staff': False,
    },
}

DEMO_PROJECT_CODES = (
    'PRJ-DEMO-001',
    'PRJ-DEMO-002',
    'PRJ-DEMO-003',
    'PRJ-DEMO-004',
    'PRJ-DEMO-005',
    'PRJ-DEMO-006',
    'PRJ-DEMO-007',
)

DEMO_DONATION_CODES = (
    'DON-DEMO-001',
    'DON-DEMO-002',
    'DON-DEMO-003',
    'DON-DEMO-004',
    'DON-DEMO-005',
)


def first_choice_value(choices: Any) -> str:
    """
    PRE: choices contiene al menos una opción Django válida.
    POST: retorna el valor almacenado de la primera opción.
    """
    if not choices:
        raise CommandError('Uno de los catálogos de opciones está vacío.')
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


def demo_upload(filename: str, content: bytes = DEMO_TINY_PDF) -> SimpleUploadedFile:
    """
    PRE: filename ends with an allowed extension (.pdf); content is tiny non-executable bytes.
    POST: returns an in-memory upload suitable for private storage (storage-agnostic).
    """
    return SimpleUploadedFile(filename, content, content_type='application/pdf')


def validate_demo_password(password: str) -> str:
    """
    PRE: password is the resolved --password or SIGEDON_DEMO_PASSWORD value.
    POST: returns a non-empty password or raises CommandError; never logs the value.
    """
    if password is None or not str(password).strip():
        raise CommandError('Demo password cannot be empty.')
    return str(password)


def resolve_demo_password(*, cli_password: str | None) -> str:
    """
    PRE: cli_password is --password when provided, otherwise None.
    POST: returns --password if present, else SIGEDON_DEMO_PASSWORD;
          raises CommandError if neither is a non-empty value; never logs it.
    """
    if cli_password is not None:
        return validate_demo_password(cli_password)
    env_password = os.getenv('SIGEDON_DEMO_PASSWORD')
    if env_password is None or not str(env_password).strip():
        raise CommandError(
            'Falta la contraseña demo. Use --password o defina SIGEDON_DEMO_PASSWORD.'
        )
    return validate_demo_password(env_password)


def _anchor_date() -> date:
    return date.today()


class DemoSeedResult:
    def __init__(self):
        self.users: dict[str, Any] = {}
        self.institutions: dict[str, Institution] = {}
        self.projects: dict[str, Project] = {}
        self.donations: dict[str, Donation] = {}
        self.allocations: dict[str, FundAllocation] = {}
        self.expenses: list[Expense] = []
        self.expense_requests: dict[str, ExpenseRequest] = {}
        self.updates: dict[str, ProjectUpdate] = {}
        self.counts: dict[str, int] = {}


def seed_sigedon_demo(*, password: str, skip_users: bool = False) -> DemoSeedResult:
    """
    PRE: DEBUG=True already enforced by the management command; roles can be synced.
    POST: creates/updates the full-role demo matrix atomically; never prints passwords.
    """
    password = validate_demo_password(password)
    result = DemoSeedResult()

    with transaction.atomic():
        call_command('sync_sigedon_roles', verbosity=0, stdout=StringIO())

        if skip_users:
            result.users = _load_existing_demo_users()
        else:
            result.users = _create_users(password)

        result.institutions = _create_institutions(admin=result.users.get('admin'))
        result.projects = _create_projects(admin=result.users.get('admin'))
        result.donations = _create_donations(
            institutions=result.institutions,
            admin=result.users.get('admin'),
        )
        result.allocations = _create_allocations(
            donations=result.donations,
            projects=result.projects,
        )
        result.expenses = _create_legacy_expenses(
            allocations=result.allocations,
            admin=result.users.get('admin'),
        )
        result.expense_requests = _create_expense_requests(
            allocations=result.allocations,
            users=result.users,
        )
        result.updates = _create_project_updates(
            projects=result.projects,
            users=result.users,
        )
        _attach_private_demo_files(
            institutions=result.institutions,
            projects=result.projects,
            expenses=result.expenses,
            users=result.users,
        )
        _close_demo_project_if_needed(
            projects=result.projects,
            allocations=result.allocations,
            admin=result.users.get('admin'),
        )

        result.counts = collect_demo_counts()

    return result


def collect_demo_counts() -> dict[str, int]:
    """
    PRE: demo seed may or may not have run.
    POST: returns sanitized entity counts for demo-scoped natural keys only.
    """
    User = get_user_model()
    return {
        'users': User.objects.filter(
            username__in=[d['username'] for d in DEMO_USER_DEFINITIONS.values()]
        ).count(),
        'institutions': Institution.objects.filter(
            name__in=_institution_names()
        ).count(),
        'projects': Project.objects.filter(code__in=DEMO_PROJECT_CODES).count(),
        'donations': Donation.objects.filter(code__in=DEMO_DONATION_CODES).count(),
        'allocations': FundAllocation.objects.filter(
            donation__code__in=DEMO_DONATION_CODES
        ).count(),
        'expenses': Expense.objects.filter(
            allocation__donation__code__in=DEMO_DONATION_CODES
        ).count(),
        'expense_requests': ExpenseRequest.objects.filter(
            purpose__in=DEMO_ER_PURPOSE.values()
        ).count(),
        'expense_request_events': ExpenseRequestEvent.objects.filter(
            expense_request__purpose__in=DEMO_ER_PURPOSE.values()
        ).count(),
        'updates': ProjectUpdate.objects.filter(
            title__in=DEMO_UPDATE_TITLE.values()
        ).count(),
        'supporting_documents': SupportingDocument.objects.filter(
            expense__allocation__donation__code__in=DEMO_DONATION_CODES
        ).count(),
    }


def verify_sigedon_demo() -> list[str]:
    """
    PRE: database is readable; no writes are performed.
    POST: returns a list of sanitized problem messages (empty means ready).
    """
    errors: list[str] = []
    User = get_user_model()

    for key, definition in DEMO_USER_DEFINITIONS.items():
        try:
            user = User.objects.get(username=definition['username'])
        except User.DoesNotExist:
            errors.append(f'Missing demo user: {definition["username"]}')
            continue
        if not user.is_active:
            errors.append(f'Demo user inactive: {definition["username"]}')
        if user.is_superuser:
            errors.append(f'Demo user must not be superuser: {definition["username"]}')
        if bool(user.is_staff) != bool(definition['is_staff']):
            errors.append(f'Demo user staff flag mismatch: {definition["username"]}')
        role = get_user_functional_role(user)
        if role is None or role.name != definition['group']:
            errors.append(f'Demo user role mismatch: {definition["username"]}')

    for code in DEMO_PROJECT_CODES:
        if not Project.objects.filter(code=code).exists():
            errors.append(f'Missing demo project: {code}')

    public = Project.objects.filter(code='PRJ-DEMO-001', is_public=True, status=Project.Status.ACTIVE)
    if not public.exists():
        errors.append('Expected public active project PRJ-DEMO-001')
    private = Project.objects.filter(code='PRJ-DEMO-002', is_public=False)
    if not private.exists():
        errors.append('Expected private project PRJ-DEMO-002')
    empty = Project.objects.filter(code='PRJ-DEMO-006')
    if empty.exists() and FundAllocation.objects.filter(project__code='PRJ-DEMO-006').exists():
        errors.append('PRJ-DEMO-006 must have no financial activity')
    closed = Project.objects.filter(code='PRJ-DEMO-007', status=Project.Status.CLOSED)
    if not closed.exists():
        errors.append('Expected closed project PRJ-DEMO-007')

    for code in DEMO_DONATION_CODES:
        if not Donation.objects.filter(code=code).exists():
            errors.append(f'Missing demo donation: {code}')

    registered = Donation.objects.filter(code='DON-DEMO-002', status=Donation.Status.REGISTERED)
    if not registered.exists():
        errors.append('DON-DEMO-002 must be REGISTERED')
    received = Donation.objects.filter(code='DON-DEMO-001', status=Donation.Status.RECEIVED)
    if not received.exists():
        errors.append('DON-DEMO-001 must be RECEIVED')
    else:
        donation = received.get()
        if donation.available_balance < Decimal('0.00'):
            errors.append('DON-DEMO-001 available balance is negative')
        if donation.allocation_progress == 'unallocated':
            errors.append('DON-DEMO-001 should be partially allocated')

    fully = Donation.objects.filter(code='DON-DEMO-004', status=Donation.Status.RECEIVED).first()
    if fully is None:
        errors.append('Missing DON-DEMO-004')
    elif fully.allocation_progress != 'fully_allocated':
        errors.append('DON-DEMO-004 must be fully allocated')

    for key, purpose in DEMO_ER_PURPOSE.items():
        req = ExpenseRequest.objects.filter(purpose=purpose).first()
        if req is None:
            errors.append(f'Missing expense request scenario: {key}')
            continue
        expected = {
            'pending': ExpenseRequest.Status.PENDING_DECISION,
            'approved': ExpenseRequest.Status.APPROVED_RESERVED,
            'denied': ExpenseRequest.Status.DENIED,
            'withdrawn': ExpenseRequest.Status.WITHDRAWN,
            'fulfilled': ExpenseRequest.Status.FULFILLED,
            'annulled': ExpenseRequest.Status.ANNULLED,
        }[key]
        if req.status != expected:
            errors.append(f'Expense request {key} status mismatch')
        event_count = ExpenseRequestEvent.objects.filter(expense_request=req).count()
        if event_count < 1:
            errors.append(f'Expense request {key} missing events')
        if key == 'fulfilled' and req.expense_id is None:
            errors.append('Fulfilled request must link an expense')
        if key == 'fulfilled' and req.expense_id:
            if not SupportingDocument.objects.filter(expense_id=req.expense_id).exists():
                errors.append('Fulfilled expense missing supporting document')

    for key, title in DEMO_UPDATE_TITLE.items():
        update = ProjectUpdate.objects.filter(title=title).first()
        if update is None:
            errors.append(f'Missing project update scenario: {key}')
            continue
        if key == 'draft' and update.status != ProjectUpdate.Status.UNPUBLISHED:
            errors.append('Draft update must be unpublished')
        if key != 'draft' and update.status != ProjectUpdate.Status.PUBLISHED:
            errors.append(f'Update {key} must be published')
        if key == 'published_public' and not update.attachments.exists():
            errors.append('Public published update should have an attachment')
        if key == 'published_public':
            public_count = update.attachments.filter(is_public=True).count()
            private_count = update.attachments.filter(is_public=False).count()
            if public_count != 1:
                errors.append(
                    'Public published update must have exactly one explicitly public attachment'
                )
            if private_count < 1:
                errors.append(
                    'Public published update must keep at least one private attachment'
                )
        if key == 'reviewed_ok':
            review = ProjectUpdateReview.objects.filter(project_update=update).first()
            if review is None or not hasattr(review, 'decision'):
                errors.append('Conforming update missing review decision')
            elif review.decision.outcome != ProjectUpdateReviewDecision.Outcome.CONFORMING:
                errors.append('Conforming update decision mismatch')
        if key == 'observed':
            review = ProjectUpdateReview.objects.filter(project_update=update).first()
            if review is None or not hasattr(review, 'decision'):
                errors.append('Observed update missing review decision')
            else:
                remediation = ProjectUpdateRemediation.objects.filter(decision=review.decision).first()
                if remediation is None:
                    errors.append('Observed update missing remediation')
                elif not remediation.attachments.exists():
                    errors.append('Observed remediation missing private attachment')

    from django.db.models import Count

    for row in Project.objects.filter(code__in=DEMO_PROJECT_CODES).values('code').annotate(
        c=Count('id')
    ):
        if row['c'] > 1:
            errors.append(f'Duplicate demo project code: {row["code"]}')
    for row in Donation.objects.filter(code__in=DEMO_DONATION_CODES).values('code').annotate(
        c=Count('id')
    ):
        if row['c'] > 1:
            errors.append(f'Duplicate demo donation code: {row["code"]}')
    for purpose in DEMO_ER_PURPOSE.values():
        if ExpenseRequest.objects.filter(purpose=purpose).count() > 1:
            errors.append('Duplicate demo expense request purpose')

    for snippet in _demo_visible_text_snippets():
        lowered = snippet.casefold()
        for forbidden in DEMO_FORBIDDEN_SUBSTRINGS:
            if forbidden in lowered:
                errors.append('Demo visible text still references the original implementation')
                return errors

    return errors


def _institution_names() -> list[str]:
    return [data['name'] for data in _institution_definitions().values()]


def _demo_visible_text_snippets() -> list[str]:
    """
    PRE: demo entities may or may not exist.
    POST: returns concatenated visible fields of demo-scoped records.
    """
    snippets: list[str] = []
    for definition in DEMO_USER_DEFINITIONS.values():
        snippets.extend(
            [
                definition['email'],
                definition['first_name'],
                definition['last_name'],
            ]
        )
    for institution in Institution.objects.filter(name__in=_institution_names()):
        snippets.extend(
            [
                institution.name,
                institution.contact_email,
                institution.contact_phone,
                institution.responsible_person,
            ]
        )
    for project in Project.objects.filter(code__in=DEMO_PROJECT_CODES):
        snippets.extend(
            [
                project.name,
                project.description,
                project.objective,
                project.location,
            ]
        )
    for donation in Donation.objects.filter(code__in=DEMO_DONATION_CODES):
        snippets.extend(
            [
                donation.objective,
                donation.restrictions,
                donation.support_reference,
            ]
        )
    for allocation in FundAllocation.objects.filter(donation__code__in=DEMO_DONATION_CODES):
        snippets.extend([allocation.responsible_person, allocation.notes])
    for expense in Expense.objects.filter(allocation__donation__code__in=DEMO_DONATION_CODES):
        snippets.extend(
            [
                expense.reason,
                expense.provider_or_recipient,
                expense.description,
                expense.observations,
            ]
        )
    return [value for value in snippets if value]


def _load_existing_demo_users() -> dict[str, Any]:
    User = get_user_model()
    result = {}
    for key, definition in DEMO_USER_DEFINITIONS.items():
        try:
            result[key] = User.objects.get(username=definition['username'])
        except User.DoesNotExist:
            continue
    return result


def _create_users(password: str) -> dict[str, Any]:
    User = get_user_model()
    result = {}
    for key, definition in DEMO_USER_DEFINITIONS.items():
        try:
            group = Group.objects.get(name=definition['group'])
        except Group.DoesNotExist as exc:
            raise CommandError(f'No existe el grupo {definition["group"]!r}.') from exc

        user, _ = User.objects.update_or_create(
            username=definition['username'],
            defaults={
                'email': definition['email'],
                'first_name': definition['first_name'],
                'last_name': definition['last_name'],
                'is_active': True,
                'is_staff': definition['is_staff'],
            },
        )
        user.set_password(password)
        user.save(update_fields=['password'])
        set_user_functional_role(user, group)
        if user.is_superuser:
            user.is_superuser = False
            user.save(update_fields=['is_superuser'])
        result[key] = user
    return result


def _institution_definitions() -> dict[str, dict[str, Any]]:
    return {
        'donor': {
            'name': 'Agencia Humanitaria Delta',
            'role': Institution.Role.DONOR,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'cooperacion@delta.example.invalid',
            'contact_phone': '+000 555 0100',
            'responsible_person': 'Laura Mendoza',
            'status': Institution.Status.ACTIVE,
            'with_legal': True,
        },
        'receiver': {
            'name': 'Fundación Horizonte',
            'role': Institution.Role.RECEIVER,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'proyectos@horizonte.example.invalid',
            'contact_phone': '+000 555 0101',
            'responsible_person': 'Andrés Pellicer',
            'status': Institution.Status.ACTIVE,
            'with_legal': False,
        },
        'executor': {
            'name': 'Red Comunitaria Aurora',
            'role': Institution.Role.EXECUTOR,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'aurora@sigedon.local',
            'contact_phone': '+000 555 0102',
            'responsible_person': 'Sofía Rangel',
            'status': Institution.Status.ACTIVE,
            'with_legal': False,
        },
        'ally': {
            'name': 'Asociación Comunitaria Río Claro',
            'role': Institution.Role.ALLY,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'rioclaro@sigedon.local',
            'contact_phone': '+000 555 0103',
            'responsible_person': 'Héctor Vidal',
            'status': Institution.Status.ACTIVE,
            'with_legal': False,
        },
        'supervisor': {
            'name': 'Observatorio Cívico Monteluz',
            'role': Institution.Role.SUPERVISOR,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'observatorio@sigedon.local',
            'contact_phone': '+000 555 0104',
            'responsible_person': 'Clara Montes',
            'status': Institution.Status.ACTIVE,
            'with_legal': False,
        },
        'inactive_ally': {
            'name': 'Centro de Innovación Archimango',
            'role': Institution.Role.ALLY,
            'country': DEMO_INSTITUTION_COUNTRY,
            'contact_email': 'archimango@sigedon.local',
            'contact_phone': '+000 555 0105',
            'responsible_person': 'Ivo Archimango',
            'status': Institution.Status.INACTIVE,
            'with_legal': False,
        },
    }


def _create_institutions(*, admin) -> dict[str, Institution]:
    institution_type = first_choice_value(
        Institution._meta.get_field('institution_type').choices
    )
    definitions = _institution_definitions()
    result = {}
    for key, data in definitions.items():
        institution, _created = Institution.objects.update_or_create(
            name=data['name'],
            defaults={
                'institution_type': institution_type,
                'role': data['role'],
                'country': data['country'],
                'contact_email': data['contact_email'],
                'contact_phone': data['contact_phone'],
                'responsible_person': data['responsible_person'],
                'status': data['status'],
            },
        )
        save_validated(institution)
        if data['with_legal'] and not institution.legal_document:
            institution.legal_document.save(
                'demo-institution-legal.pdf',
                demo_upload('demo-institution-legal.pdf'),
                save=True,
            )
        result[key] = institution
    return result


def _project_definitions() -> list[tuple]:
    return [
        (
            'aurora',
            'PRJ-DEMO-001',
            'Centro Comunitario Aurora',
            'Aurora, Valle Sereno, República de Monteluz',
            (
                'Construcción y puesta en marcha del centro comunitario de Aurora '
                'para asambleas, formación y servicios de proximidad.'
            ),
            (
                'Fortalecer la convivencia local y la gestión transparente de '
                'recursos en Aurora.'
            ),
            Decimal('50000.00'),
        ),
        (
            'rio_claro',
            'PRJ-DEMO-002',
            'Sistema de Agua Río Claro',
            'Río Claro, Valle Sereno, República de Monteluz',
            (
                'Captación, almacenamiento y distribución de agua segura para '
                'hogares y servicios comunitarios de Río Claro.'
            ),
            (
                'Garantizar acceso continuo a agua potable con operación '
                'comunitaria documentada.'
            ),
            Decimal('45000.00'),
        ),
        (
            'horizonte',
            'PRJ-DEMO-003',
            'Rehabilitación Escuela Horizonte',
            'Monte Azul, Valle Sereno, República de Monteluz',
            (
                'Rehabilitación de aulas, sanitarios y espacios comunes de la '
                'Escuela Horizonte en Monte Azul.'
            ),
            (
                'Restablecer condiciones seguras de aprendizaje para la comunidad '
                'educativa de Monte Azul.'
            ),
            Decimal('40000.00'),
        ),
        (
            'norte',
            'PRJ-DEMO-004',
            'Red de Atención Comunitaria Norte',
            'Puerto Norte, Valle Sereno, República de Monteluz',
            (
                'Articulación de puntos de atención comunitaria, referencia y '
                'seguimiento en Puerto Norte.'
            ),
            (
                'Mejorar la cobertura de atención de primera línea con registro '
                'trazable de actividades.'
            ),
            Decimal('35000.00'),
        ),
        (
            'archimango',
            'PRJ-DEMO-005',
            'Laboratorio Archimango',
            'San Lirio, Valle Sereno, República de Monteluz',
            (
                'Laboratorio comunitario de prototipado y formación técnica en '
                'San Lirio. Incluye un módulo de referencia del apócrifo atlas '
                'de Mangolandia.'
            ),
            (
                'Dotar a San Lirio de un espacio de innovación aplicada a '
                'soluciones locales replicables.'
            ),
            Decimal('30000.00'),
        ),
        (
            'empty',
            'PRJ-DEMO-006',
            'Proyecto DEMO sin actividad financiera',
            'Valle Sereno, República de Monteluz',
            (
                'Expediente de planificación comunitaria sin asignaciones ni '
                'gastos asociados.'
            ),
            (
                'Reservar un caso de proyecto activo sin movimiento financiero '
                'para pruebas de consulta.'
            ),
            Decimal('10000.00'),
        ),
        (
            'closed',
            'PRJ-DEMO-007',
            'Programa Comunitario Finalizado',
            'Valle Sereno, República de Monteluz',
            (
                'Programa comunitario de Valle Sereno ya cerrado, conservado '
                'como expediente histórico de demostración.'
            ),
            (
                'Documentar un ciclo completo de ejecución y cierre sin reabrir '
                'la operación.'
            ),
            Decimal('5000.00'),
        ),
    ]


def _apply_project_visible_fields(
    project: Project,
    *,
    name,
    description,
    objective,
    location,
    budget,
    today: date,
) -> Project:
    project.name = name
    project.description = description
    project.objective = objective
    project.location = location
    if project.status != Project.Status.CLOSED:
        project.estimated_budget = budget
        project.start_date = today - timedelta(days=30)
        project.end_date = today + timedelta(days=335)
    return project


def _create_projects(*, admin) -> dict[str, Project]:
    today = _anchor_date()
    definitions = _project_definitions()

    projects = {}
    for key, code, name, location, description, objective, budget in definitions:
        project = Project.objects.filter(code=code).first()
        if project is not None and project.status == Project.Status.CLOSED:
            # Never reopen closed demo projects on rerun; still refresh visible copy.
            _apply_project_visible_fields(
                project,
                name=name,
                description=description,
                objective=objective,
                location=location,
                budget=budget,
                today=today,
            )
            projects[key] = save_validated(project)
            continue

        if project is None:
            project = Project(
                code=code,
                name=name,
                description=description,
                objective=objective,
                location=location,
                estimated_budget=budget,
                start_date=today - timedelta(days=30),
                end_date=today + timedelta(days=335),
                status=Project.Status.ACTIVE,
                is_public=False,
            )
        else:
            _apply_project_visible_fields(
                project,
                name=name,
                description=description,
                objective=objective,
                location=location,
                budget=budget,
                today=today,
            )
            # Preserve is_public; publication is applied below via service.
        save_validated(project)
        projects[key] = project

    public_candidate = projects['aurora']
    if (
        admin is not None
        and public_candidate.status == Project.Status.ACTIVE
        and not public_candidate.is_public
    ):
        projects['aurora'] = publish_project(
            project_id=public_candidate.pk,
            actor=admin,
        )

    return projects


def _create_donations(*, institutions, admin) -> dict[str, Donation]:
    today = _anchor_date()
    donation_type = first_choice_value(
        Donation._meta.get_field('donation_type').choices
    )
    donor = institutions['donor']

    specs = [
        {
            'key': 'main',
            'code': 'DON-DEMO-001',
            'amount': Decimal('200000.00'),
            'objective': (
                'Financiar actividades comunitarias de los cinco proyectos '
                'activos en Valle Sereno, República de Monteluz.'
            ),
            'restrictions': 'Uso exclusivo en actividades aprobadas y documentadas.',
            'commitment_date': today - timedelta(days=50),
            'received_date': today - timedelta(days=40),
            'target_status': Donation.Status.RECEIVED,
            'support_reference': 'CONVENIO-DEMO-2026-001',
        },
        {
            'key': 'registered',
            'code': 'DON-DEMO-002',
            'amount': Decimal('25000.00'),
            'objective': 'Donación DEMO registrada pendiente de recepción.',
            'restrictions': '',
            'commitment_date': today - timedelta(days=10),
            'received_date': None,
            'target_status': Donation.Status.REGISTERED,
            'support_reference': 'CONVENIO-DEMO-2026-002',
        },
        {
            'key': 'available',
            'code': 'DON-DEMO-003',
            'amount': Decimal('15000.00'),
            'objective': 'Donación DEMO recibida con saldo disponible sin asignar.',
            'restrictions': '',
            'commitment_date': today - timedelta(days=20),
            'received_date': today - timedelta(days=15),
            'target_status': Donation.Status.RECEIVED,
            'support_reference': 'CONVENIO-DEMO-2026-003',
        },
        {
            'key': 'full',
            'code': 'DON-DEMO-004',
            'amount': Decimal('8000.00'),
            'objective': 'Donación DEMO recibida y totalmente asignada.',
            'restrictions': '',
            'commitment_date': today - timedelta(days=35),
            'received_date': today - timedelta(days=30),
            'target_status': Donation.Status.RECEIVED,
            'support_reference': 'CONVENIO-DEMO-2026-004',
        },
        {
            'key': 'closed_funding',
            'code': 'DON-DEMO-005',
            'amount': Decimal('2000.00'),
            'objective': 'Donación DEMO para financiar el programa comunitario finalizado.',
            'restrictions': '',
            'commitment_date': today - timedelta(days=90),
            'received_date': today - timedelta(days=80),
            'target_status': Donation.Status.RECEIVED,
            'support_reference': 'CONVENIO-DEMO-2026-005',
        },
    ]

    result = {}
    for spec in specs:
        donation = Donation.objects.filter(code=spec['code']).first()
        if donation is None:
            donation = Donation(
                code=spec['code'],
                donor=donor,
                donation_type=donation_type,
                amount=spec['amount'],
                currency='USD',
                objective=spec['objective'],
                restrictions=spec['restrictions'],
                commitment_date=spec['commitment_date'],
                received_date=spec['received_date'],
                support_reference=spec['support_reference'],
                status=Donation.Status.REGISTERED,
            )
            save_validated(donation)
        elif donation.status != Donation.Status.ANNULLED:
            donation.donor = donor
            donation.donation_type = donation_type
            donation.amount = spec['amount']
            donation.currency = 'USD'
            donation.objective = spec['objective']
            donation.restrictions = spec['restrictions']
            donation.commitment_date = spec['commitment_date']
            # Keep received_date for RECEIVED; set for REGISTERED→RECEIVED path.
            if donation.status == Donation.Status.REGISTERED:
                donation.received_date = spec['received_date']
            elif spec['received_date'] is not None:
                donation.received_date = spec['received_date']
            donation.support_reference = spec['support_reference']
            donation.save()

        if donation.status == Donation.Status.ANNULLED:
            result[spec['key']] = donation
            continue

        if (
            admin is not None
            and spec['target_status'] == Donation.Status.RECEIVED
            and donation.status == Donation.Status.REGISTERED
        ):
            if donation.received_date is None:
                donation.received_date = today - timedelta(days=1)
                donation.save(update_fields=['received_date', 'updated_at'])
            donation = transition_donation_status(
                donation.pk,
                actor=admin,
                target_status=Donation.Status.RECEIVED,
            )
        result[spec['key']] = donation
    return result


def _create_allocations(*, donations, projects) -> dict[str, FundAllocation]:
    today = _anchor_date()
    budget_category = first_choice_value(
        FundAllocation._meta.get_field('budget_category').choices
    )

    amounts = {
        'aurora': (donations['main'], projects['aurora'], Decimal('40000.00')),
        'rio_claro': (donations['main'], projects['rio_claro'], Decimal('35000.00')),
        'horizonte': (donations['main'], projects['horizonte'], Decimal('30000.00')),
        'norte': (donations['main'], projects['norte'], Decimal('25000.00')),
        'archimango': (donations['main'], projects['archimango'], Decimal('20000.00')),
        'full_only': (donations['full'], projects['archimango'], Decimal('8000.00')),
        'closed_alloc': (
            donations['closed_funding'],
            projects['closed'],
            Decimal('2000.00'),
        ),
    }

    allocations = {}
    for key, (donation, project, amount) in amounts.items():
        allocation = FundAllocation.objects.filter(
            donation=donation,
            project=project,
            budget_category=budget_category,
        ).first()
        if allocation is not None and allocation.status != FundAllocation.Status.ACTIVE:
            allocations[key] = allocation
            continue

        if allocation is None:
            allocation = FundAllocation(
                donation=donation,
                project=project,
                budget_category=budget_category,
                amount=amount,
                responsible_person='Marina Soler',
                allocation_date=today - timedelta(days=25),
                status=FundAllocation.Status.ACTIVE,
                notes='Asignación inicial del escenario demostrativo.',
            )
        else:
            allocation.amount = amount
            allocation.responsible_person = 'Marina Soler'
            allocation.allocation_date = today - timedelta(days=25)
            allocation.notes = 'Asignación inicial del escenario demostrativo.'
        allocations[key] = save_validated(allocation)
    return allocations


def _create_legacy_expenses(*, allocations, admin) -> list[Expense]:
    """
    PRE: allocations have available balance; admin may be None when --skip-users.
    POST: creates or reuses a few REGISTERED expenses with supporting docs via legacy service.
    """
    today = _anchor_date()
    category = first_choice_value(Expense._meta.get_field('category').choices)
    payment_method = first_choice_value(
        Expense._meta.get_field('payment_method').choices
    )

    definitions = [
        (
            allocations['aurora'],
            'Compra de materiales comunitarios',
            Decimal('3500.00'),
            'Proveedor comunitario Aurora',
            15,
        ),
        (
            allocations['rio_claro'],
            'Jornada de diagnóstico territorial',
            Decimal('2200.00'),
            'Brigada técnica Valle Sereno',
            12,
        ),
        (
            allocations['horizonte'],
            'Logística para asamblea comunitaria',
            Decimal('1800.00'),
            'Proveedor logístico Monte Azul',
            9,
        ),
        (
            allocations['norte'],
            'Traslado de equipo de campo',
            Decimal('950.00'),
            'Servicio de transporte Monte Azul',
            6,
        ),
    ]

    expenses = []
    for allocation, reason, amount, recipient, days_ago in definitions:
        existing = Expense.objects.filter(
            allocation=allocation,
            amount=amount,
        ).exclude(status=Expense.Status.ANNULLED).first()
        if existing is not None:
            if (
                existing.reason != reason
                or existing.provider_or_recipient != recipient
                or existing.description != 'Registro demostrativo para pruebas operativas.'
            ):
                existing.reason = reason
                existing.provider_or_recipient = recipient
                existing.description = 'Registro demostrativo para pruebas operativas.'
                save_validated(existing)
            if not existing.supporting_documents.exists() and admin is not None:
                # Historical demo rows without support: attach once via service path.
                from apps.operations.services import create_supporting_document

                create_supporting_document(
                    expense_id=existing.pk,
                    title='Soporte DEMO',
                    file=demo_upload(f'demo-support-{existing.pk}.pdf'),
                    notes='Adjunto DEMO para satisfacer el requisito de soporte.',
                    actor=admin,
                )
            expenses.append(existing)
            continue

        if admin is None:
            continue

        expense = create_expense_legacy(
            allocation=allocation,
            expense_date=today - timedelta(days=days_ago),
            category=category,
            amount=amount,
            reason=reason,
            provider_or_recipient=recipient,
            payment_method=payment_method,
            description='Registro demostrativo para pruebas operativas.',
            observations='',
            actor=admin,
            support_title='Soporte DEMO',
            support_file=demo_upload(f'demo-legacy-{reason[:12].replace(" ", "-")}.pdf'),
            support_notes='Documento soporte DEMO.',
        )
        expenses.append(expense)
    return expenses


def _find_demo_request(key: str) -> ExpenseRequest | None:
    return (
        ExpenseRequest.objects.filter(purpose=DEMO_ER_PURPOSE[key])
        .order_by('pk')
        .first()
    )


def _create_expense_requests(*, allocations, users) -> dict[str, ExpenseRequest]:
    operator = users.get('operator')
    committee = users.get('committee')
    admin = users.get('admin')
    if operator is None or committee is None or admin is None:
        return {}

    today = _anchor_date()
    category = first_choice_value(Expense._meta.get_field('category').choices)
    payment_method = first_choice_value(
        Expense._meta.get_field('payment_method').choices
    )

    # Use allocations with headroom after legacy expenses.
    pending_alloc = allocations['archimango']  # 20000, no legacy expense
    approved_alloc = allocations['norte']  # 25000 - 950
    denied_alloc = allocations['horizonte']
    withdrawn_alloc = allocations['rio_claro']
    fulfilled_alloc = allocations['aurora']
    annulled_alloc = allocations['full_only']

    scenarios = {
        'pending': {
            'allocation': pending_alloc,
            'amount': Decimal('1200.00'),
            'target': ExpenseRequest.Status.PENDING_DECISION,
            'attach': True,
        },
        'approved': {
            'allocation': approved_alloc,
            'amount': Decimal('1500.00'),
            'target': ExpenseRequest.Status.APPROVED_RESERVED,
            'attach': False,
        },
        'denied': {
            'allocation': denied_alloc,
            'amount': Decimal('800.00'),
            'target': ExpenseRequest.Status.DENIED,
            'attach': False,
        },
        'withdrawn': {
            'allocation': withdrawn_alloc,
            'amount': Decimal('600.00'),
            'target': ExpenseRequest.Status.WITHDRAWN,
            'attach': False,
        },
        'fulfilled': {
            'allocation': fulfilled_alloc,
            'amount': Decimal('1000.00'),
            'target': ExpenseRequest.Status.FULFILLED,
            'attach': False,
        },
        'annulled': {
            'allocation': annulled_alloc,
            'amount': Decimal('500.00'),
            'target': ExpenseRequest.Status.ANNULLED,
            'attach': False,
        },
    }

    result = {}
    for key, spec in scenarios.items():
        request = _find_demo_request(key)
        if request is None:
            request = create_expense_request(
                fund_allocation=spec['allocation'],
                requested_amount=spec['amount'],
                purpose=DEMO_ER_PURPOSE[key],
                requested_date=today - timedelta(days=5),
                actor=operator,
            )
            if spec['attach']:
                add_expense_request_attachments(
                    expense_request_id=request.pk,
                    files=[demo_upload(f'demo-er-{key}.pdf')],
                    title='Adjunto DEMO de solicitud',
                    notes='Evidencia DEMO',
                    actor=operator,
                )

        request = _advance_expense_request(
            request,
            target=spec['target'],
            operator=operator,
            committee=committee,
            admin=admin,
            category=category,
            payment_method=payment_method,
            today=today,
        )
        result[key] = request
    return result


def _advance_expense_request(
    request: ExpenseRequest,
    *,
    target: str,
    operator,
    committee,
    admin,
    category: str,
    payment_method: str,
    today: date,
) -> ExpenseRequest:
    """
    PRE: request exists; target is a supported demo terminal/queue status.
    POST: applies only missing transitions via domain services; never replays terminals.
    """
    request.refresh_from_db()
    if request.status == target:
        return request

    if target == ExpenseRequest.Status.PENDING_DECISION:
        return request

    if request.status == ExpenseRequest.Status.PENDING_DECISION:
        if target == ExpenseRequest.Status.WITHDRAWN:
            return withdraw_expense_request(
                request,
                reason='Retiro DEMO por corrección operativa.',
                actor=operator,
            )
        if target == ExpenseRequest.Status.DENIED:
            return deny_expense_request(
                request,
                decision_note='Denegación DEMO: justificación insuficiente.',
                actor=committee,
            )
        if target in {
            ExpenseRequest.Status.APPROVED_RESERVED,
            ExpenseRequest.Status.FULFILLED,
            ExpenseRequest.Status.ANNULLED,
        }:
            request = approve_expense_request(
                request,
                decision_note='Aprobación DEMO del comité.',
                actor=committee,
            )

    request.refresh_from_db()
    if request.status == target:
        return request

    if (
        target == ExpenseRequest.Status.FULFILLED
        and request.status == ExpenseRequest.Status.APPROVED_RESERVED
    ):
        return fulfill_expense_request(
            request,
            expense_date=today - timedelta(days=2),
            amount=request.reserved_amount,
            reason=request.purpose,
            provider_or_recipient='Comercial Valle Sereno',
            payment_method=payment_method,
            description='Gasto DEMO generado desde solicitud cumplida.',
            support_file=demo_upload('demo-er-fulfilled-support.pdf'),
            support_title='Soporte DEMO cumplimiento',
            category=category,
            actor=admin,
        )

    if (
        target == ExpenseRequest.Status.ANNULLED
        and request.status == ExpenseRequest.Status.APPROVED_RESERVED
    ):
        return annul_expense_request(
            request,
            reason='Anulación DEMO administrativa de reserva.',
            actor=admin,
        )

    request.refresh_from_db()
    return request


def _ensure_demo_published_update_attachments(*, published, operator, admin) -> None:
    """
    PRE: published is the demo public-update scenario; operator/admin may be None.
    POST: ensures one explicitly public and one private update attachment without
          duplicating files across idempotent seed runs.
    """
    public_attachment = ProjectUpdateAttachment.objects.filter(
        project_update=published,
        title=DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE,
    ).first()
    if public_attachment is None:
        legacy = ProjectUpdateAttachment.objects.filter(
            project_update=published,
            title='Evidencia DEMO',
        ).first()
        if legacy is not None:
            if published.status == ProjectUpdate.Status.UNPUBLISHED:
                legacy.title = DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE
                legacy.save(update_fields=('title',))
            elif legacy.title != DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE:
                # Demo repair only: published attachment titles are otherwise immutable.
                legacy.title = DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE
                models.Model.save(legacy, update_fields=('title',))
            public_attachment = legacy
        elif published.status == ProjectUpdate.Status.UNPUBLISHED and operator is not None:
            public_attachment = add_project_update_attachment(
                update_id=published.pk,
                file=demo_upload('demo-update-public.pdf'),
                title=DEMO_UPDATE_PUBLIC_ATTACHMENT_TITLE,
                actor=operator,
            )

    private_attachment = ProjectUpdateAttachment.objects.filter(
        project_update=published,
        title=DEMO_UPDATE_PRIVATE_ATTACHMENT_TITLE,
    ).first()
    if private_attachment is None and operator is not None:
        if published.status == ProjectUpdate.Status.UNPUBLISHED:
            private_attachment = add_project_update_attachment(
                update_id=published.pk,
                file=demo_upload('demo-update-private.pdf'),
                title=DEMO_UPDATE_PRIVATE_ATTACHMENT_TITLE,
                actor=operator,
            )
        elif (
            public_attachment is not None
            and ProjectUpdateAttachment.objects.filter(project_update=published).count()
            == 1
        ):
            # Demo repair only: published updates are otherwise immutable.
            private_attachment = ProjectUpdateAttachment(
                project_update=published,
                title=DEMO_UPDATE_PRIVATE_ATTACHMENT_TITLE,
                file=demo_upload('demo-update-private.pdf'),
                uploaded_by=operator if getattr(operator, 'is_authenticated', False) else None,
                is_public=False,
            )
            models.Model.save(private_attachment)

    if admin is None:
        return

    if public_attachment is not None:
        public_attachment.refresh_from_db()
        if not public_attachment.is_public:
            publish_project_update_attachment(
                attachment_id=public_attachment.pk,
                actor=admin,
            )

    if private_attachment is not None:
        private_attachment.refresh_from_db()
        if private_attachment.is_public:
            unpublish_project_update_attachment(
                attachment_id=private_attachment.pk,
                actor=admin,
            )


def _create_project_updates(*, projects, users) -> dict[str, ProjectUpdate]:
    operator = users.get('operator')
    committee = users.get('committee')
    admin = users.get('admin')
    if operator is None:
        return {}

    today = _anchor_date()
    result = {}

    def _get_or_register(key: str, project: Project, description: str) -> ProjectUpdate:
        title = DEMO_UPDATE_TITLE[key]
        existing = ProjectUpdate.objects.filter(project=project, title=title).first()
        if existing is not None:
            return existing
        return register_advance(
            project_id=project.pk,
            title=title,
            description=description,
            update_date=today,
            created_by=operator,
            reported_by=operator,
        )

    draft = _get_or_register(
        'draft',
        projects['rio_claro'],
        'Borrador DEMO editable por el operador de campo.',
    )
    result['draft'] = draft

    published = _get_or_register(
        'published_public',
        projects['aurora'],
        'Avance DEMO publicado visible cuando el proyecto es público.',
    )
    _ensure_demo_published_update_attachments(
        published=published,
        operator=operator,
        admin=admin,
    )
    if (
        admin is not None
        and published.status == ProjectUpdate.Status.UNPUBLISHED
    ):
        published = publish_project_update(published.pk, admin)
    result['published_public'] = published

    plain = _get_or_register(
        'no_attachment',
        projects['horizonte'],
        'Avance DEMO publicado sin adjunto.',
    )
    if admin is not None and plain.status == ProjectUpdate.Status.UNPUBLISHED:
        plain = publish_project_update(plain.pk, admin)
    result['no_attachment'] = plain

    conforming = _get_or_register(
        'reviewed_ok',
        projects['norte'],
        'Avance DEMO con decisión conforme del comité.',
    )
    if admin is not None and conforming.status == ProjectUpdate.Status.UNPUBLISHED:
        conforming = publish_project_update(conforming.pk, admin)
    if committee is not None and conforming.status == ProjectUpdate.Status.PUBLISHED:
        review = ProjectUpdateReview.objects.filter(project_update=conforming).first()
        if review is None:
            review = create_project_update_review(
                update_id=conforming.pk,
                observations='Revisión documental DEMO sin hallazgos.',
                actor=committee,
            )
        if not ProjectUpdateReviewDecision.objects.filter(review=review).exists():
            create_project_update_review_decision(
                review_id=review.pk,
                outcome=ProjectUpdateReviewDecision.Outcome.CONFORMING,
                rationale='Fundamento DEMO: el avance es conforme.',
                actor=committee,
            )
    result['reviewed_ok'] = conforming

    observed = _get_or_register(
        'observed',
        projects['archimango'],
        'Avance DEMO observado que requiere remediación.',
    )
    if admin is not None and observed.status == ProjectUpdate.Status.UNPUBLISHED:
        observed = publish_project_update(observed.pk, admin)
    if committee is not None and observed.status == ProjectUpdate.Status.PUBLISHED:
        review = ProjectUpdateReview.objects.filter(project_update=observed).first()
        if review is None:
            review = create_project_update_review(
                update_id=observed.pk,
                observations='Revisión DEMO: se requieren evidencias adicionales.',
                actor=committee,
            )
        decision = ProjectUpdateReviewDecision.objects.filter(review=review).first()
        if decision is None:
            decision = create_project_update_review_decision(
                review_id=review.pk,
                outcome=ProjectUpdateReviewDecision.Outcome.OBSERVED,
                rationale='Fundamento DEMO: observación formal del comité.',
                actor=committee,
            )
        remediation = ProjectUpdateRemediation.objects.filter(decision=decision).first()
        if remediation is None:
            remediation = create_project_update_remediation(
                decision_id=decision.pk,
                response='Respuesta DEMO del operador a la observación.',
                actor=operator,
            )
        if not ProjectUpdateRemediationAttachment.objects.filter(
            remediation=remediation
        ).exists():
            if remediation.status == ProjectUpdateRemediation.Status.DRAFT:
                add_project_update_remediation_attachment(
                    remediation_id=remediation.pk,
                    file=demo_upload('demo-remediation.pdf'),
                    title='Evidencia remediación DEMO',
                    actor=operator,
                )
    result['observed'] = observed
    return result


def _attach_private_demo_files(*, institutions, projects, expenses, users) -> None:
    admin = users.get('admin')
    if admin is None:
        return

    project = projects['rio_claro']
    if not ProjectDocument.objects.filter(
        project=project,
        title='Plan de trabajo DEMO',
    ).exists():
        ProjectDocument.objects.create(
            project=project,
            document_type=ProjectDocument.DocumentType.WORK_PLAN,
            title='Plan de trabajo DEMO',
            file=demo_upload('demo-project-document.pdf'),
            description='Documento de proyecto DEMO.',
            uploaded_by=admin,
        )


def _close_demo_project_if_needed(*, projects, allocations, admin) -> None:
    if admin is None:
        return
    project = projects.get('closed')
    allocation = allocations.get('closed_alloc')
    if project is None or allocation is None:
        return
    if project.status == Project.Status.CLOSED:
        return

    allocation.refresh_from_db()
    if allocation.status == FundAllocation.Status.ACTIVE:
        # Finish only when no open expense requests exist on this allocation.
        open_statuses = ExpenseRequest.open_financial_statuses()
        if not ExpenseRequest.objects.filter(
            fund_allocation=allocation,
            status__in=open_statuses,
        ).exists():
            allocation = finish_fund_allocation(allocation.pk, actor=admin)

    allocation.refresh_from_db()
    project.refresh_from_db()
    if (
        allocation.status in {
            FundAllocation.Status.FINISHED,
            FundAllocation.Status.ANNULLED,
        }
        and project.status == Project.Status.ACTIVE
    ):
        finish_project(project.pk, actor=admin)
