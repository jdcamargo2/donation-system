from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .models import (
    AuditLog, Donation, Expense, FundAllocation, Institution, Project,
    ProjectDocument, ProjectUpdate, ProjectUpdateAttachment, ProjectUpdateReview, ProjectUpdateReviewDecision,
    ProjectUpdateRemediation, ProjectUpdateRemediationAttachment, SupportingDocument,
)
from .services import (
    ExpenseFinalizedError,
    ProjectUpdateImmutableError,
    ensure_expense_is_deletable,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    log_create,
    log_update,
    ensure_operational_entity_is_editable,
)
from .project_update_responsibles import eligible_project_update_reporters, validate_project_update_reporter


class FundAllocationAdminForm(forms.ModelForm):
    class Meta:
        model = FundAllocation
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['donation'].queryset = Donation.objects.filter(currency=OPERATING_CURRENCY)

    def clean_donation(self):
        donation = self.cleaned_data['donation']
        if donation.currency != OPERATING_CURRENCY:
            raise ValidationError(_('SIGEDON solo permite operaciones financieras en USD.'))
        return donation


class ExpenseAdminForm(forms.ModelForm):
    class Meta:
        model = Expense
        exclude = ('currency',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['allocation'].queryset = FundAllocation.objects.filter(
            donation__currency=OPERATING_CURRENCY
        )

    def clean(self):
        cleaned_data = super().clean()
        allocation = cleaned_data.get('allocation')
        if allocation and allocation.donation.currency != OPERATING_CURRENCY:
            raise ValidationError(_('La asignación seleccionada no corresponde a una donación en USD.'))
        return cleaned_data


class ProjectUpdateAdminForm(forms.ModelForm):
    class Meta:
        model = ProjectUpdate
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'reported_by' in self.fields:
            self.fields['reported_by'].label = _('Persona responsable del avance')
            self.fields['reported_by'].required = True
            self.fields['reported_by'].queryset = eligible_project_update_reporters()


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'role', 'country', 'status')
    search_fields = ('name', 'responsible_person', 'contact_email')
    list_filter = ('role', 'status')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'status', 'is_public', 'estimated_budget')
    search_fields = ('code', 'name')
    list_filter = ('status', 'is_public')
    readonly_fields = (
        'code',
        'status',
        'is_public',
        'terminal_reason',
        'terminal_at',
        'terminal_by',
    )

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is an optional project shown in admin.
        # POST: terminal projects expose every persisted field as readonly.
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status == Project.Status.CLOSED:
            readonly.update(field.name for field in self.model._meta.concrete_fields)
        return tuple(readonly)

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional Project admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def get_actions(self, request):
        """
        PRE: request targets the Project admin changelist.
        POST: removes the bulk delete action while leaving other actions unchanged.
        """
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def save_model(self, request, obj, form, change):
        """
        PRE: obj is new or an existing project submitted through admin.
        POST: creates ACTIVE via model default and preserves persisted status/is_public on edits.
        """
        if change:
            persisted = Project.objects.get(pk=obj.pk)
            ensure_operational_entity_is_editable(persisted)
            obj.status = persisted.status
            obj.is_public = persisted.is_public
        else:
            obj.status = Project.Status.ACTIVE
            obj.is_public = False
        super().save_model(request, obj, form, change)


