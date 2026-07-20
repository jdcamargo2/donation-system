"""Incremental Kobo staging with a database lease and immutable remote revisions."""
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.integrations.kobo.errors import KoboConfigurationError, KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.models import KoboAsset, KoboProcessingEvent, KoboSubmission, KoboSubmissionRemoteRevision, KoboSyncRun


@dataclass(frozen=True)
class AssetSyncResult:
    status: str
    mode: str
    cursor_before: object
    cursor_after: object
    watermark_before: object
    watermark_after: object
    pages_fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    remote_updates_detected: int = 0
    failed: int = 0
    partial: bool = False


def canonical_payload_hash(payload: dict) -> str:
    """PRE: payload is JSON data. POST: returns a deterministic content hash."""
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _remote_timestamp(payload: dict):
    # PRE: payload is a remote submission object. POST: returns Kobo _last_edited or None.
    value = payload.get("_last_edited")
    return parse_datetime(value) if isinstance(value, str) else None


def _acquire(asset_id, actor):
    # PRE: asset_id exists. POST: owns a non-expired lease or returns None when occupied.
    now = timezone.now()
    lease = timedelta(seconds=getattr(settings, "KOBO_SYNC_LEASE_SECONDS", 900))
    with transaction.atomic():
        asset = KoboAsset.objects.select_for_update().get(pk=asset_id)
        if asset.sync_lease_expires_at and asset.sync_lease_expires_at > now:
            return None, None
        recovered = bool(asset.sync_lease_expires_at and asset.sync_lease_expires_at <= now)
        if recovered:
            KoboSyncRun.objects.filter(asset=asset, status=KoboSyncRun.Status.RUNNING).update(status=KoboSyncRun.Status.ABANDONED, finished_at=now, error_code="lease_expired", safe_error_message="Synchronization lease expired.")
        asset.sync_lease_started_at, asset.sync_lease_expires_at = now, now + lease
        asset.save(update_fields=("sync_lease_started_at", "sync_lease_expires_at"))
        return asset, recovered


def sync_asset_submissions(*, asset, client, actor=None, full=False, max_pages=None):
    """
    PRE: asset is persisted and client is configured for Kobo.
    POST: complete runs alone advance remote cursor/watermark; imported data is never overwritten.
    """
    leased_asset, recovered = _acquire(asset.pk, actor)
    if leased_asset is None:
        return AssetSyncResult("SYNC_ALREADY_RUNNING", "full" if full else "incremental", asset.last_successful_sync_cursor, None, asset.last_remote_watermark, None)
    mode = KoboSyncRun.Mode.FULL if full or leased_asset.last_successful_sync_cursor is None else KoboSyncRun.Mode.INCREMENTAL
    run = KoboSyncRun.objects.create(asset=leased_asset, triggered_by=actor if getattr(actor, "is_authenticated", False) else None, kind=KoboSyncRun.Kind.SUBMISSIONS, mode=mode, cursor_before=leased_asset.last_successful_sync_cursor, watermark_before=leased_asset.last_remote_watermark, lease_recovered=recovered)
    created = updated = unchanged = detected = failed = 0
    watermark = leased_asset.last_remote_watermark
    partial = False
    try:
        for payload in client.iter_submissions(leased_asset.asset_uid):
            try:
                external_id = payload.get("_uuid")
                if not isinstance(external_id, str) or not external_id:
                    raise KoboPayloadError("Kobo submission _uuid is required.")
                digest, remote_at = canonical_payload_hash(payload), _remote_timestamp(payload)
                with transaction.atomic():
                    submission, was_created = KoboSubmission.objects.select_for_update().get_or_create(form_definition=leased_asset.form_definition, external_id=external_id, defaults={"asset": leased_asset, "raw_payload": payload, "remote_updated_at": remote_at, "last_remote_payload_hash": digest})
                    if was_created: created += 1
                    elif submission.last_remote_payload_hash == digest: unchanged += 1
                    elif submission.status in (KoboSubmission.Status.IMPORTED, KoboSubmission.Status.REJECTED, KoboSubmission.Status.APPROVED_FOR_IMPORT):
                        KoboSubmissionRemoteRevision.objects.get_or_create(submission=submission, payload_hash=digest, defaults={"payload": payload, "remote_updated_at": remote_at, "remote_version": str(payload.get("version", ""))})
                        submission.remote_update_pending = True
                        submission.save(update_fields=("remote_update_pending",))
                        KoboProcessingEvent.objects.get_or_create(submission=submission, stage="remote_sync", code="remote_update_detected", defaults={"level": KoboProcessingEvent.Level.WARNING, "message":"Remote update requires review."})
                        detected += 1
                    else:
                        KoboSubmissionRemoteRevision.objects.get_or_create(submission=submission, payload_hash=submission.last_remote_payload_hash or digest, defaults={"payload": submission.raw_payload, "remote_updated_at": submission.remote_updated_at, "remote_version": submission.remote_version})
                        submission.raw_payload, submission.last_remote_payload_hash, submission.remote_updated_at = payload, digest, remote_at
                        submission.status, submission.normalized_payload, submission.project = KoboSubmission.Status.RECEIVED, {}, None
                        submission.save(update_fields=("raw_payload", "last_remote_payload_hash", "remote_updated_at", "status", "normalized_payload", "project"))
                        KoboProcessingEvent.objects.create(submission=submission, stage="remote_sync", level=KoboProcessingEvent.Level.INFO, code="remote_update_applied", message="Remote update returned to staging.")
                        updated += 1
                if remote_at and (watermark is None or remote_at > watermark): watermark = remote_at
            except KoboPayloadError: failed += 1
    except KoboIntegrationError:
        partial = True
    finally:
        now = timezone.now()
        with transaction.atomic():
            asset_locked = KoboAsset.objects.select_for_update().get(pk=leased_asset.pk)
            if not partial:
                asset_locked.last_successful_sync_cursor = watermark
                asset_locked.last_remote_watermark = watermark
                asset_locked.last_successful_sync_at = now
            asset_locked.sync_lease_started_at = asset_locked.sync_lease_expires_at = None
            asset_locked.save()
            run.status = KoboSyncRun.Status.PARTIAL if partial else KoboSyncRun.Status.SUCCEEDED
            run.partial, run.finished_at, run.cursor_after, run.watermark_after = partial, now, (None if partial else watermark), (None if partial else watermark)
            run.items_created, run.items_updated, run.items_unchanged, run.remote_updates_detected, run.items_failed = created, updated, unchanged, detected, failed
            run.save()
    return AssetSyncResult(run.status, mode, run.cursor_before, run.cursor_after, run.watermark_before, run.watermark_after, created=created, updated=updated, unchanged=unchanged, remote_updates_detected=detected, failed=failed, partial=partial)
