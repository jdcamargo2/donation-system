"""Spreadsheet formula-injection neutralization for operational CSV exports."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.operations.csv_export import DANGEROUS_CSV_PREFIXES, escape_csv_cell
from apps.operations.models import Donation, Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import (
    ROLE_EXTERNAL_AUDITOR,
    ROLE_FIELD_OPERATOR,
    ROLE_SIGEDON_ADMIN,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
    create_user,
)


def _parse_csv(response):
    return list(csv.reader(io.StringIO(response.content.decode('utf-8'))))


def _assert_no_raw_dangerous_text_cells(rows, *, text_column_indexes):
    """
    PRE: rows is csv.reader output; text_column_indexes are user-controlled columns.
    POST: raises AssertionError if any selected cell is raw text with a formula prefix.
    """
    for row in rows[1:]:
        for index in text_column_indexes:
            cell = row[index]
            if cell == '':
                continue
            significant = cell.lstrip(' \t\r\n')
            if significant and significant[0] in DANGEROUS_CSV_PREFIXES:
                raise AssertionError(f'Raw dangerous CSV cell at column {index}: {cell!r}')


class EscapeCsvCellHelperTests(TestCase):
    def test_none_becomes_empty_string(self):
        self.assertEqual(escape_csv_cell(None), '')

    def test_empty_string_unchanged(self):
        self.assertEqual(escape_csv_cell(''), '')

    def test_ordinary_text_unchanged(self):
        self.assertEqual(escape_csv_cell('Proyecto Alfa'), 'Proyecto Alfa')

    def test_formula_prefixes_are_apostrophe_escaped(self):
        cases = {
            '=SUM(A1:A2)': "'=SUM(A1:A2)",
            '+CMD()': "'+CMD()",
            '-1+2': "'-1+2",
            '@payload': "'@payload",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(escape_csv_cell(raw), expected)

    def test_leading_whitespace_before_dangerous_prefix_is_escaped(self):
        cases = (
            ' =SUM(1,2)',
            '\t=SUM(1,2)',
            '\r+CMD',
            '\n@payload',
            ' \t\r\n-1+2',
        )
        for raw in cases:
            with self.subTest(raw=repr(raw)):
                escaped = escape_csv_cell(raw)
                self.assertTrue(escaped.startswith("'"))
                self.assertEqual(escaped, f"'{raw}")

    def test_already_apostrophe_prefixed_text_unchanged(self):
        self.assertEqual(escape_csv_cell("'=SUM(A1:A2)"), "'=SUM(A1:A2)")

    def test_unicode_ordinary_text_unchanged(self):
        self.assertEqual(escape_csv_cell('Apoyo alimentario — Caracas'), 'Apoyo alimentario — Caracas')

    def test_writer_still_quotes_embedded_special_characters(self):
        values = (
            'a,b',
            'dice "hola"',
            "linea1\nlinea2",
        )
        for value in values:
            with self.subTest(value=repr(value)):
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([escape_csv_cell(value)])
                parsed = next(csv.reader(io.StringIO(buf.getvalue())))
                self.assertEqual(parsed, [value])

    def test_negative_decimal_and_integer_are_not_apostrophe_prefixed(self):
        self.assertEqual(escape_csv_cell(Decimal('-25.00')), Decimal('-25.00'))
        self.assertEqual(escape_csv_cell(-3), -3)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([escape_csv_cell(Decimal('-25.00')), escape_csv_cell(-3)])
        parsed = next(csv.reader(io.StringIO(buf.getvalue())))
        self.assertEqual(parsed, ['-25.00', '-3'])
        self.assertFalse(parsed[0].startswith("'"))
        self.assertFalse(parsed[1].startswith("'"))

    def test_zero_unchanged(self):
        self.assertEqual(escape_csv_cell(0), 0)
        self.assertEqual(escape_csv_cell(Decimal('0.00')), Decimal('0.00'))

    def test_date_and_datetime_retain_representation(self):
        day = date(2026, 7, 8)
        moment = datetime(2026, 7, 8, 12, 30, 0)
        self.assertEqual(escape_csv_cell(day), day)
        self.assertEqual(escape_csv_cell(moment), moment)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([escape_csv_cell(day), escape_csv_cell(moment)])
        parsed = next(csv.reader(io.StringIO(buf.getvalue())))
        self.assertEqual(parsed[0], str(day))
        self.assertEqual(parsed[1], str(moment))

    def test_generated_code_unchanged(self):
        self.assertEqual(escape_csv_cell('PRJ-000001'), 'PRJ-000001')

    def test_hyphen_prefixed_user_text_is_escaped(self):
        self.assertEqual(escape_csv_cell('-IMPORTANTE'), "'-IMPORTANTE")


class CsvExportSecurityEndpointTests(TestCase):
    def setUp(self):
        sync_operation_roles()
        self.superuser = create_user(username='csv-super')
        self.admin = self._role_user('csv-admin', ROLE_SIGEDON_ADMIN)
        self.auditor = self._role_user('csv-auditor', ROLE_EXTERNAL_AUDITOR)
        self.operator = self._role_user('csv-operator', ROLE_FIELD_OPERATOR)
        self.unauthorized = get_user_model().objects.create_user(
            username='csv-none',
            password='pass-12345',
        )

        self.donor = create_institution(name='=HYPERLINK("http://evil")')
        self.other_donor = create_institution(name='Donante seguro')
        self.project = create_project(code='PRJ-CSV-001', name='=SUM(A1:A2)')
        self.project.location = '+CMD()'
        self.project.status = Project.Status.ACTIVE
        self.project.start_date = date(2026, 1, 15)
        self.project.save(update_fields=('location', 'status', 'start_date', 'updated_at'))

        self.closed = create_project(code='PRJ-CSV-002', name='Proyecto cerrado')
        self.closed.status = Project.Status.CLOSED
        self.closed.save(update_fields=('status', 'updated_at'))

        self.donation = create_donation(
            code='DON-CSV-001',
            donor=self.donor,
            amount=Decimal('1000.50'),
            status=Donation.Status.RECEIVED,
        )
        self.donation.received_date = date(2026, 7, 10)
        self.donation.save(update_fields=('received_date', 'updated_at'))

        self.other_donation = create_donation(
            code='DON-CSV-002',
            donor=self.other_donor,
            amount=Decimal('50.00'),
            status=Donation.Status.REGISTERED,
        )

        self.allocation = create_allocation(
            donation=self.donation,
            project=self.project,
            amount=Decimal('100.00'),
        )
        self.expense = create_expense(
            allocation=self.allocation,
            amount=Decimal('25.00'),
            reason='@payload gasto',
        )

    def _role_user(self, username, role_name):
        user = get_user_model().objects.create_user(username=username, password='pass-12345')
        user.groups.add(Group.objects.get(name=role_name))
        return user

    def test_project_csv_escapes_malicious_name_and_location(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('project_export_csv'), {'q': 'PRJ-CSV-001'})
        rows = _parse_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="proyectos.csv"',
        )
        self.assertEqual(
            rows[0],
            [
                'Código',
                'Nombre',
                'Estado',
                'Visibilidad',
                'Presupuesto USD',
                'Inicio',
                'Cierre',
                'Ubicación',
            ],
        )
        data_row = next(row for row in rows[1:] if row[0] == 'PRJ-CSV-001')
        self.assertEqual(data_row[1], "'=SUM(A1:A2)")
        self.assertEqual(data_row[7], "'+CMD()")
        self.assertEqual(data_row[4], str(self.project.estimated_budget))
        self.assertFalse(data_row[4].startswith("'"))
        self.assertEqual(data_row[5], '2026-01-15')
        _assert_no_raw_dangerous_text_cells(rows, text_column_indexes=(1, 7))

    def test_donation_csv_escapes_malicious_institution_name(self):
        self.client.force_login(self.auditor)
        response = self.client.get(
            reverse('donation_export_csv'),
            {'status': Donation.Status.RECEIVED},
        )
        rows = _parse_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="donaciones.csv"',
        )
        self.assertEqual(
            rows[0],
            [
                'Código',
                'Institución donante',
                'Monto',
                'Moneda',
                'Estado',
                'Compromiso',
                'Recepción',
            ],
        )
        data_row = next(row for row in rows[1:] if row[0] == 'DON-CSV-001')
        self.assertEqual(data_row[1], "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(data_row[2], '1000.50')
        self.assertFalse(data_row[2].startswith("'"))
        codes = {row[0] for row in rows[1:]}
        self.assertIn('DON-CSV-001', codes)
        self.assertNotIn('DON-CSV-002', codes)
        _assert_no_raw_dangerous_text_cells(rows, text_column_indexes=(1,))

    def test_allocation_csv_escapes_pass_through_and_keeps_amount(self):
        self.client.force_login(self.auditor)
        response = self.client.get(
            reverse('allocation_export_csv'),
            {'q': self.allocation.code},
        )
        rows = _parse_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            rows[0],
            [
                'Código',
                'Donación',
                'Proyecto',
                'Monto USD',
                'Estado',
                'Ejecución',
                'Fecha',
                'Categoría',
            ],
        )
        data_row = next(row for row in rows[1:] if row[0] == self.allocation.code)
        self.assertEqual(data_row[1], self.donation.code)
        self.assertEqual(data_row[2], self.project.code)
        self.assertEqual(data_row[3], '100.00')
        self.assertFalse(data_row[3].startswith("'"))

    def test_expense_csv_escapes_malicious_reason(self):
        self.client.force_login(self.auditor)
        response = self.client.get(
            reverse('expense_export_csv'),
            {'q': self.expense.code},
        )
        rows = _parse_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            rows[0],
            [
                'Código',
                'Proyecto',
                'Asignación',
                'Motivo',
                'Monto',
                'Moneda',
                'Estado',
                'Fecha',
            ],
        )
        data_row = next(row for row in rows[1:] if row[0] == self.expense.code)
        self.assertEqual(data_row[3], "'@payload gasto")
        self.assertEqual(data_row[4], '25.00')
        self.assertFalse(data_row[4].startswith("'"))
        _assert_no_raw_dangerous_text_cells(rows, text_column_indexes=(3,))

    def test_project_csv_respects_list_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('project_export_csv'),
            {'status': Project.Status.ACTIVE},
        )
        rows = _parse_csv(response)
        codes = {row[0] for row in rows[1:]}
        self.assertIn('PRJ-CSV-001', codes)
        self.assertNotIn('PRJ-CSV-002', codes)

    def test_authorized_roles_receive_exports(self):
        cases = (
            (self.superuser, 'project_export_csv', 'proyectos.csv'),
            (self.admin, 'donation_export_csv', 'donaciones.csv'),
            (self.auditor, 'expense_export_csv', 'gastos.csv'),
            (self.operator, 'project_export_csv', 'proyectos.csv'),
        )
        for user, url_name, filename in cases:
            with self.subTest(user=user.username, url_name=url_name):
                self.client.force_login(user)
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
                self.assertEqual(
                    response['Content-Disposition'],
                    f'attachment; filename="{filename}"',
                )

    def test_operator_denied_financial_exports(self):
        for url_name in ('donation_export_csv', 'allocation_export_csv', 'expense_export_csv'):
            with self.subTest(url_name=url_name):
                self.client.force_login(self.operator)
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 403)

    def test_unauthorized_user_denied(self):
        self.client.force_login(self.unauthorized)
        response = self.client.get(reverse('project_export_csv'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse('project_export_csv'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
