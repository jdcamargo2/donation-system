from dataclasses import dataclass

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset


FORM_DEFINITION_ROLES = {
    (FICHA_01_FORM_ID, FICHA_01_VERSION): KoboAsset.FormRole.TERRITORIAL_PROFILE,
    (FICHA_10_FORM_ID, FICHA_10_VERSION): KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
    (FICHA_11_FORM_ID, FICHA_11_VERSION): KoboAsset.FormRole.PRIORITIZATION_MATRIX,
}


REJECTION_REASON_LABELS = {
    "test_submission": "Submission de prueba",
    "duplicate": "Duplicada",
    "incorrect_data": "Datos incorrectos",
    "incomplete": "Información incompleta",
    "wrong_project": "Proyecto incorrecto",
    "other": "Otro",
}


@dataclass(frozen=True)
class SyncResult:
    fetched_count: int
    created_count: int
    existing_count: int
    failed_count: int


@dataclass(frozen=True)
class ProcessingBatchResult:
    selected_count: int
    processed_count: int
    ready_count: int
    validation_failed_count: int
    processing_failed_count: int
    skipped_count: int


@dataclass(frozen=True)
class ReviewResult:
    submission_id: int
    previous_status: str
    final_status: str
    reviewed_by_id: int


@dataclass(frozen=True)
class ProjectAssociationResult:
    submission_id: int
    asset_id: int | None
    project_id: int | None
    previous_status: str
    final_status: str
    associated: bool


@dataclass(frozen=True)
class OperationalImportResult:
    submission_id: int
    project_id: int | None
    imported: bool
    already_imported: bool


@dataclass(frozen=True)
class KoboRejectionResult:
    submission_id: int
    rejected: bool
    already_rejected: bool


@dataclass(frozen=True)
class KoboRestoreResult:
    submission_id: int
    restored: bool
    already_ready: bool


@dataclass(frozen=True)
class RoutingResolution:
    binding_id: int
    asset_id: int
    project_id: int
    routing_type: str
    source_field: str
    source_value: str


@dataclass(frozen=True)
class AssetDiscoveryResult:
    fetched_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    unavailable_count: int
    failed_count: int


@dataclass(frozen=True)
class AssetReadiness:
    ready: bool
    code: str
    message: str
    routing_type: str | None
    active_binding_count: int


@dataclass(frozen=True)
class WebhookConvergenceResult:
    submission_id: int
    final_status: str
    completed: bool
