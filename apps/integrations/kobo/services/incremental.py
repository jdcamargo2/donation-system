"""Incremental Kobo staging with an owned database lease and private revisions."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.integrations.kobo.errors import KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.models import KoboAsset, KoboProcessingEvent, KoboSubmission, KoboSubmissionRemoteRevision, KoboSyncRun
from apps.integrations.kobo.processors import process_submission
from apps.integrations.kobo.services.territorial_routing import route_normalized_submission


REMOTE_WATERMARK_FIELD = "_last_edited"
REMOTE_MINIMUM_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)


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
    normalized: int = 0
    routed: int = 0
    pending: int = 0
    conflicts: int = 0
    validation_failed: int = 0
    processing_failed: int = 0
    partial: bool = False


def canonical_payload_hash(payload: dict) -> str:
    """PRE: payload is JSON data. POST: returns a deterministic content hash."""
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def build_incremental_submission_params(*, watermark, full: bool) -> dict[str, str]:
    """PRE: watermark is aware or None. POST: returns only Kobo's supported query parameter."""
    if full or watermark is None:
        return {}
    if timezone.is_naive(watermark):
        raise KoboPayloadError("Stored Kobo watermark must include a timezone.")
    overlap = timedelta(seconds=getattr(settings, "KOBO_SYNC_OVERLAP_SECONDS", 300))
    start = max(watermark - overlap, REMOTE_MINIMUM_TIMESTAMP)
    return {"query": json.dumps({REMOTE_WATERMARK_FIELD: {"$gte": start.isoformat().replace("+00:00", "Z")}}, separators=(",", ":"))}


def _remote_timestamp(payload: dict):
    """PRE: payload is a remote submission. POST: returns an aware timestamp or None."""
    value = payload.get(REMOTE_WATERMARK_FIELD)
    parsed = parse_datetime(value) if isinstance(value, str) else None
    return parsed if parsed is not None and not timezone.is_naive(parsed) else None


def _store_remote_revision(submission: KoboSubmission) -> None:
    """PRE: submission is locked. POST: stores a hash that matches its old payload."""
    previous_hash = canonical_payload_hash(submission.raw_payload)
    KoboSubmissionRemoteRevision.objects.get_or_create(
        submission=submission,
        payload_hash=previous_hash,
        defaults={
            "payload": submission.raw_payload,
            "remote_updated_at": submission.remote_updated_at,
            "remote_version": submission.remote_version,
        },
    )


def _reset_derived_submission_fields(submission: KoboSubmission) -> None:
    """PRE: submission is locked and not protected. POST: clears only payload-derived state."""
    submission.status = KoboSubmission.Status.RECEIVED
    submission.normalized_payload = {}
    submission.normalized_at = None
    submission.processed_at = None
    submission.project = None
    submission.routing_status = KoboSubmission.RoutingStatus.UNRESOLVED
    submission.routing_reason_code = ""
    submission.routing_resolved_at = None
    submission.pastoral_zone = ""
    submission.parish = ""
    submission.primary_community = ""
    submission.assessment_date = None
    submission.nucleo_code_original = ""
    submission.nucleo_code_normalized = ""
    submission.error_code = ""
    submission.error_message = ""
    submission.remote_update_pending = False


def _is_protected_submission(submission: KoboSubmission) -> bool:
    """PRE: submission is persisted. POST: identifies snapshots immutable after human action."""
    return submission.status in {
        KoboSubmission.Status.IMPORTED,
        KoboSubmission.Status.REJECTED,
        KoboSubmission.Status.APPROVED_FOR_IMPORT,
    }


def _converge_submission(submission: KoboSubmission) -> tuple[str, str]:
    """PRE: submission was staged or reset to received. POST: normalizes then routes safely."""
    outcome = process_submission(
        submission,
        default_timezone=timezone.get_current_timezone(),
    )
    submission.refresh_from_db()
    if outcome.final_status != KoboSubmission.Status.READY_FOR_REVIEW:
        return outcome.final_status, KoboSubmission.RoutingStatus.UNRESOLVED
    routing = route_normalized_submission(submission)
    return outcome.final_status, routing.status


