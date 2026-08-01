from decimal import Decimal
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.operations.models import AuditLog, Donation, Expense, FundAllocation, Project
from apps.operations.pagination import build_pagination_page_numbers, parse_page_size
from apps.operations.tests.helpers import (
    TEST_DATE,
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
    create_user,
)

SIGEDON_CSS = Path(__file__).resolve().parents[2] / 'static' / 'web' / 'css' / 'sigedon.css'

# Each known AuditLog action must resolve to a dedicated semantic CSS class.
AUDIT_ACTION_SEMANTIC_CLASSES = {
    AuditLog.Action.CREATED: 'ops-audit-action-created',
    AuditLog.Action.UPDATED: 'ops-audit-action-updated',
    AuditLog.Action.VALIDATED: 'ops-audit-action-validated',
    AuditLog.Action.REJECTED: 'ops-audit-action-rejected',
    AuditLog.Action.ANNULLED: 'ops-audit-action-annulled',
    AuditLog.Action.ASSIGNED: 'ops-audit-action-assigned',
    AuditLog.Action.EXECUTED: 'ops-audit-action-executed',
    AuditLog.Action.CLOSED: 'ops-audit-action-closed',
    AuditLog.Action.EXPENSE_CANCELLED: 'ops-audit-action-expense_cancelled',
    AuditLog.Action.PUBLISHED: 'ops-audit-action-published',
    AuditLog.Action.UNPUBLISHED: 'ops-audit-action-unpublished',
    AuditLog.Action.COMPLETED: 'ops-audit-action-completed',
    AuditLog.Action.REOPENED: 'ops-audit-action-reopened',
    AuditLog.Action.REORDERED: 'ops-audit-action-reordered',
    AuditLog.Action.DELETED: 'ops-audit-action-deleted',
}


def _extract_css_rule_block(css_text, selector_fragment, *, exact_selector=False):
    """
    PRE: css_text is the SIGEDON stylesheet and selector_fragment appears in a rule.
    POST: returns the first matching rule body, or raises AssertionError.
    """
    if exact_selector:
        pattern = re.compile(
            rf'(?m)^\s*{re.escape(selector_fragment)}\s*\{{([^}}]+)\}}',
        )
    else:
        pattern = re.compile(
            rf'[^{{}}]*{re.escape(selector_fragment)}[^{{}}]*\{{([^}}]+)\}}',
            re.MULTILINE,
        )
    match = pattern.search(css_text)
    if match is None:
        raise AssertionError(f'No CSS rule found for selector fragment {selector_fragment!r}')
    return match.group(1)


def _rule_declares_light_text_on_soft_background(rule_body):
    """
    PRE: rule_body is a CSS declarations block for an audit action badge.
    POST: returns True when the rule uses white/near-white text (low contrast risk).
    """
    color_match = re.search(r'(?<![\w-])color\s*:\s*([^;]+);', rule_body)
    if color_match is None:
        return False
    color = color_match.group(1).strip().lower()
    return color in {'#fff', '#ffffff', 'white', '#f8fafc', '#f4f7fb', '#eef2f6', '#eef2f7'}


def _rule_text_color(rule_body):
    """
    PRE: rule_body is a CSS declarations block.
    POST: returns the declared text color value, or raises AssertionError.
    """
    color_match = re.search(r'(?<![\w-])color\s*:\s*([^;]+);', rule_body)
    if color_match is None:
        raise AssertionError('Rule does not declare a text color')
    return color_match.group(1).strip().lower()


class ParsePageSizeTests(TestCase):
    def test_defaults_and_allowed_values(self):
        self.assertEqual(parse_page_size({}), 20)
        self.assertEqual(parse_page_size({'page_size': '20'}), 20)
        self.assertEqual(parse_page_size({'page_size': '50'}), 50)
        self.assertEqual(parse_page_size({'page_size': '100'}), 100)

    def test_invalid_values_fall_back_to_default(self):
        for raw in ('', '0', '19', '21', '200', 'abc', '-1', '20.5'):
            with self.subTest(raw=raw):
                self.assertEqual(parse_page_size({'page_size': raw}), 20)


class BuildPaginationPageNumbersTests(TestCase):
    def test_inserts_ellipsis_for_distant_pages(self):
        class FakePaginator:
            num_pages = 10

        class FakePage:
            number = 5
            paginator = FakePaginator()

        self.assertEqual(
            build_pagination_page_numbers(FakePage()),
            [1, None, 3, 4, 5, 6, 7, None, 10],
        )


class AuditTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.donor = create_institution()
        self.project = create_project()
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.donation = create_donation(donor=self.donor, amount=Decimal('100.00'))
        self.allocation = create_allocation(
            donation=self.donation, project=self.project, amount=Decimal('50.00')
        )
        self.expense = create_expense(allocation=self.allocation, amount=Decimal('10.00'))

    def test_creating_donation_records_audit_log(self):
        self.client.post(
            reverse('donation_create'),
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '200.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )

        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATED, model_name='Donación'
            ).exists()
        )

    def test_updating_donation_records_audit_log(self):
        self.client.post(
            reverse('donation_update', args=[self.donation.pk]),
            data={
                'donor': self.donor.pk,
                'donation_type': 'goods',
                'amount': '120.00',
                'currency': 'USD',
                'objective': 'Apoyar atención de emergencia',
                'restrictions': '',
                'commitment_date': '',
                'received_date': '',
                'status': Donation.Status.RECEIVED,
                'support_reference': '',
            },
        )

        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATED, model_name='Donación'
            ).exists()
        )

    def test_creating_and_updating_allocation_records_audit_logs(self):
        self.client.post(
            reverse('allocation_create'),
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': 'health_psychosocial',
                'amount': '20.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            },
        )
        self.client.post(
            reverse('allocation_update', args=[self.allocation.pk]),
            data={
                'donation': self.donation.pk,
                'project': self.project.pk,
                'budget_category': self.allocation.budget_category,
                'amount': '55.00',
                'responsible_person': '',
                'allocation_date': TEST_DATE,
                'status': FundAllocation.Status.ACTIVE,
                'notes': '',
            },
        )

        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.ASSIGNED, model_name='Asignación de fondos'
            ).count(),
            2,
        )

    def test_creating_and_updating_expense_records_audit_logs(self):
        self.client.post(
            reverse('expense_create'),
            data={
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '5.00',
                'currency': 'USD',
                'reason': 'New expense',
                'provider_or_recipient': 'Provider A',
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'support_file': SimpleUploadedFile('create.pdf', b'%PDF soporte'),
            },
        )
        self.client.post(
            reverse('expense_update', args=[self.expense.pk]),
            data={
                'allocation': self.allocation.pk,
                'expense_date': TEST_DATE,
                'category': 'food',
                'amount': '15.00',
                'currency': 'USD',
                'reason': self.expense.reason,
                'provider_or_recipient': self.expense.provider_or_recipient,
                'payment_method': 'bank_transfer',
                'description': '',
                'observations': '',
                'support_file': SimpleUploadedFile('update.pdf', b'%PDF soporte'),
            },
        )

        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.EXECUTED, model_name='Gasto'
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATED, model_name='Gasto'
            ).count(),
            1,
        )

    def test_delete_audit_logging_is_not_implemented_yet(self):
        # TODO: Add delete audit assertions when delete views create AuditLog records.
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.ANNULLED).exists())

    def test_legacy_model_names_are_displayed_in_spanish(self):
        cases = [
            ('Donation', 'Donación'),
            ('Project', 'Proyecto'),
            ('Institution', 'Institución'),
            ('Fund Allocation', 'Asignación de fondos'),
            ('Expense', 'Gasto'),
        ]

        for index, (legacy_name, expected_name) in enumerate(cases, start=1):
            AuditLog.objects.create(
                action=AuditLog.Action.CREATED,
                model_name=legacy_name,
                entity_id=str(index),
                entity_label=f'Entidad {index}',
                summary='Record updated.',
            )

        response = self.client.get(reverse('audit_log_list'))

        for legacy_name, expected_name in cases:
            with self.subTest(legacy_name=legacy_name):
                self.assertContains(response, expected_name)
                self.assertNotContains(response, f'Creada {legacy_name}')

    def test_legacy_audit_summary_is_displayed_in_spanish(self):
        AuditLog.objects.create(
            action=AuditLog.Action.CREATED,
            model_name='Project',
            entity_id='1',
            entity_label='PRJ-001',
            summary='Project created.',
        )

        response = self.client.get(reverse('audit_log_list'))

        self.assertContains(response, 'Proyecto creado.')
        self.assertNotContains(response, 'Project created.')


