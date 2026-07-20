from django.apps import AppConfig


class KoboConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.integrations.kobo'

    def ready(self):
        # PRE: Django has loaded installed application configurations.
        # POST: Kobo contributes its Project-detail context without Operations importing Kobo.
        from apps.operations.integrations import register_project_detail_extension
        from apps.integrations.kobo.project_extension import get_project_detail_context

        register_project_detail_extension(get_project_detail_context)
