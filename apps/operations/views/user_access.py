"""Superuser-only institutional user management views.

Access gate: request.user.is_authenticated and request.user.is_superuser.
Authenticated non-superusers receive 403 (repository convention for internal
authorization transparency). Anonymous users redirect to institutional login.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from ..forms import (
    InstitutionalUserCreateForm,
    InstitutionalUserPasswordResetForm,
    InstitutionalUserUpdateForm,
)
from ..pagination import (
    ALLOWED_PAGE_SIZES,
    DEFAULT_PAGE_SIZE,
    build_pagination_page_numbers,
    parse_page_size,
)
from ..role_services import get_user_functional_role, operation_role_names
from ..user_access_services import (
    activate_institutional_user,
    create_institutional_user,
    deactivate_institutional_user,
    reset_institutional_password,
    update_institutional_user,
)
from .common import add_service_errors_to_form

User = get_user_model()


class SuperuserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """
    PRE: request.user may be anonymous, staff, functional admin, or superuser.
    POST: anonymous → login redirect with safe next; authenticated non-superuser
          → 403; superuser proceeds.
    """

    raise_exception = True

    def test_func(self):
        user = self.request.user
        return bool(user.is_authenticated and user.is_superuser)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(
                _('Solo un superusuario puede gestionar cuentas institucionales.')
            )
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(
            self.request.get_full_path(),
            self.get_login_url(),
            self.get_redirect_field_name(),
        )


class UserAccessListView(SuperuserRequiredMixin, ListView):
    template_name = 'web/user_access_list.html'
    context_object_name = 'users'
    paginate_by = DEFAULT_PAGE_SIZE

    def get_paginate_by(self, queryset):
        return parse_page_size(self.request.GET, allowed=ALLOWED_PAGE_SIZES)

    def get_queryset(self):
        qs = User.objects.all().order_by('username')
        q = (self.request.GET.get('q') or '').strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
            )
        role = (self.request.GET.get('role') or '').strip()
        if role in operation_role_names():
            qs = qs.filter(groups__name=role)
        status = (self.request.GET.get('status') or '').strip()
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)
        return qs.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_obj = context['page_obj']
        users = list(context['users'])
        for user in users:
            user.display_functional_role = get_user_functional_role(user)
        context.update(
            {
                'title': _('Gestión de usuarios'),
                'users': users,
                'role_choices': operation_role_names(),
                'filter_q': (self.request.GET.get('q') or '').strip(),
                'filter_role': (self.request.GET.get('role') or '').strip(),
                'filter_status': (self.request.GET.get('status') or '').strip(),
                'pagination_page_numbers': build_pagination_page_numbers(page_obj),
            }
        )
        return context


class UserAccessCreateView(SuperuserRequiredMixin, FormView):
    template_name = 'web/object_form.html'
    form_class = InstitutionalUserCreateForm
    success_url = reverse_lazy('user_access_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': _('Nuevo usuario institucional'),
                'list_url_name': 'user_access_list',
                'submit_label': _('Crear usuario'),
                'form_subtitle': _(
                    'Asigne un rol funcional canónico y una contraseña temporal. '
                    'Entregue las credenciales por un canal externo aprobado. '
                    'El usuario deberá cambiar la contraseña al iniciar sesión.'
                ),
            }
        )
        return context

    def form_valid(self, form):
        try:
            user = create_institutional_user(
                actor=self.request.user,
                username=form.cleaned_data['username'],
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                email=form.cleaned_data['email'],
                functional_role=form.cleaned_data['functional_role'],
                temporary_password=form.cleaned_data['temporary_password'],
                is_active=form.cleaned_data['is_active'],
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _(
                'Usuario %(username)s creado. Entregue la contraseña temporal '
                'por un canal externo aprobado. El usuario deberá cambiarla '
                'al iniciar sesión.'
            )
            % {'username': user.username},
        )
        return redirect('user_access_detail', pk=user.pk)


class UserAccessDetailView(SuperuserRequiredMixin, DetailView):
    model = User
    template_name = 'web/user_access_detail.html'
    context_object_name = 'target_user'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = self.object
        context.update(
            {
                'title': _('Usuario: %(username)s') % {'username': target.username},
                'functional_role': get_user_functional_role(target),
                'is_readonly_superuser': target.is_superuser,
            }
        )
        return context


class UserAccessUpdateView(SuperuserRequiredMixin, FormView):
    template_name = 'web/object_form.html'
    form_class = InstitutionalUserUpdateForm

    def dispatch(self, request, *args, **kwargs):
        self.target = get_object_or_404(User, pk=kwargs['pk'])
        if self.target.is_superuser:
            raise PermissionDenied(
                _('Las cuentas superusuario no se gestionan desde este panel.')
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.target
        return kwargs

    def get_success_url(self):
        return reverse('user_access_detail', kwargs={'pk': self.target.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': _('Editar usuario institucional'),
                'list_url_name': 'user_access_list',
                'submit_label': _('Guardar cambios'),
                'form_subtitle': _(
                    'El nombre de usuario no se modifica. No se pueden asignar '
                    'privilegios de superusuario ni personal técnico desde este panel.'
                ),
            }
        )
        return context

    def form_valid(self, form):
        try:
            update_institutional_user(
                actor=self.request.user,
                target=self.target,
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data['last_name'],
                email=form.cleaned_data['email'],
                functional_role=form.cleaned_data['functional_role'],
                is_active=form.cleaned_data['is_active'],
            )
        except PermissionDenied:
            raise
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Usuario actualizado.'))
        return redirect(self.get_success_url())


class UserAccessActivateView(SuperuserRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        try:
            activate_institutional_user(actor=request.user, target=target)
        except PermissionDenied:
            raise
        messages.success(request, _('Usuario activado.'))
        return redirect('user_access_detail', pk=target.pk)


class UserAccessDeactivateView(SuperuserRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        try:
            deactivate_institutional_user(
                actor=request.user,
                target=target,
                retain_session_key=request.session.session_key,
            )
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
            return redirect('user_access_detail', pk=target.pk)
        except PermissionDenied:
            raise
        messages.success(
            request,
            _('Usuario desactivado. Sus sesiones activas fueron invalidadas.'),
        )
        return redirect('user_access_detail', pk=target.pk)


class UserAccessResetPasswordView(SuperuserRequiredMixin, FormView):
    template_name = 'web/object_form.html'
    form_class = InstitutionalUserPasswordResetForm

    def dispatch(self, request, *args, **kwargs):
        self.target = get_object_or_404(User, pk=kwargs['pk'])
        if self.target.is_superuser:
            raise PermissionDenied(
                _('Las cuentas superusuario no se gestionan desde este panel.')
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.target
        return kwargs

    def get_success_url(self):
        return reverse('user_access_detail', kwargs={'pk': self.target.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': _('Restablecer contraseña temporal'),
                'list_url_name': 'user_access_list',
                'submit_label': _('Restablecer contraseña'),
                'form_subtitle': _(
                    'Defina una contraseña temporal para %(username)s. '
                    'No se mostrará de nuevo. Entreguela por un canal externo aprobado.'
                )
                % {'username': self.target.username},
            }
        )
        return context

    def form_valid(self, form):
        try:
            reset_institutional_password(
                actor=self.request.user,
                target=self.target,
                temporary_password=form.cleaned_data['temporary_password'],
                retain_session_key=self.request.session.session_key,
            )
        except PermissionDenied:
            raise
        messages.success(
            self.request,
            _(
                'La contraseña temporal fue actualizada. '
                'El usuario deberá cambiarla al iniciar sesión.'
            ),
        )
        return redirect(self.get_success_url())
