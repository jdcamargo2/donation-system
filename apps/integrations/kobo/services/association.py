from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    ProjectAssociationResult,
)
from apps.integrations.kobo.errors import KoboConfigurationError, KoboPayloadError
from apps.integrations.kobo.form_registry import KoboFormType, resolve_form_type
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboAttachment,
    KoboProcessingEvent,
    KoboProjectBinding,
    KoboSubmission,
)
from apps.integrations.kobo.services.routing import resolve_project_binding


def _association_failure(
    submission: KoboSubmission,
    *,
    previous_status: str,
    error_code: str,
    error_message: str,
) -> ProjectAssociationResult:
    # PRE: submission is locked inside an atomic association attempt.
    # POST: preserves review status, records a safe warning, and returns failure.
    submission.error_code = error_code
    submission.error_message = error_message
    submission.save(update_fields=("error_code", "error_message"))
    KoboProcessingEvent.objects.create(
        submission=submission,
        stage="project_association",
        level=KoboProcessingEvent.Level.WARNING,
        code=error_code,
        message=error_message,
    )
    return ProjectAssociationResult(
        submission_id=submission.pk,
        asset_id=None,
        project_id=None,
        previous_status=previous_status,
        final_status=submission.status,
        associated=False,
    )


def assign_normalized_submission_to_direct_project(
    submission: KoboSubmission,
) -> bool:
    """
    PRE: submission is persisted, normalized, and ready for review.
    POST: assigns the sole active direct-binding project and processed timestamp,
    or records a safe routing failure without changing the review status.
    """
    if submission is None or submission.pk is None:
        raise KoboConfigurationError("Kobo submission must exist.")

    def fail() -> bool:
        with transaction.atomic():
            locked_submission = KoboSubmission.objects.select_for_update().get(
                pk=submission.pk
            )
            already_recorded = (
                locked_submission.error_code == "routing_configuration_error"
                and locked_submission.error_message
                == "Kobo project routing could not be resolved."
            )
            locked_submission.error_code = "routing_configuration_error"
            locked_submission.error_message = "Kobo project routing could not be resolved."
            locked_submission.save(update_fields=("error_code", "error_message"))
            if not already_recorded:
                KoboProcessingEvent.objects.create(
                    submission=locked_submission,
                    stage="project_routing",
                    level=KoboProcessingEvent.Level.ERROR,
                    code="routing_configuration_error",
                    message="Kobo project routing could not be resolved.",
                )
        submission.error_code = "routing_configuration_error"
        submission.error_message = "Kobo project routing could not be resolved."
        return False

    try:
        locked_submission = KoboSubmission.objects.select_related(
            "asset__form_definition"
        ).get(pk=submission.pk)
        asset = locked_submission.asset
        if (
            locked_submission.status != KoboSubmission.Status.READY_FOR_REVIEW
            or asset is None
            or not asset.is_active
            or not asset.form_definition.is_active
            or asset.form_role
            != FORM_DEFINITION_ROLES.get(
                (asset.form_definition.form_id, asset.form_definition.version)
            )
        ):
            return fail()
        bindings = list(
            asset.project_bindings.filter(is_active=True).select_related("project")
        )
        if (
            len(bindings) != 1
            or bindings[0].routing_type != KoboProjectBinding.RoutingType.DIRECT
        ):
            return fail()
        from apps.operations.models import Project

        if bindings[0].project.status != Project.Status.ACTIVE:
            return fail()
    except (KoboAsset.DoesNotExist, KoboSubmission.DoesNotExist):
        return fail()

    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        if (
            locked_submission.project_id == bindings[0].project_id
            and locked_submission.processed_at is not None
        ):
            return True
        locked_submission.project_id = bindings[0].project_id
        locked_submission.processed_at = timezone.now()
        locked_submission.error_code = ""
        locked_submission.error_message = ""
        locked_submission.save(
            update_fields=(
                "project",
                "processed_at",
                "error_code",
                "error_message",
            )
        )
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="project_routing",
            level=KoboProcessingEvent.Level.INFO,
            code="project_assigned",
            message="Kobo submission assigned to its direct project.",
        )
    submission.project_id = bindings[0].project_id
    submission.processed_at = locked_submission.processed_at
    submission.error_code = ""
    submission.error_message = ""
    return True


