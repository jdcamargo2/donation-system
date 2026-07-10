from django.views.generic import ListView, TemplateView

from .selectors import (
    get_public_project_detail,
    get_public_projects,
    get_public_transparency_summary,
    get_recent_approved_updates,
)


class PublicHomeView(TemplateView):
    template_name = 'public_portal/public_home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['summary'] = get_public_transparency_summary()
        context['recent_updates'] = get_recent_approved_updates()
        return context


class PublicProjectListView(ListView):
    template_name = 'public_portal/public_project_list.html'
    context_object_name = 'projects'
    paginate_by = 20

    def get_queryset(self):
        return get_public_projects()


class PublicProjectDetailView(TemplateView):
    template_name = 'public_portal/public_project_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_public_project_detail(self.kwargs['pk']))
        return context


class PublicUpdatesFeedView(ListView):
    template_name = 'public_portal/public_updates_feed.html'
    context_object_name = 'updates'
    paginate_by = 20

    def get_queryset(self):
        return get_recent_approved_updates(limit=200)
