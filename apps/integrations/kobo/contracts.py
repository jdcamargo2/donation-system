from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class AttachmentPrivacy(StrEnum):
    PRIVATE = "private"
    INTERNAL_REVIEW = "internal_review"
    PUBLIC_CANDIDATE = "public_candidate"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class KoboAttachmentPayload:
    field_name: str
    source_url: str
    filename: str | None = None
    content_type: str | None = None
    privacy_level: AttachmentPrivacy = AttachmentPrivacy.INTERNAL_REVIEW


@dataclass(frozen=True)
class KoboSubmissionPayload:
    external_id: str
    form_id: str
    form_version: str

    pastoral_zone: str
    parish: str
    primary_community: str | None = None
    assessment_date: date | None = None

    submitted_at: datetime | None = None
    submitted_by: str | None = None
    device_id: str | None = None

    normalized_payload: dict[str, Any] = field(default_factory=dict)
    attachments: tuple[KoboAttachmentPayload, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: ValidationSeverity
    field_name: str | None = None


@dataclass(frozen=True)
class ProcessingResult:
    success: bool
    status: str
    issues: tuple[ValidationIssue, ...] = ()