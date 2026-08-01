"""Parent-scoped protected file preview/download views."""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView

from ..file_access import (
    DISPOSITION_ATTACHMENT,
    DISPOSITION_INLINE,
    user_can_access_project_supporting_document,
)
from ..models import (
    Expense,
    Institution,
    Project,
    ProjectDocument,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
    SupportingDocument,
)
from .common import OperationsPermissionRequiredMixin, _protected_file_response


class ProtectedFileDispositionMixin:
    """Streams a FileField with inline or attachment disposition."""

    disposition = DISPOSITION_ATTACHMENT
    file_attr = 'file'
    missing_message = _('El archivo no está disponible.')

    def get_file_field(self, obj):
        return getattr(obj, self.file_attr)

    def get(self, request, *args, **kwargs):
        obj = self.get_object()
        return _protected_file_response(
            self.get_file_field(obj),
            disposition=self.disposition,
            missing_message=self.missing_message,
        )


class InstitutionLegalDocumentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    permission_required = 'operations.view_institution'
    model = Institution
    file_attr = 'legal_document'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El documento legal no está disponible.')


class InstitutionLegalDocumentPreviewView(InstitutionLegalDocumentDownloadView):
    disposition = DISPOSITION_INLINE


class ProjectDocumentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    permission_required = 'operations.view_projectdocument'
    model = ProjectDocument
    file_attr = 'file'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El documento de proyecto no está disponible.')
    pk_url_kwarg = 'pk'

    def get_queryset(self):
        """
        PRE: URL includes project_pk and document pk.
        POST: only documents belonging to that project are visible.
        """
        return ProjectDocument.objects.filter(project_id=self.kwargs['project_pk'])

    def get_object(self, queryset=None):
        # Also require the project itself exists (no metadata leak across projects).
        get_object_or_404(Project, pk=self.kwargs['project_pk'])
        return super().get_object(queryset=queryset)


class ProjectDocumentPreviewView(ProjectDocumentDownloadView):
    disposition = DISPOSITION_INLINE


class ProjectUpdateAttachmentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    permission_required = (
        'operations.view_project',
        'operations.view_projectupdate',
        'operations.view_projectupdateattachment',
    )
    model = ProjectUpdateAttachment
    file_attr = 'file'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El adjunto del avance no está disponible.')
    pk_url_kwarg = 'pk'

    def get_queryset(self):
        """
        PRE: URL nests project_pk, update_pk, and attachment pk.
        POST: attachment must belong to that update and that project's update.
        """
        return ProjectUpdateAttachment.objects.filter(
            pk=self.kwargs['pk'],
            project_update_id=self.kwargs['update_pk'],
            project_update__project_id=self.kwargs['project_pk'],
        )

    def get_object(self, queryset=None):
        get_object_or_404(
            ProjectUpdate,
            pk=self.kwargs['update_pk'],
            project_id=self.kwargs['project_pk'],
        )
        return super().get_object(queryset=queryset)


class ProjectUpdateAttachmentPreviewView(ProjectUpdateAttachmentDownloadView):
    disposition = DISPOSITION_INLINE


class ProjectUpdateRemediationAttachmentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    permission_required = (
        'operations.view_projectupdateremediation',
        'operations.view_projectupdateremediationattachment',
    )
    model = ProjectUpdateRemediationAttachment
    file_attr = 'file'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El adjunto de remediación no está disponible.')
    pk_url_kwarg = 'pk'

    def get_queryset(self):
        return ProjectUpdateRemediationAttachment.objects.filter(
            pk=self.kwargs['pk'],
            remediation_id=self.kwargs['remediation_pk'],
        )

    def get_object(self, queryset=None):
        get_object_or_404(ProjectUpdateRemediation, pk=self.kwargs['remediation_pk'])
        return super().get_object(queryset=queryset)


class ProjectUpdateRemediationAttachmentPreviewView(
    ProjectUpdateRemediationAttachmentDownloadView
):
    disposition = DISPOSITION_INLINE


class SupportingDocumentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    """
    Expense-scoped supporting document download.

    Authorization: view_supportingdocument plus project visibility via the
    narrow helper (does not require view_expense).
    """

    permission_required = 'operations.view_supportingdocument'
    model = SupportingDocument
    file_attr = 'document'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El archivo del documento soporte no está disponible.')
    pk_url_kwarg = 'pk'

    def get_queryset(self):
        return SupportingDocument.objects.select_related(
            'expense__allocation__project',
        ).filter(
            pk=self.kwargs['pk'],
            expense_id=self.kwargs['expense_pk'],
        )

    def get_object(self, queryset=None):
        expense = get_object_or_404(
            Expense.objects.select_related('allocation__project'),
            pk=self.kwargs['expense_pk'],
        )
        document = super().get_object(queryset=queryset)
        if document.expense.allocation.project_id != expense.allocation.project_id:
            raise Http404(self.missing_message)
        if not user_can_access_project_supporting_document(self.request.user, document):
            raise Http404(self.missing_message)
        return document


class SupportingDocumentPreviewView(SupportingDocumentDownloadView):
    disposition = DISPOSITION_INLINE


class ProjectSupportingDocumentDownloadView(
    OperationsPermissionRequiredMixin,
    ProtectedFileDispositionMixin,
    DetailView,
):
    """
    Project-scoped supporting document access for roles without view_expense.

    Policy: view_supportingdocument + view_project + document belongs to an
    expense allocated to the URL project. No financial fields are exposed here.
    """

    permission_required = (
        'operations.view_project',
        'operations.view_supportingdocument',
    )
    model = SupportingDocument
    file_attr = 'document'
    disposition = DISPOSITION_ATTACHMENT
    missing_message = _('El archivo del documento soporte no está disponible.')
    pk_url_kwarg = 'pk'

    def get_queryset(self):
        return SupportingDocument.objects.select_related(
            'expense__allocation__project',
        ).filter(
            pk=self.kwargs['pk'],
            expense__allocation__project_id=self.kwargs['project_pk'],
        )

    def get_object(self, queryset=None):
        get_object_or_404(Project, pk=self.kwargs['project_pk'])
        document = super().get_object(queryset=queryset)
        if not user_can_access_project_supporting_document(self.request.user, document):
            raise Http404(self.missing_message)
        return document


class ProjectSupportingDocumentPreviewView(ProjectSupportingDocumentDownloadView):
    disposition = DISPOSITION_INLINE
