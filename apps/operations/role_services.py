from django.contrib.auth.models import Group, Permission

from .roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_PERMISSION_CODENAMES,
    ROLE_PROJECT_COMMITTEE,
    ROLE_PROJECT_UPDATE_DECIDER,
    ROLE_PROJECT_UPDATE_REVIEWER,
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
)


# PRE: Django auth permissions for apps.operations have been created by migrations.
# POST: creates or updates all SIGEDON groups, removing excluded and legacy mutation permissions idempotently.
def sync_operation_roles():
    operations_permissions = Permission.objects.filter(content_type__app_label='operations')
    permissions_by_codename = {permission.codename: permission for permission in operations_permissions}
    synced_groups = {}

    admin_group, _ = Group.objects.get_or_create(name=ROLE_SIGEDON_ADMIN)
    admin_group.permissions.set(
        operations_permissions.exclude(codename__in=ADMIN_EXCLUDED_PERMISSION_CODENAMES)
    )
    synced_groups[ROLE_SIGEDON_ADMIN] = admin_group

    for role_name, codenames in ROLE_PERMISSION_CODENAMES.items():
        missing_codenames = sorted(codenames - permissions_by_codename.keys())
        if missing_codenames:
            missing = ', '.join(missing_codenames)
            raise ValueError(f'Permisos operativos no encontrados: {missing}')
        group, _ = Group.objects.get_or_create(name=role_name)
        group.permissions.set([permissions_by_codename[codename] for codename in sorted(codenames)])
        synced_groups[role_name] = group

    return synced_groups


def operation_role_names():
    return [
        ROLE_SIGEDON_ADMIN,
        ROLE_FIELD_OPERATOR,
        ROLE_EXTERNAL_AUDITOR,
        ROLE_PROJECT_COMMITTEE,
        ROLE_PROJECT_UPDATE_REVIEWER,
        ROLE_PROJECT_UPDATE_DECIDER,
    ]
