"""Superuser-only institutional user access services.

Authority contract:
- only request.user.is_superuser may invoke mutating helpers;
- functional roles never grant identity authority;
- simplified panel cannot create or modify superusers;
- passwords are never logged or written to AuditLog.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import AuditLog, UserAccessProfile
from .role_services import (
    get_user_functional_role,
    operation_role_names,
    set_user_functional_role,
)
from .services import log_action

User = get_user_model()
logger = logging.getLogger('sigedon.user_access')


def require_superuser(actor) -> None:
    """
    PRE: actor is the requesting user.
    POST: raises PermissionDenied unless authenticated superuser.
    """
    if not getattr(actor, 'is_authenticated', False) or not actor.is_superuser:
        raise PermissionDenied(
            _('Solo un superusuario puede gestionar cuentas institucionales.')
        )


def get_or_create_access_profile(user: User) -> UserAccessProfile:
    """
    PRE: user is persisted.
    POST: returns the access profile; creates one with must_change_password=False
          when missing (safe default for deployment superusers / legacy users).
    """
    profile, _created = UserAccessProfile.objects.get_or_create(user=user)
    return profile


def user_requires_password_change(user: User) -> bool:
    """
    PRE: user may lack an access profile.
    POST: True only when the profile exists and must_change_password is set.
    """
    try:
        return bool(user.access_profile.must_change_password)
    except UserAccessProfile.DoesNotExist:
        return False


def invalidate_user_sessions(user: User, *, retain_session_key: str | None = None) -> int:
    """
    PRE: SESSION_ENGINE is database-backed (production default); user is persisted.
    POST: deletes sessions whose decoded ``_auth_user_id`` matches user.pk,
          except retain_session_key when provided (actor session). Never logs
          session keys. Expired/corrupt sessions are skipped safely.
    """
    if user.pk is None:
        raise ValueError('No se pueden invalidar sesiones de un usuario sin guardar.')
    target_id = str(user.pk)
    removed = 0
    for session in Session.objects.iterator():
        if retain_session_key and session.session_key == retain_session_key:
            continue
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get('_auth_user_id', '')) != target_id:
            continue
        session.delete()
        removed += 1
    return removed


def _audit_user_event(*, actor, action, target, summary: str) -> AuditLog:
    """
    PRE: summary contains only safe metadata (no passwords/tokens/session ids).
    POST: appends an AuditLog row targeting the User instance.
    """
    return log_action(actor, action, target, summary)


def create_institutional_user(
    *,
    actor,
    username: str,
    first_name: str,
    last_name: str,
    email: str,
    functional_role: Group,
    temporary_password: str,
    is_active: bool = True,
) -> User:
    """
    PRE: actor is superuser; functional_role is a canonical SIGEDON group;
         temporary_password already passed Django validators at the form layer.
    POST: creates a non-superuser, non-staff institutional user with exactly one
          functional role and must_change_password=True. Rolls back on role failure.
          Does not email the password.
    """
    require_superuser(actor)
    if functional_role is None or functional_role.name not in operation_role_names():
        raise ValidationError({'functional_role': _('Debe asignar un rol funcional canónico.')})

    with transaction.atomic():
        user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            is_active=is_active,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(temporary_password)
        user.save()
        set_user_functional_role(user, functional_role)
        profile = get_or_create_access_profile(user)
        profile.must_change_password = True
        profile.password_reset_by = actor
        profile.password_reset_at = timezone.now()
        profile.save(
            update_fields=['must_change_password', 'password_reset_by', 'password_reset_at']
        )
        _audit_user_event(
            actor=actor,
            action=AuditLog.Action.CREATED,
            target=user,
            summary=(
                'Usuario institucional creado. '
                f'target_user_id={user.pk}; target_username={user.username}; '
                f'new_role={functional_role.name}; new_active_state={user.is_active}; '
                f'actor_user_id={actor.pk}'
            ),
        )
    logger.info(
        'institutional_user_created target_user_id=%s actor_user_id=%s',
        user.pk,
        actor.pk,
    )
    return user


def update_institutional_user(
    *,
    actor,
    target: User,
    first_name: str,
    last_name: str,
    email: str,
    functional_role: Group,
    is_active: bool,
) -> User:
    """
    PRE: actor is superuser; target is a non-superuser institutional account;
         functional_role is canonical; actor is not deactivating themselves.
    POST: updates profile fields and exactly one functional role atomically.
    """
    require_superuser(actor)
    if target.is_superuser:
        raise PermissionDenied(
            _('Las cuentas superusuario no se gestionan desde este panel.')
        )
    if target.pk == actor.pk and not is_active:
        raise ValidationError({'is_active': _('No puede desactivar su propia cuenta.')})
    if functional_role is None or functional_role.name not in operation_role_names():
        raise ValidationError({'functional_role': _('Debe asignar un rol funcional canónico.')})

    previous_role = get_user_functional_role(target)
    previous_active = target.is_active
    previous_role_name = previous_role.name if previous_role else ''

    with transaction.atomic():
        target.first_name = first_name
        target.last_name = last_name
        target.email = email
        target.is_active = is_active
        target.save(update_fields=['first_name', 'last_name', 'email', 'is_active'])
        set_user_functional_role(target, functional_role)
        if previous_role_name != functional_role.name:
            _audit_user_event(
                actor=actor,
                action=AuditLog.Action.UPDATED,
                target=target,
                summary=(
                    'Rol funcional actualizado. '
                    f'target_user_id={target.pk}; target_username={target.username}; '
                    f'previous_role={previous_role_name}; new_role={functional_role.name}; '
                    f'actor_user_id={actor.pk}'
                ),
            )
        if previous_active != is_active:
            if is_active:
                _activate_audit(actor, target)
            else:
                _deactivate_side_effects(actor, target, retain_session_key=None)
    return target


def activate_institutional_user(*, actor, target: User) -> User:
    """
    PRE: actor is superuser; target is a non-superuser account.
    POST: sets is_active=True when previously False; audits the event.
    """
    require_superuser(actor)
    if target.is_superuser:
        raise PermissionDenied(
            _('Las cuentas superusuario no se gestionan desde este panel.')
        )
    if target.is_active:
        return target
    with transaction.atomic():
        target.is_active = True
        target.save(update_fields=['is_active'])
        _activate_audit(actor, target)
    return target


def deactivate_institutional_user(
    *,
    actor,
    target: User,
    retain_session_key: str | None = None,
) -> User:
    """
    PRE: actor is superuser; target is a different non-superuser account.
    POST: sets is_active=False, invalidates target sessions, audits the event.
    """
    require_superuser(actor)
    if target.pk == actor.pk:
        raise ValidationError(_('No puede desactivar su propia cuenta.'))
    if target.is_superuser:
        raise PermissionDenied(
            _('Las cuentas superusuario no se gestionan desde este panel.')
        )
    if not target.is_active:
        return target
    with transaction.atomic():
        target.is_active = False
        target.save(update_fields=['is_active'])
        _deactivate_side_effects(actor, target, retain_session_key=retain_session_key)
    return target


def reset_institutional_password(
    *,
    actor,
    target: User,
    temporary_password: str,
    retain_session_key: str | None = None,
) -> User:
    """
    PRE: actor is superuser; target is a non-superuser; password already validated.
    POST: sets a new temporary password, must_change_password=True, invalidates
          target sessions (actor session retained), audits without secrets.
    """
    require_superuser(actor)
    if target.is_superuser:
        raise PermissionDenied(
            _('Las cuentas superusuario no se gestionan desde este panel.')
        )
    with transaction.atomic():
        target.set_password(temporary_password)
        target.save(update_fields=['password'])
        profile = get_or_create_access_profile(target)
        profile.must_change_password = True
        profile.password_reset_by = actor
        profile.password_reset_at = timezone.now()
        profile.save(
            update_fields=['must_change_password', 'password_reset_by', 'password_reset_at']
        )
        invalidate_user_sessions(target, retain_session_key=retain_session_key)
        _audit_user_event(
            actor=actor,
            action=AuditLog.Action.UPDATED,
            target=target,
            summary=(
                'Contraseña temporal restablecida. '
                f'target_user_id={target.pk}; target_username={target.username}; '
                f'actor_user_id={actor.pk}'
            ),
        )
    logger.info(
        'institutional_password_reset target_user_id=%s actor_user_id=%s',
        target.pk,
        actor.pk,
    )
    return target


def clear_must_change_password(*, user: User) -> None:
    """
    PRE: user completed authenticated password change successfully.
    POST: must_change_password=False when a profile exists; creates nothing new
          unless a profile already required the flag.
    """
    try:
        profile = user.access_profile
    except UserAccessProfile.DoesNotExist:
        return
    if not profile.must_change_password:
        return
    profile.must_change_password = False
    profile.save(update_fields=['must_change_password'])
    _audit_user_event(
        actor=user,
        action=AuditLog.Action.UPDATED,
        target=user,
        summary=(
            'Cambio obligatorio de contraseña completado. '
            f'target_user_id={user.pk}; target_username={user.username}; '
            f'actor_user_id={user.pk}'
        ),
    )


def _activate_audit(actor, target) -> None:
    _audit_user_event(
        actor=actor,
        action=AuditLog.Action.UPDATED,
        target=target,
        summary=(
            'Usuario activado. '
            f'target_user_id={target.pk}; target_username={target.username}; '
            f'previous_active_state=False; new_active_state=True; '
            f'actor_user_id={actor.pk}'
        ),
    )


def _deactivate_side_effects(actor, target, *, retain_session_key) -> None:
    invalidate_user_sessions(target, retain_session_key=retain_session_key)
    _audit_user_event(
        actor=actor,
        action=AuditLog.Action.UPDATED,
        target=target,
        summary=(
            'Usuario desactivado. '
            f'target_user_id={target.pk}; target_username={target.username}; '
            f'previous_active_state=True; new_active_state=False; '
            f'actor_user_id={actor.pk}'
        ),
    )
