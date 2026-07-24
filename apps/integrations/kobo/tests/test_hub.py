from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from types import SimpleNamespace
from unittest.mock import patch

from apps.integrations.kobo.contracts import PastoralZone
from apps.integrations.kobo.hub import PAGE_SIZE, incident_queryset, pending_review_queryset
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

    def create_incident_submission(self, external_id, **changes):
        # PRE: external_id is unique; changes may override status/routing for an incident.
        # POST: persists a row that incident_queryset must include (not auto-importable).
        values = {
            "status": KoboSubmission.Status.READY_FOR_REVIEW,
            "routing_status": KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            "project": None,
            "pastoral_zone": "",
        }
        values.update(changes)
        return self.create_submission(external_id, **values)

    def test_dashboard_and_mapping_list_are_available_to_territorial_reader(self):
        response = self.client.get(reverse("kobo:hub"))
        self.assertContains(response, "KoboToolbox")
        self.assertContains(response, "Integración de formularios")
        self.assertContains(response, "Recepción automática de formularios")
        self.assertContains(response, "Asignación de zonas")
        self.assertContains(response, "Núcleos registrados")
        self.assertContains(response, "Incidencias")
        self.assertContains(response, "Formularios importados")
        self.assertContains(response, "Zonas configuradas")
        self.assertContains(response, "Sincronizar KoboToolbox")
        self.assertContains(response, "Las fichas llegan automáticamente")
        self.assertContains(response, "¿Qué debes hacer ahora?")
        self.assertContains(response, "Ver asignación de zonas")
        self.assertContains(response, "Ver historial de sincronizaciones")
        self.assertContains(response, "Ver incidencias")
        self.assertNotContains(response, "Formularios pendientes de revisión")
        self.assertNotContains(response, "Resumen por ficha")
        self.assertNotContains(response, "Aprobar e importar")
        self.assertNotContains(response, "Actualizar ahora")
        self.assertNotContains(response, "Sincronización completa")
        self.assertNotContains(response, "Casos por revisar")
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

    def test_dashboard_shows_single_sync_button_and_htmx_polling(self):
        response = self.client.get(reverse("kobo:hub"))
        html = response.content.decode()

        self.assertEqual(html.count("Sincronizar KoboToolbox"), 2)  # heading + button
        self.assertContains(response, 'hx-post="%s"' % reverse("kobo:sync_all"))
        self.assertContains(response, 'action="%s"' % reverse("kobo:sync_all"))
        self.assertContains(response, 'hx-get="%s"' % reverse("kobo:dashboard_status"))
        self.assertContains(response, 'hx-trigger="load, every 15s"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, reverse("kobo:sync_asset", args=(1, "incremental")))
        self.assertNotContains(response, "Última actualización por ficha")

    def test_dashboard_status_polling_is_protected_compact_and_aggregated(self):
        with self.assertNumQueries(8):
            response = self.client.get(
                reverse("kobo:dashboard_status"),
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formularios importados")
        self.assertNotContains(response, "raw_payload")
        self.assertNotContains(response, "webhook_received")

        unauthorized = get_user_model().objects.create_user("hub-status-unauthorized")
        self.client.force_login(unauthorized)
        self.assertEqual(
            self.client.get(reverse("kobo:dashboard_status")).status_code,
            403,
        )

    def test_dashboard_shows_missing_zone_assignments_and_metrics(self):
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="centro",
            project=self.project,
            is_active=True,
        )
        self.create_incident_submission("hub-incident-1")
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
        # READY + RESOLVED + project is eligible for auto-import, not an incident.
        self.create_submission(
            "hub-ready-resolved",
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )

        response = self.client.get(reverse("kobo:hub"))

        self.assertContains(response, "Faltan asignaciones para:")
        self.assertContains(response, "Montaña")
        self.assertContains(response, "Asocia cada zona pastoral con su proyecto")
        self.assertContains(response, "1 de 5")
        self.assertContains(response, "Zonas sin configurar")
        self.assertContains(response, "Formularios importados")
        self.assertContains(response, "Incidencias")
        self.assertRegex(
            response.content.decode(),
            r"Formularios importados</div>\s*<div class=\"ops-metric-value\">2</div>",
        )
        self.assertRegex(
            response.content.decode(),
            r"Incidencias</div>\s*<div class=\"ops-metric-value\">1</div>",
        )
        self.assertContains(response, 'href="%s?zone=' % reverse("kobo:mapping_list"))
        self.assertContains(response, reverse("kobo:conflict_list"))
        self.assertNotContains(response, "Formularios pendientes de revisión")
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
        for _ in range(3):
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

        self.assertContains(response, "Sincronizar KoboToolbox")
        self.assertContains(response, "Última sincronización")
        self.assertContains(response, "Completada")
        self.assertNotContains(response, "Actualizar ahora")
        self.assertNotContains(response, "Sincronización completa")
        self.assertNotContains(response, "Resumen por ficha")
        self.assertNotContains(response, "Última actualización por ficha")
        self.assertNotContains(response, 'action="%s"' % reverse("kobo:sync_asset", args=(asset.pk, "incremental")))
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

        self.assertContains(cases, "Incidencias")
        self.assertContains(cases, "Formularios que no pudieron procesarse automáticamente")
        self.assertContains(cases, "Formularios sin núcleo registrado")
        self.assertContains(cases, "Conflictos de asignación")
        self.assertContains(cases, "Errores de procesamiento")
        self.assertContains(cases, "Actualizaciones remotas pendientes")
        self.assertNotContains(cases, "Formularios pendientes de revisión")
        self.assertNotContains(cases, "Casos por revisar")
        self.assertNotContains(cases, "Conflictos territoriales")
        self.assertContains(identities, "Núcleos registrados")
        self.assertContains(identities, "Código del núcleo")
        self.assertContains(identities, "Fecha de registro")
        self.assertNotContains(identities, "Identidades territoriales")
        self.assertContains(pending, "Incidencias")
        self.assertNotContains(pending, "Pendientes de revisión")
        self.assertNotContains(pending, "Formularios pendientes de revisión")
        self.assertNotContains(pending, "Submissions pendientes de routing")

    def test_incident_count_and_list_use_shared_queryset(self):
        for index in range(3):
            self.create_incident_submission(f"hub-incident-shared-{index}")
        self.create_submission(
            "hub-imported-noise",
            status=KoboSubmission.Status.IMPORTED,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )
        self.create_submission(
            "hub-ready-resolved-noise",
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            project=self.project,
        )
        self.create_submission(
            "hub-validation-incident",
            status=KoboSubmission.Status.VALIDATION_FAILED,
            routing_status=KoboSubmission.RoutingStatus.UNRESOLVED,
        )

        self.assertEqual(incident_queryset().count(), 4)
        self.assertEqual(pending_review_queryset().count(), 4)

        dashboard = self.client.get(reverse("kobo:hub"))
        cases = self.client.get(reverse("kobo:conflict_list"))
        listing = self.client.get(reverse("kobo:pending_submission_list"))
        filtered = self.client.get(
            reverse("kobo:pending_submission_list"),
            {"nucleo_code": "NV-TA-01"},
        )

        self.assertContains(dashboard, "Incidencias")
        self.assertRegex(
            dashboard.content.decode(),
            r"Incidencias</div>\s*<div class=\"ops-metric-value\">4</div>",
        )
        self.assertContains(cases, "Ver listado")
        self.assertContains(
            cases,
            'href="%s?routing_status=pending_identity"' % reverse("kobo:pending_submission_list"),
        )
        self.assertEqual(listing.context["page_obj"].paginator.count, 4)
        self.assertEqual(len(listing.context["page_obj"].object_list), 4)
        self.assertEqual(filtered.context["page_obj"].paginator.count, 4)
        self.assertEqual(filtered.context["list_mode"], "incidents")

        for index in range(PAGE_SIZE + 2):
            self.create_incident_submission(
                f"hub-incident-page-{index}",
                code=f"NV-PAGE-{index:03d}",
            )
        page_one = self.client.get(reverse("kobo:pending_submission_list"))
        page_two = self.client.get(reverse("kobo:pending_submission_list"), {"page": 2})
        expected_total = 4 + PAGE_SIZE + 2
        self.assertEqual(page_one.context["page_obj"].paginator.count, expected_total)
        self.assertEqual(page_two.context["page_obj"].paginator.count, expected_total)
        self.assertEqual(page_one.context["list_mode"], "incidents")
        self.assertEqual(page_two.context["list_mode"], "incidents")

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
                    'hx-get="%s?zone=%s"'
                    % (reverse("kobo:mapping_modal"), zone.value),
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

        invalid = self.client.get(reverse("kobo:mapping_list"), {"zone": "no-existe"})
        self.assertIsNone(invalid.context["selected_zone"])
        self.assertNotContains(invalid, "Configurar zona:")

    def test_mapping_modal_get_returns_configure_zone(self):
        response = self.client.get(
            reverse("kobo:mapping_modal"),
            {"zone": PastoralZone.CENTRO.value},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Configurar zona Centro")
        self.assertContains(response, 'hx-post="%s"' % reverse("kobo:configure_mapping"))
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "<html")

        missing = self.client.get(reverse("kobo:mapping_modal"))
        self.assertEqual(missing.status_code, 404)

    def test_configure_via_modal_htmx(self):
        response = self.client.post(
            reverse("kobo:configure_mapping"),
            {"pastoral_zone": "centro", "project": self.project.pk, "mapping_mode": "configure"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asignación guardada.")
        self.assertContains(response, 'id="zone-centro"')
        self.assertContains(response, 'id="kobo-modal-root"')
        self.assertNotContains(response, "<html")
        self.assertTrue(
            KoboPastoralZoneProjectMapping.objects.filter(
                pastoral_zone="centro", project=self.project, is_active=True
            ).exists()
        )

    def test_configure_requires_permission_and_never_saves_on_get(self):
        before = KoboPastoralZoneProjectMapping.objects.count()
        get_response = self.client.get(
            reverse("kobo:mapping_list"),
            {"zone": PastoralZone.CENTRO.value},
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(KoboPastoralZoneProjectMapping.objects.count(), before)

        user = get_user_model().objects.create_user("hub-mapping-reader")
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
        self.assertNotContains(identities, "<tbody>")

        self.assertContains(conflicts, "No hay conflictos de asignación abiertos.")
        self.assertContains(conflicts, "Cuando aparezca un conflicto")
        self.assertNotContains(conflicts, "No hay conflictos de asignación en este filtro.")

        self.assertContains(pending, "No hay incidencias pendientes.")
        self.assertContains(pending, "no hay formularios bloqueados en el procesamiento automático")
        self.assertNotContains(pending, "No hay formularios pendientes de revisión.")
        self.assertNotContains(pending, "<tbody>")

        self.assertContains(history, "Todavía no hay sincronizaciones registradas.")
        self.assertContains(history, "el historial completo aparecerá aquí")
        self.assertNotContains(history, "<tbody>")

    def test_reduced_dashboard_omits_redundant_tables(self):
        response = self.client.get(reverse("kobo:hub"))
        html = response.content.decode()
        self.assertNotIn("Estado de las zonas pastorales", html)
        self.assertNotIn("Últimas sincronizaciones", html)
        self.assertNotIn("Resumen por ficha", html)
        self.assertNotIn("Formularios pendientes de revisión", html)
        self.assertIn("¿Qué debes hacer ahora?", html)
        self.assertIn("Formularios importados", html)
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
            sync = self.client.post(reverse("kobo:sync_all"))
            modal = self.client.get(
                reverse("kobo:mapping_modal"),
                {"zone": PastoralZone.CENTRO.value},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(history.status_code, 404)
        self.assertEqual(sync.status_code, 404)
        self.assertEqual(modal.status_code, 404)

    def test_user_without_read_permission_is_forbidden(self):
        user = get_user_model().objects.create_user("hub-no-permission")
        self.client.force_login(user)
        response = self.client.get(reverse("kobo:hub"))
        self.assertEqual(response.status_code, 403)

    def test_sync_all_post_fallback_redirects_without_htmx(self):
        url = reverse("kobo:sync_all")
        self.assertEqual(self.client.get(url).status_code, 405)
        result = SimpleNamespace(
            status="SUCCEEDED",
            assets_processed=2,
            forms_found=3,
            created=1,
            updated=1,
            unchanged=1,
            imported=2,
            incidents=0,
            errors=0,
        )
        with patch(
            "apps.integrations.kobo.hub.build_kobo_api_client",
            return_value=SimpleNamespace(),
        ), patch(
            "apps.integrations.kobo.hub.sync_supported_assets",
            return_value=result,
        ) as synchronize:
            response = self.client.post(url)

        self.assertRedirects(response, reverse("kobo:hub"))
        synchronize.assert_called_once()
        self.assertFalse(synchronize.call_args.kwargs["full"])
        self.assertEqual(synchronize.call_args.kwargs["actor"], self.actor)

    def test_sync_all_htmx_returns_fragment_without_full_page(self):
        result = SimpleNamespace(
            status="PARTIAL",
            assets_processed=3,
            forms_found=5,
            created=2,
            updated=1,
            unchanged=2,
            imported=1,
            incidents=2,
            errors=1,
        )
        with patch(
            "apps.integrations.kobo.hub.build_kobo_api_client",
            return_value=SimpleNamespace(),
        ), patch(
            "apps.integrations.kobo.hub.sync_supported_assets",
            return_value=result,
        ):
            response = self.client.post(
                reverse("kobo:sync_all"),
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importados: 1")
        self.assertContains(response, "Incidencias: 2")
        self.assertNotContains(response, "<html")
        self.assertNotContains(response, "ops-page-header")
        self.assertNotContains(response, "Sincronizar KoboToolbox")

    def test_sync_all_requires_change_asset_permission(self):
        user = get_user_model().objects.create_user("hub-sync-reader")
        user.user_permissions.add(
            Permission.objects.get(codename="view_territorial_administration")
        )
        self.client.force_login(user)
        response = self.client.post(reverse("kobo:sync_all"))
        self.assertEqual(response.status_code, 403)

    def test_sync_all_requires_csrf_when_enforced(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.actor)
        response = csrf_client.post(reverse("kobo:sync_all"))
        self.assertEqual(response.status_code, 403)

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
