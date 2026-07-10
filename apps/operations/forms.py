from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import Donation, Expense, FundAllocation, Institution, Project, ProjectUpdate, SupportingDocument


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = f"{field.widget.attrs.get('class', '')} {css_class}".strip()


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
            'estimated_budget': _('Ingrese el monto con dos decimales. Ejemplo: 1500.00'),
        }
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }


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
    class Meta:
        model = Donation
        fields = [
            'donor',
            'donation_type',
            'amount',
            'currency',
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
            'currency': _('Moneda'),
            'objective': _('Objetivo'),
            'restrictions': _('Restricciones o condiciones'),
            'commitment_date': _('Fecha de compromiso'),
            'received_date': _('Fecha de recepción'),
            'status': _('Estado'),
            'support_reference': _('Referencia de soporte'),
        }
        help_texts = {
            'amount': _('Ingrese el monto con dos decimales. Ejemplo: 1500.00'),
        }
        widgets = {
            'commitment_date': forms.DateInput(attrs={'type': 'date'}),
            'received_date': forms.DateInput(attrs={'type': 'date'}),
        }


class FundAllocationForm(BootstrapFormMixin, forms.ModelForm):
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
            'amount': _('Ingrese el monto con dos decimales. Ejemplo: 1500.00'),
            'notes': _('Opcional. Use este campo para aclaraciones internas.'),
        }
        widgets = {
            'allocation_date': forms.DateInput(attrs={'type': 'date'}),
        }


class ExpenseForm(BootstrapFormMixin, forms.ModelForm):
    support_title = forms.CharField(required=False, max_length=160, label=_('Título del documento soporte'))
    support_file = forms.FileField(required=False, label=_('Documento soporte'))

    class Meta:
        model = Expense
        fields = [
            'allocation',
            'expense_date',
            'category',
            'amount',
            'currency',
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
            'currency': _('Moneda'),
            'reason': _('Motivo'),
            'provider_or_recipient': _('Proveedor o destinatario'),
            'payment_method': _('Método de pago'),
            'description': _('Descripción'),
            'observations': _('Observaciones'),
            'status': _('Estado'),
        }
        help_texts = {
            'amount': _('Ingrese el monto con dos decimales. Ejemplo: 1500.00'),
        }
        widgets = {
            'expense_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        support_file = cleaned_data.get('support_file')
        has_existing_support = self.instance.pk and self.instance.supporting_documents.exists()
        if status == Expense.Status.VALIDATED and not support_file and not has_existing_support:
            raise ValidationError(_('Un gasto validado debe tener al menos un documento soporte.'))
        return cleaned_data

    # PRE: form.is_valid() has returned True and the expense can be saved.
    # POST: saves the expense and creates one supporting document when a file was submitted.
    def save(self, commit=True):
        expense = super().save(commit=commit)
        support_file = self.cleaned_data.get('support_file')
        if commit and support_file:
            title = self.cleaned_data.get('support_title') or support_file.name
            SupportingDocument.objects.create(expense=expense, title=title, document=support_file)
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