def associate_submission_with_project(
    submission: KoboSubmission,
    *,
    reviewed_by,
) -> ProjectAssociationResult:
    """
    PRE: feature enablement was checked at the view boundary; submission is
    approved with dict payload/zone and reviewed_by is authenticated.
    POST: atomically resolves exact active asset/binding, imports the association,
    or records a safe expected warning without modifying either payload.
    """
    if not getattr(reviewed_by, "is_authenticated", False):
        raise KoboConfigurationError("An authenticated reviewer is required.")

    with transaction.atomic():
        locked_submission = KoboSubmission.objects.select_for_update().get(
            pk=submission.pk
        )
        previous_status = locked_submission.status
        if previous_status == KoboSubmission.Status.IMPORTED:
            return ProjectAssociationResult(
                submission_id=locked_submission.pk,
                asset_id=locked_submission.asset_id,
                project_id=locked_submission.project_id,
                previous_status=previous_status,
                final_status=previous_status,
                associated=False,
            )
        if previous_status != KoboSubmission.Status.APPROVED_FOR_IMPORT:
            raise KoboPayloadError("Submission is not approved for project association.")

        if not isinstance(locked_submission.raw_payload, dict):
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="invalid_raw_payload",
                error_message="Submission payload is unavailable for association.",
            )
        asset_uid = locked_submission.raw_payload.get("_xform_id_string")
        if not isinstance(asset_uid, str) or not asset_uid.strip():
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_uid_missing",
                error_message="Kobo asset identifier is missing.",
            )
        try:
            asset = KoboAsset.objects.get(asset_uid=asset_uid)
        except KoboAsset.DoesNotExist:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_not_found",
                error_message="Configured Kobo asset was not found.",
            )
        if not asset.is_active:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_inactive",
                error_message="Configured Kobo asset is inactive.",
            )
        expected_role = FORM_DEFINITION_ROLES.get(
            (asset.form_definition.form_id, asset.form_definition.version)
        )
        if asset.form_role != expected_role:
            return _association_failure(
                locked_submission,
                previous_status=previous_status,
                error_code="asset_role_incompatible",
                error_message="Kobo asset role is incompatible with this submission.",
            )

        try:
            form_type = resolve_form_type(
                locked_submission.form_definition.form_id,
                locked_submission.form_definition.version,
            )
        except KoboPayloadError:
            form_type = None
        uses_territorial_routing = (
            form_type
            in {
                KoboFormType.FICHA_1,
                KoboFormType.FICHA_10,
                KoboFormType.FICHA_11,
            }
            and locked_submission.routing_status
            != KoboSubmission.RoutingStatus.UNRESOLVED
        )
        if uses_territorial_routing:
            if (
                locked_submission.routing_status
                != KoboSubmission.RoutingStatus.RESOLVED
                or locked_submission.project_id is None
            ):
                return _association_failure(
                    locked_submission,
                    previous_status=previous_status,
                    error_code="territorial_routing_unresolved",
                    error_message="Submission territorial routing is not resolved.",
                )
            routing_project_id = locked_submission.project_id
        else:
            try:
                routing = resolve_project_binding(locked_submission, asset)
                routing_project_id = routing.project_id
            except KoboConfigurationError as exc:
                error_code = str(exc)
                if error_code not in {"routing_not_found", "routing_ambiguous"}:
                    error_code = "routing_configuration_error"
                return _association_failure(
                    locked_submission,
                    previous_status=previous_status,
                    error_code=error_code,
                    error_message="No unique active project route matches this submission.",
                )
            except KoboPayloadError:
                return _association_failure(
                    locked_submission,
                    previous_status=previous_status,
                    error_code="routing_value_invalid",
                    error_message="Submission routing data is invalid or unavailable.",
                )

        associated_at = timezone.now()
        locked_submission.asset = asset
        locked_submission.project_id = routing_project_id
        locked_submission.imported_at = associated_at
        if locked_submission.processed_at is None:
            locked_submission.processed_at = associated_at
        locked_submission.status = KoboSubmission.Status.IMPORTED
        locked_submission.error_code = ""
        locked_submission.error_message = ""
        locked_submission.save(
            update_fields=(
                "asset",
                "project",
                "imported_at",
                "processed_at",
                "status",
                "error_code",
                "error_message",
            )
        )
        KoboProcessingEvent.objects.create(
            submission=locked_submission,
            stage="project_association",
            level=KoboProcessingEvent.Level.INFO,
            code="project_associated",
            message="Submission associated with its configured project.",
        )

    submission.asset_id = asset.pk
    submission.project_id = routing_project_id
    submission.imported_at = associated_at
    submission.processed_at = locked_submission.processed_at
    submission.status = locked_submission.status
    submission.error_code = ""
    submission.error_message = ""
    return ProjectAssociationResult(
        submission_id=submission.pk,
        asset_id=asset.pk,
        project_id=routing_project_id,
        previous_status=previous_status,
        final_status=submission.status,
        associated=True,
    )


