"""Institutional authentication URL configuration.

Only approved routes are exposed. Email-based password reset is intentionally
absent until SMTP/provider infrastructure is configured and reviewed.
"""

from django.urls import path

from core.auth_views import (
    InstitutionalLoginView,
    InstitutionalLogoutView,
    InstitutionalPasswordChangeDoneView,
    InstitutionalPasswordChangeView,
)

urlpatterns = [
    path(
        'login/',
        InstitutionalLoginView.as_view(),
        name='login',
    ),
    path(
        'logout/',
        InstitutionalLogoutView.as_view(),
        name='logout',
    ),
    path(
        'password_change/',
        InstitutionalPasswordChangeView.as_view(),
        name='password_change',
    ),
    path(
        'password_change/done/',
        InstitutionalPasswordChangeDoneView.as_view(),
        name='password_change_done',
    ),
]
