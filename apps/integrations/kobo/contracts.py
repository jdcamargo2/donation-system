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


class PastoralZone(StrEnum):
    CATIA_LA_MAR = "catia_la_mar"
    CENTRO = "centro"
    ESTE = "este"
    MONTANA = "montana"
    INSULAR = "insular"


class TerritorialRoutingStatus(StrEnum):
    RESOLVED = "resolved"
    PENDING_IDENTITY = "pending_identity"
    CONFLICT = "conflict"
    ERROR = "error"


class TerritorialRoutingReasonCode(StrEnum):
    MISSING_NUCLEO_CODE = "missing_nucleo_code"
    INVALID_NUCLEO_CODE = "invalid_nucleo_code"
    MISSING_PASTORAL_ZONE = "missing_pastoral_zone"
    INVALID_PASTORAL_ZONE = "invalid_pastoral_zone"
    UNKNOWN_TERRITORIAL_IDENTITY = "unknown_territorial_identity"
    TERRITORIAL_IDENTITY_CONFLICT = "territorial_identity_conflict"
    UNSUPPORTED_FORM = "unsupported_form"


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
class TerritorialRoutingResult:
    """Project assignment is not import; routing resolution is not import; review approval is not import."""

    status: TerritorialRoutingStatus
    form_type: str
    nucleo_code_original: str | None = None
    nucleo_code_normalized: str | None = None
    pastoral_zone: PastoralZone | None = None
    project_id: int | None = None
    reason_code: TerritorialRoutingReasonCode | None = None
    message: str | None = None


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
