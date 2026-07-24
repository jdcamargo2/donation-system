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
    UNRESOLVED = "unresolved"
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
    TERRITORIAL_IDENTITY_INVALID = "territorial_identity_invalid"
    TERRITORIAL_IDENTITY_CONFLICT = "territorial_identity_conflict"
    MISSING_ZONE_PROJECT_MAPPING = "missing_zone_project_mapping"
    UNSUPPORTED_FORM = "unsupported_form"
    TERRITORIAL_CONFLICT_REJECTED = "territorial_conflict_rejected"


class TerritorialAdministrationStatus(StrEnum):
    SUCCESS = "success"
    ALREADY_APPLIED = "already_applied"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    INVALID_STATE = "invalid_state"
    FAILED = "failed"


class TerritorialConflictDecision(StrEnum):
    KEEP_EXISTING = "keep_existing"
    ACCEPT_PROPOSED = "accept_proposed"
    DISMISS = "dismissed"


class TerritorialAdministrationReasonCode(StrEnum):
    ACTOR_REQUIRED = "actor_required"
    PERMISSION_DENIED = "permission_denied"
    INVALID_PASTORAL_ZONE = "invalid_pastoral_zone"
    PROJECT_NOT_AVAILABLE = "project_not_available"
    ZONE_MAPPING_IN_USE = "zone_mapping_in_use"
    MAPPING_NOT_FOUND = "mapping_not_found"
    REASON_REQUIRED = "reason_required"
    CONFLICT_NOT_FOUND = "conflict_not_found"
    ALREADY_RESOLVED = "already_resolved"
    CONFLICT_DECISION_MISMATCH = "conflict_decision_mismatch"
    PROPOSED_MAPPING_NOT_AVAILABLE = "proposed_mapping_not_available"
    TERRITORIAL_IDENTITY_ALREADY_USED = "territorial_identity_already_used"
    IDENTITY_NOT_FOUND = "identity_not_found"
    INVALID_IDENTITY_TRANSITION = "invalid_identity_transition"
    INVALID_CONFLICT_DECISION = "invalid_conflict_decision"
    INVALID_RECONCILIATION_LIMIT = "invalid_reconciliation_limit"
    CONCURRENT_UPDATE = "concurrent_update"


@dataclass(frozen=True)
class TerritorialAdministrationResult:
    status: TerritorialAdministrationStatus
    reason_code: TerritorialAdministrationReasonCode | None = None
    entity_id: int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TerritorialReconciliationResult:
    status: TerritorialAdministrationStatus
    identity_id: int | None = None
    reason_code: TerritorialAdministrationReasonCode | None = None
    resolved: int = 0
    still_pending: int = 0
    conflicts: int = 0
    errors: int = 0
    skipped: int = 0
    has_more: bool = False
    warnings: tuple[str, ...] = ()
    scanned: int = 0
    routed: int = 0
    imported: int = 0
    incidents: int = 0
    failed: int = 0
    remaining: int = 0


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