@admin.register(ProjectUpdate)
class ProjectUpdateAdmin(admin.ModelAdmin):
    form = ProjectUpdateAdminForm
    list_display = (
        'project', 'title', 'reported_by', 'update_date',
        'status', 'created_at', 'created_by',
    )
    list_filter = ('status', 'update_date', 'created_at')
    search_fields = ('title', 'description', 'project__name', 'project__code')
    readonly_fields = ('status', 'created_at', 'updated_at', 'created_by')

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: obj is the optional advance displayed by Django admin.
        POST: status is always readonly; published material fields are readonly.
        """
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status != ProjectUpdate.Status.DRAFT:
            readonly.update(
                ('project', 'title', 'description', 'update_date', 'reported_by')
            )
        return tuple(readonly)

    def has_delete_permission(self, request, obj=None):
        """
        PRE: obj is an optional advance targeted by an admin delete operation.
        POST: final advances cannot be deleted through admin.
        """
        if obj is not None:
            try:
                ensure_project_update_is_deletable(obj)
            except ProjectUpdateImmutableError:
                return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        """
        PRE: obj is an advance selected for ordinary admin deletion.
        POST: deletes only non-final advances; final states fail safely.
        """
        ensure_project_update_is_deletable(obj)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        """
        PRE: queryset contains advances selected by the admin bulk delete action.
        POST: deletes the batch only when every advance is non-final.
        """
        for project_update in queryset:
            ensure_project_update_is_deletable(project_update)
        return super().delete_queryset(request, queryset)

    def save_model(self, request, obj, form, change):
        """
        PRE: admin form is valid and obj is new or targets an existing advance.
        POST: creates DRAFT only and saves material changes only on existing DRAFT advances.
        """
        if change:
            persisted = ProjectUpdate.objects.get(pk=obj.pk)
            ensure_project_update_is_editable(persisted)
            validate_project_update_reporter(obj.reported_by)
            reported_by_changed = persisted.reported_by_id != obj.reported_by_id
            obj.status = persisted.status
            obj.created_by = persisted.created_by
        else:
            validate_project_update_reporter(obj.reported_by)
            obj.status = ProjectUpdate.Status.DRAFT
            obj.created_by = request.user if request.user.is_authenticated else None
        super().save_model(request, obj, form, change)
        if change:
            summary = (
                _('Atribución de la persona responsable del avance actualizada desde administración.')
                if reported_by_changed else _('Borrador de avance actualizado desde administración.')
            )
            log_update(request.user, obj, summary)
        else:
            log_create(
                request.user,
                obj,
                _('Avance de proyecto creado como borrador con persona responsable asignada desde administración.'),
            )


@admin.register(ProjectUpdateReview)
class ProjectUpdateReviewAdmin(admin.ModelAdmin):
    list_display = ('project_update', 'reviewed_by', 'reviewed_at')
    search_fields = ('project_update__title', 'project_update__project__code')
    readonly_fields = ('project_update', 'observations', 'reviewed_by', 'reviewed_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProjectUpdateReviewDecision)
class ProjectUpdateReviewDecisionAdmin(admin.ModelAdmin):
    list_display = ('review', 'outcome', 'decided_by', 'decided_at')
    search_fields = ('review__project_update__title', 'review__project_update__project__code')
    readonly_fields = ('review', 'outcome', 'rationale', 'decided_by', 'decided_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProjectDocument)
class ProjectDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'document_type', 'created_at', 'uploaded_by')
    list_filter = ('document_type', 'created_at')
    search_fields = ('title', 'project__code', 'project__name')


@admin.register(ProjectUpdateAttachment)
class ProjectUpdateAttachmentAdmin(admin.ModelAdmin):
    list_display = ('project_update', 'title', 'created_at', 'uploaded_by')
    search_fields = ('title', 'project_update__title')

    def save_model(self, request, obj, form, change):
        # PRE: el formulario admin contiene un adjunto nuevo o modificado.
        # POST: guarda solo cuando el avance padre continúa DRAFT.
        project_update = ProjectUpdate.objects.get(pk=obj.project_update_id)
        ensure_project_update_is_editable(project_update)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'project_update':
            kwargs['queryset'] = ProjectUpdate.objects.filter(status=ProjectUpdate.Status.DRAFT)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        if obj and obj.project_update.status == ProjectUpdate.Status.PUBLISHED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.project_update.status == ProjectUpdate.Status.PUBLISHED:
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        # PRE: obj is an attachment selected for ordinary admin deletion.
        # POST: deletes only an attachment whose parent update remains DRAFT.
        ensure_project_update_is_editable(obj.project_update)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # PRE: queryset contains attachments selected by the admin bulk delete action.
        # POST: deletes the batch only when every parent update remains DRAFT.
        for attachment in queryset.select_related('project_update'):
            ensure_project_update_is_editable(attachment.project_update)
        return super().delete_queryset(request, queryset)


@admin.register(ProjectUpdateRemediation)
class ProjectUpdateRemediationAdmin(admin.ModelAdmin):
    list_display = ('decision', 'status', 'created_by', 'submitted_by', 'resolved_by')
    readonly_fields = ('decision', 'created_by', 'submitted_by', 'submitted_at', 'resolved_by', 'resolved_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status != ProjectUpdateRemediation.Status.DRAFT:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status != ProjectUpdateRemediation.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(ProjectUpdateRemediationAttachment)
class ProjectUpdateRemediationAttachmentAdmin(admin.ModelAdmin):
    list_display = ('remediation', 'title', 'created_at', 'uploaded_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.remediation.status != ProjectUpdateRemediation.Status.DRAFT:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.remediation.status != ProjectUpdateRemediation.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ('code', 'donor', 'amount', 'currency', 'status', 'allocation_progress_display', 'received_date')
    search_fields = ('code', 'donor__name')
    list_filter = ('status',)
    readonly_fields = ('code', 'currency', 'status', 'terminal_reason', 'terminal_at', 'terminal_by')

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is an optional donation shown in admin.
        # POST: terminal donations expose every persisted field as readonly.
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status == Donation.Status.ANNULLED:
            readonly.update(field.name for field in self.model._meta.concrete_fields)
        return tuple(readonly)

    def save_model(self, request, obj, form, change):
        """
        PRE: obj is new or an existing donation submitted through admin.
        POST: creates REGISTERED and preserves persisted status on ordinary edits.
        """
        if change:
            persisted = Donation.objects.get(pk=obj.pk)
            ensure_operational_entity_is_editable(persisted)
            obj.status = persisted.status
        else:
            obj.status = Donation.Status.REGISTERED
            obj.currency = OPERATING_CURRENCY
        super().save_model(request, obj, form, change)

    @admin.display(description=_('Asignación'))
    def allocation_progress_display(self, obj):
        return obj.allocation_progress_label


@admin.register(FundAllocation)
class FundAllocationAdmin(admin.ModelAdmin):
    form = FundAllocationAdminForm
    list_display = ('code', 'donation', 'project', 'budget_category', 'amount', 'status', 'execution_progress_display', 'allocation_date')
    search_fields = ('code', 'donation__code', 'project__code', 'project__name', 'budget_category')
    list_filter = ('status', 'allocation_date')
    readonly_fields = ('code', 'status', 'terminal_reason', 'terminal_at', 'terminal_by')

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is an optional allocation shown in admin.
        # POST: terminal allocations expose every persisted field as readonly.
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status in {FundAllocation.Status.FINISHED, FundAllocation.Status.ANNULLED}:
            readonly.update(field.name for field in self.model._meta.concrete_fields)
        return tuple(readonly)

    def save_model(self, request, obj, form, change):
        """
        PRE: obj is new or an existing allocation submitted through admin.
        POST: creates ACTIVE and preserves persisted status on ordinary edits.
        """
        if change:
            persisted = FundAllocation.objects.get(pk=obj.pk)
            ensure_operational_entity_is_editable(persisted)
            obj.status = persisted.status
        else:
            obj.status = FundAllocation.Status.ACTIVE
        super().save_model(request, obj, form, change)

    @admin.display(description=_('Ejecución'))
    def execution_progress_display(self, obj):
        return obj.execution_progress_label


class SupportingDocumentInline(admin.TabularInline):
    model = SupportingDocument
    extra = 0

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == Expense.Status.ANNULLED:
            return False
        return super().has_delete_permission(request, obj)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm
    list_display = ('code', 'reason', 'allocation', 'amount', 'currency', 'status', 'expense_date')
    search_fields = ('code', 'reason', 'provider_or_recipient', 'allocation__project__name')
    list_filter = ('status', 'expense_date')
    readonly_fields = ('code', 'currency', 'status', 'terminal_reason', 'terminal_at', 'terminal_by')
    inlines = [SupportingDocumentInline]

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is the optional expense displayed by Django admin.
        # POST: status/validation metadata are always readonly; finalized expenses
        # expose every persisted field as readonly.
        base_readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status == Expense.Status.ANNULLED:
            base_readonly.update(
                field.name for field in self.model._meta.concrete_fields
            )
        return tuple(base_readonly)

    def has_delete_permission(self, request, obj=None):
        # PRE: obj is an optional expense targeted by an admin delete operation.
        # POST: finalized expenses cannot be deleted through ordinary admin paths.
        if obj is not None:
            try:
                ensure_expense_is_deletable(obj)
            except ExpenseFinalizedError:
                return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        # PRE: obj is an expense selected for ordinary admin deletion.
        # POST: deletes only editable expenses; finalized expenses fail safely.
        ensure_expense_is_deletable(obj)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # PRE: queryset contains expenses selected by the admin bulk delete action.
        # POST: deletes the batch only when every expense is ordinarily deletable.
        for expense in queryset:
            ensure_expense_is_deletable(expense)
        return super().delete_queryset(request, queryset)

    # PRE: form has passed admin validation and obj contains an ordinary editable state.
    # POST: creates registered expenses or saves editable expenses without allowing
    # manual status/validation metadata transitions.
    def save_model(self, request, obj, form, change):
        if change:
            persisted = Expense.objects.get(pk=obj.pk)
            if persisted.status == Expense.Status.ANNULLED:
                raise ExpenseFinalizedError(
                    _('Los gastos finalizados no admiten edición administrativa.')
                )
            obj.status = persisted.status
        else:
            obj.status = Expense.Status.REGISTERED
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        # PRE: Django admin is evaluating direct Expense creation.
        # POST: returns False because mandatory support is created atomically through the operational form.
        return False


@admin.register(SupportingDocument)
class SupportingDocumentAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('title', 'expense', 'uploaded_at')
    search_fields = ('title', 'expense__reason')

    def has_delete_permission(self, request, obj=None):
        if (
            obj
            and (
                obj.expense.status == Expense.Status.ANNULLED
                or obj.expense.supporting_documents.count() <= 1
            )
        ):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('created_at', 'action', 'model_name', 'entity_label', 'user')
    search_fields = ('model_name', 'entity_label', 'summary')
    list_filter = ('action', 'model_name', 'created_at')
    ordering = ('-created_at',)

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: request targets the read-only audit admin and obj is optional.
        POST: returns every persisted audit field as readonly.
        """
        return tuple(field.name for field in self.model._meta.concrete_fields)

    def has_add_permission(self, request):
        """
        PRE: request targets the AuditLog admin.
        POST: always denies manual creation, including for superusers.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional AuditLog admin object.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional AuditLog admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def has_view_permission(self, request, obj=None):
        """
        PRE: request has an authenticated Django user.
        POST: permits read-only admin access exactly with view_auditlog permission.
        """
        return request.user.has_perm('operations.view_auditlog')
