from dataclasses import replace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.integrations.kobo.client import KoboApiClient, KoboRemoteAsset
from apps.integrations.kobo.services.common import (
    AssetDiscoveryResult,
    AssetReadiness,
    FORM_DEFINITION_ROLES,
)
from apps.integrations.kobo.errors import KoboIntegrationError, KoboPayloadError
from apps.integrations.kobo.form_registry import get_registered_form
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboDiscoveredAsset,
    KoboFormDefinition,
    KoboSyncRun,
)


def _remote_asset_values(remote_asset: KoboRemoteAsset) -> dict:
    # PRE: remote_asset is a validated safe client projection.
    # POST: returns only fields permitted in discovery staging.
    return {
        "name": remote_asset.name,
        "asset_type": remote_asset.asset_type,
        "deployment_status": remote_asset.deployment_status,
        "owner_username": remote_asset.owner_username,
        "remote_created_at": remote_asset.created_at,
        "remote_modified_at": remote_asset.modified_at,
        "metadata_snapshot": remote_asset.safe_metadata,
        "is_available": True,
    }


def discover_assets(
    client: KoboApiClient,
    *,
    limit: int = 100,
    dry_run: bool = False,
) -> AssetDiscoveryResult:
    """
    PRE: feature enablement was checked at the command boundary and client is
    configured.
    POST: after a complete successful fetch, creates/updates discovery staging,
    marks unseen history unavailable unless dry-run, and creates no integrations.
    """
    sync_run = None if dry_run else KoboSyncRun.objects.create(
        kind=KoboSyncRun.Kind.DISCOVERY,
        status=KoboSyncRun.Status.RUNNING,
    )
    try:
        listed_assets = client.list_assets(limit=limit)
    except KoboIntegrationError:
        if sync_run:
            sync_run.status = KoboSyncRun.Status.FAILED
            sync_run.error_code = "remote_error"
            sync_run.safe_error_message = "Kobo remote discovery failed."
            sync_run.finished_at = timezone.now()
            sync_run.save()
        raise
    remote_assets = []
    detail_failures = 0
    for remote_asset in listed_assets:
        try:
            detail = client.get_asset_detail(remote_asset.asset_uid)
        except (KoboIntegrationError, KoboPayloadError):
            detail_failures += 1
            remote_assets.append(remote_asset)
            continue
        metadata = {
            **remote_asset.safe_metadata,
            **{key: value for key, value in detail.items() if value is not None},
        }
        remote_assets.append(replace(remote_asset, safe_metadata=metadata))
    remote_by_uid = {asset.asset_uid: asset for asset in remote_assets}
    existing_by_uid = {
        asset.asset_uid: asset
        for asset in KoboDiscoveredAsset.objects.filter(
            asset_uid__in=remote_by_uid
        )
    }
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    for asset_uid, remote_asset in remote_by_uid.items():
        existing = existing_by_uid.get(asset_uid)
        if existing is None:
            created_count += 1
            continue
        expected_values = _remote_asset_values(remote_asset)
        has_changes = any(
            getattr(existing, field_name) != value
            for field_name, value in expected_values.items()
        )
        updated_count += int(has_changes)
        unchanged_count += int(not has_changes)

    partial = detail_failures > 0
    unavailable_queryset = KoboDiscoveredAsset.objects.filter(
        is_available=True
    ).exclude(asset_uid__in=remote_by_uid)
    unavailable_count = 0 if partial else unavailable_queryset.count()
    if not dry_run:
        seen_at = timezone.now()
        with transaction.atomic():
            for asset_uid, remote_asset in remote_by_uid.items():
                values = _remote_asset_values(remote_asset)
                values["last_seen_at"] = seen_at
                KoboDiscoveredAsset.objects.update_or_create(
                    asset_uid=asset_uid,
                    defaults=values,
                )
            if not partial:
                unavailable_queryset.update(is_available=False)
        sync_run.status = KoboSyncRun.Status.PARTIAL if partial else KoboSyncRun.Status.SUCCEEDED
        sync_run.partial = partial
        sync_run.finished_at = timezone.now()
        sync_run.items_seen = len(remote_assets)
        sync_run.items_created = created_count
        sync_run.items_updated = updated_count
        sync_run.items_failed = detail_failures
        sync_run.save()

    return AssetDiscoveryResult(
        fetched_count=len(remote_assets),
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        unavailable_count=0 if dry_run else unavailable_count,
        failed_count=detail_failures,
        partial=partial,
    )


