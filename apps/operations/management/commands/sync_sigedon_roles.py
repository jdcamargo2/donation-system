from django.core.management.base import BaseCommand

from apps.operations.role_services import sync_operation_roles


class Command(BaseCommand):
    help = 'Sincroniza grupos y permisos operativos de SIGEDON.'

    def handle(self, *args, **options):
        groups = sync_operation_roles()
        self.stdout.write(self.style.SUCCESS('Roles operativos de SIGEDON sincronizados.'))
        for role_name, group in groups.items():
            permission_count = group.permissions.count()
            self.stdout.write(f'- {role_name}: {permission_count} permisos')
