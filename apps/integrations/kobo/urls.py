from django.urls import path

from apps.integrations.kobo import views

app_name = "kobo"
urlpatterns = [
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
    path("submissions/", views.submission_list, name="submission_list"),
    path(
        "submissions/<int:pk>/",
        views.submission_detail,
        name="submission_detail",
    ),
    path(
        "submissions/<int:pk>/review/",
        views.review_submission_action,
        name="submission_review",
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
    path(
        "submissions/<int:pk>/associate-project/",
        views.associate_project_action,
        name="submission_associate_project",
    ),
]
