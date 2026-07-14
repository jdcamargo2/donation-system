"""
Pruebas del comando verify_restored_data (solo lectura).

PRE: usa TemporaryDirectory + override_settings(MEDIA_ROOT) y mocks de
     conexion PostgreSQL cuando hace falta; no toca produccion.
POST: cubre estado valido, archivo ausente, adjunto Kobo inconsistente y
      trigger ausente.
"""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboFormDefinition,
    KoboSubmission,
)
from apps.operations.models import (
    AuditLog,
    OperationalCodeSequence,
    ProjectDocument,
    SupportingDocument,
)
from apps.operations.tests.helpers import (
    create_allocation,
    create_donation,
    create_expense,
    create_institution,
    create_project,
)

COMMAND_MODULE = 'apps.operations.management.commands.verify_restored_data'


def _build_trigger_connection(*, installed: bool, vendor: str = 'postgresql'):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [(installed,)]
    mock_connection = MagicMock()
    mock_connection.vendor = vendor
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = False
    return mock_connection


class VerifyRestoredDataCommandTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / 'media'
        self.media_root.mkdir()
        self.media_override = override_settings(MEDIA_ROOT=str(self.media_root))
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        # Las secuencias se siembran en migraciones; asegurar presencia sin duplicar.
        if not OperationalCodeSequence.objects.exists():
            OperationalCodeSequence.objects.create(
                namespace='donation',
                prefix='DON',
                next_value=1,
            )
        self.institution = create_institution()
        self.project = create_project()
        create_donation(donor=self.institution)
        AuditLog.objects.create(
            user=None,
            action=AuditLog.Action.CREATED,
            model_name='Institution',
            entity_id=str(self.institution.pk),
            entity_label=self.institution.name,
            summary='Institution created.',
        )

    def _run(self, *, trigger_installed=True):
        out = StringIO()
        with patch(
            f'{COMMAND_MODULE}.connection',
            _build_trigger_connection(installed=trigger_installed),
        ):
            call_command('verify_restored_data', stdout=out)
        return out.getvalue()

    def test_valid_restored_state(self):
        document = ProjectDocument.objects.create(
            project=self.project,
            document_type=ProjectDocument.DocumentType.WORK_PLAN,
            title='Plan operativo',
            file=SimpleUploadedFile('plan.pdf', b'plan-data', content_type='application/pdf'),
        )

        output = self._run()

        self.assertIn('Datos restaurados consistentes.', output)
        self.assertIn('FileField comprobados:', output)
        self.assertNotIn(document.file.name, output)

    def test_missing_file_reference_fails(self):
        expense = create_expense(
            allocation=create_allocation(
                donation=create_donation(code='DON-MISS-FILE', donor=self.institution),
                project=self.project,
            )
        )
        supporting = SupportingDocument(
            expense=expense,
            title='Soporte ausente',
        )
        supporting.document.name = 'supporting_documents/2026/07/missing.pdf'
        supporting.save()

        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            with patch(
                f'{COMMAND_MODULE}.connection',
                _build_trigger_connection(installed=True),
            ):
                call_command('verify_restored_data', stdout=out)

        self.assertIn('FileField faltantes', str(ctx.exception))
        self.assertNotIn('missing.pdf', out.getvalue())

    def test_kobo_downloaded_without_file_fails(self):
        form = KoboFormDefinition.objects.create(
            form_id='aAAAAAAA',
            title='Form',
            version='v1',
        )
        submission = KoboSubmission.objects.create(
            form_definition=form,
            external_id='ext-1',
            raw_payload={'_uuid': 'ext-1'},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
        )
        attachment = KoboAttachment(
            submission=submission,
            field_name='photo',
            status=KoboAttachment.Status.DOWNLOADED,
        )
        attachment.file.name = 'kobo/attachments/gone.jpg'
        attachment.save()

        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            with patch(
                f'{COMMAND_MODULE}.connection',
                _build_trigger_connection(installed=True),
            ):
                call_command('verify_restored_data', stdout=out)

        self.assertIn('Kobo DOWNLOADED', str(ctx.exception))
        self.assertNotIn('gone.jpg', out.getvalue())

    def test_missing_trigger_fails(self):
        with self.assertRaises(CommandError) as ctx:
            self._run(trigger_installed=False)

        self.assertIn('trigger append-only ausente', str(ctx.exception))
