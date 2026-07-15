from django.apps import AppConfig


class OperationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.operations'

    def ready(self):
        from apps.operations.db_compat import apply_flush_trigger_bypass

        apply_flush_trigger_bypass()
