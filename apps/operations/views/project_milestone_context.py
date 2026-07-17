from django.db.models import Prefetch
from django.shortcuts import get_object_or_404

from ..milestones import get_milestone_progress
from ..models import Project, ProjectMilestone


# PRE: the caller is building a Project queryset for milestone presentation.
# POST: returns the canonical ordered prefetch with both optional users joined.
def project_milestone_prefetch():
    return Prefetch(
        'milestones',
        queryset=ProjectMilestone.objects.select_related(
            'completed_by',
            'created_by',
        ).order_by('position', 'pk'),
        to_attr='detail_milestones',
    )


# PRE: project was loaded with project_milestone_prefetch and user is authenticated.
# POST: returns the complete presentation context shared by detail and HTMX partial responses.
def build_project_milestone_context(project, user):
    granted_permissions = user.get_all_permissions()
    can_view_project = 'operations.view_project' in granted_permissions
    return {
        'object': project,
        'project': project,
        'project_milestones': project.detail_milestones,
        'milestone_progress': get_milestone_progress(project.detail_milestones),
        'can_add_project_milestone': (
            can_view_project
            and 'operations.add_projectmilestone' in granted_permissions
        ),
        'can_change_project_milestone': (
            can_view_project
            and 'operations.change_projectmilestone' in granted_permissions
        ),
        'can_complete_project_milestone': (
            can_view_project
            and 'operations.complete_projectmilestone' in granted_permissions
        ),
        'can_delete_project_milestone': (
            can_view_project
            and 'operations.delete_projectmilestone' in granted_permissions
        ),
        'can_reorder_project_milestone': (
            can_view_project
            and 'operations.reorder_projectmilestone' in granted_permissions
        ),
        'milestone_mutations_allowed': project.status not in (
            Project.Status.CLOSED,
            Project.Status.ANNULLED,
        ),
    }


# PRE: project_id identifies the project whose post-mutation component must be rendered.
# POST: returns the project with the same canonical milestone prefetch or raises HTTP 404.
def get_project_for_milestone_partial(project_id):
    return get_object_or_404(
        Project.objects.prefetch_related(project_milestone_prefetch()),
        pk=project_id,
    )
