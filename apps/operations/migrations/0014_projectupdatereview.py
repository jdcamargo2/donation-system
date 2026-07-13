from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operations', '0013_projectupdate_reported_by'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectUpdateReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('observations', models.TextField(verbose_name='Observaciones del Comité')),
                ('reviewed_at', models.DateTimeField(auto_now_add=True, editable=False, verbose_name='Fecha de revisión')),
                ('project_update', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='committee_review', to='operations.projectupdate', verbose_name='Avance de proyecto')),
                ('reviewed_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_update_reviews', to=settings.AUTH_USER_MODEL, verbose_name='Revisado por')),
            ],
            options={
                'verbose_name': 'revisión documental de avance',
                'verbose_name_plural': 'revisiones documentales de avances',
                'ordering': ['-reviewed_at'],
            },
        ),
    ]
