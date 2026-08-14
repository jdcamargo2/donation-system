"""Disconnected public-demo edition: Kobo is visible, remote operation is off."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.integrations.kobo.demo import DEMO_ENDPOINT, DEMO_MESSAGE, DEMO_STATUS
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import KoboFormDefinition, KoboSubmission
from apps.operations.models import Project
from apps.operations.role_services import sync_operation_roles
from apps.operations.roles import ROLE_EXTERNAL_AUDITOR, ROLE_FIELD_OPERATOR
from apps.operations.tests.helpers import create_project


@override_settings(KOBO_ENABLED=False)
class KoboDisconnectedDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.operator = user_model.objects.create_user(
            username="kobo-demo-operator",
            password="test-password",
        )
        view_permission = Permission.objects.get(codename="view_kobosubmission")
        change_permission = Permission.objects.get(codename="change_kobosubmission")
        hub_permission = Permission.objects.get(
            codename="view_territorial_administration"
        )
        cls.operator.user_permissions.add(
            view_permission,
            change_permission,
            hub_permission,
            Permission.objects.get(codename="view_koboasset"),
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 01 - Territorio",
            version=FICHA_01_VERSION,
        )
        cls.submission = KoboSubmission.objects.create(
            form_definition=cls.form_definition,
            external_id="demo-disabled-submission",
            raw_payload={"_uuid": "demo-disabled-submission"},
            status=KoboSubmission.Status.RECEIVED,
        )

    def setUp(self):
        self.client.force_login(self.operator)

    def test_operational_kobo_routes_return_404(self):
        get_paths = (
            reverse("kobo:submission_list"),
            reverse("kobo:submission_detail", args=(self.submission.pk,)),
            reverse("kobo:pending_submission_list"),
            reverse("kobo:mapping_list"),
            reverse("kobo:sync_history"),
            reverse("kobo:discovered_asset_list"),
        )
        for path in get_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

        post_paths = (
            reverse("kobo:submission_retry_normalization", args=(self.submission.pk,)),
            reverse("kobo:submission_retry_attachments", args=(self.submission.pk,)),
            reverse("kobo:sync_all"),
        )
        for path in post_paths:
            with self.subTest(path=path):
                with patch(
                    "apps.integrations.kobo.views.build_kobo_api_client"
                ) as views_client, patch(
                    "apps.integrations.kobo.hub.build_kobo_api_client"
                ) as hub_client:
                    response = self.client.post(path)
                self.assertEqual(response.status_code, 404)
                views_client.assert_not_called()
                hub_client.assert_not_called()

    def test_process_kobo_submissions_fails_cleanly_without_http_client(self):
        with patch(
            "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client"
        ) as client_factory, patch(
            "urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaisesMessage(
                CommandError,
                "Kobo integration is disabled.",
            ):
                call_command(
                    "process_kobo_submissions",
                    download_attachments=True,
                    stdout=StringIO(),
                )
        client_factory.assert_not_called()
        urlopen.assert_not_called()
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, KoboSubmission.Status.RECEIVED)

    def test_internal_demo_ui_shows_disconnected_capability(self):
        response = self.client.get(reverse("kobo:hub"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "kobo/hub/demo.html")
        self.assertContains(response, DEMO_MESSAGE)
        self.assertContains(response, DEMO_STATUS)
        self.assertContains(response, DEMO_ENDPOINT)
        self.assertContains(response, "Demo Field Operations")
        self.assertContains(response, "Ficha 1 · Registro territorial")
        self.assertContains(response, "demo_asset_territorial_01")
        self.assertContains(response, "No disponible en edición demo")
        self.assertContains(response, "MANGO-FIELD-01")
        self.assertContains(response, "Integración disponible")
        self.assertNotContains(response, "Sincronizar KoboToolbox")
        self.assertNotContains(response, reverse("kobo:sync_all"))
        self.assertNotContains(response, reverse("kobo:pending_submission_list"))
        self.assertNotContains(response, "hx-get")

    def test_public_portal_does_not_show_kobo_demo_ui(self):
        self.client.logout()
        project = create_project()
        project.is_public = True
        project.status = Project.Status.ACTIVE
        project.save(update_fields=("is_public", "status"))
        public_urls = (
            reverse("public_portal:public_home"),
            reverse("public_portal:public_project_list"),
            reverse("public_portal:public_project_detail", args=(project.pk,)),
            reverse("public_portal:public_updates_feed"),
        )
        for path in public_urls:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, DEMO_MESSAGE)
                self.assertNotContains(response, DEMO_ENDPOINT)
                self.assertNotContains(response, "KoboToolbox")
                self.assertNotContains(response, reverse("kobo:hub"))

        for template_path in Path("templates/public_portal").glob("*.html"):
            source = template_path.read_text()
            self.assertNotIn("kobo:hub", source)
            self.assertNotIn("kobo-demo.example.invalid", source)
            self.assertNotIn("KoboToolbox", source)

    def test_sidebar_shows_kobo_capability_for_authorized_internal_users(self):
        sync_operation_roles()
        user_model = get_user_model()
        auditor = user_model.objects.create_user("demo-auditor")
        auditor.groups.add(Group.objects.get(name=ROLE_EXTERNAL_AUDITOR))
        operator = user_model.objects.create_user("demo-field")
        operator.groups.add(Group.objects.get(name=ROLE_FIELD_OPERATOR))

        self.client.force_login(auditor)
        auditor_dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(auditor_dashboard, "KoboToolbox")
        self.assertContains(auditor_dashboard, reverse("kobo:hub"))

        self.client.force_login(operator)
        operator_dashboard = self.client.get(reverse("dashboard"))
        self.assertNotContains(operator_dashboard, "KoboToolbox")
        self.assertNotContains(operator_dashboard, reverse("kobo:hub"))


@override_settings(KOBO_ENABLED=True)
class KoboEnabledContractsRemainAvailableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="kobo-on-viewer",
            password="test-password",
        )
        cls.viewer.user_permissions.add(
            Permission.objects.get(codename="view_kobosubmission"),
            Permission.objects.get(codename="view_territorial_administration"),
        )
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 01 - Territorio",
            version=FICHA_01_VERSION,
        )
        cls.submission = KoboSubmission.objects.create(
            form_definition=cls.form_definition,
            external_id="demo-enabled-submission",
            raw_payload={
                "_uuid": "demo-enabled-submission",
                "today": "2026-07-12",
                "nucleo_code": "NV-001",
                "pastoral_zone": "catia_la_mar",
                "parish": "aurora",
                "community_sector": "rio_claro",
                "location": "0 0",
                "estimated_households": 10000,
                "access_difficulties": "unknown",
                "initial_priority_perception": "medium",
                "_attachments": [],
            },
            status=KoboSubmission.Status.RECEIVED,
        )

    def setUp(self):
        self.client.force_login(self.viewer)

    def test_operational_list_and_hub_remain_available(self):
        listing = self.client.get(reverse("kobo:submission_list"))
        detail = self.client.get(
            reverse("kobo:submission_detail", args=(self.submission.pk,))
        )
        hub = self.client.get(reverse("kobo:hub"))

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(hub.status_code, 200)
        self.assertTemplateUsed(hub, "kobo/hub/dashboard.html")
        self.assertNotContains(hub, DEMO_MESSAGE)
        self.assertNotContains(hub, DEMO_ENDPOINT)
        self.assertContains(hub, "Sincronizar KoboToolbox")
        self.assertNotContains(listing, DEMO_STATUS)

    def test_process_kobo_submissions_runs_when_enabled(self):
        stdout = StringIO()
        with patch(
            "apps.integrations.kobo.management.commands.process_kobo_submissions.build_kobo_api_client"
        ) as client_factory:
            call_command("process_kobo_submissions", stdout=stdout)
        client_factory.assert_not_called()
        self.assertIn("selected=1", stdout.getvalue())
        self.assertIn("failed=0", stdout.getvalue())
