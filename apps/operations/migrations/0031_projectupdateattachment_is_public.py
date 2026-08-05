from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0030_expense_request_event_expense_protect'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectupdateattachment',
            name='is_public',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Solo los adjuntos marcados explícitamente como públicos pueden '
                    'aparecer en el portal; el avance y el proyecto padre también deben '
                    'ser públicos.'
                ),
                verbose_name='público en el portal de transparencia',
            ),
        ),
    ]
