# Generated manually for UserAccessProfile institutional access lifecycle.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operations', '0032_alter_fundallocation_budget_category_label'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserAccessProfile',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'must_change_password',
                    models.BooleanField(
                        default=False,
                        verbose_name='debe cambiar la contraseña',
                    ),
                ),
                (
                    'password_reset_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='contraseña restablecida en',
                    ),
                ),
                (
                    'password_reset_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='contraseña restablecida por',
                    ),
                ),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='access_profile',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'perfil de acceso institucional',
                'verbose_name_plural': 'perfiles de acceso institucional',
            },
        ),
    ]
