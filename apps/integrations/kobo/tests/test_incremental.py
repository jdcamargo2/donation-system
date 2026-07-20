from datetime import UTC, datetime, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.integrations.kobo.errors import KoboTransientRemoteError
from apps.integrations.kobo.models import KoboAsset, KoboFormDefinition, KoboSubmission, KoboSyncRun
from apps.integrations.kobo.services.incremental import (
    _finish_run,
    _start_run,
    build_incremental_submission_params,
    canonical_payload_hash,
    sync_asset_submissions,
)


class IncrementalClient:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def iter_submissions(self, asset_uid, *, params, max_pages):
        # PRE: synchronization provides its selected asset and safe remote parameters.
        # POST: records the boundary call and yields the configured remote outcomes.
        self.calls.append((asset_uid, params, max_pages))
        for outcome in self.outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
            yield outcome


@override_settings(KOBO_SYNC_OVERLAP_SECONDS=300)
class KoboIncrementalSynchronizationTests(TestCase):
    def setUp(self):
        self.form = KoboFormDefinition.objects.create(
            form_id="incremental-form", title="Incremental", version="1"
        )
        self.asset = KoboAsset.objects.create(
            asset_uid="incremental-asset",
            name="Incremental asset",
            form_definition=self.form,
            form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE,
        )

    def payload(self, external_id="remote-1", *, edited_at=None, **changes):
        # PRE: external_id identifies a remote submission and edited_at is aware text.
        # POST: returns one complete payload suitable for the incremental boundary.
        payload = {
            "_uuid": external_id,
            "_last_edited": edited_at or "2026-07-20T12:00:00Z",
        }
        payload.update(changes)
        return payload

    def test_parameter_builder_uses_only_confirmed_kobo_field_and_keeps_watermark(self):
        watermark = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

        self.assertEqual(build_incremental_submission_params(watermark=None, full=False), {})
        self.assertEqual(build_incremental_submission_params(watermark=watermark, full=True), {})
        with self.settings(KOBO_SYNC_OVERLAP_SECONDS=0):
            params = build_incremental_submission_params(watermark=watermark, full=False)
        self.assertEqual(params, {"query": '{"_last_edited":{"$gte":"2026-07-20T12:00:00Z"}}'})
        self.assertEqual(watermark, datetime(2026, 7, 20, 12, 0, tzinfo=UTC))

    def test_success_advances_cursor_watermark_and_releases_owned_lease(self):
        client = IncrementalClient([self.payload()])

        result = sync_asset_submissions(asset=self.asset, client=client, max_pages=3)

        self.asset.refresh_from_db()
        run = KoboSyncRun.objects.get()
        self.assertEqual(result.status, KoboSyncRun.Status.SUCCEEDED)
        self.assertEqual(result.created, 1)
        self.assertEqual(client.calls[0][1], {})
        self.assertEqual(self.asset.last_remote_watermark, datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
        self.assertEqual(self.asset.last_successful_sync_cursor, self.asset.last_remote_watermark)
        self.assertIsNotNone(self.asset.last_successful_sync_at)
        self.assertIsNone(self.asset.sync_lease_run_id)
        self.assertIsNotNone(run.finished_at)

    def test_partial_and_failed_runs_preserve_cursor_and_release_lease(self):
        initial = timezone.now() - timedelta(hours=1)
        self.asset.last_remote_watermark = self.asset.last_successful_sync_cursor = initial
        self.asset.save()
        partial = sync_asset_submissions(
            asset=self.asset, client=IncrementalClient([KoboTransientRemoteError("offline")])
        )
        self.asset.refresh_from_db()
        self.assertEqual(partial.status, KoboSyncRun.Status.PARTIAL)
        self.assertEqual(self.asset.last_remote_watermark, initial)
        self.assertIsNone(self.asset.sync_lease_run_id)

        failed = sync_asset_submissions(
            asset=self.asset, client=IncrementalClient([RuntimeError("internal")])
        )
        self.asset.refresh_from_db()
        self.assertEqual(failed.status, KoboSyncRun.Status.FAILED)
        self.assertEqual(self.asset.last_successful_sync_cursor, initial)
        self.assertIsNone(self.asset.sync_lease_run_id)

    def test_protected_submission_records_private_remote_revision_without_overwrite(self):
        original = self.payload("reviewed", edited_at="2026-07-20T10:00:00Z")
        submission = KoboSubmission.objects.create(
            form_definition=self.form,
            asset=self.asset,
            external_id="reviewed",
            raw_payload=original,
            status=KoboSubmission.Status.IMPORTED,
            remote_updated_at=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            last_remote_payload_hash=canonical_payload_hash(original),
        )
        changed = self.payload("reviewed", marker="new")

        result = sync_asset_submissions(asset=self.asset, client=IncrementalClient([changed]))

        submission.refresh_from_db()
        self.assertEqual(result.remote_updates_detected, 1)
        self.assertEqual(submission.raw_payload, original)
        self.assertTrue(submission.remote_update_pending)
        self.assertEqual(submission.remote_revisions.count(), 1)

    def test_repeated_hash_is_unchanged_even_at_same_remote_timestamp(self):
        payload = self.payload()
        KoboSubmission.objects.create(
            form_definition=self.form, asset=self.asset, external_id="remote-1",
            raw_payload=payload, remote_updated_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            last_remote_payload_hash=canonical_payload_hash(payload),
        )

        result = sync_asset_submissions(asset=self.asset, client=IncrementalClient([payload]))

        self.assertEqual((result.created, result.updated, result.unchanged), (0, 0, 1))

    def test_old_run_cannot_release_a_reassigned_lease(self):
        old_asset, old_run = _start_run(self.asset.pk, None, False)
        old_asset.sync_lease_expires_at = timezone.now() - timedelta(seconds=1)
        old_asset.save(update_fields=("sync_lease_expires_at",))
        _, new_run = _start_run(self.asset.pk, None, False)

        _finish_run(
            run=old_run, candidate_watermark=None, partial=True,
            error_code="REMOTE_SYNC_FAILED", safe_error_message="safe", counters=(0, 0, 0, 0, 0),
        )

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sync_lease_run_id, new_run.pk)
