from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.integrations.kobo.services.common import (
    FORM_DEFINITION_ROLES,
    ProjectAssociationResult,
)
from apps.integrations.kobo.errors import KoboConfigurationError
from apps.integrations.kobo.models import (
    KoboAsset,
    KoboAttachment,
    KoboProcessingEvent,
    KoboProjectBinding,
    KoboSubmission,
)
from apps.integrations.kobo.services.importers import import_kobo_submission


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
    PRE: feature enablement was checked and territorial routing already assigned
    the supported, approved submission to its project.
    POST: delegates the legacy UI action to the sole materializing import service;
    it never marks IMPORTED through project association alone.
    """
    previous_status = submission.status
    result = import_kobo_submission(submission, actor=reviewed_by)
    submission.refresh_from_db()
    return ProjectAssociationResult(
        submission_id=submission.pk,
        asset_id=submission.asset_id,
        project_id=submission.project_id,
        previous_status=previous_status,
        final_status=submission.status,
        associated=result.imported,
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
