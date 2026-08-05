from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .forms import SigedonAdminUserCreationForm, SigedonUserChangeForm
from .models import (
    AuditLog, Donation, Expense, FundAllocation, Institution, Project,
    ProjectDocument, ProjectUpdate, ProjectUpdateAttachment, ProjectUpdateReview, ProjectUpdateReviewDecision,
    ProjectUpdateRemediation, ProjectUpdateRemediationAttachment, SupportingDocument,
)
from .role_services import operation_role_names
from .services import (
    ProjectUpdateImmutableError,
    ensure_project_allows_operational_mutation,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    log_create,
    log_update,
    ensure_operational_entity_is_editable,
    project_allows_operational_mutation,
)
from .project_update_responsibles import eligible_project_update_reporters, validate_project_update_reporter
from .upload_limits import attach_private_upload_validator


def _configure_private_file_formfield(formfield):
    """
    PRE: formfield is a Django FileField form field for private operational media.
    POST: applies PrivateClearableFileInput and the shared upload size validator.
    """
    from .forms import PrivateClearableFileInput

    formfield.widget = PrivateClearableFileInput()
    attach_private_upload_validator(formfield)
    return formfield


def _is_canonical_sigedon_group(group):
    """
    PRE: group is a Group instance or None.
    POST: True iff group exists and its name is a canonical SIGEDON role.
    """
    return group is not None and group.name in operation_role_names()


class SigedonGroupAdminForm(forms.ModelForm):
    """Reject create/rename that would forge a canonical SIGEDON role name."""

    class Meta:
        model = Group
        fields = '__all__'

    def clean_name(self):
        name = self.cleaned_data['name']
        if name not in operation_role_names():
            return name
        if self.instance.pk is None:
            raise ValidationError(
                _('Los grupos funcionales SIGEDON no pueden crearse desde el admin.')
            )
        if _is_canonical_sigedon_group(self.instance):
            raise ValidationError(
                _('Los grupos funcionales SIGEDON son de solo lectura.')
            )
        raise ValidationError(
            _('No se puede renombrar un grupo al nombre de un rol funcional SIGEDON.')
        )