def get_project_imported_submissions(
    project,
    *,
    form_role=None,
):
    """
    PRE: project exists.
    POST: returns imported submissions for the exact project and active assets,
    optionally filtered by role, ordered by assessment and receipt descending,
    with related review data loaded and without modifying state.
    """
    downloaded_attachments = KoboAttachment.objects.filter(
        status=KoboAttachment.Status.DOWNLOADED
    ).order_by("pk")
    queryset = (
        KoboSubmission.objects.filter(
            project=project,
            status=KoboSubmission.Status.IMPORTED,
            asset__is_active=True,
        )
        .select_related("form_definition", "asset", "project")
        .prefetch_related(
            Prefetch(
                "attachments",
                queryset=downloaded_attachments,
                to_attr="downloaded_attachments",
            ),
            "processing_events",
        )
        .annotate(
            attachment_count=Count("attachments", distinct=True),
            downloaded_attachment_count=Count(
                "attachments",
                filter=Q(attachments__status=KoboAttachment.Status.DOWNLOADED),
                distinct=True,
            ),
        )
        .order_by("-assessment_date", "-received_at")
    )
    if form_role is not None:
        queryset = queryset.filter(asset__form_role=form_role)
    return queryset


def get_project_pending_submissions(project):
    """
    PRE: project exists and is the internal project selected by the user.
    POST: returns only its active-asset submissions ready for an operational
    import, with safe display relations and counts, without modifying state.
    """
    return (
        KoboSubmission.objects.filter(
            project=project,
            status=KoboSubmission.Status.READY_FOR_REVIEW,
            imported_at__isnull=True,
            asset__is_active=True,
        )
        .exclude(
            routing_status__in=(
                KoboSubmission.RoutingStatus.PENDING_IDENTITY,
                KoboSubmission.RoutingStatus.CONFLICT,
                KoboSubmission.RoutingStatus.ERROR,
            )
        )
        .select_related("form_definition", "asset", "project")
        .annotate(
            attachment_count=Count("attachments", distinct=True),
            downloaded_attachment_count=Count(
                "attachments",
                filter=Q(attachments__status=KoboAttachment.Status.DOWNLOADED),
                distinct=True,
            ),
        )
        .order_by("-received_at", "-pk")
    )


def get_project_submission_history(project):
    """
    PRE: project exists and identifies the internal project being consulted.
    POST: returns only imported or rejected submissions for that project without
    applying active-asset filters that would hide historical decisions.
    """
    return (
        KoboSubmission.objects.filter(
            project=project,
            status__in=(
                KoboSubmission.Status.IMPORTED,
                KoboSubmission.Status.REJECTED,
            ),
        )
        .select_related("form_definition", "asset", "project")
        .prefetch_related("processing_events")
        .order_by("-imported_at", "-received_at", "-pk")
    )
