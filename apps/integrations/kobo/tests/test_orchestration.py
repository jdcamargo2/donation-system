from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.integrations.kobo.mappings.ficha_01 import FICHA_01_FORM_ID, FICHA_01_VERSION
from apps.integrations.kobo.mappings.ficha_10 import FICHA_10_FORM_ID, FICHA_10_VERSION
from apps.integrations.kobo.mappings.ficha_11 import FICHA_11_FORM_ID, FICHA_11_VERSION
from apps.integrations.kobo.models import KoboAsset, KoboFormDefinition, KoboSyncRun
from apps.integrations.kobo.services.incremental import AssetSyncResult
from apps.integrations.kobo.services.orchestration import sync_supported_assets


def _asset_result(
    *,
    status=KoboSyncRun.Status.SUCCEEDED,
    created=0,
    updated=0,
    unchanged=0,
    imported=0,
    incidents=0,
    failed=0,
    partial=False,
):
    return AssetSyncResult(
        status=status,
        mode="incremental",
        cursor_before=None,
        cursor_after=None,
        watermark_before=None,
        watermark_after=None,
        created=created,
        updated=updated,
        unchanged=unchanged,
        imported=imported,
        incidents=incidents,
        failed=failed,
        partial=partial,
    )


class KoboOrchestrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ficha_1 = KoboFormDefinition.objects.create(
            form_id=FICHA_01_FORM_ID,
            title="Ficha 1 orch",
            version=FICHA_01_VERSION,
        )
        cls.ficha_10 = KoboFormDefinition.objects.create(
            form_id=FICHA_10_FORM_ID,
            title="Ficha 10 orch",
            version=FICHA_10_VERSION,
        )
        cls.ficha_11 = KoboFormDefinition.objects.create(
            form_id=FICHA_11_FORM_ID,
            title="Ficha 11 orch",
            version=FICHA_11_VERSION,
        )
        cls.asset_1 = KoboAsset.objects.create(
            asset_uid="orch-ficha-1",
            name="Orch Ficha 1",
            form_definition=cls.ficha_1,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=True,
        )
        cls.asset_10 = KoboAsset.objects.create(
            asset_uid="orch-ficha-10",
            name="Orch Ficha 10",
            form_definition=cls.ficha_10,
            form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
            is_active=True,
        )
        cls.asset_11 = KoboAsset.objects.create(
            asset_uid="orch-ficha-11",
            name="Orch Ficha 11",
            form_definition=cls.ficha_11,
            form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX,
            is_active=True,
        )

    def test_sync_continues_when_one_asset_fails_and_aggregates_counts(self):
        outcomes = {
            self.asset_1.pk: _asset_result(created=2, imported=2),
            self.asset_10.pk: RuntimeError("remote unavailable"),
            self.asset_11.pk: _asset_result(
                created=1,
                updated=1,
                unchanged=3,
                imported=1,
                incidents=2,
            ),
        }
        calls = []

        def fake_sync(*, asset, client, actor=None, full=False, max_pages=None):
            calls.append(asset.pk)
            outcome = outcomes[asset.pk]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch(
            "apps.integrations.kobo.services.orchestration.sync_asset_submissions",
            side_effect=fake_sync,
        ):
            result = sync_supported_assets(
                client=SimpleNamespace(),
                actor=SimpleNamespace(username="sync-clicker"),
                full=False,
            )

        self.assertEqual(set(calls), {self.asset_1.pk, self.asset_10.pk, self.asset_11.pk})
        self.assertEqual(result.assets_processed, 3)
        self.assertEqual(result.created, 3)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.unchanged, 3)
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.incidents, 2)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.forms_found, 7)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(len(result.asset_results), 3)
        failed = next(item for item in result.asset_results if item.asset_id == self.asset_10.pk)
        self.assertTrue(failed.error)
        self.assertEqual(failed.status, "FAILED")

    def test_all_assets_succeed_aggregate_status(self):
        def fake_sync(*, asset, client, actor=None, full=False, max_pages=None):
            return _asset_result(created=1, imported=1, unchanged=2)

        with patch(
            "apps.integrations.kobo.services.orchestration.sync_asset_submissions",
            side_effect=fake_sync,
        ) as mocked:
            result = sync_supported_assets(client=SimpleNamespace(), full=False)

        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.assets_processed, 3)
        self.assertEqual(result.created, 3)
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.unchanged, 6)
        self.assertEqual(result.incidents, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.forms_found, 9)

    def test_inactive_assets_are_skipped(self):
        KoboAsset.objects.create(
            asset_uid="orch-inactive",
            name="Inactive",
            form_definition=self.ficha_1,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
            is_active=False,
        )
        with patch(
            "apps.integrations.kobo.services.orchestration.sync_asset_submissions",
            return_value=_asset_result(),
        ) as mocked:
            result = sync_supported_assets(client=SimpleNamespace())

        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(result.assets_processed, 3)
