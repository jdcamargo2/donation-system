import re
from decimal import Decimal

from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .choices import (
    EXPENSE_CATEGORY_CHOICES,
    OPERATING_CURRENCY,
    PAYMENT_METHOD_CHOICES,
)
from .models import (
    Donation, Expense, FundAllocation, Institution, Project, ProjectDocument,
    ProjectMilestone,
    ProjectUpdate, ProjectUpdateReview, ProjectUpdateReviewDecision,
    ProjectUpdateRemediation, SupportingDocument,
)
from .project_update_responsibles import (
    actor_must_self_report_project_update,
    eligible_project_update_reporters,
)
from .role_services import (
    functional_role_groups,
    get_user_functional_roles,
    operation_role_names,
    set_user_functional_role,
)


SELECT_PLACEHOLDER = _('Seleccione una opción')
MONEY_PLACEHOLDER = _('Ej. 1.500,00')
DATE_PLACEHOLDER = _('dd/mm/aaaa')
CANONICAL_DATE_FORMAT = '%Y-%m-%d'
DATE_INPUT_FORMATS = ['%Y-%m-%d', '%d/%m/%Y']
TERMINAL_REASON_MIN_LENGTH = 10
TERMINAL_REASON_MAX_LENGTH = 500


CANONICAL_MONEY_RE = re.compile(r'^\d+(?:\.\d{1,2})?$')
LOCALIZED_MONEY_RE = re.compile(r'^\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?$')
LOCALIZED_DECIMAL_RE = re.compile(r'^\d+(?:,\d{1,2})?$')


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        # PRE: data contiene cero o más archivos enviados por el navegador.
        # POST: devuelve una lista cuyos archivos fueron validados individualmente;
        #       si required=True y no hay archivos, propaga el ValidationError de FileField.
        files = data if isinstance(data, (list, tuple)) else [data]
        cleaned = [super(MultipleFileField, self).clean(item, initial) for item in files if item]
        if cleaned:
            return cleaned
        if self.required:
            super(MultipleFileField, self).clean(None, initial)
        return []


# PRE: value is a submitted monetary value or an empty value accepted by the field.
# POST: returns canonical decimal text, preserves non-string/empty values, or raises ValidationError for invalid syntax.
def normalize_localized_money(value):
    if not isinstance(value, str) or value == '':
        return value
    normalized = value.strip()
    if normalized == '':
        return normalized
    if CANONICAL_MONEY_RE.fullmatch(normalized):
        return normalized
    if LOCALIZED_MONEY_RE.fullmatch(normalized) or LOCALIZED_DECIMAL_RE.fullmatch(normalized):
        return normalized.replace('.', '').replace(',', '.')
    raise ValidationError(_('Ingrese un monto válido con máximo dos decimales.'))


# PRE: attrs contains only HTML attributes required by a known operations date field.
# POST: returns a text date widget that renders its original named input in canonical ISO format.
def build_date_widget(attrs=None):
    default_attrs = {
        'autocomplete': 'off',
        'placeholder': DATE_PLACEHOLDER,
        'class': 'ops-input datepicker',
        'data-date-picker': 'operations',
    }
    if attrs:
        default_attrs.update(attrs)
    return forms.DateInput(format=CANONICAL_DATE_FORMAT, attrs=default_attrs)


class MoneyInput(forms.TextInput):
    input_type = 'text'

    def __init__(self, attrs=None):
        default_attrs = {
            'inputmode': 'decimal',
            'autocomplete': 'off',
            'placeholder': MONEY_PLACEHOLDER,
            'class': 'ops-input money-input js-money-input',
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class MoneyDecimalField(forms.DecimalField):
    widget = MoneyInput

    # PRE: value may use either 1500.00 or Spanish-style 1.500,00 formatting.
    # POST: returns a Decimal-compatible value normalized for Django DecimalField validation.
    def to_python(self, value):
        return super().to_python(normalize_localized_money(value))

    # PRE: value is an initial monetary value or submitted text being re-rendered.
    # POST: formats numeric initial values for Spanish display and preserves submitted text exactly.
    def prepare_value(self, value):
        if isinstance(value, (Decimal, int)):
            canonical = format(Decimal(value), '.2f')
            integer, decimals = canonical.split('.')
            return f'{int(integer):,}'.replace(',', '.') + f',{decimals}'
        return value


class TerminalActionReasonForm(forms.Form):
    reason = forms.CharField(
        label=_('Motivo'),
        min_length=TERMINAL_REASON_MIN_LENGTH,
        max_length=TERMINAL_REASON_MAX_LENGTH,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
    )

    def clean_reason(self):
        """
        PRE: reason contains submitted terminal-action justification text.
        POST: returns a trimmed non-blank reason within the explicit length bounds.
        """
        reason = self.cleaned_data['reason'].strip()
        if not reason:
            raise ValidationError(_('El motivo de anulación es obligatorio.'))
        return reason


class TerminalActionConfirmationForm(forms.Form):
    pass


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for _field_name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = SELECT_PLACEHOLDER
            elif isinstance(field, forms.ChoiceField) and field.required:
                choices = list(field.choices)
                if choices and choices[0][0] != '':
                    field.choices = [('', SELECT_PLACEHOLDER)] + choices
            if isinstance(field, forms.DateField):
                field.input_formats = DATE_INPUT_FORMATS
            css_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            current_classes = field.widget.attrs.get('class', '').split()
            if css_class not in current_classes:
                current_classes.append(css_class)
            if isinstance(field, MoneyDecimalField):
                for money_class in ('money-input', 'js-money-input'):
                    if money_class not in current_classes:
                        current_classes.append(money_class)
            if isinstance(field.widget, forms.Textarea) and 'ops-textarea' not in current_classes:
                current_classes.append('ops-textarea')
            field.widget.attrs['class'] = ' '.join(current_classes)
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3


class ProjectUpdateRemediationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectUpdateRemediation
        fields = ['response']
        widgets = {'response': forms.Textarea(attrs={'rows': 5})}


class ProjectUpdateRemediationResolveForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=[
        (ProjectUpdateRemediation.Status.ACCEPTED, _('Aceptar')),
        (ProjectUpdateRemediation.Status.REJECTED, _('Rechazar')),
    ])
    resolution_notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}))


class ProjectUpdateRemediationAttachmentForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(required=False, max_length=200)
    file = forms.FileField(
        widget=forms.FileInput(
            attrs={
                'data-file-upload-preview': 'true',
            }
        ),
    )


class InstitutionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Institution
        fields = [
            'name',
            'institution_type',
            'role',
            'country',
            'contact_email',
            'contact_phone',
            'responsible_person',
            'legal_document',
            'status',
        ]
        labels = {
            'name': _('Nombre'),
            'institution_type': _('Tipo de institución'),
            'role': _('Rol'),
            'country': _('País'),
            'contact_email': _('Correo de contacto'),
            'contact_phone': _('Teléfono de contacto'),
            'responsible_person': _('Responsable institucional'),
            'legal_document': _('Documento legal'),
            'status': _('Estado'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['legal_document'].widget.attrs[
            'data-file-upload-preview'
        ] = 'true'


class ProjectForm(BootstrapFormMixin, forms.ModelForm):
    estimated_budget = MoneyDecimalField(
        required=False,
        label=_('Presupuesto estimado'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00'),
    )

    class Meta:
        model = Project
        fields = [
            'name',
            'description',
            'objective',
            'location',
            'estimated_budget',
            'start_date',
            'end_date',
        ]
        labels = {
            'name': _('Nombre'),
            'description': _('Descripción'),
            'objective': _('Objetivo'),
            'location': _('Ubicación'),
            'estimated_budget': _('Presupuesto estimado'),
            'start_date': _('Fecha de inicio'),
            'end_date': _('Fecha de cierre'),
        }
        help_texts = {
            'start_date': _('Formato: dd/mm/aaaa.'),
            'end_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'start_date': build_date_widget(),
            'end_date': build_date_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['estimated_budget'].initial = None

    def clean_estimated_budget(self):
        return self.cleaned_data.get('estimated_budget') or Project._meta.get_field('estimated_budget').default


class ProjectMilestoneForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectMilestone
        fields = ['title', 'description']
        labels = {
            'title': _('Título'),
            'description': _('Descripción'),
        }
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class ProjectUpdateResponsibleFormMixin:
    def __init__(self, *args, user=None, **kwargs):
        # PRE: optional authenticated actor for role-aware reported_by presentation.
        # POST: Operator sees a disabled self-reporter field; Admin/superuser keep the selector.
        super().__init__(*args, **kwargs)
        field = self.fields['reported_by']
        field.label = _('Persona responsable del avance')
        if actor_must_self_report_project_update(user):
            field.queryset = get_user_model()._default_manager.filter(pk=user.pk)
            field.initial = user
            field.disabled = True
            field.required = False
            field.empty_label = None
            field.help_text = _(
                'El responsable se asigna automáticamente al usuario que registra el avance.'
            )
        else:
            field.required = True
            field.queryset = eligible_project_update_reporters()


class ProjectUpdateForm(ProjectUpdateResponsibleFormMixin, BootstrapFormMixin, forms.ModelForm):
    attachments = MultipleFileField(
        label=_('Adjuntos'),
        required=False,
        help_text=_('Puede seleccionar varios archivos a la vez.'),
        widget=MultipleFileInput(attrs={
            'data-file-upload': 'multiple',
            'data-file-upload-preview': 'true',
        }),
    )

    class Meta:
        model = ProjectUpdate
        fields = [
            'project',
            'title',
            'description',
            'update_date',
            'reported_by',
        ]
        labels = {
            'project': _('Proyecto'),
            'title': _('Título'),
            'description': _('Descripción'),
            'update_date': _('Fecha del avance'),
            'reported_by': _('Persona responsable del avance'),
        }
        widgets = {'update_date': build_date_widget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_qs = Project.objects.filter(status=Project.Status.ACTIVE)
        if self.instance.pk and self.instance.project_id:
            project_qs = Project.objects.filter(
                Q(status=Project.Status.ACTIVE) | Q(pk=self.instance.project_id)
            )
        self.fields['project'].queryset = project_qs


class ProjectUpdateForProjectForm(ProjectUpdateResponsibleFormMixin, BootstrapFormMixin, forms.ModelForm):
    attachments = MultipleFileField(
        label=_('Adjuntos'),
        required=False,
        help_text=_('Puede seleccionar varios archivos a la vez.'),
        widget=MultipleFileInput(attrs={
            'data-file-upload': 'multiple',
            'data-file-upload-preview': 'true',
        }),
    )

    class Meta:
        model = ProjectUpdate
        fields = [
            'title',
            'description',
            'update_date',
            'reported_by',
        ]
        labels = {
            'title': _('Título'),
            'description': _('Descripción'),
            'update_date': _('Fecha del avance'),
            'reported_by': _('Persona responsable del avance'),
        }
        widgets = {'update_date': build_date_widget()}


class ProjectUpdateReviewForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectUpdateReview
        fields = ['observations']
        labels = {'observations': _('Observaciones del Comité')}


class ProjectUpdateReviewDecisionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectUpdateReviewDecision
        fields = ['outcome', 'rationale']
        labels = {
            'outcome': _('Resultado'),
            'rationale': _('Fundamento de la decisión'),
        }


class ProjectDocumentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectDocument
        fields = ('document_type', 'title', 'file', 'description')
        labels = {
            'document_type': _('Tipo de documento'),
            'title': _('Título'),
            'file': _('Archivo'),
            'description': _('Descripción'),
        }
        widgets = {
            'file': forms.FileInput(
                attrs={
                    'data-file-upload-preview': 'true',
                }
            ),
        }


class ProjectUpdateAttachmentForm(BootstrapFormMixin, forms.Form):
    files = MultipleFileField(
        label=_('Archivos'),
        help_text=_('Puede seleccionar varios archivos a la vez.'),
        widget=MultipleFileInput(attrs={
            'data-file-upload': 'multiple',
            'data-file-upload-preview': 'true',
        }),
    )


def format_usd_amount(value: Decimal) -> str:
    # PRE: value es un monto Decimal calculado por el dominio.
    # POST: retorna una etiqueta USD estable para ayudas y opciones del formulario.
    return f'USD {value:,.2f}'


class DonationWithBalanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, donation):
        # PRE: donation pertenece al queryset operable del formulario.
        # POST: muestra identidad, saldo y restricciones sin alterar la donación.
        restrictions = (donation.restrictions or '').strip() or _('Sin restricciones registradas')
        return _('%(donation)s · Disponible: %(balance)s · Restricciones: %(restrictions)s') % {
            'donation': donation,
            'balance': format_usd_amount(donation.available_balance),
            'restrictions': restrictions,
        }


class AllocationWithBalanceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, allocation):
        # PRE: allocation pertenece al queryset operativo del formulario.
        # POST: muestra asignación, ejecutado y saldo disponible sin mutación.
        return _('%(allocation)s · Ejecutado: %(executed)s · Disponible: %(available)s') % {
            'allocation': allocation,
            'executed': format_usd_amount(allocation.executed_amount),
            'available': format_usd_amount(allocation.available_balance),
        }


class DonationForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00. El nivel de asignación se calcula automáticamente según los fondos distribuidos.'),
    )

    class Meta:
        model = Donation
        fields = [
            'donor',
            'donation_type',
            'amount',
            'objective',
            'restrictions',
            'commitment_date',
            'received_date',
            'support_reference',
        ]
        labels = {
            'donor': _('Donante'),
            'donation_type': _('Tipo de donación'),
            'amount': _('Monto'),
            'objective': _('Objetivo'),
            'restrictions': _('Restricciones o condiciones'),
            'commitment_date': _('Fecha de compromiso'),
            'received_date': _('Fecha de recepción'),
            'support_reference': _('Referencia de soporte'),
        }
        help_texts = {
            'commitment_date': _('Formato: dd/mm/aaaa.'),
            'received_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'commitment_date': build_date_widget(),
            'received_date': build_date_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['donation_type'].initial = None


class FundAllocationForm(BootstrapFormMixin, forms.ModelForm):
    donation = DonationWithBalanceChoiceField(
        queryset=Donation.objects.none(),
        label=_('Donación'),
        error_messages={'invalid_choice': _('La donación no está operativa o no tiene saldo disponible.')},
    )
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00. El nivel de ejecución se calcula automáticamente según los gastos registrados.'),
    )

    class Meta:
        model = FundAllocation
        fields = [
            'donation',
            'project',
            'budget_category',
            'amount',
            'responsible_person',
            'allocation_date',
            'notes',
        ]
        labels = {
            'donation': _('Donación'),
            'project': _('Proyecto'),
            'budget_category': _('Categoría'),
            'amount': _('Monto'),
            'responsible_person': _('Responsable'),
            'allocation_date': _('Fecha de asignación'),
            'notes': _('Notas'),
        }
        help_texts = {
            'notes': _('Opcional. Use este campo para aclaraciones internas.'),
            'allocation_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'allocation_date': build_date_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_donation_id = self.instance.donation_id if self.instance.pk else None
        donations = Donation.objects.filter(
            currency=OPERATING_CURRENCY,
            status=Donation.Status.RECEIVED,
        )
        eligible_donation_ids = [
            donation.pk for donation in donations
            if donation.available_balance > 0 or donation.pk == current_donation_id
        ]
        self.fields['donation'].queryset = donations.filter(pk__in=eligible_donation_ids)
        project_qs = Project.objects.filter(status=Project.Status.ACTIVE)
        if self.instance.pk and self.instance.project_id:
            project_qs = Project.objects.filter(
                Q(status=Project.Status.ACTIVE) | Q(pk=self.instance.project_id)
            )
        self.fields['project'].queryset = project_qs
        selected_donation_id = self.data.get(self.add_prefix('donation')) or current_donation_id
        selected_donation = donations.filter(pk=selected_donation_id).first() if selected_donation_id else None
        if selected_donation:
            restrictions = (selected_donation.restrictions or '').strip() or _('Sin restricciones registradas.')
            maximum = selected_donation.available_balance
            if self.instance.pk and self.instance.donation_id == selected_donation.pk:
                maximum += self.instance.amount
            self.fields['donation'].help_text = _('Restricciones: %(restrictions)s') % {'restrictions': restrictions}
            self.fields['amount'].help_text = _('Monto máximo disponible: %(maximum)s.') % {
                'maximum': format_usd_amount(maximum)
            } + ' ' + _('Ejemplo: 1.500,00.')
        else:
            self.fields['amount'].help_text = _(
                'Seleccione una donación para consultar el monto máximo disponible. Ejemplo: 1.500,00.'
            )

    def clean(self):
        # PRE: donation y amount contienen la selección propuesta o errores de campo previos.
        # POST: rechaza sin saldo o exceso con mensajes operativos antes de delegar al backend.
        cleaned_data = super().clean()
        donation = cleaned_data.get('donation')
        amount = cleaned_data.get('amount')
        if not donation or amount is None:
            return cleaned_data
        available = donation.available_balance
        if self.instance.pk and self.instance.donation_id == donation.pk:
            available += self.instance.amount
        if available <= 0:
            self.add_error('donation', _('La donación seleccionada no tiene saldo disponible.'))
        elif amount > available:
            self.add_error('amount', _('El monto excede el saldo disponible de %(balance)s.') % {
                'balance': format_usd_amount(available)
            })
        return cleaned_data


class ExpenseForm(BootstrapFormMixin, forms.ModelForm):
    allocation = AllocationWithBalanceChoiceField(
        queryset=FundAllocation.objects.none(),
        label=_('Asignación'),
        error_messages={'invalid_choice': _('La asignación no está operativa o no tiene saldo disponible.')},
    )
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00'),
    )
    support_title = forms.CharField(required=False, max_length=160, label=_('Referencia o título del soporte'))
    support_file = forms.FileField(
        required=False,
        label=_('Documento soporte'),
        widget=forms.FileInput(
            attrs={
                'data-file-upload-preview': 'true',
            }
        ),
    )

    class Meta:
        model = Expense
        fields = [
            'allocation',
            'expense_date',
            'category',
            'amount',
            'reason',
            'provider_or_recipient',
            'payment_method',
            'description',
            'observations',
        ]
        labels = {
            'allocation': _('Asignación'),
            'expense_date': _('Fecha del gasto'),
            'category': _('Categoría'),
            'amount': _('Monto'),
            'reason': _('Motivo'),
            'provider_or_recipient': _('Proveedor o destinatario'),
            'payment_method': _('Método de pago'),
            'description': _('Descripción'),
            'observations': _('Observaciones'),
        }
        help_texts = {
            'expense_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'expense_date': build_date_widget(),
        }

    def clean(self):
        """
        PRE: submitted expense data may create a record or edit an existing registered expense.
        POST: requires protected support for every resulting expense and never exposes lifecycle state.
        """
        cleaned_data = super().clean()
        support_file = cleaned_data.get('support_file')
        has_existing_support = self.instance.pk and self.instance.supporting_documents.exists()
        if not support_file and not has_existing_support:
            self.add_error('support_file', _('Falta el documento soporte obligatorio para verificar el gasto.'))
        allocation = cleaned_data.get('allocation')
        amount = cleaned_data.get('amount')
        if allocation and allocation.status != FundAllocation.Status.ACTIVE:
            self.add_error('allocation', _('La asignación seleccionada no está operativa y no acepta gastos.'))
        elif allocation and amount is not None:
            available = allocation.available_balance
            if self.instance.pk and self.instance.allocation_id == allocation.pk:
                available += self.instance.amount
            if available <= 0:
                self.add_error('allocation', _('La asignación seleccionada no tiene saldo disponible.'))
            elif amount > available:
                self.add_error('amount', _('El monto excede el saldo disponible de %(balance)s.') % {
                    'balance': format_usd_amount(available)
                })
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_allocation_id = self.instance.allocation_id if self.instance.pk else None
        allocations = FundAllocation.objects.filter(
            donation__currency=OPERATING_CURRENCY,
            status=FundAllocation.Status.ACTIVE,
        )
        eligible_allocation_ids = [
            allocation.pk for allocation in allocations
            if allocation.available_balance > 0 or allocation.pk == current_allocation_id
        ]
        self.fields['allocation'].queryset = allocations.filter(pk__in=eligible_allocation_ids)
        selected_allocation_id = self.data.get(self.add_prefix('allocation')) or current_allocation_id
        selected_allocation = allocations.filter(pk=selected_allocation_id).first() if selected_allocation_id else None
        if selected_allocation:
            available = selected_allocation.available_balance
            if self.instance.pk and self.instance.allocation_id == selected_allocation.pk:
                available += self.instance.amount
            self.fields['amount'].help_text = _(
                'Ejecutado: %(executed)s. Máximo disponible para este gasto: %(available)s.'
            ) % {
                'executed': format_usd_amount(selected_allocation.executed_amount),
                'available': format_usd_amount(available),
            } + ' ' + _('Ejemplo: 1.500,00.')
        else:
            self.fields['amount'].help_text = _(
                'Seleccione una asignación para consultar lo ejecutado y disponible. Ejemplo: 1.500,00.'
            )
        self.fields['support_title'].help_text = _(
            'Indique la referencia, número o título que identifica el soporte.'
        )
        self.fields['support_file'].help_text = _(
            'Obligatorio al crear el gasto. Adjunte el archivo que permite verificar la operación.'
        )

    # PRE: form.is_valid() has returned True and the expense can be saved.
    # POST: returns an unsaved instance for commit=False, otherwise persists through transactional expense services.
    def save(self, commit=True):
        expense = super().save(commit=False)
        if not commit:
            return expense
        from .services import create_expense, update_expense

        service_data = {
            'allocation': self.cleaned_data['allocation'],
            'expense_date': self.cleaned_data['expense_date'],
            'category': self.cleaned_data['category'],
            'amount': self.cleaned_data['amount'],
            'reason': self.cleaned_data['reason'],
            'provider_or_recipient': self.cleaned_data['provider_or_recipient'],
            'payment_method': self.cleaned_data['payment_method'],
            'description': self.cleaned_data.get('description', ''),
            'observations': self.cleaned_data.get('observations', ''),
            'support_title': self.cleaned_data.get('support_title', ''),
            'support_file': self.cleaned_data.get('support_file'),
        }
        if expense.pk:
            expense = update_expense(expense=expense, **service_data)
        else:
            expense = create_expense(**service_data)
        self.instance = expense
        self.save_m2m()
        return expense


class ExpenseRequestAllocationChoiceField(forms.ModelChoiceField):
    """Allocation picker for Expense Request forms with safe Operator-facing labels."""

    include_project_in_label = False

    def label_from_instance(self, allocation):
        # PRE: allocation belongs to the annotated operational queryset.
        # POST: returns category + available balance; never donor/donation code/__str__.
        label = _('%(category)s · Disponible: %(available)s') % {
            'category': allocation.get_budget_category_display(),
            'available': format_usd_amount(allocation.available_balance),
        }
        if self.include_project_in_label:
            project = allocation.project
            label = _('%(label)s · %(project_code)s · %(project_name)s') % {
                'label': label,
                'project_code': project.code,
                'project_name': project.name,
            }
        return label


class ExpenseRequestApproveForm(BootstrapFormMixin, forms.Form):
    """
    Committee approval confirmation form.

    Financial values and actor come from the persisted request and request.user;
    this form never accepts amount, allocation, status, or actor from POST.
    """

    decision_note = forms.CharField(
        label=_('Observación del Comité'),
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_(
            'La aprobación reservará el monto solicitado en la asignación seleccionada.'
        ),
    )


class ExpenseRequestFulfillmentForm(BootstrapFormMixin, forms.Form):
    """
    Administrator fulfillment form: converts an APPROVED_RESERVED request into an Expense.

    Allocation, request identity, reserved amount, currency, and status come from the
    persisted request and fulfill_expense_request; this form never accepts them from POST.
    """

    expense_date = forms.DateField(
        label=_('Fecha del gasto'),
        help_text=_('Formato: dd/mm/aaaa.'),
        widget=build_date_widget(),
        input_formats=DATE_INPUT_FORMATS,
    )
    amount = MoneyDecimalField(
        label=_('Monto'),
        min_value=Decimal('0.01'),
    )
    category = forms.ChoiceField(
        label=_('Categoría'),
        choices=EXPENSE_CATEGORY_CHOICES,
    )
    reason = forms.CharField(
        label=_('Motivo'),
        max_length=220,
        strip=True,
    )
    provider_or_recipient = forms.CharField(
        label=_('Proveedor o destinatario'),
        max_length=160,
        strip=True,
    )
    payment_method = forms.ChoiceField(
        label=_('Método de pago'),
        choices=PAYMENT_METHOD_CHOICES,
    )
    description = forms.CharField(
        label=_('Descripción'),
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    observations = forms.CharField(
        label=_('Observaciones'),
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    support_file = forms.FileField(
        label=_('Documento soporte'),
        widget=forms.FileInput(
            attrs={
                'data-file-upload-preview': 'true',
            }
        ),
        help_text=_(
            'Obligatorio. Adjunte el archivo que permite verificar la operación.'
        ),
    )
    support_title = forms.CharField(
        required=False,
        max_length=160,
        label=_('Referencia o título del soporte'),
        help_text=_(
            'Indique la referencia, número o título que identifica el soporte.'
        ),
    )
    support_notes = forms.CharField(
        label=_('Notas del soporte'),
        required=False,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('Opcional. Use este campo para aclaraciones internas sobre el soporte.'),
    )

    def __init__(self, *args, reserved_amount=None, **kwargs):
        # PRE: reserved_amount is the persisted request reservation (authoritative max).
        # POST: caps amount validation and help text; never trusts POST for the ceiling.
        super().__init__(*args, **kwargs)
        self.reserved_amount = reserved_amount
        if reserved_amount is not None:
            self.fields['amount'].max_value = reserved_amount
            self.fields['amount'].help_text = _(
                'Máximo según la reserva: %(max)s. El saldo no utilizado se liberará '
                'automáticamente.'
            ) % {'max': format_usd_amount(reserved_amount)}
        else:
            self.fields['amount'].help_text = _(
                'El monto no puede superar la reserva de la solicitud. Ejemplo: 1.500,00.'
            )

    def clean_reason(self):
        reason = self.cleaned_data['reason'].strip()
        if not reason:
            raise ValidationError(_('El motivo del gasto es obligatorio.'))
        return reason

    def clean_provider_or_recipient(self):
        provider = self.cleaned_data['provider_or_recipient'].strip()
        if not provider:
            raise ValidationError(_('El proveedor o destinatario es obligatorio.'))
        return provider

    def clean_amount(self):
        # PRE: amount passed MoneyDecimalField parsing and min_value.
        # POST: rejects non-positive or above-reservation values; service remains authoritative.
        amount = self.cleaned_data['amount']
        if amount is None:
            return amount
        if amount <= 0:
            raise ValidationError(_('El monto del gasto debe ser mayor que cero.'))
        if self.reserved_amount is not None and amount > self.reserved_amount:
            raise ValidationError(
                _('El monto no puede superar la reserva de %(max)s.')
                % {'max': format_usd_amount(self.reserved_amount)}
            )
        return amount


class ExpenseRequestAttachmentForm(BootstrapFormMixin, forms.Form):
    """
    Optional attachment upload for a pending Expense Request.

    Parent request and uploader are set by the view/service, never from POST.
    """

    title = forms.CharField(
        label=_('Título'),
        max_length=160,
        help_text=_('Se aplica a todos los archivos cargados en este envío.'),
    )
    notes = forms.CharField(
        label=_('Notas'),
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Opcional. Aclaraciones internas sobre los adjuntos.'),
    )
    files = MultipleFileField(
        label=_('Archivos'),
        help_text=_('Puede seleccionar varios archivos a la vez.'),
        widget=MultipleFileInput(attrs={
            'data-file-upload': 'multiple',
            'data-file-upload-preview': 'true',
        }),
    )


class ExpenseRequestForm(BootstrapFormMixin, forms.Form):
    """
    Global or project-scoped Expense Request create/update form.

    Does not call ModelForm.save(); views must invoke expense_request_services.
    """

    fund_allocation = ExpenseRequestAllocationChoiceField(
        queryset=FundAllocation.objects.none(),
        label=_('Asignación'),
        help_text=_(
            'La solicitud no reserva fondos hasta que sea aprobada por el Comité.'
        ),
        error_messages={
            'invalid_choice': _(
                'La asignación seleccionada no está disponible para esta solicitud.'
            ),
        },
    )
    requested_amount = MoneyDecimalField(
        label=_('Monto solicitado'),
        min_value=Decimal('0.01'),
        help_text=_(
            'El monto será validado nuevamente al momento de la aprobación. Ejemplo: 1.500,00.'
        ),
    )
    purpose = forms.CharField(
        label=_('Propósito'),
        max_length=220,
        strip=True,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    requested_date = forms.DateField(
        label=_('Fecha de solicitud'),
        help_text=_('Formato: dd/mm/aaaa.'),
        widget=build_date_widget(),
    )

    def __init__(
        self,
        *args,
        project=None,
        include_project_in_label=False,
        include_allocation_id=None,
        **kwargs,
    ):
        # PRE: optional project scopes allocation choices; include_allocation_id keeps edit row.
        # POST: binds annotated queryset and label policy without mutating domain state.
        from .selectors import expense_request_allocation_choices

        super().__init__(*args, **kwargs)
        self.project = project
        self.fields['fund_allocation'].include_project_in_label = include_project_in_label
        self.fields['fund_allocation'].queryset = expense_request_allocation_choices(
            project=project,
            include_allocation_id=include_allocation_id,
        )

    def clean_purpose(self):
        purpose = self.cleaned_data['purpose'].strip()
        if not purpose:
            raise ValidationError(_('El propósito de la solicitud es obligatorio.'))
        return purpose


class ExpenseRequestForProjectForm(ExpenseRequestForm):
    """Project-context create form: allocations limited to the URL project."""

    def __init__(self, *args, project, **kwargs):
        # PRE: project is the authorized parent from the create-for-project route.
        # POST: scopes allocation choices to that project without a mutable project input.
        if project is None:
            raise ValueError('ExpenseRequestForProjectForm requires a project.')
        kwargs.setdefault('include_project_in_label', False)
        kwargs['project'] = project
        super().__init__(*args, **kwargs)


class ExpenseAnnulmentForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        label=_('Razón de anulación'),
        widget=forms.Textarea(attrs={'rows': 4}),
        strip=True,
    )

    def clean_reason(self):
        # PRE: reason is the browser-supplied cancellation explanation.
        # POST: returns non-empty trimmed text or raises a field validation error.
        reason = self.cleaned_data['reason'].strip()
        if not reason:
            raise ValidationError(_('La razón de anulación es obligatoria.'))
        return reason


class SupportingDocumentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SupportingDocument
        fields = [
            'title',
            'document',
            'notes',
        ]
        labels = {
            'title': _('Título'),
            'document': _('Archivo'),
            'notes': _('Notas'),
        }
        help_texts = {
            'document': _('Adjunte el comprobante, factura, recibo o evidencia documental del gasto.'),
            'notes': _('Opcional. Use este campo para aclaraciones internas sobre el soporte.'),
        }
        widgets = {
            'document': forms.FileInput(
                attrs={
                    'data-file-upload-preview': 'true',
                }
            ),
        }


# ---------------------------------------------------------------------------
# Django admin: single functional SIGEDON role + technical groups
# ---------------------------------------------------------------------------

FUNCTIONAL_ROLE_HELP = _(
    'Como máximo un rol funcional SIGEDON. Deje «Ninguno» para cuentas de '
    'servicio o superusuarios sin rol operativo.'
)
TECHNICAL_GROUPS_HELP = _(
    'Grupos técnicos no funcionales. Los roles SIGEDON se asignan arriba '
    'en «Rol funcional SIGEDON».'
)
MULTI_ROLE_INCONSISTENCY = _(
    'Este usuario tiene más de un rol funcional SIGEDON. Seleccione un único '
    'rol (o «Ninguno») y guarde para reparar la inconsistencia.'
)


class SigedonUserRoleFormMixin:
    """
    Shared admin-form behavior: one optional functional role, technical groups
    excluding canonical roles, and an M2M save path that does not wipe the role.

    Concrete forms must declare ``functional_role`` themselves so Django's
    DeclarativeFieldsMetaclass registers it (plain mixins do not).
    """

    def _configure_role_fields(self):
        """
        PRE: form fields include functional_role and groups.
        POST: querysets and labels separate canonical roles from technical groups.
        """
        self.fields['functional_role'].queryset = functional_role_groups()
        self.fields['functional_role'].empty_label = _('Ninguno')
        self.fields['functional_role'].required = False
        self.fields['functional_role'].help_text = FUNCTIONAL_ROLE_HELP

        groups_field = self.fields['groups']
        groups_field.queryset = (
            Group.objects.exclude(name__in=operation_role_names()).order_by('name')
        )
        groups_field.label = _('Grupos técnicos adicionales')
        groups_field.required = False
        groups_field.help_text = TECHNICAL_GROUPS_HELP

    def _set_functional_role_initial(self):
        """
        PRE: self.instance may be a persisted user.
        POST: initial functional_role reflects 0/1 roles; multi-role stays empty
              and records a repairable inconsistency flag.
        """
        self._functional_role_inconsistency = False
        if not self.instance.pk:
            self.fields['functional_role'].initial = None
            return
        roles = list(get_user_functional_roles(self.instance))
        if len(roles) == 1:
            self.fields['functional_role'].initial = roles[0]
        else:
            self.fields['functional_role'].initial = None
            if len(roles) > 1:
                self._functional_role_inconsistency = True

    def full_clean(self):
        super().full_clean()
        # Unbound change forms short-circuit validation; still surface multi-role
        # inconsistency so the admin can repair it on the next valid POST.
        if (
            not self.is_bound
            and getattr(self, '_functional_role_inconsistency', False)
        ):
            # Unbound full_clean never builds cleaned_data; seed it so add_error works.
            self.cleaned_data = {}
            self.add_error(None, MULTI_ROLE_INCONSISTENCY)

    def clean_functional_role(self):
        """
        PRE: functional_role is empty or a Group from the functional queryset.
        POST: returns None or a canonical role group.
        """
        role = self.cleaned_data.get('functional_role')
        if role is None:
            return None
        if role.name not in operation_role_names():
            raise ValidationError(
                _('El rol seleccionado no es un rol funcional SIGEDON canónico.')
            )
        return role

    def clean_groups(self):
        """
        PRE: groups contains submitted technical group selections.
        POST: rejects any canonical functional role smuggled into the field.
        """
        groups = list(self.cleaned_data.get('groups') or [])
        canonical = set(operation_role_names())
        illicit = [group.name for group in groups if group.name in canonical]
        if illicit:
            raise ValidationError(
                _(
                    'Los roles funcionales SIGEDON no pueden asignarse como '
                    'grupos técnicos: %(roles)s.'
                )
                % {'roles': ', '.join(illicit)}
            )
        return groups

    def clean(self):
        """
        PRE: field-level cleaning has run.
        POST: at most one functional role is represented by the form payload.
        """
        cleaned_data = super().clean()
        role = cleaned_data.get('functional_role')
        technical = cleaned_data.get('groups') or []
        functional_from_technical = [
            group for group in technical if group.name in operation_role_names()
        ]
        represented = ([role] if role else []) + functional_from_technical
        if len(represented) > 1:
            raise ValidationError(
                _('Solo se permite un rol funcional SIGEDON por usuario.')
            )
        return cleaned_data

    def _save_m2m(self):
        """
        Stock ModelForm._save_m2m would call groups.set(technical_only) and wipe
        the functional SIGEDON role that is excluded from the groups queryset.
        Persist direct permissions and technical groups explicitly, then apply
        the selected functional role once via set_user_functional_role.
        """
        cleaned_data = self.cleaned_data
        with transaction.atomic():
            if 'user_permissions' in cleaned_data:
                self.instance.user_permissions.set(cleaned_data['user_permissions'])

            # Authoritative for non-functional groups only; deselect removes.
            submitted_technical = list(cleaned_data.get('groups') or [])
            existing_technical = list(
                self.instance.groups.exclude(name__in=operation_role_names())
            )
            if existing_technical:
                self.instance.groups.remove(*existing_technical)
            if submitted_technical:
                self.instance.groups.add(*submitted_technical)

            set_user_functional_role(
                self.instance,
                cleaned_data.get('functional_role'),
            )


class SigedonUserChangeForm(SigedonUserRoleFormMixin, UserChangeForm):
    functional_role = forms.ModelChoiceField(
        label=_('Rol funcional SIGEDON'),
        queryset=Group.objects.none(),
        required=False,
        empty_label=_('Ninguno'),
        help_text=FUNCTIONAL_ROLE_HELP,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._configure_role_fields()
        self._set_functional_role_initial()


class SigedonAdminUserCreationForm(SigedonUserRoleFormMixin, AdminUserCreationForm):
    functional_role = forms.ModelChoiceField(
        label=_('Rol funcional SIGEDON'),
        queryset=Group.objects.none(),
        required=False,
        empty_label=_('Ninguno'),
        help_text=FUNCTIONAL_ROLE_HELP,
    )
    groups = forms.ModelMultipleChoiceField(
        label=_('Grupos técnicos adicionales'),
        queryset=Group.objects.none(),
        required=False,
        help_text=TECHNICAL_GROUPS_HELP,
        widget=FilteredSelectMultiple(_('Grupos técnicos adicionales'), is_stacked=False),
    )
    user_permissions = forms.ModelMultipleChoiceField(
        label=_('Permisos de usuario'),
        queryset=Permission.objects.all(),
        required=False,
        widget=FilteredSelectMultiple(_('Permisos de usuario'), is_stacked=False),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user_permissions'].queryset = (
            Permission.objects.select_related('content_type').order_by(
                'content_type__app_label',
                'content_type__model',
                'codename',
            )
        )
        self._configure_role_fields()
        self.fields['functional_role'].initial = None
