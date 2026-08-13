"""
URL configuration for SIGEDON.

Ownership is explicit:
- public portal at canonical root (Spanish paths);
- internal operational application under /panel/;
- institutional auth under /accounts/ (restricted routes only);
- Kobo management under /panel/integrations/kobo/;
- stable external webhook at /integrations/kobo/webhook/;
- legacy /transparency/** as permanent redirects only.
"""

from django.contrib import admin
from django.urls import include, path

from apps.integrations.kobo import views as kobo_views
from apps.public_portal.urls import legacy_transparency_urlpatterns
from core.health import healthz, readyz

urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('readyz/', readyz, name='readyz'),
    path('admin/', admin.site.urls),
    path('accounts/', include('core.auth_urls')),
    # Stable external webhook. Management UI is under /panel/integrations/kobo/.
    path(
        'integrations/kobo/webhook/',
        kobo_views.webhook_submission,
        name='kobo_webhook',
    ),
    path(
        'panel/integrations/kobo/',
        include('apps.integrations.kobo.urls'),
    ),
    path('panel/', include('apps.operations.urls')),
    path('transparency/', include(legacy_transparency_urlpatterns)),
    path('', include('apps.public_portal.urls')),
]

# Private operational media is never mounted via static() — not even in DEBUG.
# Access files only through authenticated protected preview/download endpoints.
# Public STATIC assets: Django staticfiles in DEBUG; WhiteNoise from STATIC_ROOT
# in production (see core.settings STORAGES / WhiteNoiseMiddleware).
