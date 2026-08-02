from django.contrib.auth.mixins import LoginRequiredMixin

from django.views.generic import TemplateView

from ..services import get_dashboard_metrics


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_dashboard_metrics(user=self.request.user))
        return context
