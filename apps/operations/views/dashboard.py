from django.contrib.auth.mixins import LoginRequiredMixin

from django.views.generic import TemplateView

from ..role_services import get_user_functional_role
from ..roles import ROLE_EXTERNAL_AUDITOR
from ..services import get_dashboard_metrics


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_dashboard_metrics(user=self.request.user))
        functional_role = get_user_functional_role(self.request.user)
        # Presentation-only: hide Accesos rápidos for Auditor externo.
        # Does not revoke permissions or alter sidebar/routes.
        context['show_financial_quick_actions'] = not (
            functional_role is not None
            and functional_role.name == ROLE_EXTERNAL_AUDITOR
        )
        return context