def _start_run(asset_id, actor, full):
    """PRE: asset exists. POST: atomically creates a run that owns the asset lease, or None."""
    now = timezone.now()
    lease = timedelta(seconds=getattr(settings, "KOBO_SYNC_LEASE_SECONDS", 900))
    with transaction.atomic():
        asset = KoboAsset.objects.select_for_update().get(pk=asset_id)
        if asset.sync_lease_expires_at and asset.sync_lease_expires_at > now:
            return None
        recovered = bool(asset.sync_lease_expires_at and asset.sync_lease_expires_at <= now)
        if recovered and asset.sync_lease_run_id:
            KoboSyncRun.objects.filter(pk=asset.sync_lease_run_id, status=KoboSyncRun.Status.RUNNING).update(status=KoboSyncRun.Status.ABANDONED, finished_at=now, error_code="SYNC_LEASE_EXPIRED", safe_error_message="Synchronization lease expired.")
        mode = KoboSyncRun.Mode.FULL if full or asset.last_remote_watermark is None else KoboSyncRun.Mode.INCREMENTAL
        run = KoboSyncRun.objects.create(asset=asset, triggered_by=actor if getattr(actor, "is_authenticated", False) else None, kind=KoboSyncRun.Kind.SUBMISSIONS, mode=mode, cursor_before=asset.last_successful_sync_cursor, watermark_before=asset.last_remote_watermark, lease_recovered=recovered)
        asset.sync_lease_started_at, asset.sync_lease_expires_at, asset.sync_lease_run = now, now + lease, run
        asset.save(update_fields=("sync_lease_started_at", "sync_lease_expires_at", "sync_lease_run"))
        return asset, run


def _finish_run(*, run, candidate_watermark, partial, error_code="", safe_error_message="", counters):
    """PRE: run owns its active lease. POST: terminal state and cursor update are atomic."""
    now = timezone.now()
    with transaction.atomic():
        run = KoboSyncRun.objects.select_for_update().get(pk=run.pk)
        asset = KoboAsset.objects.select_for_update().get(pk=run.asset_id)
        owns_lease = asset.sync_lease_run_id == run.pk
        success = not partial and not error_code
        if success:
            watermark = max(filter(None, (asset.last_remote_watermark, candidate_watermark)), default=None)
            asset.last_remote_watermark = watermark
            asset.last_successful_sync_cursor = max(filter(None, (asset.last_successful_sync_cursor, watermark)), default=None)
            asset.last_successful_sync_at = now
            run.cursor_after, run.watermark_after = asset.last_successful_sync_cursor, watermark
        if owns_lease:
            asset.sync_lease_started_at = asset.sync_lease_expires_at = None
            asset.sync_lease_run = None
        asset.save()
        run.status = KoboSyncRun.Status.SUCCEEDED if success else (KoboSyncRun.Status.PARTIAL if partial else KoboSyncRun.Status.FAILED)
        run.partial, run.finished_at, run.error_code, run.safe_error_message = partial, now, error_code, safe_error_message
        run.items_created, run.items_updated, run.items_unchanged, run.remote_updates_detected, run.items_failed = counters
        run.metadata = {"candidate_remote_watermark": candidate_watermark.isoformat() if candidate_watermark else None}
        run.save()
        return run


