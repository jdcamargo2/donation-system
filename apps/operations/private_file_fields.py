"""Canonical inventory of SIGEDON private FileField surfaces.

PRE: Django app registry is ready when iterating instances.
POST: yields (model, field_name, upload_to_hint) without requiring network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.db import models


@dataclass(frozen=True)
class PrivateFileFieldSpec:
    """One canonical private FileField used by backup/migration tooling."""

    app_label: str
    model_name: str
    field_name: str
    upload_to_hint: str

    @property
    def label(self) -> str:
        return f'{self.app_label}.{self.model_name}.{self.field_name}'


# Keep in sync with every private FileField. Do not hardcode a single model.
PRIVATE_FILE_FIELD_SPECS: tuple[PrivateFileFieldSpec, ...] = (
    PrivateFileFieldSpec(
        'operations', 'Institution', 'legal_document', 'institution_documents/%Y/%m/'
    ),
    PrivateFileFieldSpec(
        'operations', 'ProjectDocument', 'file', 'project_documents/%Y/%m/'
    ),
    PrivateFileFieldSpec(
        'operations',
        'ProjectUpdateAttachment',
        'file',
        'project_update_attachments/%Y/%m/',
    ),
    PrivateFileFieldSpec(
        'operations',
        'ProjectUpdateRemediationAttachment',
        'file',
        'project_update_remediation_attachments/%Y/%m/',
    ),
    PrivateFileFieldSpec(
        'operations', 'SupportingDocument', 'document', 'supporting_documents/%Y/%m/'
    ),
    PrivateFileFieldSpec(
        'operations',
        'ExpenseRequestAttachment',
        'file',
        'expense_request_attachments/%Y/%m/',
    ),
    PrivateFileFieldSpec(
        'kobo', 'KoboAttachment', 'file', 'kobo/attachments/'
    ),
)


def iter_private_file_field_specs() -> tuple[PrivateFileFieldSpec, ...]:
    """
    PRE: none.
    POST: returns the frozen inventory of private FileField specs.
    """
    return PRIVATE_FILE_FIELD_SPECS


def get_model_for_spec(spec: PrivateFileFieldSpec):
    """
    PRE: app registry is populated.
    POST: returns the model class for the spec.
    """
    return apps.get_model(spec.app_label, spec.model_name)


def iter_referenced_storage_names():
    """
    PRE: database is reachable; models are migrated.
    POST: yields unique non-empty storage object names referenced by FileFields.
          Deduplicates identical keys. Never prints names to callers' stdout.
    """
    seen: set[str] = set()
    for spec in PRIVATE_FILE_FIELD_SPECS:
        model = get_model_for_spec(spec)
        field = model._meta.get_field(spec.field_name)
        if not isinstance(field, models.FileField):
            raise TypeError(f'{spec.label} is not a FileField')
        queryset = model.objects.exclude(**{f'{spec.field_name}__isnull': True}).exclude(
            **{f'{spec.field_name}': ''}
        )
        for instance in queryset.iterator():
            file_field = getattr(instance, spec.field_name)
            name = getattr(file_field, 'name', '') or ''
            if not name or name in seen:
                continue
            seen.add(name)
            yield name, spec, instance.pk
