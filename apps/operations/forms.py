import re
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .choices import OPERATING_CURRENCY
from .models import Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument


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

    def clean(self):
        """
        PRE: status and review_notes contain the submitted review decision.
        POST: requires a non-blank reason for rejection and returns cleaned data.
        """
        cleaned_data = super().clean()
        if (
            cleaned_data.get('status') == ProjectUpdate.Status.REJECTED
            and not (cleaned_data.get('review_notes') or '').strip()
        ):
            self.add_error('review_notes', _('La razón del rechazo es obligatoria.'))
        return cleaned_data


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
            'notes',
        ]
        labels = {
            'donation': _('Donación'),
            'project': _('Proyecto'),
            'budget_category': _('Categoría presupuestaria'),
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
            'expense_date': build_date_widget(),
        }

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        support_file = cleaned_data.get('support_file')
        has_existing_support = self.instance.pk and self.instance.supporting_documents.exists()
        if status == Expense.Status.VALIDATED and not support_file and not has_existing_support:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        return cleaned_data

    def _post_clean(self):
        # PRE: clean() accepted a candidate expense state from the operational UI.
        # POST: validates ordinary model fields without materializing VALIDATED
        # before validate_expense() can atomically attach actor/date metadata.
        requested_status = self.cleaned_data.get('status')
        if requested_status != Expense.Status.VALIDATED:
            return super()._post_clean()
        persisted_status = Expense.Status.REGISTERED
        if self.instance.pk:
            persisted_status = Expense.objects.filter(pk=self.instance.pk).values_list(
                'status', flat=True
            ).first() or Expense.Status.REGISTERED
        self.cleaned_data['status'] = persisted_status
        try:
            super()._post_clean()
        finally:
            self.cleaned_data['status'] = requested_status

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            choice
            for choice in self.fields['status'].choices
            if choice[0] not in {
                Expense.Status.ANNULLED,
                Expense.Status.CANCELLED,
            }
        ]
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
        if service_data['status'] == Expense.Status.VALIDATED:
            # ModelForm.save() has no authenticated actor. Browser views perform
            # validation explicitly through create/update_expense with request.user.
            service_data['status'] = Expense.Status.REGISTERED
        if expense.pk:
            expense = update_expense(expense=expense, **service_data)
        else:
            expense = create_expense(**service_data)
        self.instance = expense
        self.save_m2m()
        return expense


class ExpenseCancellationForm(BootstrapFormMixin, forms.Form):
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
