from django.views.generic import ListView

from ..models import AuditLog

from .common import (
    FilteredListContextMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    apply_list_filters,
)


class AuditLogListView(
    OperationsPermissionRequiredMixin,
    FilteredListContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_auditlog'
    model = AuditLog
    template_name = 'web/audit_log_list.html'
    context_object_name = 'logs'
    status_choices = AuditLog.Action.choices

    def get_queryset(self):
        return apply_list_filters(
            AuditLog.objects.select_related('user'),
            self.request.GET,
            text_fields=('entity_id', 'entity_label', 'model_name', 'summary'),
            date_field='created_at__date',
            status_field='action',
        ).order_by('-created_at', '-pk')
