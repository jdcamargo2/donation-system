from apps.integrations.kobo.client import KoboRemoteAsset
from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.forms import KoboAssetProjectLinkForm
from apps.integrations.kobo.forms import KoboProjectBindingForm
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboDiscoveredAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.services import activate_kobo_asset
from apps.integrations.kobo.services import associate_submission_with_project
from apps.integrations.kobo.services import configure_discovered_asset
from apps.integrations.kobo.services import create_project_binding
from apps.integrations.kobo.services import deactivate_kobo_asset
from apps.integrations.kobo.services import discover_assets
from apps.integrations.kobo.services import get_asset_readiness
from apps.integrations.kobo.services import link_asset_to_project
from apps.integrations.kobo.services import unlink_asset_from_project
from apps.integrations.kobo.services import validate_routing_source_field
from apps.operations.models import Project
from apps.operations.models import ProjectUpdate
from datetime import datetime
from datetime import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone
from io import StringIO
from unittest.mock import patch


class StubAssetClient:
    def __init__(self, assets=(), exception=None, details=None):
        self.assets = tuple(assets)
        self.exception = exception
        self.details = details or {}
        self.calls = []

    def list_assets(self, *, limit=100):
        # PRE: discovery supplies a positive page size.
        # POST: records the call and returns or raises the configured result.
        self.calls.append(limit)
        if self.exception is not None:
            raise self.exception
        return self.assets

    def get_asset_detail(self, asset_uid):
        # PRE: discovery requests technical metadata for one listed asset UID.
        # POST: returns configured safe detail metadata or raises its configured error.
        detail = self.details.get(asset_uid, {})
        if isinstance(detail, Exception):
            raise detail
        return detail


class KoboAssetDiscoveryTests(TestCase):
    def remote_asset(self, uid="discovered-1", **overrides):
        # PRE: uid identifies one validated remote discovery projection.
        # POST: returns immutable safe metadata with requested overrides.
        values = {
            "asset_uid": uid,
            "name": f"Discovered {uid}",
            "asset_type": "survey",
            "deployment_status": "deployed",
            "owner_username": "owner-user",
            "created_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
            "modified_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
            "safe_metadata": {
                "uid": uid,
                "name": f"Discovered {uid}",
                "asset_type": "survey",
            },
        }
        values.update(overrides)
        return KoboRemoteAsset(**values)

    def test_discovery_creates_assets_without_configuring_integrations(self):
        project = Project.objects.create(
            code="PRJ-DISCOVERY",
            name="Unchanged project",
        )

        result = discover_assets(StubAssetClient([self.remote_asset()]))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.created_count, 1)
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")
        self.assertTrue(discovered.is_available)
        self.assertEqual(discovered.metadata_snapshot["uid"], "discovered-1")
        self.assertFalse(KoboAsset.objects.exists())
        self.assertFalse(KoboProjectBinding.objects.exists())
        self.assertFalse(KoboFormDefinition.objects.exists())
        project.refresh_from_db()
        self.assertEqual(project.name, "Unchanged project")

    def test_second_discovery_is_idempotent_and_updates_last_seen(self):
        client = StubAssetClient([self.remote_asset()])
        discover_assets(client)
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")
        old_seen_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        discovered.last_seen_at = old_seen_at
        discovered.save(update_fields=("last_seen_at",))

        result = discover_assets(client)
        discovered.refresh_from_db()

        self.assertEqual(KoboDiscoveredAsset.objects.count(), 1)
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.unchanged_count, 1)
        self.assertGreater(discovered.last_seen_at, old_seen_at)

    def test_discovery_updates_name_and_safe_metadata(self):
        discover_assets(StubAssetClient([self.remote_asset()]))
        changed = self.remote_asset(
            name="Updated safe name",
            safe_metadata={"uid": "discovered-1", "name": "Updated safe name"},
        )

        result = discover_assets(StubAssetClient([changed]))
        discovered = KoboDiscoveredAsset.objects.get(asset_uid="discovered-1")

        self.assertEqual(result.updated_count, 1)
        self.assertEqual(discovered.name, "Updated safe name")
        self.assertEqual(discovered.metadata_snapshot["name"], "Updated safe name")

    def test_complete_discovery_marks_absent_assets_unavailable(self):
        discover_assets(
            StubAssetClient(
                [self.remote_asset("present"), self.remote_asset("absent")]
            )
        )

        result = discover_assets(StubAssetClient([self.remote_asset("present")]))

        self.assertEqual(result.unavailable_count, 1)
        self.assertTrue(
            KoboDiscoveredAsset.objects.get(asset_uid="present").is_available
        )
        self.assertFalse(
            KoboDiscoveredAsset.objects.get(asset_uid="absent").is_available
        )

    def test_failed_discovery_does_not_mark_absent_assets_unavailable(self):
        discover_assets(StubAssetClient([self.remote_asset("existing")]))

        with self.assertRaises(KoboIntegrationError):
            discover_assets(
                StubAssetClient(exception=KoboIntegrationError("safe failure"))
            )

        self.assertTrue(
            KoboDiscoveredAsset.objects.get(asset_uid="existing").is_available
        )

    @override_settings(KOBO_ENABLED=False)
    def test_command_blocks_when_kobo_is_disabled(self):
        with self.assertRaises(CommandError):
            call_command("discover_kobo_assets", stdout=StringIO())

    @override_settings(
        KOBO_ENABLED=True,
        KOBO_BASE_URL="https://kf.example.test",
        KOBO_API_TOKEN="command-discovery-secret",
        KOBO_REQUEST_TIMEOUT_SECONDS=15,
    )
    def test_command_dry_run_does_not_persist_or_print_sensitive_data(self):
        remote_asset = self.remote_asset(
            name="https://private.example.test/signed",
        )
        output = StringIO()

        with patch(
            "apps.integrations.kobo.management.commands.discover_kobo_assets.KoboApiClient",
            return_value=StubAssetClient([remote_asset]),
        ):
            call_command(
                "discover_kobo_assets",
                dry_run=True,
                limit=25,
                stdout=output,
            )

        self.assertFalse(KoboDiscoveredAsset.objects.exists())
        self.assertIn("fetched=1 would_create=1 would_update=0", output.getvalue())
        self.assertNotIn("command-discovery-secret", output.getvalue())
        self.assertNotIn("https://", output.getvalue())


