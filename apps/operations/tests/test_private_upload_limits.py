"""Operational private upload size policy tests."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.operations.forms import (
    ExpenseRequestAttachmentForm,
    InstitutionForm,
    ProjectDocumentForm,
    ProjectUpdateAttachmentForm,
    ProjectUpdateRemediationAttachmentForm,
    SupportingDocumentForm,
)
from apps.operations.upload_limits import validate_private_upload_size


@override_settings(SIGEDON_MAX_PRIVATE_UPLOAD_BYTES=1024)
class PrivateUploadLimitTests(SimpleTestCase):
    def _file(self, size: int, name: str = 'doc.bin'):
        return SimpleUploadedFile(name, b'x' * size, content_type='application/octet-stream')

    def test_validator_accepts_exact_max(self):
        validate_private_upload_size(self._file(1024))

    def test_validator_rejects_max_plus_one(self):
        with self.assertRaises(ValidationError) as raised:
            validate_private_upload_size(self._file(1025))
        self.assertIn('tamaño máximo', str(raised.exception))
        self.assertNotIn('/tmp', str(raised.exception))

    def test_upload_forms_wire_shared_validator(self):
        forms = (
            InstitutionForm(),
            ProjectDocumentForm(),
            SupportingDocumentForm(),
            ProjectUpdateRemediationAttachmentForm(),
            ProjectUpdateAttachmentForm(),
            ExpenseRequestAttachmentForm(),
        )
        for form in forms:
            with self.subTest(form=type(form).__name__):
                file_fields = [
                    field
                    for field in form.fields.values()
                    if hasattr(field, 'validators')
                    and any(
                        getattr(item, '__name__', '') == 'validate_private_upload_size'
                        or item is validate_private_upload_size
                        for item in field.validators
                    )
                ]
                self.assertTrue(file_fields, msg=type(form).__name__)

    def test_multiple_file_forms_enforce_per_file_cap(self):
        forms = (
            ProjectUpdateAttachmentForm(
                data={},
                files={'files': [self._file(10, 'a.bin'), self._file(1025, 'b.bin')]},
            ),
            ExpenseRequestAttachmentForm(
                data={'title': 'Adjunto'},
                files={'files': [self._file(10, 'a.bin'), self._file(1025, 'b.bin')]},
            ),
        )
        for form in forms:
            with self.subTest(form=type(form).__name__):
                self.assertFalse(form.is_valid())
                joined = ' '.join(str(err) for err in form.errors.values())
                self.assertIn('tamaño máximo', joined)
                self.assertNotIn('/tmp', joined)
                self.assertNotIn('media/', joined)

    def test_single_file_form_rejects_oversized(self):
        form = ProjectUpdateRemediationAttachmentForm(
            data={'title': 'x'},
            files={'file': self._file(1025)},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('tamaño máximo', str(form.errors))

    def test_small_valid_file_accepted(self):
        form = ProjectUpdateRemediationAttachmentForm(
            data={'title': 'ok'},
            files={'file': self._file(16, 'ok.bin')},
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_helper_attaches_validator(self):
        from django import forms as django_forms
        from apps.operations.admin import _configure_private_file_formfield

        field = django_forms.FileField()
        configured = _configure_private_file_formfield(field)
        self.assertIn(validate_private_upload_size, configured.validators)
