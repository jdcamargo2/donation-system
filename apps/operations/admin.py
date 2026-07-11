from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY, OPERATING_CURRENCY_CHOICES
from .models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument
from .services import validate_expense


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
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')


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
        if obj and obj.status == Expense.Status.VALIDATED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm
    list_display = ('reason', 'allocation', 'amount', 'currency', 'status', 'expense_date')
    search_fields = ('reason', 'provider_or_recipient', 'allocation__project__name')
    list_filter = ('status', 'currency', 'expense_date')
    readonly_fields = ('currency',)
    inlines = [SupportingDocumentInline]

    # PRE: form has passed ExpenseAdminForm validation and obj contains the requested admin state.
    # POST: saves ordinary changes and routes every new validated transition through validate_expense().
    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = Expense.objects.filter(pk=obj.pk).values_list('status', flat=True).first()
        requested_validation = (
            obj.status == Expense.Status.VALIDATED
            and previous_status != Expense.Status.VALIDATED
        )
        if requested_validation:
            obj.status = previous_status or Expense.Status.REGISTERED
        super().save_model(request, obj, form, change)
        if requested_validation:
            validated_expense = validate_expense(obj.pk, request.user)
            obj.status = validated_expense.status
            obj.validated_by = validated_expense.validated_by
            obj.validated_at = validated_expense.validated_at


@admin.register(SupportingDocument)
class SupportingDocumentAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('title', 'expense', 'uploaded_at')
    search_fields = ('title', 'expense__reason')

    def has_delete_permission(self, request, obj=None):
        if (
            obj
            and obj.expense.status == Expense.Status.VALIDATED
            and obj.expense.supporting_documents.count() <= 1
        ):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'model_name', 'entity_label', 'user')
    search_fields = ('model_name', 'entity_label', 'summary')
    list_filter = ('action', 'model_name', 'created_at')
    readonly_fields = ('created_at',)
