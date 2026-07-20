from django.conf import settings


def kobo_feature(request):
    """
    PRE: request is being rendered through the Django template engine.
    POST: exposes the single Kobo feature-flag decision to shared navigation templates.
    """
    return {"kobo_enabled": settings.KOBO_ENABLED}
