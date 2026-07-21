from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.hub import PAGE_SIZE, pending_review_queryset
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.models import KoboSyncRun
from apps.integrations.kobo.models import KoboTerritorialIdentityConflict
from apps.integrations.kobo.presentation import pastoral_zone_label
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
        self.assertContains(response, "¿Qué debes hacer ahora?")
        self.assertContains(response, "Resumen por ficha")
        self.assertContains(response, "Última actualización por ficha")
        self.assertContains(response, "Ver asignación de zonas")
        self.assertContains(response, "Ver historial de sincronizaciones")
        self.assertContains(response, "Ver formularios pendientes")
        self.assertNotContains(response, "Estado de las zonas pastorales")
        self.assertNotContains(response, "¿Qué necesita atención?")
        self.assertNotContains(response, "Formularios importados")
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
        self.assertContains(response, "Zonas sin configurar")
        self.assertContains(response, "Formularios pendientes de revisión")
        self.assertContains(response, 'href="%s?zone=' % reverse("kobo:mapping_list"))
        self.assertContains(response, reverse("kobo:pending_submission_list"))
        self.assertNotContains(response, "Faltan mappings para:")
        self.assertNotContains(response, "Montana")
        self.assertNotContains(response, "Configurar 4 zonas pendientes")
        self.assertNotContains(response, "No hay conflictos ni errores de asignación")

    def test_dashboard_sync_section_and_recent_runs_use_operator_language(self):
        asset = KoboAsset.objects.create(
            asset_uid="hub-sync-ui",
            name="Hub sync UI",
            form_definition=self.ficha_1,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        for index in range(3):
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
        self.assertContains(response, "Última actualización por ficha")
        self.assertContains(response, "Actualización")
        self.assertContains(response, "Completada")
        self.assertContains(response, 'action="%s"' % reverse("kobo:sync_asset", args=(asset.pk, "incremental")))
        self.assertContains(response, 'action="%s"' % reverse("kobo:sync_asset", args=(asset.pk, "full")))
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertEqual(response.content.decode().count("Completada"), 1)
        self.assertNotContains(response, "Últimas sincronizaciones")
        self.assertNotContains(response, "Sincronización incremental")
        self.assertNotContains(response, "SUCCEEDED")
        self.assertNotContains(response, "INCREMENTAL")

        history = self.client.get(reverse("kobo:sync_history"))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Historial de sincronizaciones")
        self.assertEqual(history.content.decode().count("Completada"), 3)

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
        self.assertContains(pending, "Formularios pendientes de revisión")
        self.assertContains(pending, "Pendientes de revisión")
        self.assertNotContains(pending, "Submissions pendientes de routing")
        self.assertNotContains(pending, "Error de routing")

    def test_pending_review_count_and_list_use_shared_queryset(self):
        for index in range(3):
            self.create_submission(
                f"hub-pending-shared-{index}",
                status=KoboSubmission.Status.READY_FOR_REVIEW,
            )
        self.create_submission(
            "hub-imported-noise",
            status=KoboSubmission.Status.IMPORTED,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )
        self.create_submission(
            "hub-routing-noise",
            status=KoboSubmission.Status.PROCESSING_FAILED,
            routing_status=KoboSubmission.RoutingStatus.ERROR,
        )

        self.assertEqual(pending_review_queryset().count(), 3)

        dashboard = self.client.get(reverse("kobo:hub"))
        cases = self.client.get(reverse("kobo:conflict_list"))
        listing = self.client.get(reverse("kobo:pending_submission_list"))
        filtered = self.client.get(
            reverse("kobo:pending_submission_list"),
            {"nucleo_code": "NV-TA-01"},
        )

        self.assertContains(dashboard, "Formularios pendientes de revisión")
        self.assertRegex(dashboard.content.decode(), r"Formularios pendientes de revisión</div>\s*<div class=\"ops-metric-value\">3</div>")
        self.assertContains(cases, "Ver listado")
        self.assertContains(
            cases,
            'href="%s"' % reverse("kobo:pending_submission_list"),
        )
        self.assertContains(cases, ">3</div>", count=1)
        self.assertEqual(listing.context["page_obj"].paginator.count, 3)
        self.assertEqual(len(listing.context["page_obj"].object_list), 3)
        self.assertEqual(filtered.context["page_obj"].paginator.count, 3)
        self.assertEqual(filtered.context["list_mode"], "pending_review")

        for index in range(PAGE_SIZE + 2):
            self.create_submission(
                f"hub-pending-page-{index}",
                status=KoboSubmission.Status.READY_FOR_REVIEW,
                code=f"NV-PAGE-{index:03d}",
            )
        page_one = self.client.get(reverse("kobo:pending_submission_list"))
        page_two = self.client.get(reverse("kobo:pending_submission_list"), {"page": 2})
        expected_total = 3 + PAGE_SIZE + 2
        self.assertEqual(page_one.context["page_obj"].paginator.count, expected_total)
        self.assertEqual(page_two.context["page_obj"].paginator.count, expected_total)
        self.assertEqual(page_one.context["list_mode"], "pending_review")
        self.assertEqual(page_two.context["list_mode"], "pending_review")

    def test_configure_buttons_preselect_each_pastoral_zone(self):
        mapping = self.client.get(reverse("kobo:mapping_list"))
        focused_zones = (
            PastoralZone.CENTRO,
            PastoralZone.ESTE,
            PastoralZone.MONTANA,
            PastoralZone.INSULAR,
            PastoralZone.CATIA_LA_MAR,
        )
        for zone in focused_zones:
            with self.subTest(zone=zone.value):
                self.assertContains(
                    mapping,
                    'href="%s?zone=%s#configurar-zona"'
                    % (reverse("kobo:mapping_list"), zone.value),
                )
                response = self.client.get(
                    reverse("kobo:mapping_list"),
                    {"zone": zone.value},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["selected_zone"], zone)
                self.assertContains(
                    response,
                    "Configurar zona: %s" % pastoral_zone_label(zone),
                )
                self.assertContains(response, 'id="configurar-zona"')
                self.assertEqual(
                    response.context["form"]["pastoral_zone"].value(),
                    zone.value,
                )
                self.assertIn(
                    "autofocus",
                    response.context["form"]["project"].field.widget.attrs,
                )
                before = KoboPastoralZoneProjectMapping.objects.count()
                self.assertEqual(
                    KoboPastoralZoneProjectMapping.objects.count(),
                    before,
                )

        invalid = self.client.get(reverse("kobo:mapping_list"), {"zone": "no-existe"})
        self.assertIsNone(invalid.context["selected_zone"])
        self.assertContains(invalid, "Configurar zona")
        self.assertNotContains(invalid, "Configurar zona:")

    def test_configure_requires_permission_and_never_saves_on_get(self):
        before = KoboPastoralZoneProjectMapping.objects.count()
        get_response = self.client.get(
            reverse("kobo:mapping_list"),
            {"zone": PastoralZone.CENTRO.value},
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(KoboPastoralZoneProjectMapping.objects.count(), before)

        user = get_user_model().objects.create_user("hub-mapping-reader")
        from django.contrib.auth.models import Permission

        user.user_permissions.add(
            Permission.objects.get(codename="view_territorial_administration")
        )
        self.client.force_login(user)
        forbidden = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "centro", "project": self.project.pk},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(KoboPastoralZoneProjectMapping.objects.count(), before)

    def test_mapping_change_and_remove_use_operator_language(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro",
            project=self.project,
            is_active=True,
        )
        listing = self.client.get(reverse("kobo:mapping_list"))
        self.assertContains(listing, "Cambiar asignación")
        self.assertNotContains(listing, "Motivo de desactivación")
        self.assertNotContains(listing, "Confirmar desactivación")
        self.assertNotContains(listing, 'name="reason"')

        change = self.client.get(
            reverse("kobo:mapping_list"),
            {"change": "centro"},
        )
        self.assertContains(change, "Cambiar asignación: Centro")
        self.assertContains(change, "Motivo para quitar la asignación")
        self.assertContains(change, "Quitar asignación")
        self.assertContains(
            change,
            "La zona Centro dejará de asociar nuevos formularios al proyecto Centro.",
        )
        self.assertContains(
            change,
            "Los formularios ya importados no serán modificados.",
        )
        self.assertContains(change, 'id="cambiar-asignacion"')
        self.assertNotContains(change, "desactivación")

        switched = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "centro", "project": self.other_project.pk},
            follow=True,
        )
        self.assertContains(switched, "Asignación guardada.")
        self.assertTrue(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro",
                project=self.other_project,
                is_active=True,
            ).exists()
        )

        removed = self.client.post(
            reverse("kobo:deactivate_mapping", args=("centro",)),
            {"reason": "Cierre temporal del proyecto territorial"},
            follow=True,
        )
        self.assertContains(removed, "Asignación quitada.")
        self.assertFalse(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro",
                is_active=True,
            ).exists()
        )
        self.assertEqual(
            self.client.get(
                reverse("kobo:deactivate_mapping", args=("centro",))
            ).status_code,
            405,
        )

    def test_configure_validation_keeps_selected_zone(self):
        response = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "este", "project": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["selected_zone"], PastoralZone.ESTE)
        self.assertContains(
            response,
            "Configurar zona: Este",
            status_code=400,
        )
        self.assertContains(response, 'id="configurar-zona"', status_code=400)

    def test_dashboard_navigation_links_are_not_dead(self):
        response = self.client.get(reverse("kobo:hub"))
        for name in (
            "kobo:mapping_list",
            "kobo:sync_history",
            "kobo:pending_submission_list",
            "kobo:conflict_list",
            "kobo:identity_list",
        ):
            with self.subTest(name=name):
                url = reverse(name)
                self.assertContains(response, url)
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_empty_states_explain_meaning_and_action(self):
        identities = self.client.get(reverse("kobo:identity_list"))
        conflicts = self.client.get(reverse("kobo:conflict_list"))
        pending = self.client.get(reverse("kobo:pending_submission_list"))
        history = self.client.get(reverse("kobo:sync_history"))

        self.assertContains(identities, "No hay núcleos registrados.")
        self.assertContains(identities, "Los núcleos aparecen cuando")
        self.assertContains(identities, "Ver formularios pendientes")
        self.assertNotContains(identities, "<tbody>")

        self.assertContains(conflicts, "No hay conflictos de asignación abiertos.")
        self.assertContains(conflicts, "Cuando aparezca un conflicto")
        self.assertNotContains(conflicts, "No hay conflictos de asignación en este filtro.")

        self.assertContains(pending, "No hay formularios pendientes de revisión.")
        self.assertContains(pending, "no hay envíos listos para comprobación humana")
        self.assertNotContains(pending, "<tbody>")

        self.assertContains(history, "Todavía no hay sincronizaciones registradas.")
        self.assertContains(history, "el historial completo aparecerá aquí")
        self.assertNotContains(history, "<tbody>")

    def test_reduced_dashboard_omits_redundant_tables(self):
        response = self.client.get(reverse("kobo:hub"))
        html = response.content.decode()
        self.assertNotIn("Estado de las zonas pastorales", html)
        self.assertNotIn("Últimas sincronizaciones", html)
        self.assertNotIn("¿Qué necesita atención?", html)
        self.assertNotIn("Formularios importados", html)
        self.assertIn("¿Qué debes hacer ahora?", html)
        self.assertIn("Resumen por ficha", html)
        self.assertEqual(html.count("ops-metric-card"), 3)

    def test_mapping_post_delegates_to_service(self):
        response = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "centro", "project": self.project.pk},
            follow=True,
        )
        self.assertRedirects(
            response,
            reverse("kobo:mapping_list"),
            status_code=302,
            target_status_code=200,
        )
        self.assertContains(response, "Asignación guardada.")
        self.assertContains(response, self.project.name)
        self.assertTrue(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro", project=self.project, is_active=True
            ).exists()
        )
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertEqual(
            self.client.get(reverse("kobo:configure_mapping")).status_code,
            405,
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
            history = self.client.get(reverse("kobo:sync_history"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(history.status_code, 404)

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
            "apps.integrations.kobo.hub.build_kobo_api_client",
            return_value=SimpleNamespace(),
        ), patch(
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
            "kobo:sync_history",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
