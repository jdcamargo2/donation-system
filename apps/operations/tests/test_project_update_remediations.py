from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.operations.models import (
    AuditLog,
    Project,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
    ProjectUpdateRemediationError,
)
from apps.operations.services import (
    add_project_update_remediation_attachment,
    create_project_update_remediation,
    create_project_update_review,
    create_project_update_review_decision,
    delete_project_update_remediation_attachment,
    publish_project_update,
    register_advance,
    resolve_project_update_remediation,
    submit_project_update_remediation,
    update_project_update_remediation,
)
from apps.operations.tests.helpers import create_project, create_user


class ProjectUpdateRemediationTests(TestCase):
    def setUp(self):
        self.author = create_user('remediation-author')
        self.reviewer = create_user('remediation-reviewer')
        self.decider = create_user('remediation-decider')
        self.project = create_project(code='PRJ-REMEDIATION')
        self.project.status = Project.Status.ACTIVE
        self.project.save(update_fields=('status',))

    def decision(self, outcome='observed'):
        update = register_advance(self.project.pk, 'Avance observado', 'Contenido.', created_by=self.author)
        publish_project_update(update.pk, self.author)
        review = create_project_update_review(update_id=update.pk, observations='Revisión.', actor=self.reviewer)
        return create_project_update_review_decision(
            review_id=review.pk, outcome=outcome, rationale='Fundamento.', actor=self.decider
        )

    def test_observed_allows_one_remediation_and_conforming_rejects(self):
        observed = self.decision()
        remediation = create_project_update_remediation(decision_id=observed.pk, response='Respuesta.', actor=self.author)
        self.assertEqual(remediation.status, ProjectUpdateRemediation.Status.DRAFT)
        with self.assertRaises(ProjectUpdateRemediationError):
            create_project_update_remediation(decision_id=observed.pk, response='Duplicado.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            create_project_update_remediation(decision_id=self.decision('conforming').pk, response='No procede.', actor=self.author)

    def test_draft_can_change_response_and_attachments_then_submit(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Inicial.', actor=self.author)
        update_project_update_remediation(remediation_id=remediation.pk, response='Corregida.', actor=self.author)
        attachment = add_project_update_remediation_attachment(
            remediation_id=remediation.pk, title='Evidencia', file=SimpleUploadedFile('proof.pdf', b'proof'), actor=self.author
        )
        submitted = submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        self.assertEqual(submitted.status, ProjectUpdateRemediation.Status.SUBMITTED)
        with self.assertRaises(ProjectUpdateRemediationError):
            update_project_update_remediation(remediation_id=remediation.pk, response='Tardía.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            delete_project_update_remediation_attachment(attachment_id=attachment.pk, actor=self.author)

    def test_resolution_requires_submitted_terminal_status_and_notes(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Respuesta.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes='Notas.', actor=self.reviewer)
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        with self.assertRaises(ValidationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes=' ', actor=self.reviewer)
        resolved = resolve_project_update_remediation(remediation_id=remediation.pk, status='accepted', resolution_notes='Conforme.', actor=self.reviewer)
        self.assertEqual(resolved.status, ProjectUpdateRemediation.Status.ACCEPTED)
        with self.assertRaises(ProjectUpdateRemediationError):
            resolve_project_update_remediation(remediation_id=remediation.pk, status='rejected', resolution_notes='Otra.', actor=self.reviewer)

    def test_anonymous_actor_and_bulk_mutation_fail_without_partial_changes(self):
        remediation = create_project_update_remediation(decision_id=self.decision().pk, response='Respuesta.', actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            submit_project_update_remediation(remediation_id=remediation.pk, actor=AnonymousUser())
        submit_project_update_remediation(remediation_id=remediation.pk, actor=self.author)
        with self.assertRaises(ProjectUpdateRemediationError):
            ProjectUpdateRemediation.objects.filter(pk=remediation.pk).update(response='Evasión.')
        remediation.refresh_from_db()
        self.assertEqual(remediation.response, 'Respuesta.')
        self.assertTrue(AuditLog.objects.filter(entity_id=str(remediation.pk)).exists())
