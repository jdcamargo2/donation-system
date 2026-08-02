"""Django app configuration for the SIGEDON core project package."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    label = 'core'
    verbose_name = 'SIGEDON core'

    def ready(self):
        # PRE: Django app registry is loading installed apps.
        # POST: deploy system checks are registered via import side effects.
        from core import checks  # noqa: F401
