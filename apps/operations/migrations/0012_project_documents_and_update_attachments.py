from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_legacy_evidence(apps, schema_editor):
    # PRE: ProjectUpdate conserva el campo evidence y ProjectUpdateAttachment ya existe.
    # POST: cada evidencia existente queda referenciada por un adjunto sin mover el archivo físico.
    ProjectUpdate = apps.get_model('operations', 'ProjectUpdate')
    ProjectUpdateAttachment = apps.get_model('operations', 'ProjectUpdateAttachment')
    for project_update in ProjectUpdate.objects.exclude(evidence='').exclude(evidence__isnull=True).iterator():
        ProjectUpdateAttachment.objects.create(
            project_update_id=project_update.pk,
            file=project_update.evidence.name,
            title='Evidencia migrada',
            uploaded_by_id=project_update.created_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operations', '0011_simplify_project_update_lifecycle'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(choices=[('proposal', 'Propuesta'), ('work_plan', 'Plan de trabajo'), ('action_plan', 'Plan de acción'), ('report', 'Informe'), ('other', 'Otro')], max_length=20)),
                ('title', models.CharField(max_length=200)),
                ('file', models.FileField(upload_to='project_documents/%Y/%m/')),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='operations.project')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_project_documents', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'documento de proyecto', 'verbose_name_plural': 'documentos de proyecto', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ProjectUpdateAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='project_update_attachments/%Y/%m/')),
                ('title', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('project_update', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='operations.projectupdate')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_project_update_attachments', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'adjunto de avance', 'verbose_name_plural': 'adjuntos de avance', 'ordering': ['created_at']},
        ),
        migrations.RunPython(migrate_legacy_evidence, migrations.RunPython.noop),
        migrations.RemoveField(model_name='projectupdate', name='evidence'),
    ]
