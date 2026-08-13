from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import DonationForm
from ..models import (
    Donation,
    FundAllocation,
)
from ..selectors import with_donation_list_metrics
from ..services import (
    DONATION_STATUS_TRANSITIONS,
    OperationalEntityFinalizedError,
    annul_donation,
    create_donation,
    ensure_operational_entity_is_editable,
    get_donation_financial_summary,
    transition_donation_status,
    update_donation,
)
from .common import (
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


class DonationAnnulView(TerminalActionView):
    permission_required = 'operations.change_donation'
    model = Donation
    action_service = staticmethod(annul_donation)
    detail_url_name = 'donation_detail'
    action_title = _('Anular donación')
    consequence = _('Solo puede anularse si no tiene fondos asignados. Esta acción es irreversible.')
    submit_label = _('Confirmar anulación')
    success_message = _('Donación anulada.')


class DonationListView(
    OperationsPermissionRequiredMixin,
    FilteredListContextMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_list.html'
    context_object_name = 'objects'
    route_prefix = 'donation'
    page_title = _('Donaciones')
    status_choices = Donation.Status.choices
    institution_filter = True
    export_url_name = 'donation_export_csv'

    def get_queryset(self):
        return apply_list_filters(
            with_donation_list_metrics(Donation.objects.select_related('donor')),
            self.request.GET,
            text_fields=('code', 'donor__name'), date_field='received_date',
            institution_field='donor_id',
        ).order_by('-received_date', '-created_at', '-pk')


class DonationDetailView(StateTransitionContextMixin, OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_detail.html'
    route_prefix = 'donation'
    page_title = _('Donación')
    transition_map = DONATION_STATUS_TRANSITIONS
    transition_url_name = 'donation_status_transition'

    def get_queryset(self):
        """
        PRE: the requested donation is visible to the current user.
        POST: returns donor and terminal metadata with an allocation count, without preloading rows or expenses.
        """
        return Donation.objects.select_related('donor', 'terminal_by').annotate(
            donation_allocation_count=Count('allocations'),
        )

    def get_context_data(self, **kwargs):
        """
        PRE: self.object contains the annotated allocation count from get_queryset.
        POST: exposes the complete financial summary and at most five latest allocation rows independently.
        """
        context = super().get_context_data(**kwargs)
        allowed_targets = DONATION_STATUS_TRANSITIONS.get(self.object.status, ())
        context['can_annul'] = (
            Donation.Status.ANNULLED in allowed_targets
            and not self.object.allocations.exclude(status=FundAllocation.Status.ANNULLED).exists()
        )
        financial_summary = get_donation_financial_summary(self.object)
        recent_allocations = list(
            self.object.allocations.select_related('project').order_by(
                '-allocation_date', '-created_at', '-pk'
            )[:5]
        )
        allocation_count = self.object.donation_allocation_count
        context['donation_financial_summary'] = financial_summary
        context['recent_donation_allocations'] = recent_allocations
        context['donation_allocation_count'] = allocation_count
        context['has_more_donation_allocations'] = allocation_count > len(recent_allocations)
        context['can_create_allocation'] = (
            self.request.user.has_perm('operations.add_fundallocation')
            and self.object.status == Donation.Status.RECEIVED
            and financial_summary['available_amount'] > 0
        )
        context['show_edit_in_more'] = (
            context['can_create_allocation']
            and self.request.user.has_perm('operations.change_donation')
            and self.object.status != Donation.Status.ANNULLED
        )
        return context


class DonationCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Nueva donación')
    success_message = _('Donación creada.')

    def form_valid(self, form):
        """
        PRE: form cleaned_data holds a proposed create payload; GET never mutates.
        POST: delegates to create_donation (authoritative audit) or redisplays field errors.
        """
        try:
            self.object = create_donation(
                actor=self.request.user,
                donor=form.cleaned_data['donor'],
                donation_type=form.cleaned_data['donation_type'],
                amount=form.cleaned_data['amount'],
                objective=form.cleaned_data['objective'],
                restrictions=form.cleaned_data.get('restrictions', ''),
                commitment_date=form.cleaned_data.get('commitment_date'),
                received_date=form.cleaned_data.get('received_date'),
                support_reference=form.cleaned_data.get('support_reference', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class DonationUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Editar donación')
    success_message = _('Donación actualizada.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary donation editing and permission handling remains authoritative.
        # POST: terminal donations return 403 without form mutation.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            donation = get_object_or_404(Donation, pk=kwargs['pk'])
            try:
                ensure_operational_entity_is_editable(donation)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """
        PRE: form cleaned_data holds a proposed update; terminal donations already blocked in dispatch.
        POST: delegates to update_donation (authoritative audit) or redisplays field errors.
        """
        try:
            self.object = update_donation(
                actor=self.request.user,
                donation=self.object,
                donor=form.cleaned_data['donor'],
                donation_type=form.cleaned_data['donation_type'],
                amount=form.cleaned_data['amount'],
                objective=form.cleaned_data['objective'],
                restrictions=form.cleaned_data.get('restrictions', ''),
                commitment_date=form.cleaned_data.get('commitment_date'),
                received_date=form.cleaned_data.get('received_date'),
                support_reference=form.cleaned_data.get('support_reference', ''),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class DonationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_donation'
    model = Donation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Eliminar donación')
    audit_summary = _('Donación eliminada.')


class DonationStatusTransitionView(StateTransitionView):
    permission_required = 'operations.change_donation'
    transition_service = staticmethod(transition_donation_status)
    detail_url_name = 'donation_detail'
