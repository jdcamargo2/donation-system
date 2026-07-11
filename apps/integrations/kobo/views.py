import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.storage import default_storage
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.integrations.kobo.client import KoboApiClient
from apps.integrations.kobo.forms import (
    KoboAssetConfigurationForm,
    KoboProjectBindingForm,
    KoboReviewForm,
)
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboAttachment,
    KoboDiscoveredAsset,
    KoboSubmission,
)
from apps.integrations.kobo.processors import (
    process_submission,
    process_submission_attachments,
)
from apps.integrations.kobo.services import (
    associate_submission_with_project,
    activate_kobo_asset,
    configure_discovered_asset,
    create_project_binding,
    deactivate_kobo_asset,
    get_asset_readiness,
    review_submission,
)


def _require_kobo_enabled() -> None:
    # PRE: a browser request targets an internal Kobo surface.
    # POST: returns only while the feature is enabled; otherwise hides the surface.
    if not settings.KOBO_ENABLED:
        raise Http404


def _local_asset_state(asset: KoboAsset | None) -> str:
    # PRE: asset is the optional configured counterpart of a discovered asset.
    # POST: returns one explicit local lifecycle state without database mutation.
    if asset is None:
        return "unconfigured"
    return "active" if asset.is_active else "configured_inactive"


def _asset_configuration_context(asset, *, binding_form=None):
    # PRE: asset is loaded with its form definition and bindings are queryable.
    # POST: returns safe configuration/readiness context without changing state.
    return {
        "asset": asset,
        "bindings": asset.project_bindings.select_related("project").order_by("pk"),
        "readiness": get_asset_readiness(asset),
        "binding_form": binding_form or KoboProjectBindingForm(),
    }


def _submission_queryset():
    # PRE: Kobo staging models are migrated.
    # POST: returns review data with relations prefetched and no state changes.
    return KoboSubmission.objects.select_related("form_definition").prefetch_related(
        "attachments",
        "processing_events",
    )


def _can_view_raw_payload(user) -> bool:
    # PRE: user is an authenticated request user.
    # POST: grants raw access only through existing elevated permission/superuser.
    return user.is_superuser or user.has_perm("kobo.change_kobosubmission")


def _can_view_sensitive_kobo_data(user) -> bool:
    # PRE: user is an authenticated request user.
    # POST: returns elevated Kobo review authorization without side effects.
    return user.is_superuser or user.has_perm("kobo.change_kobosubmission")


