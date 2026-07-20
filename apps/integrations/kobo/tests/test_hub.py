from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSyncRun
from apps.integrations.kobo.tests.test_territorial_administration import (
    TerritorialAdministrationFixtureMixin,
)


@override_settings(KOBO_ENABLED=True)
class KoboTerritorialHubTests(TerritorialAdministrationFixtureMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.actor)

    def test_dashboard_and_mapping_list_are_available_to_territorial_reader(self):
        response = self.client.get(reverse("kobo:hub"))
        self.assertContains(response, "KoboToolbox")
        response = self.client.get(reverse("kobo:mapping_list"))
        self.assertContains(response, "Mappings zona pastoral")

    def test_mapping_post_delegates_to_service(self):
        response = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "centro", "project": self.project.pk},
        )
        self.assertRedirects(response, reverse("kobo:mapping_list"))
        self.assertTrue(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro", project=self.project, is_active=True
            ).exists()
        )

    def test_identity_status_get_never_mutates(self):
        identity = self.create_identity()
        response = self.client.get(
            reverse("kobo:identity_status", args=(identity.pk, "observe"))
        )
        self.assertEqual(response.status_code, 405)
        identity.refresh_from_db()
        self.assertEqual(identity.status, identity.Status.ACTIVE)

    def test_disabled_hub_is_not_available(self):
        with self.settings(KOBO_ENABLED=False):
            response = self.client.get(reverse("kobo:hub"))
        self.assertEqual(response.status_code, 404)

    def test_user_without_read_permission_is_forbidden(self):
        user = get_user_model().objects.create_user("hub-no-permission")
        self.client.force_login(user)
        response = self.client.get(reverse("kobo:hub"))
        self.assertEqual(response.status_code, 403)

    def test_sync_action_requires_post_and_delegates_to_service(self):
        asset = KoboAsset.objects.create(
            asset_uid="hub-sync", name="Hub sync", form_definition=self.ficha_1,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )
        url = reverse("kobo:sync_asset", args=(asset.pk, "incremental"))
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch(
            "apps.integrations.kobo.hub.sync_asset_submissions",
            return_value=SimpleNamespace(status=KoboSyncRun.Status.SUCCEEDED),
        ) as synchronize:
            response = self.client.post(url)

        self.assertRedirects(response, reverse("kobo:hub"))
        self.assertEqual(synchronize.call_args.kwargs["asset"], asset)
        self.assertFalse(synchronize.call_args.kwargs["full"])
