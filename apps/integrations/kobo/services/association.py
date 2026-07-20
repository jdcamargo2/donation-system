from django.db.models import Count, Prefetch, Q

from apps.integrations.kobo.models import (
    KoboAttachment,
    KoboSubmission,
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
