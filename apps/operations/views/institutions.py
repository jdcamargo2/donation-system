from django.urls import reverse_lazy

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import InstitutionForm

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
    _protected_file_response,
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


class InstitutionLegalDocumentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_institution'
    model = Institution

    def get(self, request, *args, **kwargs):
        """
        PRE: user is authenticated with view_institution and pk identifies an
        institution whose legal document exists in storage.
        POST: returns an attachment response without mutation or storage paths.
        """
        institution = self.get_object()
        return _protected_file_response(
            institution.legal_document,
            missing_message=_('El documento legal no está disponible.'),
        )


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
