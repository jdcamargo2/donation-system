import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from django import forms

from apps.operations.choices import OPERATING_CURRENCY_CHOICES
from apps.operations.forms import DonationForm, ExpenseForm, FundAllocationForm, InstitutionForm, ProjectForm
from apps.operations.models import Donation, Expense, FundAllocation, Institution, Project, SupportingDocument
from apps.operations.tests.helpers import TEST_DATE, create_allocation, create_donation, create_expense, create_institution, create_project


class FormTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        self.donor = create_institution()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_institution_form_saves_valid_data(self):
        upload = SimpleUploadedFile('registro.pdf', b'documento legal', content_type='application/pdf')
        form = InstitutionForm(
            data={
                'name': 'Diocese Partner',
                'institution_type': 'parish',
                'role': Institution.Role.ALLY,
                'country': 'VE',
                'contact_email': 'ally@example.com',
                'contact_phone': '',
                'responsible_person': 'Coordinator',
                'legal_document': '',
                'status': Institution.Status.ACTIVE,
            },
            files={'legal_document': upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        institution = form.save()
        self.assertEqual(institution.role, Institution.Role.ALLY)
        self.assertEqual(institution.country.code, 'VE')
        self.assertTrue(institution.legal_document.name.startswith('institution_documents/'))

    def test_institution_form_shows_country_and_not_city(self):
        form = InstitutionForm()

        self.assertIn('country', form.fields)
        self.assertNotIn('city', form.fields)
        self.assertEqual(form.fields['country'].label, 'País')
        self.assertEqual(form.fields['institution_type'].widget.__class__.__name__, 'Select')

    def test_institution_form_legal_document_opts_into_clearable_file_upload_preview(self):
        form = InstitutionForm()
        field = form.fields['legal_document']

        self.assertIsInstance(field.widget, forms.ClearableFileInput)
        self.assertEqual(field.widget.attrs.get('data-file-upload-preview'), 'true')
        self.assertFalse(field.widget.allow_multiple_selected)
        self.assertFalse(field.required)
        self.assertIn('form-control', field.widget.attrs.get('class', ''))
        rendered = str(field.widget.render('legal_document', None))
        self.assertIn('data-file-upload-preview="true"', rendered)
        self.assertNotIn('multiple', rendered)

    def test_money_fields_and_selects_are_configured_for_forms(self):
        donation_form = DonationForm()
        project_form = ProjectForm()
        allocation_form = FundAllocationForm()
        expense_form = ExpenseForm()

        self.assertNotIn('code', donation_form.fields)
        self.assertNotIn('code', project_form.fields)
        self.assertNotIn('responsible_unit', project_form.fields)
        self.assertEqual(donation_form.fields['donation_type'].widget.__class__.__name__, 'Select')
        self.assertNotIn('currency', donation_form.fields)
        self.assertEqual(OPERATING_CURRENCY_CHOICES, (('USD', 'USD'),))
        self.assertEqual(donation_form.fields['amount'].widget.input_type, 'text')
        self.assertIn('money-input', donation_form.fields['amount'].widget.attrs['class'])
        self.assertIn('js-money-input', donation_form.fields['amount'].widget.attrs['class'])
        self.assertIn('form-control', donation_form.fields['amount'].widget.attrs['class'])
        self.assertEqual(donation_form.fields['amount'].widget.attrs['inputmode'], 'decimal')
        self.assertEqual(donation_form.fields['amount'].widget.attrs['autocomplete'], 'off')
        self.assertEqual(donation_form.fields['amount'].widget.attrs['placeholder'], 'Ej. 1.500,00')
        self.assertIn('1.500,00', donation_form.fields['amount'].help_text)
        self.assertIn('1.500,00', project_form.fields['estimated_budget'].help_text)
        self.assertEqual(project_form.fields['estimated_budget'].widget.input_type, 'text')
        self.assertIn('money-input', project_form.fields['estimated_budget'].widget.attrs['class'])
        self.assertEqual(project_form.fields['estimated_budget'].widget.attrs['inputmode'], 'decimal')
        self.assertEqual(allocation_form.fields['budget_category'].widget.__class__.__name__, 'Select')
        self.assertFalse(allocation_form.fields['notes'].required)
        self.assertIn('1.500,00', allocation_form.fields['amount'].help_text)
        self.assertIn('money-input', allocation_form.fields['amount'].widget.attrs['class'])
        self.assertEqual(expense_form.fields['category'].widget.__class__.__name__, 'Select')
        self.assertNotIn('currency', expense_form.fields)
        self.assertEqual(expense_form.fields['payment_method'].widget.__class__.__name__, 'Select')
        self.assertTrue(expense_form.fields['expense_date'].required)
        self.assertTrue(expense_form.fields['reason'].required)
        self.assertIn('1.500,00', expense_form.fields['amount'].help_text)
        self.assertIn('money-input', expense_form.fields['amount'].widget.attrs['class'])

    def test_project_money_input_renders_as_text_input_with_autonumeric_class(self):
        rendered = str(ProjectForm()['estimated_budget'])

        self.assertIn('type="text"', rendered)
        self.assertIn('money-input', rendered)
        self.assertIn('inputmode="decimal"', rendered)
        self.assertIn('autocomplete="off"', rendered)
        self.assertIn('placeholder="Ej. 1.500,00"', rendered)
        self.assertNotIn('type="number"', rendered)

    def test_project_form_textareas_render_with_full_width_layout_class(self):
        form = ProjectForm()

        for field_name in ['description', 'objective']:
            with self.subTest(field=field_name):
                rendered = str(form[field_name])
                self.assertIn('ops-textarea', rendered)
                self.assertIn('rows="3"', rendered)

    def test_date_fields_use_text_datepicker_instead_of_native_date_input(self):
        project_form = ProjectForm()
        donation_form = DonationForm()
        allocation_form = FundAllocationForm()
        expense_form = ExpenseForm()

        date_fields = [
            project_form.fields['start_date'],
            project_form.fields['end_date'],
            donation_form.fields['commitment_date'],
            donation_form.fields['received_date'],
            allocation_form.fields['allocation_date'],
            expense_form.fields['expense_date'],
        ]

        for field in date_fields:
            with self.subTest(field=field.label):
                self.assertEqual(field.widget.input_type, 'text')
                self.assertNotEqual(field.widget.input_type, 'date')
                self.assertIn('datepicker', field.widget.attrs['class'])
                self.assertEqual(field.widget.attrs['placeholder'], 'dd/mm/aaaa')
                self.assertIn('%d/%m/%Y', field.input_formats)

    def test_selects_use_clear_placeholder_and_expected_choices(self):
        donation_form = DonationForm()
        expense_form = ExpenseForm()

        self.assertEqual(list(donation_form.fields['donation_type'].choices)[0], ('', 'Seleccione una opción'))
        self.assertIn(('money', 'Dinero'), list(donation_form.fields['donation_type'].choices))
        self.assertNotIn(('mobile_payment', 'Pago móvil'), list(expense_form.fields['payment_method'].choices))

    def test_donation_form_create_requires_explicit_donation_type_selection(self):
        form = DonationForm()
        choices = list(form.fields['donation_type'].choices)
        empty_choices = [choice for choice in choices if choice[0] == '']
        rendered = str(form['donation_type'])

        self.assertTrue(form.fields['donation_type'].required)
        self.assertIsNone(form.fields['donation_type'].initial)
        self.assertEqual(choices[0], ('', 'Seleccione una opción'))
        self.assertEqual(len(empty_choices), 1)
        self.assertIsNone(form['donation_type'].value())
        self.assertIn(('goods', 'Bienes'), choices)
        self.assertIn('value="" selected', rendered)
        self.assertNotIn('value="goods" selected', rendered)

    def test_donation_form_create_rejects_missing_donation_type(self):
        before_count = Donation.objects.count()
        form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': '',
                'amount': '250.00',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'support_reference': '',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('donation_type', form.errors)
        self.assertEqual(form.errors['donation_type'], ['Este campo es obligatorio.'])
        self.assertEqual(Donation.objects.count(), before_count)

    def test_donation_form_edit_preserves_goods_donation_type(self):
        donation = create_donation(code='DON-GOODS', donor=self.donor)
        self.assertEqual(donation.donation_type, 'goods')
        form = DonationForm(instance=donation)
        rendered = str(form['donation_type'])

        self.assertEqual(form['donation_type'].value(), 'goods')
        self.assertIn('value="goods" selected', rendered)
        self.assertNotIn('value="" selected', rendered)

    def test_donation_form_edit_preserves_money_donation_type(self):
        donation = create_donation(code='DON-MONEY', donor=self.donor)
        donation.donation_type = 'money'
        donation.save(update_fields=('donation_type',))
        form = DonationForm(instance=donation)
        rendered = str(form['donation_type'])

        self.assertEqual(form['donation_type'].value(), 'money')
        self.assertIn('value="money" selected', rendered)
        self.assertNotIn('value="" selected', rendered)

    def test_project_form_saves_valid_data(self):
        form = ProjectForm(
            data={
                'name': 'Health support',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '500.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        project = form.save()
        self.assertEqual(project.estimated_budget, Decimal('500.00'))
        self.assertRegex(project.code, r'^PRJ-\d{6}$')
        self.assertNotEqual(project.code, self.project.code)
        self.assertEqual(Project.objects.filter(code=project.code).count(), 1)

    def test_donation_form_saves_valid_data(self):
        form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '250.00',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
                'currency': 'EUR',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        donation = form.save()
        self.assertEqual(donation.amount, Decimal('250.00'))
        self.assertEqual(donation.currency, 'USD')
        self.assertRegex(donation.code, r'^DON-\d{6}$')
        self.assertNotEqual(donation.code, self.donation.code)
        self.assertEqual(Donation.objects.filter(code=donation.code).count(), 1)

    def test_money_forms_accept_spanish_thousands_format(self):
        form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': 'money',
                'amount': '1.500,25',
                'objective': 'Aporte económico',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        donation = form.save()
        self.assertEqual(donation.amount, Decimal('1500.25'))
        self.assertEqual(donation.currency, 'USD')

    def test_money_forms_accept_spanish_thousands_format_for_all_operational_amounts(self):
        project_form = ProjectForm(
            data={
                'name': 'Budget format',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '1.500,00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertTrue(project_form.is_valid(), project_form.errors)
        project = project_form.save()
        self.assertEqual(project.estimated_budget, Decimal('1500.00'))

        allocation_form = FundAllocationForm(
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '1.500,00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )
        self.assertFalse(allocation_form.is_valid())
        self.assertIn('amount', allocation_form.errors)

        large_donation = create_donation(code='DON-003', donor=self.donor, amount=Decimal('3000.00'))
        valid_allocation_form = FundAllocationForm(
            data={
                'donation': large_donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '1.500,00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )
        self.assertTrue(valid_allocation_form.is_valid(), valid_allocation_form.errors)
        allocation = valid_allocation_form.save()
        self.assertEqual(allocation.amount, Decimal('1500.00'))

        expense_form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '1.500,00',
                'reason': 'Compra',
                'provider_or_recipient': 'Proveedor A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
            },
            files={'support_file': SimpleUploadedFile('monto.pdf', b'%PDF soporte')},
        )
        self.assertTrue(expense_form.is_valid(), expense_form.errors)
        expense = expense_form.save()
        self.assertEqual(expense.amount, Decimal('1500.00'))

    def test_forms_accept_visual_day_month_year_dates(self):
        form = ProjectForm(
            data={
                'name': 'Visual dates',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '0.00',
                'start_date': '09/07/2026',
                'end_date': '10/07/2026',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['start_date'].isoformat(), '2026-07-09')
        self.assertEqual(form.cleaned_data['end_date'].isoformat(), '2026-07-10')

    def test_forms_reject_zero_or_negative_money_where_validation_exists(self):
        donation_form = DonationForm(
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '0.00',
                'objective': '',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            }
        )
        allocation_form = FundAllocationForm(
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '-1.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )

        self.assertFalse(donation_form.is_valid())
        self.assertFalse(allocation_form.is_valid())

    def test_allocation_form_rejects_over_allocation(self):
        create_allocation(donation=self.donation, project=self.project, amount=Decimal('90.00'))
        form = FundAllocationForm(
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '15.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)
        self.assertIn('saldo disponible', form.errors['amount'][0])

    def test_allocation_form_excludes_donation_without_balance(self):
        create_allocation(donation=self.donation, project=self.project, amount=self.donation.amount)

        form = FundAllocationForm()

        self.assertNotIn(self.donation, form.fields['donation'].queryset)

    def test_allocation_form_excludes_terminal_project(self):
        self.project.status = Project.Status.CLOSED
        self.project.save(update_fields=('status',))

        form = FundAllocationForm()

        self.assertNotIn(self.project, form.fields['project'].queryset)

    def test_expense_form_excludes_allocation_without_balance(self):
        allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('40.00'),
        )
        create_expense(allocation=allocation, amount=Decimal('40.00'))

        form = ExpenseForm()

        self.assertNotIn(allocation, form.fields['allocation'].queryset)

    def test_allocation_form_shows_selected_donation_restrictions(self):
        self.donation.restrictions = 'Uso exclusivo para alimentos.'
        self.donation.save(update_fields=('restrictions',))

        form = FundAllocationForm(data={'donation': self.donation.pk})

        self.assertIn('Uso exclusivo para alimentos.', str(form.fields['donation'].help_text))
        self.assertIn('Disponible:', form.fields['donation'].label_from_instance(self.donation))

    def test_expense_form_explains_mandatory_support_and_reference(self):
        form = ExpenseForm()

        self.assertIn('Obligatorio', str(form.fields['support_file'].help_text))
        self.assertIn('referencia', str(form.fields['support_title'].help_text).lower())

    def test_expense_form_rejects_over_execution(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '45.00',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'status': Expense.Status.REGISTERED,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_expense_requires_supporting_document(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '10.00',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('documento soporte', form.errors['support_file'][0])

    def test_expense_form_creates_supporting_document(self):
        allocation = create_allocation(donation=self.donation, project=self.project, amount=Decimal('40.00'))
        upload = SimpleUploadedFile('receipt.txt', b'receipt')
        form = ExpenseForm(
            data={
                'allocation': allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '10.00',
                'reason': 'Purchase',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'support_title': 'Receipt',
                'currency': 'EUR',
            },
            files={'support_file': upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save()
        self.assertEqual(expense.currency, 'USD')
        self.assertEqual(SupportingDocument.objects.filter(expense=expense).count(), 1)

    def test_project_form_rejects_negative_budget(self):
        form = ProjectForm(
            data={
                'name': 'Negative budget',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '-1.00',
                'start_date': '',
                'end_date': '',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('estimated_budget', form.errors)

    def test_project_form_rejects_end_date_before_start_date(self):
        form = ProjectForm(
            data={
                'name': 'Invalid dates',
                'description': '',
                'objective': '',
                'location': '',
                'estimated_budget': '0.00',
                'start_date': '2026-07-08',
                'end_date': '2026-07-07',
                'status': Project.Status.ACTIVE,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('end_date', form.errors)
