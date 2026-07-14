from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operations', '0012_project_documents_and_update_attachments'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectupdate',
            name='reported_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reported_project_updates',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Responsable institucional',
            ),
        ),
    ]
