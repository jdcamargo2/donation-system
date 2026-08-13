from django.conf import settings
from django.urls import reverse

from apps.integrations.kobo.models import KoboAsset, KoboSubmission, KoboTerritorialIdentity
from apps.integrations.kobo.services import get_project_imported_submissions


def get_project_detail_context(project, user):
    """
    PRE: project and user belong to the internal Project detail request.
    POST: returns Kobo-only presentation context, empty while Kobo is disabled or unreadable.
    """
    empty_context = {
        "show_kobo_section": False,
        "kobo_territorial_submissions": (),
        "kobo_microproject_submissions": (),
        "kobo_prioritization_submissions": (),
        "kobo_submissions": (),
    }
    if not settings.KOBO_ENABLED:
        return empty_context
    has_territorial_data = KoboSubmission.objects.filter(project=project).exists()
    if not has_territorial_data:
        if user.has_perm("kobo.view_territorial_administration"):
            empty_context.update(
                {
                    "kobo_project_identity_count": 0,
                    "kobo_project_pending_count": 0,
                    "kobo_hub_project_url": f"{reverse('kobo:hub')}?project={project.pk}",
                }
            )
        return empty_context
    context = {
        "show_kobo_section": True,
        "kobo_territorial_submissions": get_project_imported_submissions(project, form_role=KoboAsset.FormRole.TERRITORIAL_PROFILE),
        "kobo_microproject_submissions": get_project_imported_submissions(project, form_role=KoboAsset.FormRole.PRIORITIZED_MICROPROJECT),
        "kobo_prioritization_submissions": get_project_imported_submissions(project, form_role=KoboAsset.FormRole.PRIORITIZATION_MATRIX),
    }
    context["kobo_submissions"] = context["kobo_territorial_submissions"]
    if not user.has_perm("kobo.view_territorial_administration"):
        return context
    identity_count = KoboTerritorialIdentity.objects.filter(project=project).count()
    pending_count = KoboSubmission.objects.filter(
        project=project,
        routing_status__in=(
            KoboSubmission.RoutingStatus.PENDING_IDENTITY,
            KoboSubmission.RoutingStatus.CONFLICT,
            KoboSubmission.RoutingStatus.ERROR,
        ),
    ).count()
    context.update({
        "kobo_project_identity_count": identity_count,
        "kobo_project_pending_count": pending_count,
        "kobo_hub_project_url": f"{reverse('kobo:hub')}?project={project.pk}",
    })
    return context