class AuditLogActionBadgeContrastTests(TestCase):
    def setUp(self):
        self.user = create_user(username='audit-badge-contrast')
        self.client.force_login(self.user)
        self.url = reverse('audit_log_list')
        self.css_text = SIGEDON_CSS.read_text(encoding='utf-8')

    def test_inventory_covers_every_auditlog_action_choice(self):
        self.assertEqual(
            set(AUDIT_ACTION_SEMANTIC_CLASSES),
            {choice.value for choice in AuditLog.Action},
        )

    def test_each_known_action_renders_semantic_class_and_visible_label(self):
        for index, (action, css_class) in enumerate(AUDIT_ACTION_SEMANTIC_CLASSES.items(), start=1):
            AuditLog.objects.create(
                user=self.user,
                action=action,
                model_name='Hito de proyecto',
                entity_id=str(index),
                entity_label=f'MILESTONE-{index:02d}',
                summary=f'Evento de prueba para {action}.',
            )

        response = self.client.get(self.url, {'page_size': '100'})
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        for action, css_class in AUDIT_ACTION_SEMANTIC_CLASSES.items():
            with self.subTest(action=action):
                label = AuditLog.Action(action).label
                self.assertIn(css_class, content)
                self.assertIn(str(label), content)
                self.assertIn(
                    f'class="badge ops-audit-action {css_class}"',
                    content,
                )

    def test_reopened_uses_dedicated_class_not_only_generic_fallback(self):
        AuditLog.objects.create(
            user=self.user,
            action=AuditLog.Action.REOPENED,
            model_name='Hito de proyecto',
            entity_id='reopened-1',
            entity_label='MILESTONE-REOPENED',
            summary='Hito reabierto.',
        )

        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertContains(response, 'ops-audit-action-reopened')
        self.assertContains(response, 'Reabierta')
        self.assertIn('ops-audit-action-reopened', self.css_text)
        reopened_rule = _extract_css_rule_block(self.css_text, '.ops-audit-action-reopened')
        self.assertIn(
            'class="badge ops-audit-action ops-audit-action-reopened"',
            content,
        )
        self.assertFalse(_rule_declares_light_text_on_soft_background(reopened_rule))
        self.assertNotIn(_rule_text_color(reopened_rule), {'#fff', '#ffffff', 'white'})

    def test_known_action_css_rules_avoid_white_text_on_light_background(self):
        for action, css_class in AUDIT_ACTION_SEMANTIC_CLASSES.items():
            with self.subTest(action=action, css_class=css_class):
                self.assertIn(f'.{css_class}', self.css_text)
                rule = _extract_css_rule_block(self.css_text, f'.{css_class}')
                self.assertFalse(
                    _rule_declares_light_text_on_soft_background(rule),
                    msg=f'{css_class} declares light text unsuitable for soft backgrounds',
                )
                self.assertRegex(rule, r'background\s*:')
                self.assertRegex(rule, r'(?<!-)color\s*:')

    def test_fallback_style_is_readable_for_unknown_action(self):
        unknown_action = 'mystery_action'
        AuditLog.objects.create(
            user=self.user,
            action=unknown_action,
            model_name='Entidad experimental',
            entity_id='unknown-1',
            entity_label='OBJ-UNKNOWN',
            summary='Acción desconocida simulada.',
        )

        response = self.client.get(self.url)
        content = response.content.decode()
        fallback_rule = _extract_css_rule_block(
            self.css_text,
            '.ops-audit-action',
            exact_selector=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('ops-audit-action-mystery_action', content)
        self.assertIn('mystery_action', content)
        self.assertIn('background:', fallback_rule)
        self.assertIn('border:', fallback_rule)
        self.assertIn('color:', fallback_rule)
        self.assertFalse(_rule_declares_light_text_on_soft_background(fallback_rule))
        fallback_color = _rule_text_color(fallback_rule)
        self.assertNotIn(fallback_color, {'#fff', '#ffffff', 'white', '#eef2f6', '#eef2f7'})
        self.assertTrue(fallback_color.startswith('#'))

    def test_audit_table_header_uses_solid_background_without_gradient(self):
        header_rule = _extract_css_rule_block(self.css_text, '.ops-audit-table thead th')
        self.assertIn('background:', header_rule)
        self.assertNotIn('linear-gradient', header_rule)
        self.assertNotIn('gradient', header_rule.lower())


class AuditLogListPaginationTests(TestCase):
    def setUp(self):
        self.user = create_user(username='audit-pager')
        self.client.force_login(self.user)
        self.url = reverse('audit_log_list')
        self.logs = []
        for index in range(45):
            log = AuditLog.objects.create(
                user=self.user,
                action=AuditLog.Action.CREATED if index % 2 == 0 else AuditLog.Action.UPDATED,
                model_name='Proyecto',
                entity_id=str(index + 1),
                entity_label=f'PRJ-PAGE-{index:03d}',
                summary=f'Registro de prueba {index:03d}.',
            )
            self.logs.append(log)

    def test_list_defaults_to_twenty_newest_first_with_stable_secondary_order(self):
        response = self.client.get(self.url)
        page_logs = list(response.context['logs'])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(page_logs), 20)
        self.assertEqual(response.context['page_size'], 20)
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(page_logs[0].entity_label, 'PRJ-PAGE-044')
        self.assertEqual(page_logs[-1].entity_label, 'PRJ-PAGE-025')
        created_pairs = [(log.created_at, log.pk) for log in page_logs]
        self.assertEqual(created_pairs, sorted(created_pairs, reverse=True))
        self.assertContains(response, 'Mostrando 1–20 de 45')
        self.assertContains(response, 'ops-audit-table')
        self.assertContains(response, '<th>Fecha y hora</th>')
        self.assertContains(response, '<th>Usuario</th>')
        self.assertContains(response, 'ops-audit-action-cell')
        self.assertContains(response, '<th>Objeto</th>')
        self.assertContains(response, '<th>Detalle</th>')
        self.assertNotContains(response, '<th>Modelo</th>')
        self.assertNotContains(response, '<th>Descripción</th>')
        self.assertContains(response, 'ops-audit-action-created')
        self.assertContains(response, 'Creada')
        self.assertContains(response, 'aria-label="Paginación del listado"')

    def test_next_page_continues_without_duplicates_or_gaps(self):
        first_page = self.client.get(self.url)
        second_page = self.client.get(self.url, {'page': '2'})
        first_labels = [log.entity_label for log in first_page.context['logs']]
        second_labels = [log.entity_label for log in second_page.context['logs']]

        self.assertEqual(
            second_labels,
            [f'PRJ-PAGE-{index:03d}' for index in range(24, 4, -1)],
        )
        self.assertEqual(len(set(first_labels) & set(second_labels)), 0)
        self.assertContains(second_page, 'Mostrando 21–40 de 45')
        self.assertContains(second_page, 'page_size=20')

    def test_page_size_options_and_invalid_fallback(self):
        for size in (20, 50, 100):
            with self.subTest(size=size):
                response = self.client.get(self.url, {'page_size': str(size)})
                self.assertEqual(response.context['page_size'], size)
                self.assertEqual(len(response.context['logs']), min(size, 45))

        invalid = self.client.get(self.url, {'page_size': '999'})
        self.assertEqual(invalid.context['page_size'], 20)
        self.assertEqual(len(invalid.context['logs']), 20)

    def test_filters_and_page_size_are_preserved_while_paging(self):
        params = {
            'q': 'PRJ-PAGE-0',
            'status': AuditLog.Action.CREATED,
            'date_from': '2020-01-01',
            'date_to': '2030-12-31',
            'page_size': '50',
            'page': '1',
        }
        response = self.client.get(self.url, params)
        content = response.content.decode()

        self.assertEqual(response.context['page_size'], 50)
        self.assertTrue(all(log.action == AuditLog.Action.CREATED for log in response.context['logs']))
        self.assertIn('name="page_size" value="50"', content)
        self.assertIn('name="q" value="PRJ-PAGE-0"', content)
        self.assertIn(f'value="{AuditLog.Action.CREATED}" selected', content)
        pagination_query = response.context['pagination_query']
        self.assertIn('page_size=50', pagination_query)
        self.assertIn('q=PRJ-PAGE-0', pagination_query)
        self.assertIn(f'status={AuditLog.Action.CREATED}', pagination_query)
        self.assertNotIn('page=', pagination_query)

    def test_filter_form_omits_page_so_search_restarts_at_first_page(self):
        response = self.client.get(
            self.url,
            {'q': 'PRJ-PAGE', 'page': '2', 'page_size': '20'},
        )
        content = response.content.decode()
        filter_markup = content.split('ops-list-filters', 1)[1].split('</form>', 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="page_size" value="20"', filter_markup)
        self.assertNotIn('name="page"', filter_markup)

    def test_list_query_count_stays_bounded(self):
        with self.assertNumQueries(4):
            response = self.client.get(self.url, {'page_size': '20'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['logs']), 20)

    def test_permission_required_remains_enforced(self):
        limited = get_user_model().objects.create_user(
            username='no-audit-pagination', password='pass-12345'
        )
        self.client.force_login(limited)

        self.assertEqual(self.client.get(self.url).status_code, 403)
