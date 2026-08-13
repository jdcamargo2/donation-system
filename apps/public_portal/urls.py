from django.urls import path
from django.views.decorators.cache import cache_page

from . import views
from .legacy_redirects import LegacyPublicRedirectView

app_name = 'public_portal'

# Canonical Spanish public routes (mounted at site root).
urlpatterns = [
    path(
        'datos/proyectos.json',
        views.public_projects_json,
        name='public_projects_json',
    ),
    path(
        'datos/metricas.json',
        views.public_metrics_json,
        name='public_metrics_json',
    ),
    path('', cache_page(60)(views.PublicHomeView.as_view()), name='public_home'),
    path(
        'proyectos/',
        cache_page(120)(views.PublicProjectListView.as_view()),
        name='public_project_list',
    ),
    path(
        'proyectos/<int:pk>/',
        cache_page(120)(views.PublicProjectDetailView.as_view()),
        name='public_project_detail',
    ),
    path(
        'avances/',
        cache_page(60)(views.PublicUpdatesFeedView.as_view()),
        name='public_updates_feed',
    ),
    path(
        'avances/<int:pk>/',
        views.PublicProjectUpdateDetailView.as_view(),
        name='public_project_update_detail',
    ),
    path(
        'avances/<int:update_id>/documentos/<int:attachment_id>/descargar/',
        views.PublicProjectUpdateAttachmentDeliveryView.as_view(),
        name='public_project_update_attachment_download',
    ),
    path(
        'avances/<int:update_id>/documentos/<int:attachment_id>/vista-previa/',
        views.PublicProjectUpdateAttachmentPreviewView.as_view(),
        name='public_project_update_attachment_preview',
    ),
]

# Permanent compatibility redirects from /transparency/** (mounted separately).
legacy_transparency_urlpatterns = [
    path(
        '',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_home',
        ),
        name='legacy_public_home',
    ),
    path(
        'projects/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_project_list',
        ),
        name='legacy_public_project_list',
    ),
    path(
        'projects/<int:pk>/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_project_detail',
            pk_kwarg='pk',
        ),
        name='legacy_public_project_detail',
    ),
    path(
        'updates/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_updates_feed',
        ),
        name='legacy_public_updates_feed',
    ),
    path(
        'updates/<int:pk>/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_project_update_detail',
            pk_kwarg='pk',
        ),
        name='legacy_public_update_detail',
    ),
    path(
        'updates/<int:update_id>/documents/<int:attachment_id>/download/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_project_update_attachment_download',
            update_id_kwarg='update_id',
            attachment_id_kwarg='attachment_id',
        ),
        name='legacy_public_update_attachment_download',
    ),
    path(
        'updates/<int:update_id>/documents/<int:attachment_id>/preview/',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_project_update_attachment_preview',
            update_id_kwarg='update_id',
            attachment_id_kwarg='attachment_id',
        ),
        name='legacy_public_update_attachment_preview',
    ),
    path(
        'data/projects.json',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_projects_json',
        ),
        name='legacy_public_projects_json',
    ),
    path(
        'data/metrics.json',
        LegacyPublicRedirectView.as_view(
            target_url_name='public_portal:public_metrics_json',
        ),
        name='legacy_public_metrics_json',
    ),
]