@override_settings(KOBO_ENABLED=True)
class KoboAssetManualConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # PRE: auth, Kobo and operations models are migrated.
        # POST: creates reusable actors and one supported active definition.
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user("kobo-config-viewer")
        cls.editor = user_model.objects.create_user("kobo-config-editor")
        permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(
                codename__in=("view_koboasset", "change_koboasset")
            )
        }
        cls.viewer.user_permissions.add(permissions["view_koboasset"])
        cls.editor.user_permissions.add(
            permissions["view_koboasset"],
            permissions["change_koboasset"],
            Permission.objects.get(
                content_type__app_label="operations",
                codename="change_project",
            ),
        )
        cls.definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 - Identificación territorial del Núcleo Vital (depurada)",
            version=FICHA_01_VERSION,
        )

    def setUp(self):
        self.discovered = KoboDiscoveredAsset.objects.create(
            asset_uid="manual-config-uid-sensitive-tail",
            name="Activo descubierto",
            asset_type="survey",
            deployment_status="deployed",
            metadata_snapshot={
                "uid": "manual-config-uid-sensitive-tail",
                "name": "Activo descubierto",
            },
            last_seen_at=django_timezone.now(),
        )
        self.project = Project.objects.create(
            code="PRJ-K13C",
            name="Proyecto K13C",
            status=Project.Status.ACTIVE,
        )

    def configure(self):
        # PRE: the default discovery is available and not configured.
        # POST: returns its newly configured inactive local asset.
        return configure_discovered_asset(
            self.discovered,
            name="Integración manual",
            form_definition=self.definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            configured_by=self.editor,
        )

    def test_configuration_uses_remote_uid_and_stays_isolated_inactive(self):
        project_count = Project.objects.count()
        asset = self.configure()

        self.assertEqual(asset.asset_uid, self.discovered.asset_uid)
        self.assertFalse(asset.is_active)
        self.assertFalse(KoboProjectBinding.objects.exists())
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertEqual(Project.objects.count(), project_count)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_configuration_rejects_unavailable_duplicate_and_unsupported_definition(self):
        self.discovered.is_available = False
        self.discovered.save(update_fields=("is_available",))
        with self.assertRaises(ValidationError):
            self.configure()
        self.discovered.is_available = True
        self.discovered.save(update_fields=("is_available",))
        self.configure()
        with self.assertRaises(ValidationError):
            self.configure()

        other = KoboDiscoveredAsset.objects.create(
            asset_uid="unsupported-definition-asset",
            name="Unsupported",
            last_seen_at=django_timezone.now(),
        )
        unsupported = KoboFormDefinition.objects.create(
            form_id="not_registered", title="No registrada", version="1"
        )
        with self.assertRaises(ValidationError):
            configure_discovered_asset(
                other,
                name="No permitida",
                form_definition=unsupported,
                form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
                configured_by=self.editor,
            )

    def test_binding_validation_and_strategy_exclusion(self):
        asset = self.configure()
        direct = create_project_binding(
            asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            project=self.project,
            source_field="",
            source_value="",
            is_active=True,
            configured_by=self.editor,
        )
        self.assertTrue(direct.is_active)
        self.assertFalse(asset.is_active)
        with self.assertRaises(ValidationError):
            create_project_binding(
                asset,
                routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
                project=self.project,
                source_field="submission.parish",
                source_value="parish-1",
                is_active=True,
                configured_by=self.editor,
            )

    def test_field_value_routes_and_unsafe_sources(self):
        asset = self.configure()
        for value in ("parish-1", "parish-2"):
            create_project_binding(
                asset,
                routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
                project=self.project,
                source_field="payload.parish_key",
                source_value=value,
                is_active=True,
                configured_by=self.editor,
            )
        self.assertTrue(get_asset_readiness(asset).ready)
        for source_field in (
            "payload._private",
            "raw_payload.secret",
            "payload.path/value",
            "payload.items[0]",
            "payload.two..parts",
            "payload.with space",
        ):
            with self.subTest(source_field=source_field):
                with self.assertRaises(ValidationError):
                    validate_routing_source_field(source_field)

    def test_readiness_activation_and_deactivation_preserve_bindings(self):
        asset = self.configure()
        self.assertEqual(get_asset_readiness(asset).code, "no_active_bindings")
        create_project_binding(
            asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            project=self.project,
            source_field="",
            source_value="",
            is_active=True,
            configured_by=self.editor,
        )
        self.assertEqual(get_asset_readiness(asset).code, "ready_to_activate")
        activate_kobo_asset(asset, activated_by=self.editor)
        self.assertTrue(asset.is_active)
        deactivate_kobo_asset(asset, deactivated_by=self.editor)
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.project_bindings.count(), 1)

    def test_readiness_rejects_mixed_routing_and_inactive_definition(self):
        asset = self.configure()
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
            is_active=True,
        )
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="submission.parish",
            source_value="parish-1",
            is_active=True,
        )
        self.assertEqual(get_asset_readiness(asset).code, "mixed_routing")
        with self.assertRaises(ValidationError):
            activate_kobo_asset(asset, activated_by=self.editor)

        self.definition.is_active = False
        self.definition.save(update_fields=("is_active",))
        self.assertEqual(get_asset_readiness(asset).code, "missing_form_definition")

    def test_browser_surfaces_require_login_permissions_and_do_not_mutate_on_get(self):
        list_url = reverse("kobo:discovered_asset_list")
        detail_url = reverse("kobo:discovered_asset_detail", args=(self.discovered.pk,))
        self.assertEqual(self.client.get(list_url).status_code, 302)
        self.client.force_login(self.editor)
        before = (KoboAsset.objects.count(), KoboProjectBinding.objects.count())
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activo descubierto, aún no configurado")
        self.assertEqual(before, (KoboAsset.objects.count(), KoboProjectBinding.objects.count()))

        self.client.force_login(get_user_model().objects.create_user("no-kobo-permission"))
        self.assertEqual(self.client.get(list_url).status_code, 403)

    def test_incompatible_discovery_hides_form_and_blocks_configuration_post(self):
        self.client.force_login(self.editor)
        detail_url = reverse(
            "kobo:discovered_asset_detail", args=(self.discovered.pk,)
        )
        response = self.client.get(detail_url)

        self.assertContains(
            response,
            "Este activo fue descubierto, pero todavía no tiene una definición "
            "soportada en SIGEDON.",
        )
        self.assertNotContains(response, "Configurar activo")

        response = self.client.post(
            reverse("kobo:configure_discovered_asset", args=(self.discovered.pk,)),
            {
                "name": "Intento incompatible",
                "form_definition": self.definition.pk,
                "form_role": KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboAsset.objects.exists())

    def test_compatible_discovery_exposes_only_fixed_definition_and_role(self):
        self.discovered.metadata_snapshot["id_string"] = FICHA_01_FORM_ID
        self.discovered.metadata_snapshot["version"] = FICHA_01_VERSION
        self.discovered.save(update_fields=("metadata_snapshot",))
        other_definition = KoboFormDefinition.objects.create(
            form_id="ficha_02_capacidad_parroquial",
            title="Ficha 02 - Capacidad parroquial",
            version="20260710",
        )
        self.client.force_login(self.editor)
        detail_url = reverse(
            "kobo:discovered_asset_detail", args=(self.discovered.pk,)
        )
        response = self.client.get(detail_url)

        self.assertContains(response, "Configurar activo")
        form = response.context["configuration_form"]
        self.assertEqual(list(form.fields["form_definition"].queryset), [self.definition])
        self.assertEqual(
            tuple(value for value, _label in form.fields["form_role"].choices),
            (KoboAsset.FormRole.TERRITORIAL_PROFILE,),
        )

        configure_url = reverse(
            "kobo:configure_discovered_asset", args=(self.discovered.pk,)
        )
        tampered = self.client.post(
            configure_url,
            {
                "name": "Manipulado",
                "form_definition": other_definition.pk,
                "form_role": KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            },
        )
        self.assertEqual(tampered.status_code, 400)
        self.assertFalse(KoboAsset.objects.exists())

        valid = self.client.post(
            configure_url,
            {
                "name": "Compatible",
                "form_definition": self.definition.pk,
                "form_role": KoboAsset.FormRole.TERRITORIAL_PROFILE,
            },
        )
        self.assertEqual(valid.status_code, 302)
        asset = KoboAsset.objects.get()
        self.assertEqual(asset.form_definition, self.definition)
        self.assertEqual(asset.form_role, KoboAsset.FormRole.TERRITORIAL_PROFILE)

    @override_settings(KOBO_ENABLED=False)
    def test_disabled_feature_hides_configuration_surfaces(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("kobo:discovered_asset_list"))
        self.assertEqual(response.status_code, 404)

    def test_binding_form_rejects_tampered_project_and_invalid_shapes(self):
        form = KoboProjectBindingForm(
            {
                "routing_type": KoboProjectBinding.RoutingType.DIRECT,
                "project": self.project.pk + 9999,
                "source_field": "submission.parish",
                "source_value": "x",
                "is_active": "on",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("project", form.errors)

    @override_settings(KOBO_ENABLED=True)
    def test_operational_configuration_hides_technical_routing_fields(self):
        asset = self.configure()
        self.client.force_login(self.editor)

        response = self.client.get(
            reverse("kobo:asset_configuration", args=(asset.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proyecto actualmente enlazado")
        self.assertContains(response, "Enlazar proyecto")
        self.assertNotContains(response, "routing_type")
        self.assertNotContains(response, "source_field")
        self.assertNotContains(response, "source_value")
        self.assertNotContains(response, "no_active_bindings")
        self.assertIsInstance(response.context["binding_form"], KoboAssetProjectLinkForm)

    @override_settings(KOBO_ENABLED=True)
    def test_operational_link_ignores_technical_post_fields_and_preserves_history(self):
        asset = self.configure()
        historical = KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="PRJ-000001",
            is_active=True,
        )
        self.client.force_login(self.editor)

        response = self.client.post(
            reverse("kobo:create_project_binding", args=(asset.pk,)),
            {
                "project": self.project.pk,
                "routing_type": KoboProjectBinding.RoutingType.FIELD_VALUE,
                "source_field": "payload.nucleo_code",
                "source_value": "PRJ-000001",
                "is_active": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha enlazada correctamente")
        historical.refresh_from_db()
        direct = KoboProjectBinding.objects.get(
            asset=asset,
            routing_type=KoboProjectBinding.RoutingType.DIRECT,
        )
        asset.refresh_from_db()
        self.assertTrue(direct.is_active)
        self.assertEqual(direct.project, self.project)
        self.assertEqual(direct.source_field, "")
        self.assertEqual(direct.source_value, "")
        self.assertFalse(historical.is_active)
        self.assertTrue(asset.is_active)
        self.assertEqual(asset.project_bindings.filter(is_active=True).count(), 1)

    def test_link_change_and_unlink_preserve_submission_history(self):
        asset = self.configure()
        other_project = Project.objects.create(
            code="PRJ-K13C-OTHER",
            name="Proyecto K13C alterno",
            status=Project.Status.ACTIVE,
        )
        historical_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="historical-linked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.IMPORTED,
            project=self.project,
            asset=asset,
            processed_at=django_timezone.now(),
        )
        link_asset_to_project(asset, project=self.project, linked_by=self.editor)
        link_asset_to_project(asset, project=other_project, linked_by=self.editor)

        asset.refresh_from_db()
        active_binding = asset.project_bindings.get(is_active=True)
        historical_submission.refresh_from_db()
        self.assertEqual(active_binding.project, other_project)
        self.assertEqual(asset.project_bindings.filter(is_active=True).count(), 1)
        self.assertEqual(historical_submission.project, self.project)

        new_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="new-linked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        result = associate_submission_with_project(
            new_submission,
            reviewed_by=self.editor,
        )
        new_submission.refresh_from_db()
        self.assertFalse(result.associated)
        self.assertIsNone(new_submission.project)
        self.assertEqual(
            new_submission.status,
            KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )

        unlink_asset_from_project(asset, unlinked_by=self.editor)
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)
        self.assertFalse(asset.project_bindings.filter(is_active=True).exists())
        historical_submission.refresh_from_db()
        self.assertEqual(historical_submission.project, self.project)

        unlinked_submission = KoboSubmission.objects.create(
            form_definition=self.definition,
            external_id="unlinked-submission",
            raw_payload={"_xform_id_string": asset.asset_uid},
            normalized_payload={},
            status=KoboSubmission.Status.APPROVED_FOR_IMPORT,
        )
        result = associate_submission_with_project(
            unlinked_submission,
            reviewed_by=self.editor,
        )
        self.assertFalse(result.associated)
        unlinked_submission.refresh_from_db()
        self.assertEqual(
            unlinked_submission.error_code,
            "IMPORT_ROUTING_UNRESOLVED",
        )

    def test_operational_link_rejects_inactive_definition_and_unsupported_asset(self):
        asset = self.configure()
        inactive_project = Project.objects.create(
            code="PRJ-K13C-INACTIVE",
            name="Proyecto K13C inactivo",
            status=Project.Status.SUSPENDED,
        )
        with self.assertRaises(ValidationError):
            link_asset_to_project(
                asset,
                project=inactive_project,
                linked_by=self.editor,
            )

        self.definition.is_active = False
        self.definition.save(update_fields=("is_active",))
        with self.assertRaises(ValidationError):
            link_asset_to_project(asset, project=self.project, linked_by=self.editor)

        self.definition.is_active = True
        self.definition.save(update_fields=("is_active",))
        asset.form_role = KoboAsset.FormRole.PRIORITIZED_MICROPROJECT
        asset.save(update_fields=("form_role",))
        with self.assertRaises(ValidationError):
            link_asset_to_project(asset, project=self.project, linked_by=self.editor)
