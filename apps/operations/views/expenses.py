from django.contrib import messages

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)

from django.shortcuts import get_object_or_404

from django.http import HttpResponseRedirect

from django.urls import (
    reverse,
    reverse_lazy,
)

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
)

from ..forms import (
    ExpenseAnnulmentForm,
    ExpenseForm,
)

from ..models import (
    AuditLog,
    Expense,
)

from ..selectors import with_expense_list_support

from ..services import (
    create_expense,
    annul_expense,
    ensure_expense_is_deletable,
    ensure_expense_is_editable,
    ExpenseFinalizedError,
    update_expense,
)

from .common import (
    DeleteAuditMixin,
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    add_service_errors_to_form,
    apply_list_filters,
)


class ExpenseListView(OperationsPermissionRequiredMixin, FilteredListContextMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_list.html'
    context_object_name = 'objects'
    route_prefix = 'expense'
    page_title = _('Gastos')
    status_choices = Expense.Status.choices
    institution_filter = True
    project_filter = True
    export_url_name = 'expense_export_csv'

    def get_queryset(self):
        queryset = with_expense_list_support(
            Expense.objects.select_related(
                'allocation__donation__donor',
                'allocation__project',
            )
        )
        return apply_list_filters(
            queryset, self.request.GET,
            text_fields=('code', 'reason', 'provider_or_recipient', 'allocation__project__code'),
            date_field='expense_date', institution_field='allocation__donation__donor_id',
            project_field='allocation__project_id',
        )


class ExpenseDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_detail.html'
    route_prefix = 'expense'
    page_title = _('Gasto')


class ExpenseCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_expense'
    model = Expense
    form_class = ExpenseForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Nuevo gasto')
    audit_action = AuditLog.Action.EXECUTED
    audit_summary = _('Gasto registrado.')

    def form_valid(self, form):
        try:
            self.object = create_expense(
                allocation=form.cleaned_data['allocation'],
                expense_date=form.cleaned_data['expense_date'],
                category=form.cleaned_data['category'],
                amount=form.cleaned_data['amount'],
                reason=form.cleaned_data['reason'],
                provider_or_recipient=form.cleaned_data['provider_or_recipient'],
                payment_method=form.cleaned_data['payment_method'],
                description=form.cleaned_data.get('description', ''),
                observations=form.cleaned_data.get('observations', ''),
                actor=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_expense'
    model = Expense
    form_class = ExpenseForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Editar gasto')
    audit_action = AuditLog.Action.EXECUTED
    audit_summary = _('Gasto actualizado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary editing of an existing expense.
        # POST: permits editable expenses only; finalized expenses return 403.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        expense = get_object_or_404(Expense, pk=kwargs['pk'])
        try:
            ensure_expense_is_editable(expense)
        except ExpenseFinalizedError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            self.object = update_expense(
                expense=self.object,
                allocation=form.cleaned_data['allocation'],
                expense_date=form.cleaned_data['expense_date'],
                category=form.cleaned_data['category'],
                amount=form.cleaned_data['amount'],
                reason=form.cleaned_data['reason'],
                provider_or_recipient=form.cleaned_data['provider_or_recipient'],
                payment_method=form.cleaned_data['payment_method'],
                description=form.cleaned_data.get('description', ''),
                observations=form.cleaned_data.get('observations', ''),
                actor=self.request.user,
                support_title=form.cleaned_data.get('support_title', ''),
                support_file=form.cleaned_data.get('support_file'),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class ExpenseDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_expense'
    model = Expense
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Eliminar gasto')
    audit_summary = _('Gasto eliminado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary deletion of an existing expense.
        # POST: permits deletable expenses only; finalized expenses return 403.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        expense = get_object_or_404(Expense, pk=kwargs['pk'])
        try:
            ensure_expense_is_deletable(expense)
        except ExpenseFinalizedError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


class ExpenseAnnulView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_expense'
    form_class = ExpenseAnnulmentForm
    template_name = 'web/object_form.html'

    def dispatch(self, request, *args, **kwargs):
        # PRE: request identifies an expense and user passed authentication/permission handling.
        # POST: loads the target without mutation; state changes remain POST-only.
        if not request.user.is_authenticated or not request.user.has_perm(
            self.permission_required
        ):
            return super().dispatch(request, *args, **kwargs)
        self.expense = get_object_or_404(Expense, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        # PRE: expense is loaded and form context may contain validation errors.
        # POST: returns cancellation-only UI context without financial/status inputs.
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'object': self.expense,
                'title': _('Anular gasto'),
                'list_url_name': 'expense_detail',
                'cancel_object_pk': self.expense.pk,
                'submit_label': _('Anular gasto'),
            }
        )
        return context

    def form_valid(self, form):
        # PRE: POST reason is valid and request user has change_expense.
        # POST: cancels through the atomic domain service and redirects to detail.
        try:
            annul_expense(
                self.expense.pk,
                actor=self.request.user,
                reason=form.cleaned_data['reason'],
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Gasto anulado.'))
        return HttpResponseRedirect(
            reverse('expense_detail', args=[self.expense.pk])
        )
