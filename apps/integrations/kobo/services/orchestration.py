"""Orchestrate multi-asset Kobo synchronization with automatic import."""

from dataclasses import dataclass, field

from apps.integrations.kobo.models import KoboAsset, KoboSyncRun
from apps.integrations.kobo.services.incremental import AssetSyncResult, sync_asset_submissions


SUPPORTED_FORM_ROLES = (
    KoboAsset.FormRole.TERRITORIAL_PROFILE,
    KoboAsset.FormRole.PRIORITIZED_MICROPROJECT,
    KoboAsset.FormRole.PRIORITIZATION_MATRIX,
)


@dataclass(frozen=True)
class AssetOrchestrationResult:
    asset_id: int
    asset_uid: str
    asset_name: str
    status: str
    mode: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    imported: int = 0
    incidents: int = 0
    failed: int = 0
    already_running: bool = False
    error: bool = False


@dataclass(frozen=True)
class OrchestratedSyncResult:
    status: str
    assets_processed: int = 0
    forms_found: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    imported: int = 0
    incidents: int = 0
    errors: int = 0
    asset_results: tuple[AssetOrchestrationResult, ...] = field(default_factory=tuple)


def _active_supported_assets():
    return list(
        KoboAsset.objects.filter(
            is_active=True,
            form_role__in=SUPPORTED_FORM_ROLES,
        )
        .select_related("form_definition")
        .order_by("form_role", "name", "asset_uid")
    )


def sync_supported_assets(*, client, actor=None, full=False, max_pages=None) -> OrchestratedSyncResult:
    """
    PRE: client is a configured Kobo API client; full defaults to False (incremental).
    POST: syncs each active supported asset independently, continues after per-asset
    failures, and returns a safe aggregate without technical exception details.
    """
    assets = _active_supported_assets()
    asset_results = []
    created = updated = unchanged = imported = incidents = errors = 0
    assets_processed = 0

    for asset in assets:
        assets_processed += 1
        try:
            sync_result: AssetSyncResult = sync_asset_submissions(
                asset=asset,
                client=client,
                actor=actor,
                full=full,
                max_pages=max_pages,
            )
        except Exception:
            errors += 1
            asset_results.append(
                AssetOrchestrationResult(
                    asset_id=asset.pk,
                    asset_uid=asset.asset_uid,
                    asset_name=asset.name,
                    status="FAILED",
                    mode="full" if full else "incremental",
                    failed=1,
                    error=True,
                )
            )
            continue

        if sync_result.status == "SYNC_ALREADY_RUNNING":
            asset_results.append(
                AssetOrchestrationResult(
                    asset_id=asset.pk,
                    asset_uid=asset.asset_uid,
                    asset_name=asset.name,
                    status=sync_result.status,
                    mode=sync_result.mode,
                    already_running=True,
                )
            )
            continue

        created += sync_result.created
        updated += sync_result.updated
        unchanged += sync_result.unchanged
        imported += sync_result.imported
        incidents += sync_result.incidents
        if (
            sync_result.failed
            or sync_result.status
            in {KoboSyncRun.Status.FAILED, KoboSyncRun.Status.PARTIAL}
            or sync_result.partial
        ):
            errors += 1

        asset_results.append(
            AssetOrchestrationResult(
                asset_id=asset.pk,
                asset_uid=asset.asset_uid,
                asset_name=asset.name,
                status=sync_result.status,
                mode=sync_result.mode,
                created=sync_result.created,
                updated=sync_result.updated,
                unchanged=sync_result.unchanged,
                imported=sync_result.imported,
                incidents=sync_result.incidents,
                failed=sync_result.failed,
                error=sync_result.status
                in {KoboSyncRun.Status.FAILED, KoboSyncRun.Status.PARTIAL}
                or bool(sync_result.failed),
            )
        )

    forms_found = created + updated + unchanged
    if errors and imported == 0 and created == 0 and updated == 0 and not asset_results:
        status = "FAILED"
    elif errors:
        status = "PARTIAL"
    else:
        status = "SUCCEEDED"

    return OrchestratedSyncResult(
        status=status,
        assets_processed=assets_processed,
        forms_found=forms_found,
        created=created,
        updated=updated,
        unchanged=unchanged,
        imported=imported,
        incidents=incidents,
        errors=errors,
        asset_results=tuple(asset_results),
    )