def sync_asset_submissions(*, asset, client, actor=None, full=False, max_pages=None):
    """PRE: asset is active and client is configured. POST: only a complete owned run advances state."""
    started = _start_run(asset.pk, actor, full)
    if started is None:
        return AssetSyncResult("SYNC_ALREADY_RUNNING", "full" if full else "incremental", asset.last_successful_sync_cursor, None, asset.last_remote_watermark, None)
    leased_asset, run = started
    created = updated = unchanged = detected = failed = pages = 0
    normalized = routed = pending = conflicts = validation_failed = processing_failed = 0
    candidate = leased_asset.last_remote_watermark
    partial = False
    error_code = safe_error = ""
    try:
        params = build_incremental_submission_params(watermark=leased_asset.last_remote_watermark, full=run.mode == KoboSyncRun.Mode.FULL)
        def count_page():
            nonlocal pages
            pages += 1

        for payload in client.iter_submissions(
            leased_asset.asset_uid,
            params=params,
            max_pages=max_pages,
            on_page_fetched=count_page,
        ):
            external_id = payload.get("_uuid")
            remote_at = _remote_timestamp(payload)
            if not isinstance(external_id, str) or not external_id or remote_at is None:
                failed += 1
                continue
            digest = canonical_payload_hash(payload)
            should_converge = False
            with transaction.atomic():
                submission, was_created = KoboSubmission.objects.select_for_update().get_or_create(form_definition=leased_asset.form_definition, external_id=external_id, defaults={"asset": leased_asset, "raw_payload": payload, "remote_updated_at": remote_at, "last_remote_payload_hash": digest})
                if was_created:
                    submission.remote_version = str(payload.get("version", ""))
                    submission.save(update_fields=("remote_version",))
                    created += 1
                    should_converge = True
                elif submission.remote_updated_at is None:
                    failed += 1
                    KoboProcessingEvent.objects.create(submission=submission, stage="remote_sync", level=KoboProcessingEvent.Level.WARNING, code="REMOTE_TIMESTAMP_INVALID", message="Stored remote timestamp prevents a safe incremental update.")
                elif remote_at < submission.remote_updated_at:
                    unchanged += 1
                elif remote_at == submission.remote_updated_at and submission.last_remote_payload_hash == digest:
                    unchanged += 1
                elif remote_at == submission.remote_updated_at:
                    revision, revision_created = KoboSubmissionRemoteRevision.objects.get_or_create(submission=submission, payload_hash=digest, defaults={"payload": payload, "remote_updated_at": remote_at, "remote_version": str(payload.get("version", ""))})
                    if revision_created:
                        detected += 1
                        conflicts += 1
                    if _is_protected_submission(submission):
                        submission.remote_update_pending = True
                        submission.save(update_fields=("remote_update_pending",))
                    KoboProcessingEvent.objects.get_or_create(submission=submission, stage="remote_sync", code="REMOTE_EQUAL_TIMESTAMP_CONFLICT", defaults={"level": KoboProcessingEvent.Level.WARNING, "message": "Remote payload conflicts with the stored timestamp."})
                elif _is_protected_submission(submission):
                    revision, revision_created = KoboSubmissionRemoteRevision.objects.get_or_create(submission=submission, payload_hash=digest, defaults={"payload": payload, "remote_updated_at": remote_at, "remote_version": str(payload.get("version", ""))})
                    if revision_created:
                        detected += 1
                    submission.remote_update_pending = True
                    submission.save(update_fields=("remote_update_pending",))
                    KoboProcessingEvent.objects.get_or_create(submission=submission, stage="remote_sync", code="REMOTE_UPDATE_DETECTED", defaults={"level": KoboProcessingEvent.Level.WARNING, "message": "Remote update requires review."})
                else:
                    _store_remote_revision(submission)
                    submission.raw_payload = payload
                    submission.last_remote_payload_hash = digest
                    submission.remote_updated_at = remote_at
                    submission.remote_version = str(payload.get("version", ""))
                    _reset_derived_submission_fields(submission)
                    submission.save()
                    updated += 1
                    should_converge = True
            if should_converge:
                final_status, routing_status = _converge_submission(submission)
                normalized += int(final_status == KoboSubmission.Status.READY_FOR_REVIEW)
                routed += int(routing_status == KoboSubmission.RoutingStatus.RESOLVED)
                pending += int(routing_status == KoboSubmission.RoutingStatus.PENDING_IDENTITY)
                conflicts += int(routing_status == KoboSubmission.RoutingStatus.CONFLICT)
                validation_failed += int(final_status == KoboSubmission.Status.VALIDATION_FAILED)
                processing_failed += int(final_status == KoboSubmission.Status.PROCESSING_FAILED)
            candidate = max(filter(None, (candidate, remote_at)))
    except KoboIntegrationError:
        partial, error_code, safe_error = True, "REMOTE_SYNC_FAILED", "Remote synchronization did not complete."
    except Exception:
        error_code, safe_error = "SYNC_FAILED", "Synchronization failed safely."
    run = _finish_run(run=run, candidate_watermark=candidate, partial=partial, error_code=error_code, safe_error_message=safe_error, counters=(created, updated, unchanged, detected, failed))
    return AssetSyncResult(run.status, run.mode, run.cursor_before, run.cursor_after, run.watermark_before, run.watermark_after, pages_fetched=pages, created=created, updated=updated, unchanged=unchanged, remote_updates_detected=detected, failed=failed, normalized=normalized, routed=routed, pending=pending, conflicts=conflicts, validation_failed=validation_failed, processing_failed=processing_failed, partial=run.partial)