class SigedonUserAdmin(DjangoUserAdmin):
    """
    User admin with a single optional SIGEDON functional role, separate from
    technical groups. Functional roles are excluded from the groups widget so
    stock groups.set() cannot leave a user with multiple canonical roles.
    """

    form = SigedonUserChangeForm
    add_form = SigedonAdminUserCreationForm
    filter_horizontal = ('groups', 'user_permissions')

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Información personal'), {'fields': ('first_name', 'last_name', 'email')}),
        (
            _('Permisos'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'functional_role',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
        (_('Fechas importantes'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('username', 'usable_password', 'password1', 'password2'),
            },
        ),
        (
            _('Permisos'),
            {
                'fields': (
                    'functional_role',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
    )


admin.site.unregister(User)
admin.site.register(User, SigedonUserAdmin)


class SigedonGroupAdmin(GroupAdmin):
    """
    Protects canonical SIGEDON functional role groups as read-only in admin,
    including for superusers. Technical groups remain fully editable.
    """

    form = SigedonGroupAdminForm
    list_display = ('name', 'sigedon_group_type', 'sigedon_management')
    search_fields = ('name',)
    ordering = ('name',)
    filter_horizontal = ('permissions',)

    @admin.display(description=_('Tipo'))
    def sigedon_group_type(self, obj):
        if _is_canonical_sigedon_group(obj):
            return _('Grupo funcional SIGEDON')
        return _('Grupo técnico')

    @admin.display(description=_('Gestión'))
    def sigedon_management(self, obj):
        if _is_canonical_sigedon_group(obj):
            return _('Sincronizado — solo lectura')
        return _('Editable')

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: obj is an optional Group shown in admin.
        POST: canonical groups expose name and permissions as readonly.
        """
        if _is_canonical_sigedon_group(obj):
            return ('name', 'permissions')
        return super().get_readonly_fields(request, obj)

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional Group admin object.
        POST: canonical groups are never changeable, including for superusers.
        """
        if _is_canonical_sigedon_group(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional Group admin object.
        POST: canonical groups are never deletable, including for superusers.
        """
        if _is_canonical_sigedon_group(obj):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        """
        PRE: admin form targets a new or existing Group.
        POST: rejects saves that mutate or forge canonical role groups.
        """
        if change:
            persisted = Group.objects.get(pk=obj.pk)
            if _is_canonical_sigedon_group(persisted):
                raise PermissionDenied(
                    _('Los grupos funcionales SIGEDON no pueden modificarse desde el admin.')
                )
        if obj.name in operation_role_names():
            raise PermissionDenied(
                _('No se pueden crear ni renombrar grupos con nombres de roles funcionales SIGEDON.')
            )
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        """
        PRE: obj is a Group selected for ordinary admin deletion.
        POST: rejects deletion of canonical groups; deletes technical groups.
        """
        if _is_canonical_sigedon_group(obj):
            raise PermissionDenied(
                _('Los grupos funcionales SIGEDON no pueden eliminarse desde el admin.')
            )
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        """
        PRE: queryset contains groups selected by the admin bulk delete action.
        POST: all-or-nothing — any canonical group denies the entire batch.
        """
        if any(_is_canonical_sigedon_group(group) for group in queryset):
            raise PermissionDenied(
                _('La selección incluye grupos funcionales SIGEDON y no puede eliminarse.')
            )
        return super().delete_queryset(request, queryset)


admin.site.unregister(Group)
admin.site.register(Group, SigedonGroupAdmin)


class FundAllocationAdminForm(forms.ModelForm):
    class Meta:
        model = FundAllocation
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Read-only Admin inspection excludes mutable fields from the form.
        if 'donation' in self.fields:
            self.fields['donation'].queryset = Donation.objects.filter(
                currency=OPERATING_CURRENCY
            )

    def clean_donation(self):
        donation = self.cleaned_data['donation']
        if donation.currency != OPERATING_CURRENCY:
            raise ValidationError(_('SIGEDON solo permite operaciones financieras en USD.'))
        return donation


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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'legal_document' and formfield is not None:
            return _configure_private_file_formfield(formfield)
        return formfield


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
        if obj and obj.status != ProjectUpdate.Status.UNPUBLISHED:
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
        POST: creates UNPUBLISHED only and saves material changes only on existing UNPUBLISHED advances.
        """
        ensure_project_allows_operational_mutation(obj.project)
        if change:
            persisted = ProjectUpdate.objects.get(pk=obj.pk)
            ensure_project_update_is_editable(persisted)
            validate_project_update_reporter(obj.reported_by)
            reported_by_changed = persisted.reported_by_id != obj.reported_by_id
            obj.status = persisted.status
            obj.created_by = persisted.created_by
        else:
            validate_project_update_reporter(obj.reported_by)
            obj.status = ProjectUpdate.Status.UNPUBLISHED
            obj.created_by = request.user if request.user.is_authenticated else None
        super().save_model(request, obj, form, change)
        if change:
            summary = (
                _('Atribución de la persona responsable del avance actualizada desde administración.')
                if reported_by_changed else _('Avance no publicado actualizado desde administración.')
            )
            log_update(request.user, obj, summary)
        else:
            log_create(
                request.user,
                obj,
                _('Avance de proyecto registrado como no publicado con persona responsable asignada desde administración.'),
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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'file' and formfield is not None:
            return _configure_private_file_formfield(formfield)
        return formfield

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'project':
            kwargs['queryset'] = Project.objects.filter(status=Project.Status.ACTIVE)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        if obj is not None and not project_allows_operational_mutation(obj.project):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not project_allows_operational_mutation(obj.project):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        # PRE: admin form targets a new or existing project document.
        # POST: persists only when the parent project still accepts operational mutations.
        ensure_project_allows_operational_mutation(obj.project)
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        ensure_project_allows_operational_mutation(obj.project)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for document in queryset.select_related('project'):
            ensure_project_allows_operational_mutation(document.project)
        return super().delete_queryset(request, queryset)


@admin.register(ProjectUpdateAttachment)
class ProjectUpdateAttachmentAdmin(admin.ModelAdmin):
    list_display = ('project_update', 'title', 'created_at', 'uploaded_by')
    search_fields = ('title', 'project_update__title')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'file' and formfield is not None:
            return _configure_private_file_formfield(formfield)
        return formfield

    def save_model(self, request, obj, form, change):
        # PRE: el formulario admin contiene un adjunto nuevo o modificado.
        # POST: guarda solo cuando el avance padre continúa UNPUBLISHED.
        project_update = ProjectUpdate.objects.get(pk=obj.project_update_id)
        ensure_project_update_is_editable(project_update)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'project_update':
            kwargs['queryset'] = ProjectUpdate.objects.filter(status=ProjectUpdate.Status.UNPUBLISHED)
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
        # POST: deletes only an attachment whose parent update remains UNPUBLISHED.
        ensure_project_update_is_editable(obj.project_update)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # PRE: queryset contains attachments selected by the admin bulk delete action.
        # POST: deletes the batch only when every parent update remains UNPUBLISHED.
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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'file' and formfield is not None:
            return _configure_private_file_formfield(formfield)
        return formfield

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

    def has_add_permission(self, request):
        """
        PRE: request targets the Donation admin.
        POST: always denies creation, including for superusers; use the application UI.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional Donation admin object.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional Donation admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def get_actions(self, request):
        """
        PRE: request targets the Donation admin changelist.
        POST: removes the bulk delete action while leaving other actions unchanged.
        """
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

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
        POST: unreachable via HTTP (mutations denied); preserves status if invoked in tests.
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

    def has_add_permission(self, request):
        """
        PRE: request targets the FundAllocation admin.
        POST: always denies creation, including for superusers.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional FundAllocation admin object.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional FundAllocation admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def get_actions(self, request):
        """
        PRE: request targets the FundAllocation admin changelist.
        POST: removes the bulk delete action while leaving other actions unchanged.
        """
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

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


def _supporting_document_filename(document):
    """
    PRE: document is a SupportingDocument instance or None.
    POST: returns the stored basename for inspection, never a media URL.
    """
    if document is None or not document.document:
        return '—'
    name = document.document.name
    return name.rsplit('/', 1)[-1] if name else '—'


class SupportingDocumentInline(admin.TabularInline):
    """Read-only inspection of support files; no add/change/delete via admin."""

    model = SupportingDocument
    extra = 0
    can_delete = False
    show_change_link = False
    fields = ('title', 'document_filename_display', 'uploaded_at', 'notes')
    readonly_fields = ('title', 'document_filename_display', 'uploaded_at', 'notes')

    def has_add_permission(self, request, obj=None):
        """
        PRE: request targets the SupportingDocument inline on ExpenseAdmin.
        POST: always denies creation, including for superusers.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional SupportingDocument inline row.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional SupportingDocument inline row.
        POST: always denies deletion, including for superusers.
        """
        return False

    @admin.display(description=_('Archivo'))
    def document_filename_display(self, obj):
        """
        PRE: obj is an optional SupportingDocument inline row.
        POST: returns filename metadata without exposing document.file.url / MEDIA_URL.
        """
        return _supporting_document_filename(obj)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('code', 'reason', 'allocation', 'amount', 'currency', 'status', 'expense_date')
    search_fields = ('code', 'reason', 'provider_or_recipient', 'allocation__project__name')
    list_filter = ('status', 'expense_date')
    inlines = [SupportingDocumentInline]
    fieldsets = (
        (
            _('Identificación'),
            {
                'fields': (
                    'code',
                    'allocation',
                    'source_expense_request_display',
                    'application_detail_link',
                ),
            },
        ),
        (
            _('Información financiera'),
            {
                'fields': (
                    'amount',
                    'currency',
                    'expense_date',
                    'status',
                    'category',
                    'payment_method',
                ),
            },
        ),
        (
            _('Registro y soporte'),
            {
                'fields': (
                    'reason',
                    'provider_or_recipient',
                    'description',
                    'observations',
                    'supporting_documents_count',
                ),
            },
        ),
        (
            _('Ciclo de vida y auditoría'),
            {
                'fields': (
                    'terminal_reason',
                    'terminal_at',
                    'terminal_by',
                    'created_at',
                    'updated_at',
                ),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: obj is an optional Expense shown in admin.
        POST: every persisted field plus inspection helpers are readonly.
        """
        concrete = tuple(field.name for field in self.model._meta.concrete_fields)
        return concrete + (
            'source_expense_request_display',
            'application_detail_link',
            'supporting_documents_count',
        )

    def has_add_permission(self, request):
        """
        PRE: request targets the Expense admin.
        POST: always denies creation, including for superusers.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional Expense admin object.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional Expense admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def get_actions(self, request):
        """
        PRE: request targets the Expense admin changelist.
        POST: removes the bulk delete action while leaving other actions unchanged.
        """
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    @admin.display(description=_('Solicitud de origen'))
    def source_expense_request_display(self, obj):
        """
        PRE: obj is a persisted Expense (or unsaved with no reverse link).
        POST: returns the linked ExpenseRequest code or an empty marker.
        """
        if obj is None or obj.pk is None:
            return '—'
        try:
            return obj.source_expense_request.code
        except ObjectDoesNotExist:
            return '—'

    @admin.display(description=_('Documentos soporte'))
    def supporting_documents_count(self, obj):
        """
        PRE: obj is a persisted Expense.
        POST: returns the related SupportingDocument count for inspection.
        """
        if obj is None or obj.pk is None:
            return 0
        return obj.supporting_documents.count()

    @admin.display(description=_('Aplicación'))
    def application_detail_link(self, obj):
        """
        PRE: obj is a persisted Expense shown in admin inspection.
        POST: returns a safe SIGEDON detail link when the expense exists.
        """
        if obj is None or obj.pk is None:
            return '—'
        url = reverse('expense_detail', args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, _('Ver en SIGEDON'))


@admin.register(SupportingDocument)
class SupportingDocumentAdmin(admin.ModelAdmin):
    """Inspection-only admin for support files; mutations stay in SIGEDON services."""

    list_display = ('title', 'expense', 'uploaded_at')
    search_fields = ('title', 'expense__reason', 'expense__code')
    list_filter = ('uploaded_at',)
    fieldsets = (
        (
            _('Identificación del documento'),
            {
                'fields': ('title',),
            },
        ),
        (
            _('Gasto relacionado'),
            {
                'fields': (
                    'expense',
                    'related_expense_link',
                ),
            },
        ),
        (
            _('Metadatos del archivo'),
            {
                'fields': (
                    'document_filename_display',
                    'protected_preview_link',
                    'protected_download_link',
                ),
            },
        ),
        (
            _('Registro y auditoría'),
            {
                'fields': (
                    'notes',
                    'uploaded_at',
                ),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        """
        PRE: obj is an optional SupportingDocument shown in admin.
        POST: every non-file persisted field plus inspection helpers are readonly.
             The FileField itself is omitted so admin never renders document.url.
        """
        concrete = tuple(
            field.name
            for field in self.model._meta.concrete_fields
            if field.name != 'document'
        )
        return concrete + (
            'document_filename_display',
            'protected_preview_link',
            'protected_download_link',
            'related_expense_link',
        )

    def has_add_permission(self, request):
        """
        PRE: request targets the SupportingDocument admin.
        POST: always denies creation, including for superusers.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        PRE: request targets an optional SupportingDocument admin object.
        POST: always denies modification, including for superusers.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        PRE: request targets an optional SupportingDocument admin object.
        POST: always denies deletion, including for superusers.
        """
        return False

    def get_actions(self, request):
        """
        PRE: request targets the SupportingDocument admin changelist.
        POST: removes the bulk delete action while leaving other actions unchanged.
        """
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    @admin.display(description=_('Archivo'))
    def document_filename_display(self, obj):
        """
        PRE: obj is an optional SupportingDocument shown in admin.
        POST: returns filename metadata without exposing document.url / MEDIA_URL.
        """
        return _supporting_document_filename(obj)

    @admin.display(description=_('Vista previa'))
    def protected_preview_link(self, obj):
        """
        PRE: obj is a persisted SupportingDocument with a related expense.
        POST: returns a SIGEDON protected preview route, never a direct storage URL.
        """
        if obj is None or obj.pk is None or obj.expense_id is None:
            return '—'
        url = reverse(
            'supporting_document_preview',
            args=[obj.expense_id, obj.pk],
        )
        return format_html('<a href="{}">{}</a>', url, _('Ver vista previa protegida'))

    @admin.display(description=_('Descarga'))
    def protected_download_link(self, obj):
        """
        PRE: obj is a persisted SupportingDocument with a related expense.
        POST: returns a SIGEDON protected download route, never a direct storage URL.
        """
        if obj is None or obj.pk is None or obj.expense_id is None:
            return '—'
        url = reverse(
            'supporting_document_download',
            args=[obj.expense_id, obj.pk],
        )
        return format_html('<a href="{}">{}</a>', url, _('Descargar mediante SIGEDON'))

    @admin.display(description=_('Gasto'))
    def related_expense_link(self, obj):
        """
        PRE: obj is a persisted SupportingDocument with a related expense.
        POST: returns a SIGEDON expense detail link when the parent expense exists.
        """
        if obj is None or obj.expense_id is None:
            return '—'
        url = reverse('expense_detail', args=[obj.expense_id])
        return format_html('<a href="{}">{}</a>', url, _('Ver gasto relacionado'))


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
