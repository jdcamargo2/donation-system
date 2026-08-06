"""Middleware enforcing mandatory password change after provisional credentials."""

from django.shortcuts import redirect
from django.urls import reverse

from apps.operations.models import UserAccessProfile


# Paths a flagged user may still reach before changing their temporary password.
_PASSWORD_CHANGE_ALLOWED_PREFIXES = (
    '/accounts/password_change/',
    '/accounts/logout/',
    '/static/',
    '/healthz/',
    '/readyz/',
)

# Only these prefixes are blocked for flagged users; public portal stays open
# and unrelated routes avoid an extra profile query.
_PASSWORD_CHANGE_ENFORCED_PREFIXES = (
    '/panel/',
    '/admin/',
    '/accounts/',
)


def user_must_change_password(user) -> bool:
    """
    PRE: user may be anonymous or authenticated.
    POST: True only when an authenticated user has an access profile with the
          must_change_password flag set. Missing profiles default to False.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return UserAccessProfile.objects.filter(
        user_id=user.pk,
        must_change_password=True,
    ).exists()


class MustChangePasswordMiddleware:
    """
    PRE: AuthenticationMiddleware has attached request.user.
    POST: flagged users may only reach password change, logout, static assets,
          and health endpoints; /panel/** is blocked; public portal remains open.
          Prevents redirect loops on the password-change views themselves.
          Profile lookup runs only for enforced path prefixes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        path = request.path
        enforced = path.startswith(_PASSWORD_CHANGE_ENFORCED_PREFIXES)
        allowed = path.startswith(_PASSWORD_CHANGE_ALLOWED_PREFIXES)
        if enforced and not allowed and user_must_change_password(user):
            return redirect(reverse('password_change'))
        return self.get_response(request)
