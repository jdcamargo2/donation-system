from django.contrib import admin

from .models import AuditLog, Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument


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


@admin.register(FundAllocation)
class FundAllocationAdmin(admin.ModelAdmin):
    list_display = ('donation', 'project', 'budget_category', 'amount', 'status', 'allocation_date')
    search_fields = ('donation__code', 'project__code', 'project__name', 'budget_category')
    list_filter = ('status', 'allocation_date')


class SupportingDocumentInline(admin.TabularInline):
    model = SupportingDocument
    extra = 0


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('reason', 'allocation', 'amount', 'currency', 'status', 'expense_date')
    search_fields = ('reason', 'provider_or_recipient', 'allocation__project__name')
    list_filter = ('status', 'currency', 'expense_date')
    inlines = [SupportingDocumentInline]


@admin.register(SupportingDocument)
class SupportingDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'expense', 'uploaded_at')
    search_fields = ('title', 'expense__reason')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'model_name', 'entity_label', 'user')
    search_fields = ('model_name', 'entity_label', 'summary')
    list_filter = ('action', 'model_name', 'created_at')
    readonly_fields = ('created_at',)
