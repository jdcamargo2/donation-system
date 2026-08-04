"""Kobo webhook authentication policy (Basic canonical; legacy header gated)."""

from __future__ import annotations

import base64
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.integrations.kobo import views as kobo_views
from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.models import KoboAsset, KoboFormDefinition


def _basic_header(username: str, password: str) -> dict:
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return {'HTTP_AUTHORIZATION': f'Basic {token}'}


def _ok_convergence(*, submission_id: int = 1):
    return mock.Mock(
        completed=True,
        final_status='imported',
        submission_id=submission_id,
    )


@override_settings(
    KOBO_ENABLED=True,
    KOBO_WEBHOOK_USERNAME='sigedon-kobo',
    KOBO_WEBHOOK_SECRET='canonical-webhook-secret',
    KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER=False,
    KOBO_WEBHOOK_MAX_BYTES=65536,
)
class KoboWebhookAuthPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.form_definition = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title='Ficha webhook auth',
            version=FICHA_01_VERSION,
        )
        cls.asset = KoboAsset.objects.create(
            asset_uid='webhook-auth-asset',
            name='Webhook auth asset',
            form_definition=cls.form_definition,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )

    def setUp(self):
        self.client = Client()
        kobo_views._legacy_header_warned = False
        self.url = reverse('kobo:webhook_submission')
        self.payload = {
            '_uuid': 'webhook-auth-uuid-1',
            '_id': 1,
            '_xform_id_string': self.asset.asset_uid,
        }

    def _post(self, *, headers=None, data=None):
        return self.client.post(
            self.url,
            data=json.dumps(data or self.payload),
            content_type='application/json',
            **(headers or {}),
        )

    def _patch_stage(self, *, submission_id: int = 1):
        submission = mock.Mock(pk=submission_id, status='received')
        return (
            mock.patch(
                'apps.integrations.kobo.views.receive_webhook_submission',
                return_value=(submission, True),
            ),
            mock.patch(
                'apps.integrations.kobo.views.converge_webhook_submission',
                return_value=_ok_convergence(submission_id=submission_id),
            ),
        )

    def test_valid_basic_succeeds(self):
        receive_patch, converge_patch = self._patch_stage()
        with receive_patch, converge_patch:
            response = self._post(
                headers=_basic_header('sigedon-kobo', 'canonical-webhook-secret')
            )
        self.assertIn(response.status_code, (200, 201))

    def test_invalid_basic_fails(self):
        response = self._post(
            headers=_basic_header('sigedon-kobo', 'wrong-secret')
        )
        self.assertEqual(response.status_code, 401)

    def test_legacy_header_fails_by_default(self):
        response = self._post(
            headers={'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret'}
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER=True)
    def test_legacy_header_succeeds_only_with_flag(self):
        receive_patch, converge_patch = self._patch_stage(submission_id=2)
        with (
            receive_patch,
            converge_patch,
            self.assertLogs('sigedon.kobo.webhook', level='WARNING') as captured,
        ):
            response = self._post(
                headers={'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret'}
            )
            response2 = self._post(
                headers={'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret'}
            )
        self.assertIn(response.status_code, (200, 201))
        self.assertIn(response2.status_code, (200, 201))
        joined = '\n'.join(captured.output)
        self.assertIn('legacy', joined.lower())
        self.assertNotIn('canonical-webhook-secret', joined)
        self.assertEqual(
            sum(1 for line in captured.output if 'legacy' in line.lower()),
            1,
        )

    def test_both_present_valid_basic_succeeds(self):
        headers = {
            **_basic_header('sigedon-kobo', 'canonical-webhook-secret'),
            'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret',
        }
        receive_patch, converge_patch = self._patch_stage(submission_id=3)
        with receive_patch, converge_patch:
            response = self._post(headers=headers)
        self.assertIn(response.status_code, (200, 201))

    def test_invalid_basic_plus_valid_legacy_fails_when_disabled(self):
        headers = {
            **_basic_header('sigedon-kobo', 'wrong-secret'),
            'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret',
        }
        response = self._post(headers=headers)
        self.assertEqual(response.status_code, 401)

    @override_settings(KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER=True)
    def test_invalid_basic_plus_valid_legacy_succeeds_when_enabled(self):
        headers = {
            **_basic_header('sigedon-kobo', 'wrong-secret'),
            'HTTP_X_KOBO_WEBHOOK_SECRET': 'canonical-webhook-secret',
        }
        receive_patch, converge_patch = self._patch_stage(submission_id=4)
        with receive_patch, converge_patch:
            response = self._post(headers=headers)
        self.assertIn(response.status_code, (200, 201))

    def test_both_present_invalid_values_fail(self):
        headers = {
            **_basic_header('sigedon-kobo', 'wrong-a'),
            'HTTP_X_KOBO_WEBHOOK_SECRET': 'wrong-b',
        }
        response = self._post(headers=headers)
        self.assertEqual(response.status_code, 401)

    @override_settings(KOBO_ENABLED=False)
    def test_disabled_kobo_returns_404(self):
        response = self._post(
            headers=_basic_header('sigedon-kobo', 'canonical-webhook-secret')
        )
        self.assertEqual(response.status_code, 404)

    def test_authentication_precedes_body_access(self):
        response = self.client.post(
            self.url,
            data=b'{}',
            content_type='application/json',
            HTTP_CONTENT_LENGTH=str(10**9),
        )
        self.assertEqual(response.status_code, 401)
