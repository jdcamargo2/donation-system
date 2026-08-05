"""Focused Spanish presentation checks for discovered Kobo assets (I18N-UI-1)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAsset, KoboDiscoveredAsset, KoboFormDefinition
from apps.integrations.kobo.presentation import presentation_label


@override_settings(KOBO_ENABLED=True)
class KoboDiscoveredAssetsUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="kobo-discovered-viewer",
            password="test-password",
        )
        cls.viewer.user_permissions.add(
            Permission.objects.get(codename="view_koboasset"),
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 01 descubierta",
            version=FICHA_01_VERSION,
        )
        cls.unconfigured = KoboDiscoveredAsset.objects.create(
            asset_uid="discovered-unconfigured-uid",
            name="Activo sin configurar",
            asset_type="form",
            deployment_status="deployed",
            last_seen_at=timezone.now(),
            is_available=True,
        )
        cls.active_discovered = KoboDiscoveredAsset.objects.create(
            asset_uid="discovered-active-uid",
            name="Activo integrado",
            asset_type="form",
            deployment_status="deployed",
            last_seen_at=timezone.now(),
            is_available=True,
        )
        cls.inactive_discovered = KoboDiscoveredAsset.objects.create(
            asset_uid="discovered-inactive-uid",
            name="Activo inactivo",
            asset_type="form",
            deployment_status="deployed",
            last_seen_at=timezone.now(),
            is_available=True,
        )
        cls.active_asset = KoboAsset.objects.create(
            asset_uid=cls.active_discovered.asset_uid,
            name=cls.active_discovered.name,
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        cls.inactive_asset = KoboAsset.objects.create(
            asset_uid=cls.inactive_discovered.asset_uid,
            name=cls.inactive_discovered.name,
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            is_active=False,
        )

    def test_presentation_labels_cover_local_asset_states(self):
        self.assertEqual(presentation_label("unconfigured"), "Sin configurar")
        self.assertEqual(presentation_label("active"), "Activo")
        self.assertEqual(
            presentation_label("configured_inactive"),
            "Configurado e inactivo",
        )
        self.assertEqual(
            presentation_label("territorial_profile"),
            "Registro territorial",
        )
        self.assertEqual(
            presentation_label("prioritized_microproject"),
            "Microproyecto priorizado",
        )
        self.assertEqual(presentation_label("internal_review"), "Revisión interna")
        self.assertEqual(presentation_label("downloaded"), "Disponible")

    def test_discovered_asset_list_renders_spanish_local_states(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("kobo:discovered_asset_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin configurar")
        self.assertContains(response, "Activo")
        self.assertContains(response, "Configurado e inactivo")
        self.assertNotContains(response, ">unconfigured<")
        self.assertNotContains(response, ">configured_inactive<")

    def test_discovered_and_configuration_detail_use_spanish_form_role(self):
        self.client.force_login(self.viewer)

        detail = self.client.get(
            reverse("kobo:discovered_asset_detail", args=(self.active_discovered.pk,))
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Registro territorial")
        self.assertNotContains(detail, "Territorial profile")

        configuration = self.client.get(
            reverse("kobo:asset_configuration", args=(self.inactive_asset.pk,))
        )
        self.assertEqual(configuration.status_code, 200)
        self.assertContains(configuration, "Microproyecto priorizado")
        self.assertNotContains(configuration, "Prioritized microproject")
