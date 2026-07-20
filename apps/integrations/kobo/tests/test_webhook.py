from apps.integrations.kobo.errors import KoboIntegrationError
from apps.integrations.kobo.errors import KoboPayloadError
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset
from apps.integrations.kobo.models import KoboDiscoveredAsset
from apps.integrations.kobo.models import KoboFormDefinition
from apps.integrations.kobo.models import KoboProcessingEvent
from apps.integrations.kobo.models import KoboProjectBinding
from apps.integrations.kobo.models import KoboPastoralZoneProjectMapping
from apps.integrations.kobo.models import KoboSubmission
from apps.integrations.kobo.models import KoboTerritorialIdentity
from apps.integrations.kobo.services import associate_submission_with_project
from apps.integrations.kobo.services import receive_webhook_submission
from apps.integrations.kobo.tests.test_contracts import KoboFicha01NormalizerTests
from apps.integrations.kobo.tests.test_contracts import KoboFicha10NormalizerTests
from apps.integrations.kobo.tests.test_contracts import KoboFicha11NormalizerTests
from apps.integrations.kobo.territorial import normalize_nucleo_code
from apps.operations.models import Project
from apps.operations.models import ProjectUpdate
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
import base64
import json


@override_settings(
    KOBO_ENABLED=True,
    KOBO_WEBHOOK_USERNAME="sigedon-kobo",
    KOBO_WEBHOOK_SECRET="test-webhook-secret",
)
class KoboWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        definitions = (
            (FICHA_01_FORM_ID, FICHA_01_VERSION, KoboAsset.FormRole.TERRITORIAL_PROFILE),
            (FICHA_10_FORM_ID, FICHA_10_VERSION, KoboAsset.FormRole.PRIORITIZED_MICROPROJECT),
            (FICHA_11_FORM_ID, FICHA_11_VERSION, KoboAsset.FormRole.PRIORITIZATION_MATRIX),
        )
        cls.project = Project.objects.create(
            code="PRJ-WEBHOOK-DIRECT",
            name="Proyecto sintético de webhook",
            status=Project.Status.ACTIVE,
        )
        cls.assets = {}
        for index, (form_id, version, role) in enumerate(definitions, start=1):
            definition = KoboFormDefinition.objects.create(
                form_id=form_id, version=version, title=f"Webhook {index}"
            )
            cls.assets[form_id] = KoboAsset.objects.create(
                asset_uid=f"webhook-asset-{index}",
                name=f"Webhook asset {index}",
                form_definition=definition,
                form_role=role,
            )
            KoboDiscoveredAsset.objects.create(
                asset_uid=cls.assets[form_id].asset_uid,
                name=f"Webhook discovery {index}",
                metadata_snapshot={"id_string": form_id, "version": version},
                last_seen_at=django_timezone.now(),
            )
            KoboProjectBinding.objects.create(
                asset=cls.assets[form_id],
                project=cls.project,
                routing_type=KoboProjectBinding.RoutingType.DIRECT,
            )
        KoboPastoralZoneProjectMapping.objects.create(
            pastoral_zone="catia_la_mar",
            project=cls.project,
        )

    def payload(self, form_id, **overrides):
        # PRE: form_id identifies an active webhook asset.
        # POST: returns a valid payload for that exact supported contract.
        asset = self.assets[form_id]
        payload = {"_uuid": f"webhook-{form_id}", "_xform_id_string": asset.asset_uid}
        if form_id == FICHA_01_FORM_ID:
            payload.update(KoboFicha01NormalizerTests().valid_payload())
        elif form_id == FICHA_10_FORM_ID:
            payload.update(KoboFicha10NormalizerTests().valid_payload())
        else:
            payload.update(KoboFicha11NormalizerTests().valid_payload())
        payload["_uuid"] = f"webhook-{form_id}"
        payload["_xform_id_string"] = asset.asset_uid
        payload.update(overrides)
        return payload

    def ficha_01_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 1 data.
        # POST: returns a valid asset-UID payload with slash-separated field paths.
        payload = KoboFicha01NormalizerTests().slash_payload(
            _uuid="webhook-ficha-01-slash",
            _xform_id_string=self.assets[FICHA_01_FORM_ID].asset_uid,
        )
        payload.update(overrides)
        return payload

    def ficha_10_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 10 data.
        # POST: returns a valid asset-UID payload with slash-separated microproject fields.
        payload = self.payload(FICHA_10_FORM_ID)
        microproject = payload.pop("microproject")
        payload.update(
            {f"microproject/{key}": value for key, value in microproject.items()}
        )
        payload["_uuid"] = "webhook-ficha-10-slash"
        payload.update(overrides)
        return payload

    def ficha_11_slash_payload(self, **overrides):
        # PRE: overrides contains only synthetic Kobo REST Services Ficha 11 data.
        # POST: returns a valid asset-UID payload with slash-separated scoring fields.
        payload = self.payload(FICHA_11_FORM_ID)
        scoring = payload.pop("scoring")
        payload.update({f"scoring/{key}": value for key, value in scoring.items()})
        payload["_uuid"] = "webhook-ficha-11-slash"
        payload.update(overrides)
        return payload

    def post(self, payload, *, secret="test-webhook-secret"):
        return self.client.post(
            reverse("kobo:webhook_submission"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_KOBO_WEBHOOK_SECRET=secret,
        )

    def create_territorial_identity(self, code, *, project=None):
        # PRE: code is canonical and project is a valid synthetic destination.
        # POST: creates an explicit Ficha 1-sourced identity without using bindings.
        project = project or self.project
        source = KoboSubmission.objects.create(
            form_definition=self.assets[FICHA_01_FORM_ID].form_definition,
            asset=self.assets[FICHA_01_FORM_ID],
            external_id=f"identity-source-{code}-{KoboSubmission.objects.count()}",
            raw_payload={"_uuid": f"identity-source-{code}"},
            normalized_payload={"nucleo_code_normalized": code},
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            project=project,
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            pastoral_zone="catia_la_mar",
            routing_status=KoboSubmission.RoutingStatus.RESOLVED,
            routing_resolved_at=django_timezone.now(),
        )
        return KoboTerritorialIdentity.objects.create(
            nucleo_code_original=code,
            nucleo_code_normalized=code,
            pastoral_zone="catia_la_mar",
            project=project,
            source_submission=source,
        )

    def post_basic(
        self,
        payload,
        *,
        username="sigedon-kobo",
        password="test-webhook-secret",
    ):
        # PRE: username and password are test-only Basic credential values.
        # POST: sends one webhook POST using an encoded Authorization header.
        credentials = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return self.client.post(
            reverse("kobo:webhook_submission"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )

    def test_rejects_method_disabled_feature_and_invalid_authentication(self):
        url = reverse("kobo:webhook_submission")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response["WWW-Authenticate"],
            'Basic realm="SIGEDON Kobo Webhook"',
        )
        self.assertEqual(self.post({}, secret="wrong").status_code, 401)
        with self.settings(KOBO_ENABLED=False):
            self.assertEqual(self.post({}).status_code, 404)

    def test_basic_authentication_accepts_valid_credentials_and_processes_submission(self):
        response = self.post_basic(self.payload(FICHA_01_FORM_ID))

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=f"webhook-{FICHA_01_FORM_ID}")
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)

    def test_webhook_normalizes_slash_payload_with_asset_uid_and_opaque_version(self):
        payload = self.ficha_01_slash_payload(__version__="deployment-opaque-version")

        response = self.post_basic(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.asset, self.assets[FICHA_01_FORM_ID])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.raw_payload, payload)
        self.assertEqual(submission.raw_payload["__version__"], "deployment-opaque-version")
        self.assertEqual(submission.project, self.project)
        self.assertIsNone(submission.processed_at)
        self.assertTrue(
            submission.processing_events.filter(
                stage="territorial_routing", code="territorial_identity_created"
            ).exists()
        )
        self.assertEqual(submission.parish, "Parroquia sintética")
        self.assertEqual(submission.normalized_payload["nucleo_code"], "NV-SYNTHETIC")

    def test_webhook_ficha_10_uses_identity_over_contrary_direct_binding(self):
        payload = self.ficha_10_slash_payload()
        territorial_project = Project.objects.create(
            code="PRJ-F10-TERRITORIAL",
            name="Proyecto territorial Ficha 10",
            status=Project.Status.ACTIVE,
        )
        self.create_territorial_identity(
            normalize_nucleo_code(payload["nucleo_code"]),
            project=territorial_project,
        )

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, territorial_project)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED
        )
        self.assertFalse(
            submission.processing_events.filter(code="project_assigned").exists()
        )
        self.assertEqual(
            submission.normalized_payload["microproject_name"],
            "Rehabilitación del centro comunitario",
        )

    def test_webhook_ficha_11_uses_identity_and_preserves_calculation_values(self):
        payload = self.ficha_11_slash_payload(
            **{
                "scoring/priority_total": "41",
                "scoring/suggested_semaphore": "red",
            }
        )
        territorial_project = Project.objects.create(
            code="PRJ-F11-TERRITORIAL",
            name="Proyecto territorial Ficha 11",
            status=Project.Status.ACTIVE,
        )
        self.create_territorial_identity(
            normalize_nucleo_code(payload["nucleo_code"]),
            project=territorial_project,
        )

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, territorial_project)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.normalized_payload["priority_total"], 10)
        self.assertEqual(submission.normalized_payload["priority_total_original"], "41")
        self.assertEqual(submission.normalized_payload["priority_total_calculated"], 10)
        self.assertTrue(submission.normalized_payload["calculation_warnings"])

    def test_webhook_rejects_incompatible_discovered_asset_metadata_without_staging(self):
        discovered = KoboDiscoveredAsset.objects.get(
            asset_uid=self.assets[FICHA_01_FORM_ID].asset_uid
        )
        discovered.metadata_snapshot = {
            "id_string": "wrong-form-id",
            "version": FICHA_01_VERSION,
        }
        discovered.save(update_fields=("metadata_snapshot",))

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_webhook_rejects_mismatched_discovered_asset_version_without_staging(self):
        discovered = KoboDiscoveredAsset.objects.get(
            asset_uid=self.assets[FICHA_01_FORM_ID].asset_uid
        )
        discovered.metadata_snapshot["version"] = "wrong-contract-version"
        discovered.save(update_fields=("metadata_snapshot",))

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 400)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_slash_payload_uses_territorial_mapping_not_direct_binding(self):
        project = self.project
        reviewer = get_user_model().objects.create_user("slash-reviewer")
        payload = self.ficha_01_slash_payload(
            **{"identification/nucleo_code": "NOT-A-PROJECT-CODE"}
        )

        response = self.post(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        submission.status = KoboSubmission.Status.APPROVED_FOR_IMPORT
        submission.save(update_fields=("status",))
        result = associate_submission_with_project(submission, reviewed_by=reviewer)
        submission.refresh_from_db()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(result.associated)
        self.assertEqual(submission.project, project)
        self.assertEqual(submission.status, KoboSubmission.Status.IMPORTED)
        self.assertIsNotNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_basic_authentication_rejects_invalid_or_malformed_credentials(self):
        url = reverse("kobo:webhook_submission")
        cases = (
            self.post_basic({}, username="other"),
            self.post_basic({}, password="other"),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Basic not-base64!",
            ),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            ),
            self.client.post(
                url,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Basic Og==",
            ),
        )

        for response in cases:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response["WWW-Authenticate"],
                    'Basic realm="SIGEDON Kobo Webhook"',
                )
        self.assertFalse(KoboSubmission.objects.exists())

    def test_basic_authentication_preserves_idempotency(self):
        payload = self.payload(FICHA_10_FORM_ID)
        first = self.post_basic(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        event_count = submission.processing_events.count()

        second = self.post_basic(payload)
        submission.refresh_from_db()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(submission.processing_events.count(), event_count)

    def test_rejects_invalid_json_and_unavailable_assets_safely(self):
        url = reverse("kobo:webhook_submission")
        self.assertEqual(self.client.post(url, data="[", content_type="application/json", HTTP_X_KOBO_WEBHOOK_SECRET="test-webhook-secret").status_code, 400)
        self.assertEqual(self.post([]).status_code, 400)
        self.assertEqual(self.post({"_xform_id_string": "missing"}).status_code, 400)

    def test_stages_and_processes_each_supported_form_without_operations_effects(self):
        for form_id in self.assets:
            with self.subTest(form_id=form_id):
                payload = self.payload(form_id)
                if form_id == FICHA_11_FORM_ID:
                    payload["scoring"]["priority_total"] = "99"
                response = self.post(payload)
                submission = KoboSubmission.objects.get(external_id=f"webhook-{form_id}")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
                self.assertEqual(submission.asset, self.assets[form_id])
                if form_id == FICHA_01_FORM_ID:
                    self.assertEqual(submission.project, self.project)
                    self.assertIsNone(submission.processed_at)
                    self.assertTrue(
                        submission.processing_events.filter(
                            stage="territorial_routing",
                            code="territorial_identity_created",
                        ).exists()
                    )
                else:
                    self.assertIsNone(submission.project)
                    self.assertIsNone(submission.processed_at)
                    self.assertEqual(
                        submission.routing_status,
                        KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                    )
                    self.assertTrue(
                        submission.processing_events.filter(
                            stage="territorial_routing",
                            code="territorial_identity_pending",
                        ).exists()
                    )
                    if form_id == FICHA_11_FORM_ID:
                        self.assertTrue(
                            submission.normalized_payload["calculation_warnings"]
                        )
                self.assertNotIn("raw_payload", response.json())
        self.assertEqual(Project.objects.count(), 1)
        self.assertFalse(ProjectUpdate.objects.exists())

    def test_webhook_ficha_1_ignores_unavailable_direct_binding(self):
        asset = self.assets[FICHA_01_FORM_ID]
        asset.project_bindings.update(is_active=False)
        payload = self.ficha_01_slash_payload()

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertTrue(
            submission.processing_events.filter(
                stage="territorial_routing", code="territorial_identity_created"
            ).exists()
        )

    def test_webhook_ficha_1_ignores_multiple_active_bindings(self):
        asset = self.assets[FICHA_01_FORM_ID]
        KoboProjectBinding.objects.create(
            asset=asset,
            project=self.project,
            routing_type=KoboProjectBinding.RoutingType.FIELD_VALUE,
            source_field="payload.nucleo_code",
            source_value="unused",
        )

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id="webhook-ficha-01-slash")
        self.assertEqual(submission.project, self.project)

    def test_webhook_ficha_1_does_not_use_inactive_direct_binding_project(self):
        inactive_binding_project = Project.objects.create(
            code="PRJ-WEBHOOK-INACTIVE-BINDING",
            name="Proyecto de binding inactivo",
            status=Project.Status.SUSPENDED,
        )
        self.assets[FICHA_01_FORM_ID].project_bindings.update(
            project=inactive_binding_project
        )

        response = self.post(self.ficha_01_slash_payload())

        self.assertEqual(response.status_code, 201)
        submission = KoboSubmission.objects.get(external_id="webhook-ficha-01-slash")
        self.assertEqual(submission.project, self.project)

    def test_duplicate_preserves_payload_and_events(self):
        payload = self.payload(FICHA_10_FORM_ID)
        self.post(payload)
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        event_count = submission.processing_events.count()
        changed = {
            **payload,
            "microproject": {
                **payload["microproject"],
                "microproject_name": "No debe sobrescribir",
            },
        }

        response = self.post(changed)
        submission.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertNotEqual(
            submission.raw_payload["microproject"]["microproject_name"],
            changed["microproject"]["microproject_name"],
        )
        self.assertEqual(submission.processing_events.count(), event_count)

    def test_retry_processes_an_existing_received_submission(self):
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=self.assets[FICHA_01_FORM_ID].form_definition,
            asset=self.assets[FICHA_01_FORM_ID],
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.RECEIVED,
        )

        response = self.post(payload)

        submission.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)

    def test_retry_keeps_ficha_1_territorial_routing_when_binding_changes(self):
        asset = self.assets[FICHA_01_FORM_ID]
        asset.project_bindings.update(is_active=False)
        payload = self.ficha_01_slash_payload()

        first = self.post(payload)
        asset.project_bindings.update(is_active=True)
        retry = self.post(payload)

        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.processing_events.filter(code="normalized").count(), 1
        )

    def test_rejects_oversized_body_without_staging(self):
        with self.settings(KOBO_WEBHOOK_MAX_BYTES=8):
            response = self.post(self.payload(FICHA_01_FORM_ID))

        self.assertEqual(response.status_code, 413)
        self.assertFalse(KoboSubmission.objects.exists())

    def test_absent_or_invalid_content_length_is_safe(self):
        payload = self.payload(FICHA_10_FORM_ID)
        url = reverse("kobo:webhook_submission")
        for content_length in (None, "invalid"):
            with self.subTest(content_length=content_length):
                headers = {"HTTP_X_KOBO_WEBHOOK_SECRET": "test-webhook-secret"}
                if content_length is not None:
                    headers["CONTENT_LENGTH"] = content_length
                response = self.client.post(
                    url,
                    data=json.dumps({**payload, "_uuid": f"{payload['_uuid']}-{content_length}"}),
                    content_type="application/json",
                    **headers,
                )
                self.assertEqual(
                    response.status_code,
                    201 if content_length is None else 400,
                )

    def test_internal_errors_do_not_expose_request_data(self):
        payload = self.payload(FICHA_01_FORM_ID)
        with patch(
            "apps.integrations.kobo.views.converge_webhook_submission",
            side_effect=RuntimeError("secret payload diagnostic"),
        ):
            response = self.post(payload)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"ok": False, "error": "internal_error"})
        self.assertNotIn("secret", response.content.decode())


