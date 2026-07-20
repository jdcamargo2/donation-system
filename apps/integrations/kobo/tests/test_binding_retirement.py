from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.services import route_normalized_submission


class KoboBindingRetirementTests(SimpleTestCase):
    def test_legacy_binding_is_not_part_of_the_runtime_public_api(self):
        # PRE: services package represents the supported Kobo runtime surface.
        # POST: legacy binding creators and resolvers cannot be imported publicly.
        import apps.integrations.kobo.services as services

        for symbol in (
            "assign_normalized_submission_to_direct_project",
            "associate_submission_with_project",
            "create_project_binding",
            "link_asset_to_project",
            "resolve_project_binding",
            "unlink_asset_from_project",
        ):
            self.assertFalse(hasattr(services, symbol), symbol)

    def test_supported_dispatcher_has_no_binding_dependency(self):
        # PRE: supported forms enter routing through the territorial dispatcher.
        # POST: its implementation cannot inspect or resolve legacy bindings.
        source = Path(route_normalized_submission.__code__.co_filename).read_text()
        self.assertNotIn("KoboProjectBinding", source)
        self.assertNotIn("resolve_project_binding", source)

    def test_legacy_routes_are_unregistered(self):
        # PRE: URL names identify former binding management surfaces.
        # POST: legacy routes resolve to no public endpoint.
        for name in ("create_project_binding", "submission_associate_project"):
            with self.assertRaises(Exception):
                reverse(f"kobo:{name}")

    def test_historical_model_remains_declared_for_data_retention(self):
        # PRE: persistent data has not yet been audited for deletion.
        # POST: the historical model remains available without a runtime service.
        self.assertEqual(KoboProjectBinding._meta.db_table, "kobo_koboprojectbinding")
