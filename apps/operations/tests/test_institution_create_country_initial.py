import re

from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations.admin import InstitutionAdmin
from apps.operations.forms import INSTITUTION_CREATE_COUNTRY_INITIAL, InstitutionForm
from apps.operations.models import Institution
from apps.operations.tests.helpers import create_institution, create_user


def country_code(value):
    return getattr(value, 'code', value)


def selected_select_value(html, field_name):
    """
    PRE: html contains a <select name="{field_name}">.
    POST: returns the value of the selected option, or None.
    """
    match = re.search(
        rf'<select[^>]*\bname="{re.escape(field_name)}"[^>]*>(.*?)</select>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        return None
    for option in re.finditer(r'<option\b([^>]*)>', match.group(1), flags=re.IGNORECASE):
        attrs = option.group(1)
        if not re.search(r'\bselected\b', attrs, flags=re.IGNORECASE):
            continue
        value = re.search(r'\bvalue="([^"]*)"', attrs, flags=re.IGNORECASE)
        return value.group(1) if value else ''
    return None


class InstitutionCreateCountryInitialTests(TestCase):
    def test_model_default_remains_venezuela(self):
        self.assertEqual(Institution._meta.get_field('country').default, 'VE')

    def test_unbound_institution_form_selects_monteluz(self):
        form = InstitutionForm()

        self.assertEqual(INSTITUTION_CREATE_COUNTRY_INITIAL, 'ZZ')
        self.assertEqual(country_code(form['country'].value()), 'ZZ')
        self.assertEqual(selected_select_value(str(form['country']), 'country'), 'ZZ')
        self.assertIn('República de Monteluz', str(form['country']))

    def test_unbound_institution_form_keeps_explicit_initial_country(self):
        form = InstitutionForm(initial={'country': 'VE'})

        self.assertEqual(country_code(form['country'].value()), 'VE')
        self.assertEqual(selected_select_value(str(form['country']), 'country'), 'VE')

    def test_edit_institution_form_keeps_persisted_country(self):
        institution = create_institution(name='Aliado Venezuela')
        form = InstitutionForm(instance=institution)

        self.assertEqual(institution.country.code, 'VE')
        self.assertEqual(country_code(form['country'].value()), 'VE')
        self.assertEqual(selected_select_value(str(form['country']), 'country'), 'VE')

    def test_create_page_selects_monteluz(self):
        user = create_user(username='institution-create-country')
        self.client.force_login(user)

        response = self.client.get(reverse('institution_create'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(selected_select_value(html, 'country'), 'ZZ')
        self.assertIn('República de Monteluz', html)

    def test_update_page_keeps_persisted_country(self):
        user = create_user(username='institution-update-country')
        self.client.force_login(user)
        institution = create_institution(name='Aliado persistido')

        response = self.client.get(reverse('institution_update', args=[institution.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected_select_value(response.content.decode(), 'country'), 'VE')

    def test_admin_add_form_selects_monteluz(self):
        user = create_user(username='institution-admin-add-country')
        self.client.force_login(user)

        response = self.client.get(reverse('admin:operations_institution_add'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(selected_select_value(html, 'country'), 'ZZ')
        self.assertIn('República de Monteluz', html)

    def test_admin_change_form_keeps_persisted_country(self):
        user = create_user(username='institution-admin-change-country')
        self.client.force_login(user)
        institution = create_institution(name='Aliado admin persistido')

        response = self.client.get(
            reverse('admin:operations_institution_change', args=[institution.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected_select_value(response.content.decode(), 'country'), 'VE')

    def test_admin_add_initial_defaults_to_monteluz_without_overriding_query(self):
        request = RequestFactory().get(
            '/admin/operations/institution/add/',
            data={'country': 'VE'},
        )
        model_admin = InstitutionAdmin(Institution, admin.site)

        initial = model_admin.get_changeform_initial_data(request)

        self.assertEqual(initial.get('country'), 'VE')
        self.assertEqual(
            model_admin.get_changeform_initial_data(
                RequestFactory().get('/admin/operations/institution/add/'),
            ).get('country'),
            'ZZ',
        )
