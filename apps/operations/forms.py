from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .models import Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument


SELECT_PLACEHOLDER = _('Seleccione una opción')
MONEY_PLACEHOLDER = _('Ej. 1.500,00')
DATE_PLACEHOLDER = _('dd/mm/aaaa')
DATE_INPUT_FORMATS = ['%Y-%m-%d', '%d/%m/%Y']


# PRE: value is a submitted money value, possibly formatted for Venezuela or already normalized.
# POST: returns a DecimalField-compatible string without thousands separators and with "." as decimal separator.
def clean_money_value(value):
    if not isinstance(value, str):
        return value
    normalized = value.strip().replace(' ', '').replace('$', '').replace('USD', '')
    if ',' in normalized and '.' in normalized:
        if normalized.rfind(',') > normalized.rfind('.'):
            return normalized.replace('.', '').replace(',', '.')
        return normalized.replace(',', '')
    if ',' in normalized:
        return normalized.replace('.', '').replace(',', '.')
    return normalized


class DatePickerInput(forms.TextInput):
    input_type = 'text'

    def __init__(self, attrs=None):
        default_attrs = {
            'autocomplete': 'off',
            'placeholder': DATE_PLACEHOLDER,
            'class': 'ops-input datepicker',
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class MoneyInput(forms.TextInput):
    input_type = 'text'

    def __init__(self, attrs=None):
        default_attrs = {
            'inputmode': 'decimal',
            'autocomplete': 'off',
            'placeholder': MONEY_PLACEHOLDER,
            'class': 'ops-input money-input',
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class MoneyDecimalField(forms.DecimalField):
    widget = MoneyInput

    # PRE: value may use either 1500.00 or Spanish-style 1.500,00 formatting.
    # POST: returns a Decimal-compatible value normalized for Django DecimalField validation.
    def to_python(self, value):
        return super().to_python(clean_money_value(value))


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
            if isinstance(field, MoneyDecimalField) and 'money-input' not in current_classes:
                current_classes.append('money-input')
            if isinstance(field.widget, forms.Textarea) and 'ops-textarea' not in current_classes:
                current_classes.append('ops-textarea')
            field.widget.attrs['class'] = ' '.join(current_classes)
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3


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
            'responsible_unit',
            'location',
            'estimated_budget',
            'start_date',
            'end_date',
            'status',
        ]
        labels = {
            'name': _('Nombre'),
            'description': _('Descripción'),
            'objective': _('Objetivo'),
            'responsible_unit': _('Unidad responsable'),
            'location': _('Ubicación'),
            'estimated_budget': _('Presupuesto estimado'),
            'start_date': _('Fecha de inicio'),
            'end_date': _('Fecha de cierre'),
            'status': _('Estado'),
        }
        help_texts = {
            'start_date': _('Formato: dd/mm/aaaa.'),
            'end_date': _('Formato: dd/mm/aaaa.'),
            'status': _(
                'Use Activo para proyectos en ejecución pública. Planificado, Suspendido, Cerrado y Anulado '
                'son estados internos.'
            ),
        }
        widgets = {
            'start_date': DatePickerInput(),
            'end_date': DatePickerInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['estimated_budget'].initial = None

    def clean_estimated_budget(self):
        return self.cleaned_data.get('estimated_budget') or Project._meta.get_field('estimated_budget').default


class ProjectUpdateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectUpdate
        fields = [
            'project',
            'title',
            'description',
            'evidence',
        ]
        labels = {
            'project': _('Proyecto'),
            'title': _('Título'),
            'description': _('Descripción'),
            'evidence': _('Evidencia'),
        }


class ProjectUpdateForProjectForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectUpdate
        fields = [
            'title',
            'description',
            'evidence',
        ]
        labels = {
            'title': _('Título'),
            'description': _('Descripción'),
            'evidence': _('Evidencia'),
        }


class ProjectUpdateReviewForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(
        choices=[
            (ProjectUpdate.Status.APPROVED, _('Aprobado')),
            (ProjectUpdate.Status.REJECTED, _('Rechazado')),
        ],
        label=_('Decisión'),
    )
    review_notes = forms.CharField(
        required=False,
        label=_('Notas de revisión'),
        widget=forms.Textarea,
    )


class DonationForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00'),
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
            'status',
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
            'status': _('Estado'),
            'support_reference': _('Referencia de soporte'),
        }
        help_texts = {
            'commitment_date': _('Formato: dd/mm/aaaa.'),
            'received_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'commitment_date': DatePickerInput(),
            'received_date': DatePickerInput(),
        }

    def save(self, commit=True):
        donation = super().save(commit=False)
        donation.currency = OPERATING_CURRENCY
        if commit:
            donation.save()
            self.save_m2m()
        return donation


class FundAllocationForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00'),
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
            'status',
            'notes',
        ]
        labels = {
            'donation': _('Donación'),
            'project': _('Proyecto'),
            'budget_category': _('Categoría presupuestaria'),
            'amount': _('Monto'),
            'responsible_person': _('Responsable'),
            'allocation_date': _('Fecha de asignación'),
            'status': _('Estado'),
            'notes': _('Notas'),
        }
        help_texts = {
            'notes': _('Opcional. Use este campo para aclaraciones internas.'),
            'allocation_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'allocation_date': DatePickerInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['donation'].queryset = Donation.objects.filter(currency=OPERATING_CURRENCY)


class ExpenseForm(BootstrapFormMixin, forms.ModelForm):
    amount = MoneyDecimalField(
        label=_('Monto'),
        help_text=_('Ingrese el monto en USD. Ejemplo: 1.500,00'),
    )
    support_title = forms.CharField(required=False, max_length=160, label=_('Título del documento soporte'))
    support_file = forms.FileField(required=False, label=_('Documento soporte'))

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
            'status',
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
            'status': _('Estado'),
        }
        help_texts = {
            'expense_date': _('Formato: dd/mm/aaaa.'),
        }
        widgets = {
            'expense_date': DatePickerInput(),
        }

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        support_file = cleaned_data.get('support_file')
        has_existing_support = self.instance.pk and self.instance.supporting_documents.exists()
        if status == Expense.Status.VALIDATED and not support_file and not has_existing_support:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['allocation'].queryset = FundAllocation.objects.filter(
            donation__currency=OPERATING_CURRENCY
        )

    # PRE: form.is_valid() has returned True and the expense can be saved.
    # POST: returns an unsaved instance for commit=False, otherwise persists through transactional expense services.
    def save(self, commit=True):
        expense = super().save(commit=False)
        expense.currency = OPERATING_CURRENCY
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
            'status': self.cleaned_data['status'],
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
