"""HTML contract tests for operational required-field indicators."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.operations.models import Project, ProjectUpdateReviewDecision
from apps.operations.services import (
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    publish_project_update,
    register_advance,
    submit_project_update_remediation,
)
from apps.operations.tests.helpers import create_expense, create_project, create_user


class _LabelInventory(HTMLParser):
    """Collect label elements keyed by their ``for`` attribute."""

    def __init__(self):
        super().__init__()
        self.labels: dict[str, dict] = {}
        self._for_id: str | None = None
        self._depth = 0
        self._mark_count = 0
        self._mark_aria_hidden: list[str | None] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'label':
            if self._for_id is None:
                self._for_id = attrs_dict.get('for')
                self._depth = 1
                self._mark_count = 0
                self._mark_aria_hidden = []
                self._text_parts = []
                return
            self._depth += 1
            return
        if self._for_id is None:
            return
        if tag == 'span':
            classes = (attrs_dict.get('class') or '').split()
            if 'required-mark' in classes:
                self._mark_count += 1
                self._mark_aria_hidden.append(attrs_dict.get('aria-hidden'))

    def handle_endtag(self, tag):
        if self._for_id is None or tag != 'label':
            return
        self._depth -= 1
        if self._depth == 0:
            for_id = self._for_id
            self.labels[for_id] = {
                'mark_count': self._mark_count,
                'mark_aria_hidden': list(self._mark_aria_hidden),
                'text': ''.join(self._text_parts).strip(),
            }
            self._for_id = None

    def handle_data(self, data):
        if self._for_id is not None:
            self._text_parts.append(data)


def inventory_labels(html: str) -> dict[str, dict]:
    parser = _LabelInventory()
    parser.feed(html)
    parser.close()
    return parser.labels


def field_label(labels: dict[str, dict], field_name: str) -> dict | None:
    return labels.get(f'id_{field_name}')


def assert_required_marker(testcase: TestCase, labels: dict[str, dict], field_name: str):
    label = field_label(labels, field_name)
    testcase.assertIsNotNone(label, f'Expected visible label for {field_name}')
    testcase.assertEqual(
        label['mark_count'],
        1,
        f'Expected exactly one .required-mark inside label for {field_name}',
    )
    testcase.assertEqual(label['mark_aria_hidden'], ['true'])


def assert_optional_marker(testcase: TestCase, labels: dict[str, dict], field_name: str):
    label = field_label(labels, field_name)
    testcase.assertIsNotNone(label, f'Expected visible label for {field_name}')
    testcase.assertEqual(
        label['mark_count'],
        0,
        f'Expected no .required-mark inside label for {field_name}',
    )


def assert_no_field_label(testcase: TestCase, labels: dict[str, dict], field_name: str):
    testcase.assertIsNone(
        field_label(labels, field_name),
        f'Expected no label for hidden/absent field {field_name}',
    )


class RequiredFieldIndicatorObjectFormTests(TestCase):
    def setUp(self):
        self.user = create_user('required-marker-object')
        self.client.force_login(self.user)

    def test_object_form_required_and_optional_labels_use_shared_marker_contract(self):
        response = self.client.get(reverse('institution_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'web/object_form.html')
        html = response.content.decode()
        labels = inventory_labels(html)

        assert_required_marker(self, labels, 'name')
        assert_required_marker(self, labels, 'institution_type')
        assert_optional_marker(self, labels, 'legal_document')
        assert_optional_marker(self, labels, 'contact_email')

        # No hidden fields on this form; ensure markers live only inside labels.
        for for_id, label in labels.items():
            if label['mark_count']:
                self.assertEqual(label['mark_aria_hidden'], ['true'], for_id)

        self.assertContains(
            response,
            'Complete los datos requeridos. Los campos marcados con asterisco son obligatorios.',
        )

    def test_object_form_edit_preserves_required_markers(self):
        from apps.operations.tests.helpers import create_institution

        institution = create_institution(name='Marker Institution')
        response = self.client.get(reverse('institution_update', args=[institution.pk]))
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'name')
        assert_optional_marker(self, labels, 'legal_document')


class RequiredFieldIndicatorProjectDocumentTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user('required-marker-doc')
        self.project = create_project(code='PRJ-MARKER-DOC')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)

    def test_project_document_create_required_markers(self):
        response = self.client.get(reverse('project_document_create', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        labels = inventory_labels(html)

        assert_required_marker(self, labels, 'document_type')
        assert_required_marker(self, labels, 'title')
        assert_required_marker(self, labels, 'file')
        assert_optional_marker(self, labels, 'description')
        self.assertIn('class="ops-file-upload"', html)
        self.assertIn('data-file-upload-preview', html)

    def test_project_document_validation_redisplay_preserves_markers(self):
        response = self.client.post(
            reverse('project_document_create', args=[self.project.pk]),
            data={
                'document_type': 'proposal',
                'title': 'Sin archivo',
                'description': 'Falta archivo.',
            },
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'document_type')
        assert_required_marker(self, labels, 'title')
        assert_required_marker(self, labels, 'file')
        assert_optional_marker(self, labels, 'description')


class RequiredFieldIndicatorProjectUpdateTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user('required-marker-update')
        self.project = create_project(code='PRJ-MARKER-UPD')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)

    def test_project_update_create_reported_by_required_attachments_optional(self):
        response = self.client.get(
            reverse('project_update_create_for_project', args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'reported_by')
        assert_required_marker(self, labels, 'title')
        assert_optional_marker(self, labels, 'attachments')

    def test_operator_project_update_reported_by_visible_without_required_marker(self):
        from django.contrib.auth.models import Group

        from apps.operations.role_services import sync_operation_roles
        from apps.operations.roles import ROLE_FIELD_OPERATOR

        sync_operation_roles()
        from django.contrib.auth import get_user_model

        operator = get_user_model().objects.create_user(
            username='operador_required_marker', password='pass-12345'
        )
        operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))
        self.client.force_login(operator)

        response = self.client.get(
            reverse('project_update_create_for_project', args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        label = field_label(labels, 'reported_by')
        self.assertIsNotNone(label)
        self.assertIn('Persona responsable del avance', label['text'])
        assert_optional_marker(self, labels, 'reported_by')
        assert_required_marker(self, labels, 'title')

    def test_standalone_attachment_required_file_marker(self):
        update = register_advance(
            self.project.pk,
            'Avance adjuntos',
            'Contenido.',
            created_by=self.user,
            reported_by=self.user,
        )
        response = self.client.get(
            reverse('project_update_attachment_create', args=[update.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'files')


class RequiredFieldIndicatorSupportingDocumentTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user('required-marker-support')
        self.expense = create_expense(reason='Gasto con soporte marker')
        self.expense.allocation.project.status = Project.Status.ACTIVE
        self.expense.allocation.project.save(update_fields=('status',))
        self.client.force_login(self.user)

    def test_supporting_document_markers(self):
        response = self.client.get(
            reverse('supporting_document_create_for_expense', args=[self.expense.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'title')
        assert_required_marker(self, labels, 'document')
        assert_optional_marker(self, labels, 'notes')


class RequiredFieldIndicatorMilestoneTests(TestCase):
    def setUp(self):
        self.user = create_user('required-marker-milestone')
        self.project = create_project(code='PRJ-MARKER-MIL')
        self.client.force_login(self.user)

    def test_milestone_title_required_description_optional(self):
        response = self.client.get(reverse('project_milestone_add', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'title')
        assert_optional_marker(self, labels, 'description')


class RequiredFieldIndicatorReviewDecisionTests(TestCase):
    def setUp(self):
        self.user = create_user('required-marker-review')
        self.project = create_project(code='PRJ-MARKER-REV')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)
        self.update = register_advance(
            self.project.pk,
            'Avance a revisar',
            'Contenido publicado.',
            created_by=self.user,
            reported_by=self.user,
        )
        publish_project_update(self.update.pk, self.user)

    def test_review_observations_has_marker(self):
        response = self.client.get(
            reverse('project_update_review_create', args=[self.update.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'observations')

    def test_decision_outcome_and_rationale_have_markers(self):
        review = create_project_update_review(
            update_id=self.update.pk,
            observations='Observación del comité.',
            actor=self.user,
        )
        response = self.client.get(
            reverse('project_update_review_decision_create', args=[review.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'outcome')
        assert_required_marker(self, labels, 'rationale')


class RequiredFieldIndicatorRemediationTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = create_user('required-marker-remediation')
        self.project = create_project(code='PRJ-MARKER-REM')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))
        self.client.force_login(self.user)
        update = register_advance(
            self.project.pk,
            'Avance observado',
            'Contenido.',
            created_by=self.user,
            reported_by=self.user,
        )
        publish_project_update(update.pk, self.user)
        review = create_project_update_review(
            update_id=update.pk, observations='Revisión.', actor=self.user
        )
        self.decision = create_project_update_review_decision(
            review_id=review.pk,
            outcome=ProjectUpdateReviewDecision.Outcome.OBSERVED,
            rationale='Fundamento.',
            actor=self.user,
        )

    def test_remediation_create_response_has_marker(self):
        response = self.client.get(
            reverse('project_update_remediation_create', args=[self.decision.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'form.as_p')
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'response')

    def test_remediation_attachment_file_required_title_optional(self):
        remediation = create_project_update_remediation(
            decision_id=self.decision.pk, response='Respuesta.', actor=self.user
        )
        response = self.client.get(
            reverse('project_update_remediation_attachment_create', args=[remediation.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'file')
        assert_optional_marker(self, labels, 'title')

    def test_remediation_resolve_resolution_notes_has_marker(self):
        remediation = create_project_update_remediation(
            decision_id=self.decision.pk, response='Respuesta.', actor=self.user
        )
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.user)
        response = self.client.get(
            reverse('project_update_remediation_resolve', args=[remediation.pk])
        )
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'status')
        assert_required_marker(self, labels, 'resolution_notes')


class RequiredFieldIndicatorTerminalActionTests(TestCase):
    def setUp(self):
        self.user = create_user('required-marker-terminal')
        self.client.force_login(self.user)

    def test_reason_required_terminal_action_has_decorative_marker(self):
        from apps.operations.tests.helpers import create_donation

        donation = create_donation(code='DON-MARKER-ANNUL')
        response = self.client.get(reverse('donation_annul', args=[donation.pk]))
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_required_marker(self, labels, 'reason')

    def test_reasonless_terminal_action_has_no_visible_marker(self):
        project = create_project(code='PRJ-MARKER-FINISH')
        response = self.client.get(reverse('project_finish', args=[project.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        labels = inventory_labels(html)
        self.assertEqual(labels, {})
        self.assertNotIn('class="required-mark"', html)
        self.assertNotIn("class='required-mark'", html)


class RequiredFieldIndicatorNegativeScopeTests(TestCase):
    def setUp(self):
        self.user = create_user('required-marker-negative')
        self.client.force_login(self.user)

    def test_optional_institution_legal_document_has_no_marker(self):
        response = self.client.get(reverse('institution_create'))
        labels = inventory_labels(response.content.decode())
        assert_optional_marker(self, labels, 'legal_document')

    def test_expense_support_file_has_no_marker_when_not_field_required(self):
        response = self.client.get(reverse('expense_create'))
        self.assertEqual(response.status_code, 200)
        labels = inventory_labels(response.content.decode())
        assert_optional_marker(self, labels, 'support_file')

    def test_filter_search_forms_do_not_render_required_markers(self):
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        filter_match = re.search(
            r'<form class="card border-0 mb-3 ops-list-filters"[^>]*>.*?</form>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(filter_match)
        self.assertNotIn('required-mark', filter_match.group(0))


class RequiredFieldIndicatorSharedIncludeSourceTests(TestCase):
    def test_shared_label_include_contract(self):
        from pathlib import Path

        source = Path('templates/web/includes/ops_form_field_label.html').read_text()
        self.assertIn('field.field.required', source)
        self.assertIn('class="required-mark"', source)
        self.assertIn('aria-hidden="true"', source)
        self.assertIn('field.id_for_label', source)
        self.assertNotIn('aria-required', source)

    def test_object_form_uses_shared_label_include(self):
        from pathlib import Path

        source = Path('templates/web/object_form.html').read_text()
        self.assertIn('web/includes/ops_form_field_label.html', source)
        self.assertNotIn('required-mark', source)
