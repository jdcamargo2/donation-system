from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .role_services import get_user_functional_roles
from .roles import ROLE_FIELD_OPERATOR


PROJECT_UPDATE_RESPONSIBLE_PERMISSION_CODENAMES = (
    'add_projectupdate',
    'change_projectupdate',
    'publish_projectupdate',
)


def eligible_project_update_reporters():
    """
    PRE: the configured user model exposes Django's standard permission relations.
    POST: returns active users able to operate project updates, without duplicates.
    """
    permission_filter = Q(
        user_permissions__content_type__app_label='operations',
        user_permissions__codename__in=PROJECT_UPDATE_RESPONSIBLE_PERMISSION_CODENAMES,
    ) | Q(
        groups__permissions__content_type__app_label='operations',
        groups__permissions__codename__in=PROJECT_UPDATE_RESPONSIBLE_PERMISSION_CODENAMES,
    )
    return get_user_model()._default_manager.filter(
        Q(is_superuser=True) | permission_filter,
        is_active=True,
    ).distinct()


def validate_project_update_reporter(reported_by):
    """
    PRE: reported_by is the proposed responsible user for a new or edited project update.
    POST: returns the eligible active user or raises a field-specific validation error.
    """
    if reported_by is None:
        raise ValidationError({'reported_by': _('Debe seleccionar una persona responsable del avance.')})
    if not eligible_project_update_reporters().filter(pk=reported_by.pk).exists():
        raise ValidationError({
            'reported_by': _('La persona responsable debe estar activa y tener permisos operativos sobre avances.')
        })
    return reported_by


def actor_must_self_report_project_update(actor) -> bool:
    """
    PRE: actor is the authenticated technical creator, or None / anonymous / unsaved.
    POST: True only when the actor's sole canonical functional role is Operador de campo;
          False for Admin, superuser, permission-only users, and unsafe/anonymous actors.
    """
    if actor is None:
        return False
    if not getattr(actor, 'is_authenticated', False):
        return False
    if getattr(actor, 'pk', None) is None:
        return False
    if getattr(actor, 'is_superuser', False):
        return False
    roles = list(get_user_functional_roles(actor))
    if len(roles) != 1:
        return False
    return roles[0].name == ROLE_FIELD_OPERATOR


def resolve_project_update_reporter(*, actor, submitted_reporter):
    """
    PRE: actor is the technical creator; submitted_reporter is the proposed responsible person.
    POST: Operador de campo always resolves to actor (validated); other authorized creators
          keep a validated submitted_reporter. Raises the project's ValidationError style
          when the resolved reporter is missing or ineligible.
    """
    if actor_must_self_report_project_update(actor):
        return validate_project_update_reporter(actor)
    return validate_project_update_reporter(submitted_reporter)
