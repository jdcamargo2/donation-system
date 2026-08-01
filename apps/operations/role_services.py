from __future__ import annotations

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db import transaction

from .roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PERMISSION_CODENAMES,
    ROLE_PROJECT_COMMITTEE,
    ROLE_SIGEDON_ADMIN,
)


AUDIT_MUTATION_PERMISSION_CODENAMES = frozenset(
    {'add_auditlog', 'change_auditlog', 'delete_auditlog'}
)
REVIEW_AND_DECISION_MUTATION_PERMISSION_CODENAMES = frozenset(
    {
        'add_projectupdatereview',
        'change_projectupdatereview',
        'delete_projectupdatereview',
        'add_projectupdatereviewdecision',
        'change_projectupdatereviewdecision',
        'delete_projectupdatereviewdecision',
        'review_projectupdate',
        'decide_projectupdate',
        'resolve_projectupdateremediation',
    }
)
ADMIN_EXCLUDED_PERMISSION_CODENAMES = (
    AUDIT_MUTATION_PERMISSION_CODENAMES
    | REVIEW_AND_DECISION_MUTATION_PERMISSION_CODENAMES
    | frozenset({'delete_project'})
)
KOBO_TERRITORIAL_ADMIN_PERMISSION_CODENAMES = frozenset(
    {
        'view_territorial_administration',
        'manage_pastoral_zone_mappings',
        'resolve_territorial_conflicts',
        'change_territorial_identity_status',
        'run_territorial_reconciliation',
    }
)
KOBO_TERRITORIAL_READ_PERMISSION_CODENAMES = frozenset(
    {'view_territorial_administration'}
)


# PRE: Django auth permissions for apps.operations have been created by migrations.
# POST: creates or updates all SIGEDON groups, removing excluded and legacy mutation permissions idempotently.
def sync_operation_roles():
    operations_permissions = Permission.objects.filter(content_type__app_label='operations')
    permissions_by_codename = {permission.codename: permission for permission in operations_permissions}
    kobo_territorial_permissions = Permission.objects.filter(
        content_type__app_label='kobo',
        codename__in=KOBO_TERRITORIAL_ADMIN_PERMISSION_CODENAMES,
    )
    kobo_permissions_by_codename = {
        permission.codename: permission for permission in kobo_territorial_permissions
    }
    missing_kobo_permissions = sorted(
        KOBO_TERRITORIAL_ADMIN_PERMISSION_CODENAMES - kobo_permissions_by_codename.keys()
    )
    if missing_kobo_permissions:
        missing = ', '.join(missing_kobo_permissions)
        raise ValueError(f'Permisos territoriales Kobo no encontrados: {missing}')
    synced_groups = {}

    admin_group, _ = Group.objects.get_or_create(name=ROLE_SIGEDON_ADMIN)
    admin_group.permissions.set(
        list(operations_permissions.exclude(codename__in=ADMIN_EXCLUDED_PERMISSION_CODENAMES))
        + list(kobo_territorial_permissions)
    )
    synced_groups[ROLE_SIGEDON_ADMIN] = admin_group

    for role_name, codenames in ROLE_PERMISSION_CODENAMES.items():
        missing_codenames = sorted(codenames - permissions_by_codename.keys())
        if missing_codenames:
            missing = ', '.join(missing_codenames)
            raise ValueError(f'Permisos operativos no encontrados: {missing}')
        group, _ = Group.objects.get_or_create(name=role_name)
        group.permissions.set([permissions_by_codename[codename] for codename in sorted(codenames)])
        if role_name in {
            ROLE_FIELD_OPERATOR,
            ROLE_EXTERNAL_AUDITOR,
            ROLE_PROJECT_COMMITTEE,
        }:
            group.permissions.add(
                *[
                    kobo_permissions_by_codename[codename]
                    for codename in sorted(KOBO_TERRITORIAL_READ_PERMISSION_CODENAMES)
                ]
            )
        synced_groups[role_name] = group

    return synced_groups


def operation_role_names():
    return [
        ROLE_SIGEDON_ADMIN,
        ROLE_FIELD_OPERATOR,
        ROLE_EXTERNAL_AUDITOR,
        ROLE_PROJECT_COMMITTEE,
    ]


def functional_role_groups():
    """
    PRE: canonical SIGEDON role groups may or may not exist in the database.
    POST: returns those groups ordered deterministically by name.
    """
    return Group.objects.filter(name__in=operation_role_names()).order_by('name')


def get_user_functional_roles(user: User):
    """
    PRE: user is a Django auth user (saved or unsaved).
    POST: returns the user's memberships in canonical functional role groups.
    """
    return user.groups.filter(name__in=operation_role_names()).order_by('name')


def get_user_functional_role(user: User) -> Group | None:
    """
    PRE: user has zero or one canonical functional role membership.
    POST: returns that Group, None when absent, or raises ValidationError if more than one.
    """
    roles = list(get_user_functional_roles(user))
    if len(roles) > 1:
        raise ValidationError(
            'El usuario tiene más de un rol funcional SIGEDON asignado.'
        )
    return roles[0] if roles else None


def set_user_functional_role(user: User, role: Group | None) -> None:
    """
    PRE: user is persisted; role is None or a canonical functional Group.
    POST: user retains exactly that functional role (or none); non-functional
          groups and direct user permissions are unchanged. Idempotent.
    """
    if user.pk is None:
        raise ValueError(
            'No se puede asignar un rol funcional a un usuario sin guardar.'
        )
    if role is not None:
        if not isinstance(role, Group):
            raise ValidationError(
                'El rol funcional debe ser un grupo Django o None.'
            )
        if role.name not in operation_role_names():
            raise ValidationError(
                f'{role.name!r} no es un rol funcional SIGEDON canónico.'
            )

    with transaction.atomic():
        current_functional = list(
            user.groups.filter(name__in=operation_role_names())
        )
        if current_functional:
            user.groups.remove(*current_functional)
        if role is not None:
            user.groups.add(role)