class KoboWebhookStagingTests(KoboWebhookTests):
    def test_service_stages_only_once_and_validates_asset_uid(self):
        asset = self.assets[FICHA_11_FORM_ID]
        payload = self.payload(FICHA_11_FORM_ID)
        submission, created = receive_webhook_submission(asset=asset, raw_payload=payload)
        duplicate, duplicate_created = receive_webhook_submission(asset=asset, raw_payload=payload)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(submission.pk, duplicate.pk)
        self.assertEqual(submission.status, KoboSubmission.Status.RECEIVED)
        self.assertEqual(submission.asset, asset)
        with self.assertRaises(KoboPayloadError):
            receive_webhook_submission(asset=asset, raw_payload={**payload, "_xform_id_string": "other"})


@override_settings(
    KOBO_ENABLED=True,
    KOBO_BASE_URL="https://kf.example.test",
    KOBO_API_TOKEN="test-token",
    KOBO_WEBHOOK_USERNAME="sigedon-kobo",
    KOBO_WEBHOOK_SECRET="test-webhook-secret",
)
class KoboReconciliationCommandTests(KoboWebhookTests):
    def test_command_dry_run_and_asset_filter(self):
        asset = self.assets[FICHA_01_FORM_ID]
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [self.payload(FICHA_01_FORM_ID)])
        with patch("apps.integrations.kobo.management.commands.reconcile_kobo_submissions.KoboApiClient", return_value=client):
            output = StringIO()
            call_command("reconcile_kobo_submissions", "--asset-uid", asset.asset_uid, "--dry-run", stdout=output)
        self.assertFalse(KoboSubmission.objects.exists())
        self.assertIn("created=1", output.getvalue())

    def test_command_rejects_disabled_feature_and_invalid_limit(self):
        with self.settings(KOBO_ENABLED=False):
            with self.assertRaises(CommandError):
                call_command("reconcile_kobo_submissions", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("reconcile_kobo_submissions", "--limit", "0", stdout=StringIO())

    def test_command_reprocesses_local_failure_despite_remote_failure(self):
        asset = self.assets[FICHA_01_FORM_ID]
        project = self.project
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(
            get_submissions=lambda asset_uid, limit: (_ for _ in ()).throw(
                KoboIntegrationError("remote unavailable")
            )
        )

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=output,
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertEqual(submission.project, project)
        self.assertTrue(submission.normalized_payload)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")
        self.assertTrue(
            submission.processing_events.filter(
                stage="reconciliation", code="local_reprocessed"
            ).exists()
        )
        self.assertIn("local_reprocessed=1", output.getvalue())
        self.assertIn("failed_assets=1", output.getvalue())

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=output,
            )
        self.assertIn("local_reprocessed=0", output.getvalue())
        self.assertEqual(KoboSubmission.objects.count(), 1)

    def test_command_dry_run_does_not_modify_local_recoverable_failure(self):
        asset = self.assets[FICHA_01_FORM_ID]
        payload = self.ficha_01_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                "--dry-run",
                stdout=output,
            )

        submission.refresh_from_db()
        self.assertEqual(submission.status, KoboSubmission.Status.VALIDATION_FAILED)
        self.assertEqual(submission.processing_events.count(), 1)
        self.assertIn("local_would_reprocess=1", output.getvalue())

    def test_command_recovers_ficha_10_slash_validation_failure_idempotently(self):
        asset = self.assets[FICHA_10_FORM_ID]
        payload = self.ficha_10_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIsNone(submission.project)
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.PENDING_IDENTITY
        )
        self.assertTrue(submission.normalized_payload)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

    def test_command_recovers_ficha_11_scoring_validation_failure_idempotently(self):
        asset = self.assets[FICHA_11_FORM_ID]
        payload = self.ficha_11_slash_payload()
        submission = KoboSubmission.objects.create(
            form_definition=asset.form_definition,
            asset=asset,
            external_id=payload["_uuid"],
            raw_payload=payload,
            status=KoboSubmission.Status.VALIDATION_FAILED,
            error_code="invalid_payload",
            error_message="Submission payload failed normalization.",
        )
        KoboProcessingEvent.objects.create(
            submission=submission,
            stage="normalization",
            level=KoboProcessingEvent.Level.ERROR,
            code="invalid_payload",
            message="Submission payload failed normalization.",
        )
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )

        submission.refresh_from_db()
        self.assertEqual(KoboSubmission.objects.count(), 1)
        self.assertEqual(submission.status, KoboSubmission.Status.READY_FOR_REVIEW)
        self.assertIsNone(submission.project)
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.PENDING_IDENTITY
        )
        self.assertTrue(submission.normalized_payload)
        self.assertIsNotNone(submission.normalized_at)
        self.assertIsNone(submission.processed_at)
        self.assertEqual(submission.error_code, "")
        self.assertEqual(submission.error_message, "")

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=StringIO(),
            )
        self.assertEqual(KoboSubmission.objects.count(), 1)

    def test_command_resolves_a_pending_ficha_10_when_identity_appears(self):
        asset = self.assets[FICHA_10_FORM_ID]
        payload = self.ficha_10_slash_payload(_uuid="reconcile-pending-ficha-10")
        client = SimpleNamespace(get_submissions=lambda asset_uid, limit: [payload])

        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=client,
        ):
            first_output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=first_output,
            )
        submission = KoboSubmission.objects.get(external_id=payload["_uuid"])
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.PENDING_IDENTITY
        )
        self.assertIn("still_pending=1", first_output.getvalue())

        self.create_territorial_identity(
            normalize_nucleo_code(payload["nucleo_code"])
        )
        with patch(
            "apps.integrations.kobo.management.commands."
            "reconcile_kobo_submissions.KoboApiClient",
            return_value=SimpleNamespace(get_submissions=lambda asset_uid, limit: []),
        ):
            second_output = StringIO()
            call_command(
                "reconcile_kobo_submissions",
                "--asset-uid",
                asset.asset_uid,
                stdout=second_output,
            )

        submission.refresh_from_db()
        self.assertEqual(submission.project, self.project)
        self.assertEqual(
            submission.routing_status, KoboSubmission.RoutingStatus.RESOLVED
        )
        self.assertIn("resolved=1", second_output.getvalue())
