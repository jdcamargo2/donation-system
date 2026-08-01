from django.db.models import Count

from django.urls import reverse, reverse_lazy

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import InstitutionForm

from ..file_access import build_protected_file_actions

from ..models import (
    AuditLog,
    Institution,
)

from .common import (
    AuditMixin,
    DeleteAuditMixin,
    DetailMetricsMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
)

class InstitutionListView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_list.html'
    context_object_name = 'objects'
    route_prefix = 'institution'
    page_title = _('Instituciones')

    def get_queryset(self):
        return Institution.objects.order_by('name', 'pk')

class InstitutionDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_detail.html'
    route_prefix = 'institution'
    page_title = _('Institución')

    def get_queryset(self):
        """
        PRE: the requested institution is visible to the current user.
        POST: returns it with its donation count, without loading its donation collection.
        """
        return Institution.objects.annotate(
            institution_donation_count=Count('donations'),
        )

    def get_context_data(self, **kwargs):
        """
        PRE: self.object includes the annotated donation count from get_queryset.
        POST: exposes at most five latest donations and their truthful total for the detail.
        """
        context = super().get_context_data(**kwargs)
        recent_donations = list(
            self.object.donations.order_by('-received_date', '-created_at', '-pk')[:5]
        )
        donation_count = self.object.institution_donation_count
        context['recent_institution_donations'] = recent_donations
        context['institution_donation_count'] = donation_count
        context['has_more_institution_donations'] = donation_count > len(recent_donations)
        if self.object.legal_document:
            context['legal_document_file_actions'] = build_protected_file_actions(
                file_field=self.object.legal_document,
                file_label=_('Documento legal'),
                uploaded_at=None,
                can_download=True,
                preview_url_name='institution_legal_document_preview',
                download_url_name='institution_legal_document_download',
                url_args=(self.object.pk,),
            )
        else:
            context['legal_document_file_actions'] = None
        return context


class InstitutionCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_institution'
    model = Institution
    form_class = InstitutionForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Nueva institución')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Institución creada.')


class InstitutionUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_institution'
    model = Institution
    form_class = InstitutionForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Editar institución')
    audit_summary = _('Institución actualizada.')


class InstitutionDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_institution'
    model = Institution
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('institution_list')
    route_prefix = 'institution'
    page_title = _('Eliminar institución')
    audit_summary = _('Institución eliminada.')
