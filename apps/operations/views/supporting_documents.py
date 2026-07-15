from django.contrib import messages

from django.core.exceptions import ValidationError

from django.shortcuts import get_object_or_404

from django.http import (
    FileResponse,
    Http404,
    HttpResponseRedirect,
)

from django.urls import reverse

from django.utils.translation import gettext_lazy as _

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
)

from ..forms import SupportingDocumentForm

from ..models import (
    Expense,
    SupportingDocument,
)

from ..services import (
    create_supporting_document,
    delete_supporting_document,
    SupportingDocumentError,
)

from .common import (
    OperationsPermissionRequiredMixin,
    add_service_errors_to_form,
)


class SupportingDocumentCreateForExpenseView(OperationsPermissionRequiredMixin, CreateView):
    permission_required = 'operations.add_supportingdocument'
    model = SupportingDocument
    form_class = SupportingDocumentForm
    template_name = 'web/supporting_document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.expense = get_object_or_404(
            Expense.objects.select_related('allocation', 'allocation__project'),
            pk=kwargs['expense_pk'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expense'] = self.expense
        return context

    def form_valid(self, form):
        # PRE: the form is valid and the request user may add supporting documents.
        # POST: delegates persistence and audit to the service, preserving the existing redirect.
        try:
            self.object = create_supporting_document(
                expense_id=self.expense.pk,
                title=form.cleaned_data['title'],
                file=form.cleaned_data['document'],
                notes=form.cleaned_data['notes'],
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        form.instance = self.object
        messages.success(self.request, _('Documento soporte adjuntado.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('expense_detail', args=[self.expense.pk])


class SupportingDocumentDownloadView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_supportingdocument'
    model = SupportingDocument

    # PRE: the requester is authenticated and has permission to view supporting documents.
    # POST: streams the requested document as an attachment without exposing its storage path.
    def get(self, request, *args, **kwargs):
        document = self.get_object()
        if not document.document.name:
            raise Http404(_('El documento soporte no tiene un archivo asociado.'))
        try:
            file_handle = document.document.open('rb')
        except (FileNotFoundError, OSError) as exc:
            raise Http404(_('El archivo del documento soporte no está disponible.')) from exc
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=document.document.name.rsplit('/', 1)[-1],
        )


class SupportingDocumentDeleteView(OperationsPermissionRequiredMixin, DeleteView):
    permission_required = 'operations.delete_supportingdocument'
    model = SupportingDocument
    template_name = 'web/supporting_document_confirm_delete.html'

    def get_success_url(self):
        return reverse('expense_detail', args=[self.object.expense_id])

    # PRE: self.object identifies a support document the user is allowed to delete.
    # POST: delegates the locked mutation and translates its domain outcome to the existing messages.
    def form_valid(self, form):
        try:
            expense_id = delete_supporting_document(
                document_id=self.object.pk,
                actor=self.request.user,
            )
        except SupportingDocumentError as error:
            messages.error(self.request, error.messages[0])
            return HttpResponseRedirect(
                reverse('expense_detail', args=[self.object.expense_id])
            )
        messages.success(self.request, _('Documento soporte eliminado.'))
        return HttpResponseRedirect(reverse('expense_detail', args=[expense_id]))
