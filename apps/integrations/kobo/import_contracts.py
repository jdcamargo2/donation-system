from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from apps.integrations.kobo.form_registry import KoboFormType


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from apps.integrations.kobo.models import KoboSubmission


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    ALREADY_IMPORTED = "already_imported"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class ImportWarning:
    code: str
    message: str


@dataclass(frozen=True)
class KoboMaterializationResult:
    materialization_type: str
    target_app_label: str
    target_model: str
    target_object_id: int
    created: bool
    warnings: tuple[ImportWarning, ...] = ()


@dataclass(frozen=True)
class KoboImportResult:
    outcome: ImportOutcome
    submission_id: int
    materialization_type: str | None
    materialization_id: int | None
    created: bool
    warnings: tuple[ImportWarning, ...]
    reason_code: str | None = None

    @property
    def imported(self) -> bool:
        return self.outcome == ImportOutcome.IMPORTED

    @property
    def already_imported(self) -> bool:
        return self.outcome == ImportOutcome.ALREADY_IMPORTED


class KoboImportHandler(Protocol):
    form_type: KoboFormType

    def validate_for_import(
        self,
        *,
        submission: "KoboSubmission",
    ) -> tuple[ImportWarning, ...]:
        """
        PRE: common import preconditions already passed for the locked submission.
        POST: returns safe, form-specific warnings or raises a controlled blocker.
        """
        ...

    def materialize(
        self,
        *,
        submission: "KoboSubmission",
        actor: "AbstractBaseUser",
    ) -> KoboMaterializationResult:
        """
        PRE: validation passed and the caller owns the surrounding DB transaction.
        POST: returns one persisted target reference or raises without external side effects.
        """
        ...


class KoboImportBlocked(Exception):
    def __init__(
        self,
        reason_code: str,
        *,
        warnings: tuple[ImportWarning, ...] = (),
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.warnings = warnings
