from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from apps.integrations.kobo.form_registry import KoboFormType
from apps.integrations.kobo.import_contracts import (
    ImportWarning,
    KoboImportBlocked,
    KoboImportHandler,
    KoboMaterializationResult,
)


MATERIALIZATION_NOT_IMPLEMENTED = "MATERIALIZATION_NOT_IMPLEMENTED"


def _calculation_warnings(submission) -> tuple[ImportWarning, ...]:
    """
    PRE: submission has passed common normalized-payload validation.
    POST: returns only safe structured calculation warnings from normalized data.
    """
    raw_warnings = submission.normalized_payload.get("calculation_warnings", ())
    if not isinstance(raw_warnings, (list, tuple)):
        return ()
    warnings = []
    for raw_warning in raw_warnings:
        if not isinstance(raw_warning, dict):
            continue
        code = raw_warning.get("code")
        message = raw_warning.get("message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            warnings.append(ImportWarning(code=code, message=message))
    return tuple(warnings)


@dataclass(frozen=True)
class StubKoboImportHandler:
    form_type: KoboFormType

    def validate_for_import(self, *, submission) -> tuple[ImportWarning, ...]:
        """
        PRE: common import preconditions passed for the handler's form type.
        POST: exposes safe Ficha 11 warnings without treating them as blockers.
        """
        if self.form_type == KoboFormType.FICHA_11:
            return _calculation_warnings(submission)
        return ()

    def materialize(self, *, submission, actor) -> KoboMaterializationResult:
        """
        PRE: the supported form reached its explicit but unfinished handler.
        POST: blocks deterministically and never creates a target entity.
        """
        raise KoboImportBlocked(
            MATERIALIZATION_NOT_IMPLEMENTED,
            warnings=self.validate_for_import(submission=submission),
        )


KOBO_IMPORT_HANDLERS: Mapping[KoboFormType, KoboImportHandler] = MappingProxyType(
    {
        KoboFormType.FICHA_1: StubKoboImportHandler(KoboFormType.FICHA_1),
        KoboFormType.FICHA_10: StubKoboImportHandler(KoboFormType.FICHA_10),
        KoboFormType.FICHA_11: StubKoboImportHandler(KoboFormType.FICHA_11),
    }
)


def get_import_handler(form_type: KoboFormType) -> KoboImportHandler:
    """
    PRE: form_type came from the closed registered-form dispatcher.
    POST: returns the exact supported handler; no generic fallback is available.
    """
    return KOBO_IMPORT_HANDLERS[form_type]
