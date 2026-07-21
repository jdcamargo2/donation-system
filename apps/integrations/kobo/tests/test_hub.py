from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.models import KoboSyncRun
from apps.integrations.kobo.models import KoboTerritorialIdentityConflict
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
        self.assertContains(response, "Integración de formularios")
        self.assertContains(response, "Recepción, revisión y organización de formularios")
        self.assertContains(response, "Asignación de zonas")
        self.assertContains(response, "Núcleos registrados")
        self.assertContains(response, "Casos por revisar")
        self.assertContains(response, "Zonas asignadas")
        self.assertContains(response, "Formularios pendientes de revisión")
        self.assertContains(response, "Formularios importados")
        self.assertContains(response, "Estado de las zonas pastorales")
        self.assertContains(response, "¿Qué necesita atención?")
        self.assertContains(response, "Formularios recibidos")
        self.assertContains(response, "Últimas sincronizaciones")
        self.assertNotContains(response, ">Mappings</")
        self.assertNotContains(response, ">Identidades</")
        self.assertNotContains(response, "Identidades territoriales")
        self.assertNotContains(response, "Sincronización incremental")
        self.assertNotContains(response, "Errores de routing")
        response = self.client.get(reverse("kobo:mapping_list"))
        self.assertContains(response, "Asignación de zonas")
        self.assertContains(response, "Relaciona cada zona pastoral con el proyecto")
        self.assertNotContains(response, "Mappings zona pastoral")
        self.assertNotContains(response, "Guardar mapping")
        self.assertContains(response, "Guardar asignación")

    def test_dashboard_shows_missing_zone_assignments_and_metrics(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro",
            project=self.project,
            is_active=True,
        )
        self.create_submission("hub-ready-1", status=KoboSubmission.Status.READY_FOR_REVIEW)
        self.create_submission(
            "hub-imported-1",
            status=KoboSubmission.Status.IMPORTED,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )
        self.create_submission(
            "hub-imported-2",
            status=KoboSubmission.Status.IMPORTED,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )

        response = self.client.get(reverse("kobo:hub"))

        self.assertContains(response, "Faltan asignaciones para:")
        self.assertContains(response, "Montaña")
        self.assertContains(response, "Asocia cada zona pastoral con su proyecto")
        self.assertContains(response, "1 de 5")
        self.assertContains(response, "Configurar 4 zonas pendientes")
        self.assertContains(response, "Revisar 1 formulario")
        self.assertContains(response, "No hay conflictos ni errores de asignación")
        self.assertContains(response, "Sin proyecto asociado")
        self.assertContains(response, "Configurada")
        self.assertNotContains(response, "Faltan mappings para:")
        self.assertNotContains(response, "Montana")

    def test_dashboard_sync_section_and_recent_runs_use_operator_language(self):
        asset = KoboAsset.objects.create(
            asset_uid="hub-sync-ui",
            name="Hub sync UI",
            form_definition=self.ficha_1,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        KoboSyncRun.objects.create(
            asset=asset,
            kind=KoboSyncRun.Kind.SUBMISSIONS,
            status=KoboSyncRun.Status.SUCCEEDED,
            mode=KoboSyncRun.Mode.INCREMENTAL,
            items_created=2,
            items_updated=1,
            items_unchanged=3,
            items_failed=0,
        )

        response = self.client.get(reverse("kobo:hub"))

        self.assertContains(response, "Sincronización de formularios")
        self.assertContains(response, "Ficha 1")
        self.assertContains(response, "Registro territorial")
        self.assertContains(response, "Actualizar ahora")
        self.assertContains(response, "Sincronización completa")
        self.assertContains(response, "La sincronización se realiza en este momento")
        self.assertContains(response, "Actualización")
        self.assertContains(response, "Completada")
        self.assertContains(response, 'action="%s"' % reverse("kobo:sync_asset", args=(asset.pk, "incremental")))
        self.assertContains(response, 'action="%s"' % reverse("kobo:sync_asset", args=(asset.pk, "full")))
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "Sincronización incremental")
        self.assertNotContains(response, "SUCCEEDED")
        self.assertNotContains(response, "INCREMENTAL")

    def test_cases_and_identity_screens_use_operator_language(self):
        identity = self.create_identity()
        incoming = self.create_submission(
            "hub-conflict-1",
            routing_status=KoboSubmission.RoutingStatus.CONFLICT,
        )
        KoboTerritorialIdentityConflict.objects.create(
            identity=identity,
            incoming_submission=incoming,
            existing_pastoral_zone=identity.pastoral_zone,
            proposed_pastoral_zone="este",
            existing_project=self.project,
            proposed_project=self.other_project,
            status=KoboTerritorialIdentityConflict.Status.OPEN,
        )

        cases = self.client.get(reverse("kobo:conflict_list"))
        identities = self.client.get(reverse("kobo:identity_list"))
        pending = self.client.get(reverse("kobo:pending_submission_list"))

        self.assertContains(cases, "Casos por revisar")
        self.assertContains(cases, "Formularios pendientes de revisión")
        self.assertContains(cases, "Formularios sin núcleo registrado")
        self.assertContains(cases, "Conflictos de asignación")
        self.assertContains(cases, "Errores de procesamiento")
        self.assertContains(cases, "Actualizaciones remotas pendientes")
        self.assertNotContains(cases, "Conflictos territoriales")
        self.assertContains(identities, "Núcleos registrados")
        self.assertContains(identities, "Código del núcleo")
        self.assertContains(identities, "Fecha de registro")
        self.assertNotContains(identities, "Identidades territoriales")
        self.assertContains(pending, "Formularios por revisar")
        self.assertContains(pending, "Sin núcleo registrado")
        self.assertNotContains(pending, "Submissions pendientes de routing")
        self.assertNotContains(pending, "Error de routing")

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

    def test_legacy_hub_urls_remain_available(self):
        for name in (
            "kobo:hub",
            "kobo:mapping_list",
            "kobo:identity_list",
            "kobo:conflict_list",
            "kobo:pending_submission_list",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
