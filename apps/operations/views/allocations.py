from django.contrib import messages

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)

from django.shortcuts import get_object_or_404

from django.http import HttpResponseRedirect

from django.urls import reverse_lazy

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import FundAllocationForm

from ..models import (
    AuditLog,
    Expense,
    FundAllocation,
)

from ..selectors import with_allocation_list_metrics

from ..services import (
    create_fund_allocation,
    get_allocation_financial_summary,
    OperationalEntityFinalizedError,
    allocation_has_effective_expenses,
    annul_fund_allocation,
    ensure_operational_entity_is_editable,
    update_fund_allocation,
    FUND_ALLOCATION_STATUS_TRANSITIONS,
    transition_fund_allocation_status,
)

from .common import (
    AuditMixin,
    DeleteAuditMixin,
    DetailMetricsMixin,
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
    StateTransitionContextMixin,
    StateTransitionView,
    TerminalActionView,
    add_service_errors_to_form,
    apply_list_filters,
)


class FundAllocationAnnulView(TerminalActionView):
    permission_required = 'operations.change_fundallocation'
    model = FundAllocation
    action_service = staticmethod(annul_fund_allocation)
    detail_url_name = 'allocation_detail'
    action_title = _('Anular asignación')
    consequence = _(
        'Al anular esta asignación, el monto volverá al saldo disponible de la donación. '
        'No puede anularse si ya existen gastos efectivos.'
    )
    submit_label = _('Confirmar anulación')
    success_message = _('Asignación anulada.')


class FundAllocationListView(
    OperationsPermissionRequiredMixin,
    FilteredListContextMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_list.html'
    context_object_name = 'objects'
    route_prefix = 'allocation'
    page_title = _('Asignaciones de fondos')
    status_choices = FundAllocation.Status.choices
    institution_filter = True
    project_filter = True
    export_url_name = 'allocation_export_csv'

    def get_queryset(self):
        return apply_list_filters(
            with_allocation_list_metrics(
                FundAllocation.objects.select_related('donation__donor', 'project')
            ),
            self.request.GET,
            text_fields=('code', 'donation__code', 'project__code', 'project__name'),
            date_field='allocation_date', institution_field='donation__donor_id',
            project_field='project_id',
        ).order_by('-allocation_date', '-created_at', '-pk')

class FundAllocationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_detail.html'
    route_prefix = 'allocation'
    page_title = _('Asignación de fondos')
    transition_map = FUND_ALLOCATION_STATUS_TRANSITIONS
    transition_url_name = 'allocation_status_transition'

    def get_queryset(self):
        # PRE: la vista consulta una asignación autorizada por clave primaria.
        # POST: carga origen, destino, metadata terminal y gastos para render sin N+1.
        return FundAllocation.objects.select_related(
            'donation__donor', 'project', 'terminal_by'
        ).prefetch_related('expenses')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed_targets = FUND_ALLOCATION_STATUS_TRANSITIONS.get(self.object.status, ())
        context['can_annul'] = (
            FundAllocation.Status.ANNULLED in allowed_targets
            and not allocation_has_effective_expenses(self.object)
        )
        expenses = list(self.object.expenses.all())
        context['allocation_financial_summary'] = get_allocation_financial_summary(self.object)
        context['registered_expenses'] = [
            expense for expense in expenses if expense.status != Expense.Status.ANNULLED
        ]
        context['annulled_expenses'] = [
            expense for expense in expenses if expense.status == Expense.Status.ANNULLED
        ]
        return context


class FundAllocationCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_fundallocation'
    model = FundAllocation
    form_class = FundAllocationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Nueva asignación de fondos')
    audit_action = AuditLog.Action.ASSIGNED
    audit_summary = _('Asignación de fondos registrada.')

    def form_valid(self, form):
        try:
            self.object = create_fund_allocation(
                donation=form.cleaned_data['donation'],
                project=form.cleaned_data['project'],
                budget_category=form.cleaned_data['budget_category'],
                amount=form.cleaned_data['amount'],
                responsible_person=form.cleaned_data.get('responsible_person', ''),
                allocation_date=form.cleaned_data['allocation_date'],
                status=FundAllocation.Status.ACTIVE,
                notes=form.cleaned_data.get('notes', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class FundAllocationUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_fundallocation'
    model = FundAllocation
    form_class = FundAllocationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Editar asignación de fondos')
    audit_action = AuditLog.Action.ASSIGNED
    audit_summary = _('Asignación de fondos actualizada.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary allocation editing and permission handling remains authoritative.
        # POST: terminal allocations return 403 without form or service mutation.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            allocation = get_object_or_404(FundAllocation, pk=kwargs['pk'])
            try:
                ensure_operational_entity_is_editable(allocation)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            self.object = update_fund_allocation(
                allocation=self.object,
                donation=form.cleaned_data['donation'],
                project=form.cleaned_data['project'],
                budget_category=form.cleaned_data['budget_category'],
                amount=form.cleaned_data['amount'],
                responsible_person=form.cleaned_data.get('responsible_person', ''),
                allocation_date=form.cleaned_data['allocation_date'],
                status=self.object.status,
                notes=form.cleaned_data.get('notes', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return HttpResponseRedirect(self.get_success_url())


class FundAllocationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_fundallocation'
    model = FundAllocation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Eliminar asignación de fondos')
    audit_summary = _('Asignación de fondos eliminada.')


class FundAllocationStatusTransitionView(StateTransitionView):
    permission_required = 'operations.change_fundallocation'
    transition_service = staticmethod(transition_fund_allocation_status)
    detail_url_name = 'allocation_detail'
