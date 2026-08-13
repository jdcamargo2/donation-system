from django.contrib import messages

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)

from django.db.models import Count

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
    FundAllocation,
    Project,
)

from ..selectors import (
    allocation_has_open_financial_work,
    expense_request_allocation_choices,
    visible_expense_requests_for_allocation,
    with_allocation_list_metrics,
)

from ..services import (
    create_fund_allocation,
    get_allocation_financial_summary,
    OperationalEntityFinalizedError,
    allocation_has_effective_expenses,
    annul_fund_allocation,
    ensure_operational_entity_is_editable,
    finish_fund_allocation,
    update_fund_allocation,
    FUND_ALLOCATION_STATUS_TRANSITIONS,
)

from .common import (
    AuditMixin,
    DeleteAuditMixin,
    DetailMetricsMixin,
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
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


class FundAllocationFinishView(TerminalActionView):
    permission_required = 'operations.change_fundallocation'
    model = FundAllocation
    action_service = staticmethod(finish_fund_allocation)
    detail_url_name = 'allocation_detail'
    action_title = _('Finalizar asignación')
    consequence = _(
        'Al finalizar esta asignación no podrá recibir nuevas solicitudes de gasto. '
        'Debe resolver primero cualquier solicitud pendiente o reserva activa.'
    )
    submit_label = _('Confirmar finalización')
    success_message = _('Asignación finalizada.')
    is_destructive = False
    requires_reason = False


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

RECENT_ALLOCATION_EXPENSE_REQUESTS_LIMIT = 5


class FundAllocationDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_detail.html'
    route_prefix = 'allocation'
    page_title = _('Asignación de fondos')

    def get_queryset(self):
        # PRE: la vista consulta una asignación autorizada por clave primaria.
        # POST: carga origen, destino, metadata terminal y el total de gastos sin precargar filas visuales.
        return FundAllocation.objects.select_related(
            'donation__donor', 'project', 'terminal_by'
        ).annotate(allocation_expense_count=Count('expenses'))

    def get_context_data(self, **kwargs):
        """
        PRE: self.object incluye el conteo anotado de gastos de la asignación.
        POST: expone el resumen completo, solicitudes visibles acotadas y hasta cinco
              gastos recientes en orden estable. El CTA de solicitud usa el selector
              canónico de asignaciones elegibles.
        """
        context = super().get_context_data(**kwargs)
        user = self.request.user
        allowed_targets = FUND_ALLOCATION_STATUS_TRANSITIONS.get(self.object.status, ())
        has_open_financial_work = allocation_has_open_financial_work(self.object)
        can_change = user.has_perm('operations.change_fundallocation')
        context['can_annul'] = (
            FundAllocation.Status.ANNULLED in allowed_targets
            and not allocation_has_effective_expenses(self.object)
        )
        context['can_finish'] = (
            can_change
            and FundAllocation.Status.FINISHED in allowed_targets
            and not has_open_financial_work
        )
        context['show_finish_guidance'] = (
            can_change
            and FundAllocation.Status.FINISHED in allowed_targets
            and has_open_financial_work
        )
        financial_summary = get_allocation_financial_summary(self.object)
        recent_expenses = list(
            self.object.expenses.order_by('-expense_date', '-created_at', '-pk')[:5]
        )
        expense_count = self.object.allocation_expense_count
        context['allocation_financial_summary'] = financial_summary
        context['recent_allocation_expenses'] = recent_expenses
        context['allocation_expense_count'] = expense_count
        context['has_more_allocation_expenses'] = expense_count > len(recent_expenses)
        context['can_create_expense_request'] = (
            user.has_perm('operations.add_expenserequest')
            and expense_request_allocation_choices(project=self.object.project)
            .filter(pk=self.object.pk)
            .exists()
        )
        context['can_view_expense_requests'] = user.has_perm(
            'operations.view_expenserequest'
        )
        if context['can_view_expense_requests']:
            linked_requests = visible_expense_requests_for_allocation(
                user=user,
                allocation=self.object,
            )
            preview = list(
                linked_requests[: RECENT_ALLOCATION_EXPENSE_REQUESTS_LIMIT + 1]
            )
            has_more = len(preview) > RECENT_ALLOCATION_EXPENSE_REQUESTS_LIMIT
            recent_requests = preview[:RECENT_ALLOCATION_EXPENSE_REQUESTS_LIMIT]
            request_count = (
                linked_requests.count() if has_more else len(recent_requests)
            )
            context['recent_allocation_expense_requests'] = recent_requests
            context['allocation_expense_request_count'] = request_count
            context['has_more_allocation_expense_requests'] = has_more
        else:
            context['recent_allocation_expense_requests'] = []
            context['allocation_expense_request_count'] = 0
            context['has_more_allocation_expense_requests'] = False
        # Edit-in-more stays available for ACTIVE allocations with remaining balance
        # even when the Expense Request CTA is hidden for other eligibility reasons.
        context['show_edit_in_more'] = (
            user.has_perm('operations.change_fundallocation')
            and self.object.status == FundAllocation.Status.ACTIVE
            and self.object.project.status == Project.Status.ACTIVE
            and financial_summary['available_amount'] > 0
        )
        context['show_more_actions'] = (
            context['show_edit_in_more']
            or context['can_finish']
            or (
                user.has_perm('operations.change_fundallocation')
                and context['can_annul']
            )
            or user.has_perm('operations.delete_fundallocation')
        )
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
