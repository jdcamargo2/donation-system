from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY, OPERATING_CURRENCY_CHOICES
from .models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument
from .services import (
    ExpenseFinalizedError,
    ProjectUpdateImmutableError,
    ensure_expense_is_deletable,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
)


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
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].choices = OPERATING_CURRENCY_CHOICES
        self.fields['allocation'].queryset = FundAllocation.objects.filter(
            donation__currency=OPERATING_CURRENCY
        )

    def clean(self):
        cleaned_data = super().clean()
        currency = cleaned_data.get('currency', self.instance.currency or OPERATING_CURRENCY)
        if currency != OPERATING_CURRENCY:
            raise ValidationError(_('SIGEDON solo permite operaciones financieras en USD.'))
        allocation = cleaned_data.get('allocation')
        if allocation and allocation.donation.currency != OPERATING_CURRENCY:
            raise ValidationError(_('La asignación seleccionada no corresponde a una donación en USD.'))
        has_existing_support = self.instance.pk and self.instance.supporting_documents.exists()
        if cleaned_data.get('status') == Expense.Status.VALIDATED and not has_existing_support:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        return cleaned_data


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'role', 'country', 'status')
    search_fields = ('name', 'responsible_person', 'contact_email')
    list_filter = ('role', 'status')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'status', 'estimated_budget')
    search_fields = ('code', 'name')
    list_filter = ('status',)


@admin.register(ProjectUpdate)
class ProjectUpdateAdmin(admin.ModelAdmin):
    list_display = ('project', 'title', 'status', 'created_at', 'created_by', 'reviewed_at')
    list_filter = ('status', 'created_at', 'reviewed_at')
    search_fields = ('title', 'description', 'project__name', 'project__code')
    readonly_fields = ('status', 'created_at', 'updated_at', 'reviewed_by', 'reviewed_at', 'review_notes')

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: obj is the optional advance displayed by Django admin.
        POST: review metadata/status are always readonly; pending/final material fields are readonly.
        """
        readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status != ProjectUpdate.Status.DRAFT:
            readonly.update(('project', 'title', 'description', 'evidence'))
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
            obj.status = persisted.status
        else:
            obj.status = ProjectUpdate.Status.DRAFT
            obj.reviewed_by = None
            obj.reviewed_at = None
            obj.review_notes = ''
        super().save_model(request, obj, form, change)


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ('code', 'donor', 'amount', 'currency', 'status', 'received_date')
    search_fields = ('code', 'donor__name')
    list_filter = ('status', 'currency')
    readonly_fields = ('currency',)


@admin.register(FundAllocation)
class FundAllocationAdmin(admin.ModelAdmin):
    form = FundAllocationAdminForm
    list_display = ('donation', 'project', 'budget_category', 'amount', 'status', 'allocation_date')
    search_fields = ('donation__code', 'project__code', 'project__name', 'budget_category')
    list_filter = ('status', 'allocation_date')


class SupportingDocumentInline(admin.TabularInline):
    model = SupportingDocument
    extra = 0

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in {
            Expense.Status.VALIDATED,
            Expense.Status.CANCELLED,
        }:
            return False
        return super().has_delete_permission(request, obj)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm
    list_display = ('reason', 'allocation', 'amount', 'currency', 'status', 'expense_date')
    search_fields = ('reason', 'provider_or_recipient', 'allocation__project__name')
    list_filter = ('status', 'currency', 'expense_date')
    readonly_fields = ('currency', 'status', 'validated_by', 'validated_at')
    inlines = [SupportingDocumentInline]

    def get_readonly_fields(self, request, obj=None):
        # PRE: obj is the optional expense displayed by Django admin.
        # POST: status/validation metadata are always readonly; finalized expenses
        # expose every persisted field as readonly.
        base_readonly = set(super().get_readonly_fields(request, obj))
        if obj and obj.status in {
            Expense.Status.VALIDATED,
            Expense.Status.CANCELLED,
        }:
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
            if persisted.status in {
                Expense.Status.VALIDATED,
                Expense.Status.CANCELLED,
            }:
                raise ExpenseFinalizedError(
                    _('Los gastos finalizados no admiten edición administrativa.')
                )
            obj.status = persisted.status
            obj.validated_by = None
            obj.validated_at = None
        else:
            obj.status = Expense.Status.REGISTERED
            obj.validated_by = None
            obj.validated_at = None
        super().save_model(request, obj, form, change)


@admin.register(SupportingDocument)
class SupportingDocumentAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('title', 'expense', 'uploaded_at')
    search_fields = ('title', 'expense__reason')

    def has_delete_permission(self, request, obj=None):
        if (
            obj
            and obj.expense.status in {
                Expense.Status.VALIDATED,
                Expense.Status.CANCELLED,
            }
            and obj.expense.supporting_documents.count() <= 1
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
