"""Shared size validation for operational private uploads."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def private_upload_max_bytes() -> int:
    """
    PRE: Django settings loaded with SIGEDON_MAX_PRIVATE_UPLOAD_BYTES.
    POST: returns the configured positive per-file byte limit.
    """
    return int(settings.SIGEDON_MAX_PRIVATE_UPLOAD_BYTES)


def validate_private_upload_size(uploaded_file) -> None:
    """
    PRE: uploaded_file is a Django UploadedFile/FieldFile or None.
    POST: raises ValidationError when size exceeds the shared operational limit;
          never echoes filesystem paths or storage names.
    """
    if uploaded_file in (None, ''):
        return
    size = getattr(uploaded_file, 'size', None)
    if size is None:
        return
    max_bytes = private_upload_max_bytes()
    if size > max_bytes:
        raise ValidationError(
            _(
                'El archivo supera el tamaño máximo permitido '
                '(%(max_mib)s MiB).'
            )
            % {'max_mib': max(1, max_bytes // (1024 * 1024))}
        )


def attach_private_upload_validator(field) -> None:
    """
    PRE: field is a forms.FileField (or subclass) on an operational upload form.
    POST: ensures validate_private_upload_size runs without duplicating entries.
    """
    validators = list(getattr(field, 'validators', []) or [])
    if validate_private_upload_size not in validators:
        validators.append(validate_private_upload_size)
    field.validators = validators