def _require_authenticated_actor(actor, *, action: str) -> None:
    # PRE: actor is the caller supplied for an auditable configuration action.
    # POST: returns only for an authenticated user; otherwise raises safely.
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ValidationError(f"An authenticated user is required to {action}.")


def _require_registered_active_definition(
    form_definition: KoboFormDefinition,
) -> None:
    # PRE: form_definition is a persisted candidate configuration definition.
    # POST: accepts only an active exact entry in the local supported-form registry.
    if form_definition is None or form_definition.pk is None:
        raise ValidationError("Form definition must exist.")
    if not form_definition.is_active:
        raise ValidationError("Form definition must be active.")
    try:
        get_registered_form(form_definition.form_id, form_definition.version)
    except KoboPayloadError as exc:
        raise ValidationError("Form definition is not registered locally.") from exc


def configure_discovered_asset(
    discovered_asset: KoboDiscoveredAsset,
    *,
    name: str,
    form_definition: KoboFormDefinition,
    form_role: str,
    configured_by,
) -> KoboAsset:
    """
    PRE: discovered asset is available and unconfigured; definition, role and
    authenticated actor are valid.
    POST: creates one inactive KoboAsset only, using the exact discovered UID.
    """
    _require_authenticated_actor(configured_by, action="configure a Kobo asset")
    if discovered_asset is None or discovered_asset.pk is None:
        raise ValidationError("Discovered asset must exist.")
    if not discovered_asset.is_available:
        raise ValidationError("Unavailable discovered assets cannot be configured.")
    _require_registered_active_definition(form_definition)
    valid_roles = {value for value, _label in KoboAsset.FormRole.choices}
    if form_role not in valid_roles:
        raise ValidationError("Kobo asset form role is invalid.")
    expected_role = FORM_DEFINITION_ROLES.get(
        (form_definition.form_id, form_definition.version)
    )
    if form_role != expected_role:
        raise ValidationError("Kobo asset role is incompatible with its form definition.")
    clean_name = name.strip() if isinstance(name, str) else ""
    if not clean_name:
        raise ValidationError("Kobo asset name is required.")

    with transaction.atomic():
        if KoboAsset.objects.filter(asset_uid=discovered_asset.asset_uid).exists():
            raise ValidationError("This discovered asset is already configured.")
        asset = KoboAsset(
            asset_uid=discovered_asset.asset_uid,
            name=clean_name,
            form_definition=form_definition,
            form_role=form_role,
            is_active=False,
        )
        asset.full_clean()
        asset.save()
    return asset


def get_asset_readiness(asset: KoboAsset) -> AssetReadiness:
    """
    PRE: asset is a persisted KoboAsset.
    POST: returns immutable readiness diagnostics without modifying any data.
    """
    if asset is None or asset.pk is None:
        raise ValidationError("Kobo asset must exist.")
    try:
        _require_registered_active_definition(asset.form_definition)
    except ValidationError:
        return AssetReadiness(
            False, "missing_form_definition", "Definición no disponible.", None, 0
        )
    status_code = "active" if asset.is_active else "ready_to_activate"
    message = "Integración activa." if asset.is_active else "Configuración lista para activar."
    return AssetReadiness(True, status_code, message, "territorial", 0)


def activate_kobo_asset(asset: KoboAsset, *, activated_by) -> KoboAsset:
    """
    PRE: asset is inactive, actor authenticated, and readiness is valid.
    POST: changes only is_active to True and returns the asset.
    """
    _require_authenticated_actor(activated_by, action="activate a Kobo asset")
    if asset.is_active:
        raise ValidationError("Kobo asset is already active.")
    readiness = get_asset_readiness(asset)
    if not readiness.ready:
        raise ValidationError(f"Kobo asset is not ready: {readiness.code}.")
    asset.is_active = True
    asset.save(update_fields=("is_active",))
    return asset


def deactivate_kobo_asset(asset: KoboAsset, *, deactivated_by) -> KoboAsset:
    """
    PRE: asset exists and actor is authenticated.
    POST: changes only is_active to False, preserving historical data and submissions.
    """
    _require_authenticated_actor(deactivated_by, action="deactivate a Kobo asset")
    if asset is None or asset.pk is None:
        raise ValidationError("Kobo asset must exist.")
    asset.is_active = False
    asset.save(update_fields=("is_active",))
    return asset
