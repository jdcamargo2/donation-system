from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from .forms import (
    DonationForm,
    ExpenseForm,
    FundAllocationForm,
    InstitutionForm,
    ProjectForm,
    ProjectUpdateForProjectForm,
    ProjectUpdateForm,
    ProjectUpdateReviewForm,
    SupportingDocumentForm,
)
from .models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument
from .services import (
    get_allocation_financial_summary,
    get_dashboard_metrics,
    get_donation_financial_summary,
    get_project_financial_summary,
    log_action,
    log_delete,
    register_advance,
    review_project_update,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_dashboard_metrics())
        return context


class OperationsPermissionRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    raise_exception = True

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())
        return redirect_to_login(self.request.get_full_path(), self.get_login_url(), self.get_redirect_field_name())


class RouteContextMixin:
    route_prefix = ''
    page_title = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': self.page_title,
                'list_url_name': f'{self.route_prefix}_list',
                'create_url_name': f'{self.route_prefix}_create',
                'detail_url_name': f'{self.route_prefix}_detail',
                'update_url_name': f'{self.route_prefix}_update',
                'delete_url_name': f'{self.route_prefix}_delete',
            }
        )
        return context


class DetailMetricsMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        metrics = []
        if isinstance(self.object, Donation):
            summary = get_donation_financial_summary(self.object)
            metrics = [
                (_('Monto'), summary['total_amount']),
                (_('Monto asignado'), summary['assigned_amount']),
                (_('Saldo disponible'), summary['available_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, Project):
            summary = get_project_financial_summary(self.object)
            metrics = [
                (_('Presupuesto estimado'), summary['estimated_budget']),
                (_('Monto financiado'), summary['funded_amount']),
                (_('Monto ejecutado'), summary['executed_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, FundAllocation):
            summary = get_allocation_financial_summary(self.object)
            metrics = [
                (_('Monto'), summary['allocated_amount']),
                (_('Monto ejecutado'), summary['executed_amount']),
                (_('Saldo disponible'), summary['available_amount']),
                (_('Estado'), self.object.get_status_display()),
            ]
        elif isinstance(self.object, Institution):
            metrics = [
                (_('Rol'), self.object.get_role_display()),
                (_('País'), self.object.country.name or '-'),
                (_('Estado'), self.object.get_status_display()),
            ]
        context['metrics'] = metrics
        return context


class AuditMixin:
    audit_action = AuditLog.Action.UPDATED
    audit_summary = _('Registro actualizado.')

    # PRE: self.object has just been saved and request.user is available.
    # POST: creates one audit log describing the saved entity and user action.
    def write_audit_log(self):
        log_action(self.request.user, self.audit_action, self.object, self.audit_summary)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.write_audit_log()
        messages.success(self.request, self.audit_summary)
        return response


class DeleteAuditMixin:
    audit_summary = _('Registro eliminado.')

    # PRE: self.object exists and is about to be deleted through an operational DeleteView.
    # POST: writes an audit record before the object is removed from the database.
    def form_valid(self, form):
        log_delete(self.request.user, self.object, self.audit_summary)
        messages.success(self.request, self.audit_summary)
        return super().form_valid(form)


class InstitutionListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_list.html'
    context_object_name = 'objects'
    route_prefix = 'institution'
    page_title = _('Instituciones')


class InstitutionDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_institution'
    model = Institution
    template_name = 'web/institution_detail.html'
    route_prefix = 'institution'
    page_title = _('Institución')


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


class ProjectListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/object_list.html'
    context_object_name = 'objects'
    route_prefix = 'project'
    page_title = _('Proyectos')


class ProjectDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_project'
    model = Project
    template_name = 'web/project_detail.html'
    route_prefix = 'project'
    page_title = _('Proyecto')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_updates'] = self.object.updates.all().order_by('-created_at')
        return context


class ProjectCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_project'
    model = Project
    form_class = ProjectForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Nuevo proyecto')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Proyecto creado.')


class ProjectUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_project'
    model = Project
    form_class = ProjectForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Editar proyecto')
    audit_summary = _('Proyecto actualizado.')


class ProjectDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_project'
    model = Project
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('project_list')
    route_prefix = 'project'
    page_title = _('Eliminar proyecto')
    audit_summary = _('Proyecto eliminado.')


class ProjectUpdateListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_list.html'
    context_object_name = 'objects'
    route_prefix = 'project_update'
    page_title = _('Avances de proyecto')

    def get_queryset(self):
        return ProjectUpdate.objects.select_related('project', 'created_by', 'reviewed_by')


class ProjectUpdateDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_detail.html'
    route_prefix = 'project_update'
    page_title = _('Avance de proyecto')


class ProjectUpdateCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Nuevo avance de proyecto')

    def form_valid(self, form):
        self.object = register_advance(
            project_id=form.cleaned_data['project'].pk,
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            evidence=form.cleaned_data.get('evidence'),
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectUpdateCreateForProjectView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForProjectForm
    template_name = 'web/project_update_form.html'
    route_prefix = 'project_update'
    page_title = _('Registrar avance')

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        context['cancel_url'] = reverse('project_detail', args=[self.project.pk])
        return context

    def form_valid(self, form):
        self.object = register_advance(
            project_id=self.project.pk,
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            evidence=form.cleaned_data.get('evidence'),
            created_by=self.request.user if self.request.user.is_authenticated else None,
        )
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('project_detail', args=[self.project.pk])


class ProjectUpdateUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Editar avance de proyecto')


class ProjectUpdateDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_projectupdate'
    model = ProjectUpdate
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Eliminar avance de proyecto')
    audit_summary = _('Avance de proyecto eliminado.')


class ProjectUpdateReviewView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_projectupdate'
    form_class = ProjectUpdateReviewForm
    template_name = 'web/project_update_review.html'

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(ProjectUpdate.objects.select_related('project', 'created_by'), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['object'] = self.object
        return context

    def form_valid(self, form):
        review_project_update(
            update_id=self.object.pk,
            reviewer=self.request.user if self.request.user.is_authenticated else None,
            status=form.cleaned_data['status'],
            notes=form.cleaned_data.get('review_notes', ''),
        )
        messages.success(self.request, _('Revisión de avance guardada.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('project_detail', args=[self.object.project.pk])


class DonationListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_list.html'
    context_object_name = 'objects'
    route_prefix = 'donation'
    page_title = _('Donaciones')

    def get_queryset(self):
        return Donation.objects.select_related('donor')


class DonationDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_donation'
    model = Donation
    template_name = 'web/donation_detail.html'
    route_prefix = 'donation'
    page_title = _('Donación')


class DonationCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Nueva donación')
    audit_action = AuditLog.Action.CREATED
    audit_summary = _('Donación creada.')


class DonationUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_donation'
    model = Donation
    form_class = DonationForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Editar donación')
    audit_summary = _('Donación actualizada.')


class DonationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_donation'
    model = Donation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('donation_list')
    route_prefix = 'donation'
    page_title = _('Eliminar donación')
    audit_summary = _('Donación eliminada.')


class FundAllocationListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_list.html'
    context_object_name = 'objects'
    route_prefix = 'allocation'
    page_title = _('Asignaciones de fondos')

    def get_queryset(self):
        return FundAllocation.objects.select_related('donation', 'project')


class FundAllocationDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailMetricsMixin, DetailView):
    permission_required = 'operations.view_fundallocation'
    model = FundAllocation
    template_name = 'web/allocation_detail.html'
    route_prefix = 'allocation'
    page_title = _('Asignación de fondos')


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


class FundAllocationDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_fundallocation'
    model = FundAllocation
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('allocation_list')
    route_prefix = 'allocation'
    page_title = _('Eliminar asignación de fondos')
    audit_summary = _('Asignación de fondos eliminada.')


class ExpenseListView(OperationsPermissionRequiredMixin, RouteContextMixin, ListView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_list.html'
    context_object_name = 'objects'
    route_prefix = 'expense'
    page_title = _('Gastos')

    def get_queryset(self):
        return Expense.objects.select_related('allocation', 'allocation__project')


class ExpenseDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_expense'
    model = Expense
    template_name = 'web/expense_detail.html'
    route_prefix = 'expense'
    page_title = _('Gasto')


class ExpenseCreateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, CreateView):
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
        if form.instance.status == Expense.Status.VALIDATED:
            form.instance.validated_by = self.request.user
            form.instance.validated_at = timezone.now()
        response = super().form_valid(form)
        if self.object.status == Expense.Status.VALIDATED:
            log_action(self.request.user, AuditLog.Action.VALIDATED, self.object, _('Gasto validado.'))
        return response


class ExpenseUpdateView(OperationsPermissionRequiredMixin, AuditMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_expense'
    model = Expense
    form_class = ExpenseForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Editar gasto')
    audit_action = AuditLog.Action.EXECUTED
    audit_summary = _('Gasto actualizado.')

    def form_valid(self, form):
        previous_status = None
        if self.object:
            previous_status = Expense.objects.filter(pk=self.object.pk).values_list('status', flat=True).first()
        if form.instance.status == Expense.Status.VALIDATED and not form.instance.validated_at:
            form.instance.validated_by = self.request.user
            form.instance.validated_at = timezone.now()
        response = super().form_valid(form)
        if previous_status != Expense.Status.VALIDATED and self.object.status == Expense.Status.VALIDATED:
            log_action(self.request.user, AuditLog.Action.VALIDATED, self.object, _('Gasto validado.'))
        return response


class ExpenseDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_expense'
    model = Expense
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('expense_list')
    route_prefix = 'expense'
    page_title = _('Eliminar gasto')
    audit_summary = _('Gasto eliminado.')


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
        form.instance.expense = self.expense
        response = super().form_valid(form)
        log_action(
            self.request.user,
            AuditLog.Action.CREATED,
            self.object,
            _('Documento soporte adjuntado.'),
        )
        messages.success(self.request, _('Documento soporte adjuntado.'))
        return response

    def get_success_url(self):
        return reverse('expense_detail', args=[self.expense.pk])


class SupportingDocumentDeleteView(OperationsPermissionRequiredMixin, DeleteView):
    permission_required = 'operations.delete_supportingdocument'
    model = SupportingDocument
    template_name = 'web/supporting_document_confirm_delete.html'

    def get_success_url(self):
        return reverse('expense_detail', args=[self.object.expense_id])

    def form_valid(self, form):
        expense = self.object.expense
        if expense.status == Expense.Status.VALIDATED and expense.supporting_documents.count() <= 1:
            messages.error(
                self.request,
                _('No se puede eliminar el último documento soporte de un gasto validado.'),
            )
            return HttpResponseRedirect(reverse('expense_detail', args=[expense.pk]))
        messages.success(self.request, _('Documento soporte eliminado.'))
        log_delete(self.request.user, self.object, _('Documento soporte eliminado.'))
        return super().form_valid(form)


class AuditLogListView(OperationsPermissionRequiredMixin, ListView):
    permission_required = 'operations.view_auditlog'
    model = AuditLog
    template_name = 'web/audit_log_list.html'
    context_object_name = 'logs'
