from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operations', '0014_projectupdatereview'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectUpdateReviewDecision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('outcome', models.CharField(choices=[('conforming', 'Conforme'), ('observed', 'Observado')], max_length=20, verbose_name='Resultado')),
                ('rationale', models.TextField(verbose_name='Fundamento de la decisión')),
                ('decided_at', models.DateTimeField(auto_now_add=True, editable=False, verbose_name='Fecha de decisión')),
                ('decided_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_update_review_decisions', to=settings.AUTH_USER_MODEL, verbose_name='Registrado por')),
                ('review', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='decision', to='operations.projectupdatereview', verbose_name='Revisión del Comité')),
            ],
            options={
                'verbose_name': 'resultado de revisión del Comité',
                'verbose_name_plural': 'resultados de revisiones del Comité',
                'ordering': ['-decided_at'],
            },
        ),
    ]
