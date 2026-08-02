from django.urls import path

from apps.integrations.kobo import views

app_name = "kobo"
urlpatterns = [
    path("", views.hub_dashboard, name="hub"),
    path("sync/", views.sync_all, name="sync_all"),
    path("sync/history/", views.sync_history, name="sync_history"),
    path("sync/<int:pk>/<str:mode>/", views.sync_asset, name="sync_asset"),
    path("dashboard/status/", views.dashboard_status, name="dashboard_status"),
    path("mappings/", views.mapping_list, name="mapping_list"),
    path("mappings/modal/", views.mapping_modal, name="mapping_modal"),
    path("mappings/configure/", views.configure_mapping, name="configure_mapping"),
    path("mappings/<str:zone>/deactivate/", views.deactivate_mapping, name="deactivate_mapping"),
    path("identities/", views.identity_list, name="identity_list"),
    path("identities/<int:pk>/", views.identity_detail, name="identity_detail"),
    path("identities/<int:pk>/status/<str:action>/", views.identity_status, name="identity_status"),
    path("identities/<int:pk>/reconcile/", views.reconcile_identity, name="reconcile_identity"),
    path("conflicts/", views.conflict_list, name="conflict_list"),
    path("conflicts/<int:pk>/", views.conflict_detail, name="conflict_detail"),
    path("conflicts/<int:pk>/resolve/", views.resolve_conflict, name="resolve_conflict"),
    path("submissions/pending/", views.pending_submission_list, name="pending_submission_list"),
    path(
        "submissions/<int:pk>/retry-import/",
        views.retry_submission_import,
        name="retry_submission_import",
    ),
    path("webhook/", views.webhook_submission, name="webhook_submission"),
    path(
        "discovered-assets/",
        views.discovered_asset_list,
        name="discovered_asset_list",
    ),
    path(
        "discovered-assets/<int:pk>/",
        views.discovered_asset_detail,
        name="discovered_asset_detail",
    ),
    path(
        "discovered-assets/<int:pk>/configure/",
        views.configure_discovered_asset_action,
        name="configure_discovered_asset",
    ),
    path(
        "assets/<int:pk>/configuration/",
        views.asset_configuration_detail,
        name="asset_configuration",
    ),
    path(
        "assets/<int:pk>/activate/",
        views.activate_kobo_asset_action,
        name="activate_asset",
    ),
    path(
        "assets/<int:pk>/deactivate/",
        views.deactivate_kobo_asset_action,
        name="deactivate_asset",
    ),
    path(
        "project-submissions/<int:pk>/",
        views.project_submission_detail,
        name="project_submission_detail",
    ),
    path(
        "project-submissions/<int:pk>/evidence/<int:attachment_pk>/",
        views.project_submission_evidence,
        name="project_submission_evidence",
    ),
    path(
        "project-submissions/<int:pk>/evidence/<int:attachment_pk>/download/",
        views.project_submission_evidence_download,
        name="project_submission_evidence_download",
    ),
    path(
        "projects/<int:project_pk>/submission-history/",
        views.project_submission_history,
        name="project_submission_history",
    ),
    path(
        "projects/<int:project_pk>/submission-history/<int:pk>/",
        views.project_submission_history_detail,
        name="project_submission_history_detail",
    ),
    path("submissions/", views.submission_list, name="submission_list"),
    path(
        "submissions/<int:pk>/",
        views.submission_detail,
        name="submission_detail",
    ),
    path(
        "submissions/<int:pk>/retry-normalization/",
        views.retry_normalization_action,
        name="submission_retry_normalization",
    ),
    path(
        "submissions/<int:pk>/retry-attachments/",
        views.retry_attachments_action,
        name="submission_retry_attachments",
    ),
]
