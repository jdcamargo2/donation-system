from django.db import migrations, models
import django.core.validators
import django.utils.timezone


def map_project_update_lifecycle(apps, schema_editor):
    # PRE: ProjectUpdate conserva los estados y metadatos del flujo de revisión anterior.
    # POST: APPROVED queda PUBLISHED; los demás estados quedan DRAFT con fecha y progreso inicial.
    ProjectUpdate = apps.get_model('operations', 'ProjectUpdate')
    for project_update in ProjectUpdate.objects.all().iterator():
        project_update.status = 'published' if project_update.status == 'approved' else 'draft'
        project_update.update_date = project_update.created_at.date()
        project_update.progress_percentage = 0
        project_update.save(
            update_fields=('status', 'update_date', 'progress_percentage')
        )


class Migration(migrations.Migration):
    dependencies = [
        ('operations', '0010_simplify_expense_lifecycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectupdate',
            name='update_date',
            field=models.DateField(blank=True, null=True, verbose_name='fecha del avance'),
        ),
        migrations.AddField(
            model_name='projectupdate',
            name='progress_percentage',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(map_project_update_lifecycle, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='projectupdate',
            name='update_date',
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name='fecha del avance'),
        ),
        migrations.AlterField(
            model_name='projectupdate',
            name='progress_percentage',
            field=models.PositiveSmallIntegerField(
                default=0,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
                verbose_name='porcentaje de progreso',
            ),
        ),
        migrations.AlterField(
            model_name='projectupdate',
            name='status',
            field=models.CharField(
                choices=[('draft', 'Borrador'), ('published', 'Publicado')],
                default='draft',
                max_length=30,
            ),
        ),
        migrations.RemoveField(model_name='projectupdate', name='reviewed_by'),
        migrations.RemoveField(model_name='projectupdate', name='reviewed_at'),
        migrations.RemoveField(model_name='projectupdate', name='review_notes'),
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('created', 'Creada'),
                    ('updated', 'Actualizada'),
                    ('validated', 'Validada'),
                    ('rejected', 'Rechazada'),
                    ('annulled', 'Anulada'),
                    ('assigned', 'Asignada'),
                    ('executed', 'Ejecutada'),
                    ('closed', 'Cerrada'),
                    ('expense_cancelled', 'Gasto anulado'),
                    ('published', 'Publicada'),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name='projectupdate',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    progress_percentage__gte=0,
                    progress_percentage__lte=100,
                ),
                name='project_update_progress_between_0_and_100',
            ),
        ),
    ]
