"""Institutional login/logout/password-change views with hardened redirects."""

from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from apps.operations.user_access_services import clear_must_change_password


class InstitutionalLoginView(LoginView):
    """
    PRE: request may include an untrusted ``next`` query/post parameter.
    POST: authenticates against institutional credentials; only same-host
          relative destinations are honored; external destinations fall back
          to LOGIN_REDIRECT_URL (/panel/).
    """

    template_name = 'registration/login.html'
    redirect_authenticated_user = False

    def get_redirect_url(self):
        redirect_to = self.request.POST.get(
            self.redirect_field_name,
            self.request.GET.get(self.redirect_field_name, ''),
        )
        if not redirect_to or '\\' in redirect_to or redirect_to.startswith('//'):
            return ''
        # Reject encoded scheme smuggling such as https:%2F%2Fevil.example
        lowered = redirect_to.lower()
        if '://' in lowered or lowered.startswith('https:') or lowered.startswith('http:'):
            return ''
        url_is_safe = url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        )
        if url_is_safe and redirect_to.startswith('/'):
            return redirect_to
        return ''


class InstitutionalLogoutView(LogoutView):
    """
    PRE: authenticated session may exist; only POST is accepted.
    POST: ends the session and redirects to the public portal root.
    GET never logs the user out.
    """

    next_page = reverse_lazy('public_portal:public_home')
    http_method_names = ['post', 'options']


class InstitutionalPasswordChangeView(PasswordChangeView):
    """
    PRE: user is authenticated; may carry must_change_password=True.
    POST: on success clears the must-change flag and redirects to done.
    """

    template_name = 'registration/password_change_form.html'
    success_url = reverse_lazy('password_change_done')

    def form_valid(self, form):
        response = super().form_valid(form)
        clear_must_change_password(user=self.request.user)
        return response


class InstitutionalPasswordChangeDoneView(PasswordChangeDoneView):
    template_name = 'registration/password_change_done.html'
