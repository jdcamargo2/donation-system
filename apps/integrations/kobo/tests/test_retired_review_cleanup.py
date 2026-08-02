"""Architectural assertions for the retired Kobo human-review surface."""

from importlib import import_module
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAsset, KoboFormDefinition, KoboSubmission
from apps.operations.models import Project


RETIRED_URL_NAMES = (
    "kobo:submission_review",
    "kobo:project_pending_submission_review",
    "kobo:project_pending_submission_import",
    "kobo:project_pending_submission_reject",
    "kobo:project_rejected_submission_restore",
)

RETIRED_LITERAL_PATHS = (
    "/integrations/kobo/submissions/1/review/",
    "/integrations/kobo/projects/1/pending-submissions/1/",
    "/integrations/kobo/projects/1/pending-submissions/1/import/",
    "/integrations/kobo/projects/1/pending-submissions/1/reject/",
    "/integrations/kobo/projects/1/submission-history/1/restore/",
)

RETIRED_FORM_NAMES = ("KoboReviewForm", "KoboRejectionForm")
RETIRED_SERVICE_NAMES = (
    "review_submission",
    "reject_kobo_submission",
    "restore_kobo_submission_to_review",
)


@override_settings(KOBO_ENABLED=True)
class RetiredKoboHumanReviewCleanupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.viewer = user_model.objects.create_user(
            username="retired-review-viewer",
            password="test-password",
        )
        cls.operator = user_model.objects.create_user(
            username="retired-review-operator",
            password="test-password",
        )
        cls.changer = user_model.objects.create_user(
            username="retired-review-changer",
            password="test-password",
        )
        view_perm = Permission.objects.get(codename="view_kobosubmission")
        change_perm = Permission.objects.get(codename="change_kobosubmission")
        project_view = Permission.objects.get(codename="view_project")
        project_change = Permission.objects.get(codename="change_project")
        hub_perm = Permission.objects.get(codename="view_territorial_administration")
        cls.viewer.user_permissions.add(view_perm, project_view, hub_perm)
        cls.operator.user_permissions.add(project_view, project_change)
        cls.changer.user_permissions.add(view_perm, change_perm, project_view, hub_perm)
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 01 cleanup",
            version=FICHA_01_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid="cleanup-asset",
            name="Cleanup asset",
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        cls.project = Project.objects.create(
            code="PRJ-CLEANUP-01",
            name="Cleanup project",
            status=Project.Status.ACTIVE,
        )
        cls.submission = KoboSubmission.objects.create(
            form_definition=cls.form_definition,
            asset=cls.asset,
            project=cls.project,
            external_id="cleanup-submission",
            raw_payload={"_uuid": "cleanup-submission"},
            normalized_payload={"nucleo_code": "NV-CLEAN"},
            status=KoboSubmission.Status.IMPORTED,
        )

    def test_removed_url_names_raise_no_reverse_match(self):
        for name in RETIRED_URL_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name, args=(1, 1) if "project" in name else (1,))

    def test_old_literal_paths_return_normal_404(self):
        self.client.force_login(self.changer)
        for path in RETIRED_LITERAL_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)

    def test_active_submission_detail_remains_reachable(self):
        self.client.force_login(self.viewer)
        url = reverse("kobo:submission_detail", args=(self.submission.pk,))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("review_form", response.context)
        self.assertNotContains(response, "Aprobar e importar")
        self.assertNotContains(response, "Rechazar formulario")
        self.assertNotContains(response, "Solicitar corrección")

    def test_project_submission_history_remains_reachable(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_history", args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("can_restore_kobo_submissions", response.context)

    def test_retry_import_remains_post_only_and_permission_protected(self):
        url = reverse("kobo:retry_submission_import", args=(self.submission.pk,))
        # Hub read is required before change permission is evaluated.
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.changer)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_imported_detail_remains_reachable(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("kobo:project_submission_detail", args=(self.submission.pk,))
        )
        self.assertEqual(response.status_code, 200)

    def test_territorial_hub_remains_available(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("kobo:hub"))
        self.assertEqual(response.status_code, 200)

    def test_retired_forms_and_services_are_gone(self):
        forms_module = import_module("apps.integrations.kobo.forms")
        services_module = import_module("apps.integrations.kobo.services")
        for name in RETIRED_FORM_NAMES:
            with self.subTest(form=name):
                self.assertFalse(hasattr(forms_module, name))
        for name in RETIRED_SERVICE_NAMES:
            with self.subTest(service=name):
                self.assertFalse(hasattr(services_module, name))
                self.assertNotIn(name, services_module.__all__)

    def test_active_templates_have_no_human_review_controls(self):
        templates_root = Path(__file__).resolve().parents[4] / "templates" / "kobo"
        forbidden = (
            "Aprobar e importar",
            "Solicitar corrección",
            "Rechazar formulario",
            "Registrar decisión",
            "kobo:submission_review",
            "kobo:project_pending_submission_review",
            "kobo:project_pending_submission_import",
            "kobo:project_pending_submission_reject",
            "kobo:project_rejected_submission_restore",
        )
        for template_path in templates_root.rglob("*.html"):
            content = template_path.read_text()
            for marker in forbidden:
                with self.subTest(template=str(template_path), marker=marker):
                    self.assertNotIn(marker, content)
