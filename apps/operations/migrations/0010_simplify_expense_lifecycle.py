import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F


def simplify_expense_statuses(apps, schema_editor):
    """
    PRE: Expense rows use the legacy review workflow and terminal fields exist.
    POST: preserves execution semantics while mapping every row to REGISTERED or ANNULLED.
    """
    Expense = apps.get_model('operations', 'Expense')
    for legacy_status in ('rejected', 'cancelled'):
        Expense.objects.filter(status=legacy_status).update(
            status='annulled',
            terminal_reason=f'Migrado desde estado {legacy_status.upper()}',
            terminal_at=F('validated_at'),
            terminal_by_id=F('validated_by_id'),
        )
    Expense.objects.filter(status__in=('in_review', 'validated')).update(
        status='registered'
    )


def restore_explicit_legacy_statuses(apps, schema_editor):
    """
    PRE: migrated annulments may carry one of the stable migration reasons.
    POST: restores only statuses whose legacy origin remains explicitly identifiable.
    """
    Expense = apps.get_model('operations', 'Expense')
    Expense.objects.filter(
        status='annulled', terminal_reason='Migrado desde estado REJECTED'
    ).update(status='rejected')
    Expense.objects.filter(
        status='annulled', terminal_reason='Migrado desde estado CANCELLED'
    ).update(status='cancelled')


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0009_separate_lifecycle_and_financial_progress'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='terminal_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='expense',
            name='terminal_by',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='terminal_expenses',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='expense',
            name='terminal_reason',
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.RunPython(simplify_expense_statuses, restore_explicit_legacy_statuses),
        migrations.RemoveField(model_name='expense', name='validated_at'),
        migrations.RemoveField(model_name='expense', name='validated_by'),
        migrations.AlterField(
            model_name='expense',
            name='status',
            field=models.CharField(
                choices=[('registered', 'Registrado'), ('annulled', 'Anulado')],
                default='registered',
                max_length=20,
            ),
        ),
    ]
