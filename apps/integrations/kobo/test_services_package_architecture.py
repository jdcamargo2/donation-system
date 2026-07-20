import ast
import importlib
import inspect
from collections import Counter
from pathlib import Path
from unittest import TestCase

from apps.integrations.kobo import errors
from apps.integrations.kobo import services


SERVICE_MODULES = (
    "association",
    "common",
    "discovery",
    "importers",
    "incremental",
    "processing",
    "submissions",
    "territorial_administration",
    "territorial_routing",
)
EXPECTED_PUBLIC_API = {
    "REJECTION_REASON_LABELS",
    "activate_kobo_asset",
    "activate_observed_territorial_identity",
    "configure_discovered_asset",
    "configure_pastoral_zone_project_mapping",
    "converge_webhook_submission",
    "deactivate_kobo_asset",
    "deactivate_pastoral_zone_project_mapping",
    "deactivate_territorial_identity",
    "discover_assets",
    "get_asset_readiness",
    "get_project_imported_submissions",
    "get_project_pending_submissions",
    "get_project_submission_history",
    "import_kobo_submission",
    "process_pending_submissions",
    "observe_territorial_identity",
    "receive_api_submission",
    "receive_webhook_submission",
    "reject_kobo_submission",
    "reconcile_territorial_identity_submissions",
    "restore_kobo_submission_to_review",
    "review_submission",
    "route_dependent_territorial_submission",
    "route_ficha_1_submission",
    "route_normalized_submission",
    "resolve_territorial_identity_conflict",
    "sync_ficha_01_submissions",
    "sync_asset_submissions",
    "sync_registered_forms",
}
EXPECTED_SIGNATURES = {
    "activate_kobo_asset": "(asset, *, activated_by)",
    "activate_observed_territorial_identity": "(*, identity, actor, reason)",
    "configure_discovered_asset": (
        "(discovered_asset, *, name, form_definition, form_role, configured_by)"
    ),
    "configure_pastoral_zone_project_mapping": "(*, pastoral_zone, project, actor)",
    "converge_webhook_submission": "(submission_id, *, default_timezone)",
    "deactivate_kobo_asset": "(asset, *, deactivated_by)",
    "deactivate_pastoral_zone_project_mapping": "(*, pastoral_zone, actor, reason)",
    "deactivate_territorial_identity": "(*, identity, actor, reason)",
    "discover_assets": "(client, *, limit=100, dry_run=False)",
    "get_asset_readiness": "(asset)",
    "get_project_imported_submissions": "(project, *, form_role=None)",
    "get_project_pending_submissions": "(project)",
    "get_project_submission_history": "(project)",
    "import_kobo_submission": "(submission, *, actor)",
    "process_pending_submissions": "(*, limit=100, default_timezone)",
    "observe_territorial_identity": "(*, identity, actor, reason)",
    "receive_api_submission": "(form_definition, raw_payload)",
    "receive_webhook_submission": "(*, asset, raw_payload)",
    "reject_kobo_submission": "(submission, *, actor, reason, comment='')",
    "reconcile_territorial_identity_submissions": "(*, identity, actor, limit=100)",
    "restore_kobo_submission_to_review": "(submission, *, actor)",
    "review_submission": "(submission, *, decision, reason, reviewed_by)",
    "route_dependent_territorial_submission": "(submission)",
    "route_ficha_1_submission": "(submission)",
    "route_normalized_submission": "(submission)",
    "resolve_territorial_identity_conflict": "(*, conflict, decision, actor, reason)",
    "sync_ficha_01_submissions": (
        "(client, asset_uid, limit=100, dry_run=False)"
    ),
    "sync_asset_submissions": "(*, asset, client, actor=None, full=False, max_pages=None)",
    "sync_registered_forms": "()",
}


def _signature_without_annotations(callable_object) -> str:
    signature = inspect.signature(callable_object)
    parameters = [
        parameter.replace(annotation=inspect.Parameter.empty)
        for parameter in signature.parameters.values()
    ]
    return str(
        signature.replace(
            parameters=parameters,
            return_annotation=inspect.Signature.empty,
        )
    )


class KoboServicesPackageArchitectureTests(TestCase):
    def test_all_service_modules_and_consumers_are_importable(self):
        for module_name in SERVICE_MODULES:
            importlib.import_module(
                f"apps.integrations.kobo.services.{module_name}"
            )

        importlib.import_module("apps.integrations.kobo.views")
        for command_name in (
            "discover_kobo_assets",
            "process_kobo_submissions",
            "reconcile_kobo_submissions",
            "register_kobo_forms",
            "sync_kobo_ficha_01",
        ):
            importlib.import_module(
                "apps.integrations.kobo.management.commands."
                f"{command_name}"
            )

    def test_facade_exports_only_the_demonstrated_public_api(self):
        self.assertEqual(set(services.__all__), EXPECTED_PUBLIC_API)
        self.assertNotIn("KoboApiClient", services.__all__)
        self.assertNotIn("KoboPayloadError", services.__all__)
        self.assertNotIn("_lock_submission_for_operational_import", services.__all__)

    def test_public_callable_signatures_remain_compatible(self):
        actual_signatures = {
            name: _signature_without_annotations(getattr(services, name))
            for name in EXPECTED_SIGNATURES
        }
        self.assertEqual(actual_signatures, EXPECTED_SIGNATURES)

    def test_service_modules_use_canonical_exception_classes(self):
        submissions = importlib.import_module(
            "apps.integrations.kobo.services.submissions"
        )
        importers = importlib.import_module(
            "apps.integrations.kobo.services.importers"
        )

        self.assertIs(submissions.KoboConfigurationError, errors.KoboConfigurationError)
        self.assertIs(submissions.KoboPayloadError, errors.KoboPayloadError)
        self.assertIs(importers.KoboPayloadError, errors.KoboPayloadError)

    def test_each_definition_has_one_owner_module(self):
        services_directory = Path(services.__file__).parent
        definitions = []
        for module_name in SERVICE_MODULES:
            tree = ast.parse(
                (services_directory / f"{module_name}.py").read_text()
            )
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    definitions.append(node.name)

        duplicates = {
            name for name, count in Counter(definitions).items() if count > 1
        }
        self.assertEqual(duplicates, set())
