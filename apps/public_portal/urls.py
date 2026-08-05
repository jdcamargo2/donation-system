from django.urls import path
from django.views.decorators.cache import cache_page

from . import views

app_name = 'public_portal'
urlpatterns = [
    path('data/projects.json', views.public_projects_json, name='public_projects_json'),
    path('data/metrics.json', views.public_metrics_json, name='public_metrics_json'),
    path('', cache_page(60)(views.PublicHomeView.as_view()), name='public_home'),
    path('projects/', cache_page(120)(views.PublicProjectListView.as_view()), name='public_project_list'),
    path('projects/<int:pk>/', cache_page(120)(views.PublicProjectDetailView.as_view()), name='public_project_detail'),
    path('updates/', cache_page(60)(views.PublicUpdatesFeedView.as_view()), name='public_updates_feed'),
    path('updates/<int:pk>/', views.PublicProjectUpdateDetailView.as_view(), name='public_project_update_detail'),
    path(
        'updates/<int:update_id>/documents/<int:attachment_id>/download/',
        views.PublicProjectUpdateAttachmentDeliveryView.as_view(),
        name='public_project_update_attachment_download',
    ),
    path(
        'updates/<int:update_id>/documents/<int:attachment_id>/preview/',
        views.PublicProjectUpdateAttachmentPreviewView.as_view(),
        name='public_project_update_attachment_preview',
    ),
]