def _detail_context(submission, user, *, review_form=None):
    # PRE: submission is loaded and user passed general view authorization.
    # POST: returns separated review context without exposing attachment sources.
    normalized_payload = submission.normalized_payload or {}
    sensitive_data = {
        "survey_responsible": normalized_payload.get("survey_responsible"),
        "parish_priest": normalized_payload.get("parish_priest"),
        "contact_phone": normalized_payload.get("contact_phone"),
        "submitted_by": submission.raw_payload.get("_submitted_by"),
        "device_id": submission.raw_payload.get("deviceid"),
    }
    sensitive_keys = {"survey_responsible", "parish_priest", "contact_phone"}
    display_normalized_payload = {
        key: value
        for key, value in normalized_payload.items()
        if key not in sensitive_keys
    }
    can_view_raw_payload = _can_view_raw_payload(user)
    return {
        "submission": submission,
        "normalized_payload": display_normalized_payload,
        "sensitive_data": sensitive_data,
        "attachments": submission.attachments.all(),
        "processing_events": submission.processing_events.all(),
        "review_form": review_form or KoboReviewForm(submission=submission),
        "can_change_submission": user.has_perm("kobo.change_kobosubmission"),
        "can_view_raw_payload": can_view_raw_payload,
        "kobo_enabled": settings.KOBO_ENABLED,
        "raw_payload_json": (
            json.dumps(submission.raw_payload, indent=2, ensure_ascii=False)
            if can_view_raw_payload
            else None
        ),
    }


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def submission_list(request):
    submissions = KoboSubmission.objects.select_related("form_definition").annotate(
        attachment_count=Count("attachments", distinct=True),
        downloaded_attachment_count=Count(
            "attachments",
            filter=Q(attachments__status=KoboAttachment.Status.DOWNLOADED),
            distinct=True,
        ),
    )
    filters = {
        "status": request.GET.get("status", "").strip(),
        "form_id": request.GET.get("form_id", "").strip(),
        "pastoral_zone": request.GET.get("pastoral_zone", "").strip(),
        "parish": request.GET.get("parish", "").strip(),
    }
    if filters["status"]:
        submissions = submissions.filter(status=filters["status"])
    if filters["form_id"]:
        submissions = submissions.filter(form_definition__form_id=filters["form_id"])
    if filters["pastoral_zone"]:
        submissions = submissions.filter(pastoral_zone=filters["pastoral_zone"])
    if filters["parish"]:
        submissions = submissions.filter(parish=filters["parish"])
    return render(
        request,
        "kobo/submission_list.html",
        {
            "submissions": submissions,
            "filters": filters,
            "status_choices": KoboSubmission.Status.choices,
            "form_ids": KoboSubmission.objects.values_list(
                "form_definition__form_id", flat=True
            ).distinct(),
        },
    )


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def submission_detail(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    return render(
        request,
        "kobo/submission_detail.html",
        _detail_context(submission, request.user),
    )


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def review_submission_action(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    form = KoboReviewForm(request.POST, submission=submission)
    if not form.is_valid():
        return render(
            request,
            "kobo/submission_detail.html",
            _detail_context(submission, request.user, review_form=form),
            status=400,
        )
    review_submission(
        submission,
        decision=form.cleaned_data["decision"],
        reason=form.cleaned_data["reason"],
        reviewed_by=request.user,
    )
    messages.success(request, "Revisión registrada.")
    return redirect("kobo:submission_detail", pk=submission.pk)


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def retry_normalization_action(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    outcome = process_submission(
        submission,
        default_timezone=timezone.get_current_timezone(),
    )
    messages.info(request, f"Normalización finalizada: {outcome.final_status}.")
    return redirect("kobo:submission_detail", pk=submission.pk)


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def retry_attachments_action(request, pk):
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    client = KoboApiClient(
        base_url=settings.KOBO_BASE_URL,
        api_token=settings.KOBO_API_TOKEN,
        timeout_seconds=settings.KOBO_REQUEST_TIMEOUT_SECONDS,
    )
    result = process_submission_attachments(
        submission,
        client=client,
        storage=default_storage,
        max_bytes=settings.KOBO_MAX_ATTACHMENT_BYTES,
    )
    messages.info(request, f"Adjuntos procesados: {result.selected}.")
    return redirect("kobo:submission_detail", pk=submission.pk)


@require_POST
@login_required
@permission_required(
    ("kobo.view_kobosubmission", "kobo.change_kobosubmission"),
    raise_exception=True,
)
def associate_project_action(request, pk):
    if not settings.KOBO_ENABLED:
        raise PermissionDenied("Kobo integration is disabled.")
    submission = get_object_or_404(_submission_queryset(), pk=pk)
    result = associate_submission_with_project(
        submission,
        reviewed_by=request.user,
    )
    if result.associated:
        messages.success(request, "Submission asociada al proyecto configurado.")
    else:
        messages.warning(request, "No fue posible asociar la submission.")
    return redirect("kobo:submission_detail", pk=submission.pk)


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def project_submission_detail(request, pk):
    if not settings.KOBO_ENABLED:
        raise Http404
    submission = get_object_or_404(
        KoboSubmission.objects.select_related(
            "project",
            "asset",
            "form_definition",
        ),
        pk=pk,
        status=KoboSubmission.Status.IMPORTED,
        project__isnull=False,
        asset__is_active=True,
    )
    normalized_payload = submission.normalized_payload or {}
    can_view_sensitive = _can_view_sensitive_kobo_data(request.user)
    evidences = submission.attachments.filter(
        status=KoboAttachment.Status.DOWNLOADED
    ).order_by("pk")
    if not can_view_sensitive:
        evidences = evidences.exclude(
            privacy_level=KoboAttachment.PrivacyLevel.PRIVATE
        )
    return render(
        request,
        "kobo/project_submission_detail.html",
        {
            "submission": submission,
            "normalized_payload": normalized_payload,
            "evidences": evidences,
            "can_view_sensitive": can_view_sensitive,
            "sensitive_data": {
                "survey_responsible": normalized_payload.get("survey_responsible"),
                "parish_priest": normalized_payload.get("parish_priest"),
                "contact_phone": normalized_payload.get("contact_phone"),
                "submitted_by": submission.raw_payload.get("_submitted_by"),
                "device_id": submission.raw_payload.get("deviceid"),
            },
        },
    )


@login_required
@permission_required("kobo.view_kobosubmission", raise_exception=True)
def project_submission_evidence(request, pk, attachment_pk):
    if not settings.KOBO_ENABLED:
        raise Http404
    attachment = get_object_or_404(
        KoboAttachment.objects.select_related("submission"),
        pk=attachment_pk,
        submission_id=pk,
        submission__status=KoboSubmission.Status.IMPORTED,
        submission__project__isnull=False,
        submission__asset__is_active=True,
        status=KoboAttachment.Status.DOWNLOADED,
    )
    if (
        attachment.privacy_level == KoboAttachment.PrivacyLevel.PRIVATE
        and not _can_view_sensitive_kobo_data(request.user)
    ):
        raise Http404
    if not attachment.file:
        raise Http404
    safe_filename = attachment.file.name.rsplit("/", 1)[-1]
    try:
        stored_file = attachment.file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404 from exc
    return FileResponse(
        stored_file,
        content_type=attachment.content_type or "application/octet-stream",
        filename=safe_filename,
    )


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def discovered_asset_list(request):
    # PRE: request user may view Kobo assets.
    # POST: renders safe discovery summaries without remote secrets or mutation.
    _require_kobo_enabled()
    discovered_assets = list(KoboDiscoveredAsset.objects.order_by("name", "pk"))
    configured_by_uid = {
        asset.asset_uid: asset
        for asset in KoboAsset.objects.filter(
            asset_uid__in=[item.asset_uid for item in discovered_assets]
        )
    }
    rows = [
        {
            "discovered": item,
            "local_asset": configured_by_uid.get(item.asset_uid),
            "local_state": _local_asset_state(configured_by_uid.get(item.asset_uid)),
            "short_uid": item.asset_uid[:12],
        }
        for item in discovered_assets
    ]
    return render(request, "kobo/discovered_asset_list.html", {"rows": rows})


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def discovered_asset_detail(request, pk):
    # PRE: request user may view Kobo assets and pk identifies a discovery candidate.
    # POST: renders safe discovery/configuration details without mutation.
    _require_kobo_enabled()
    discovered = get_object_or_404(KoboDiscoveredAsset, pk=pk)
    asset = KoboAsset.objects.filter(asset_uid=discovered.asset_uid).select_related(
        "form_definition"
    ).first()
    return render(
        request,
        "kobo/discovered_asset_detail.html",
        {
            "discovered": discovered,
            "asset": asset,
            "bindings": (
                asset.project_bindings.select_related("project").order_by("pk")
                if asset
                else ()
            ),
            "local_state": _local_asset_state(asset),
            "configuration_form": KoboAssetConfigurationForm(),
        },
    )


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def configure_discovered_asset_action(request, pk):
    # PRE: authorized POST supplies configuration fields for one discovered asset.
    # POST: creates at most one inactive local asset and redirects to its detail.
    _require_kobo_enabled()
    discovered = get_object_or_404(KoboDiscoveredAsset, pk=pk)
    existing = KoboAsset.objects.filter(asset_uid=discovered.asset_uid).first()
    if existing is not None:
        messages.error(request, "El activo descubierto ya está configurado.")
        return redirect("kobo:asset_configuration", pk=existing.pk)
    form = KoboAssetConfigurationForm(request.POST)
    if form.is_valid():
        try:
            asset = configure_discovered_asset(
                discovered,
                name=form.cleaned_data["name"],
                form_definition=form.cleaned_data["form_definition"],
                form_role=form.cleaned_data["form_role"],
                configured_by=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Activo configurado; permanece inactivo.")
            return redirect("kobo:asset_configuration", pk=asset.pk)
    return render(
        request,
        "kobo/discovered_asset_detail.html",
        {
            "discovered": discovered,
            "asset": None,
            "bindings": (),
            "local_state": "unconfigured",
            "configuration_form": form,
        },
        status=400,
    )


@login_required
@permission_required("kobo.view_koboasset", raise_exception=True)
def asset_configuration_detail(request, pk):
    # PRE: authorized request identifies a configured Kobo asset.
    # POST: renders readiness and bindings without state changes.
    _require_kobo_enabled()
    asset = get_object_or_404(
        KoboAsset.objects.select_related("form_definition"), pk=pk
    )
    return render(
        request,
        "kobo/asset_configuration_detail.html",
        _asset_configuration_context(asset),
    )


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def create_project_binding_action(request, pk):
    # PRE: authorized POST supplies one candidate binding for a configured asset.
    # POST: creates only a valid binding and never activates the asset.
    _require_kobo_enabled()
    asset = get_object_or_404(KoboAsset, pk=pk)
    form = KoboProjectBindingForm(request.POST)
    if form.is_valid():
        try:
            create_project_binding(
                asset,
                routing_type=form.cleaned_data["routing_type"],
                project=form.cleaned_data["project"],
                source_field=form.cleaned_data["source_field"],
                source_value=form.cleaned_data["source_value"],
                is_active=form.cleaned_data["is_active"],
                configured_by=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Binding creado.")
            return redirect("kobo:asset_configuration", pk=asset.pk)
    return render(
        request,
        "kobo/asset_configuration_detail.html",
        _asset_configuration_context(asset, binding_form=form),
        status=400,
    )


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def activate_kobo_asset_action(request, pk):
    # PRE: authorized POST identifies an inactive configured asset.
    # POST: activates only a ready asset; failures preserve current state.
    _require_kobo_enabled()
    asset = get_object_or_404(KoboAsset, pk=pk)
    try:
        activate_kobo_asset(asset, activated_by=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc.message))
    else:
        messages.success(request, "Integración activada.")
    return redirect("kobo:asset_configuration", pk=asset.pk)


@require_POST
@login_required
@permission_required("kobo.change_koboasset", raise_exception=True)
def deactivate_kobo_asset_action(request, pk):
    # PRE: authorized POST identifies a configured asset.
    # POST: deactivates it while preserving routing and staged submissions.
    _require_kobo_enabled()
    asset = get_object_or_404(KoboAsset, pk=pk)
    deactivate_kobo_asset(asset, deactivated_by=request.user)
    messages.success(request, "Integración desactivada.")
    return redirect("kobo:asset_configuration", pk=asset.pk)
