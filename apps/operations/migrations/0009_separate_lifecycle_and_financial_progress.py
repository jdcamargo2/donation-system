from django.db import migrations, models


def normalize_lifecycle_statuses(apps, schema_editor):
    """
    PRE: legacy Donation and FundAllocation statuses use the pre-2.1 choices.
    POST: rejects closed donations and maps every legacy non-cycle status explicitly.
    """
    Donation = apps.get_model('operations', 'Donation')
    FundAllocation = apps.get_model('operations', 'FundAllocation')

    closed_donations = Donation.objects.filter(status='closed')
    if closed_donations.exists():
        raise RuntimeError(
            'Fase 2.1 abortada: existen Donation con status="closed"; '
            'requieren revisión y mapeo manual antes de migrar.'
        )

    Donation.objects.filter(status='committed').update(status='registered')
    Donation.objects.filter(
        status__in=('partially_allocated', 'fully_allocated')
    ).update(status='received')
    FundAllocation.objects.filter(
        status__in=('created', 'partially_executed', 'fully_executed')
    ).update(status='active')
    FundAllocation.objects.filter(status='closed').update(status='finished')


def restore_closed_allocation_status(apps, schema_editor):
    """
    PRE: FundAllocation may contain the Fase 2.1 FINISHED lifecycle value.
    POST: maps FINISHED back to the only equivalent legacy lifecycle value CLOSED.
    """
    FundAllocation = apps.get_model('operations', 'FundAllocation')
    FundAllocation.objects.filter(status='finished').update(status='closed')


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0008_terminal_action_metadata'),
    ]

    operations = [
        migrations.RunPython(
            normalize_lifecycle_statuses,
            restore_closed_allocation_status,
        ),
        migrations.AlterField(
            model_name='donation',
            name='status',
            field=models.CharField(
                choices=[
                    ('registered', 'Registrada'),
                    ('received', 'Recibida'),
                    ('annulled', 'Anulada'),
                ],
                default='registered',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='fundallocation',
            name='status',
            field=models.CharField(
                choices=[
                    ('active', 'Activa'),
                    ('finished', 'Finalizada'),
                    ('annulled', 'Anulada'),
                ],
                default='active',
                max_length=30,
            ),
        ),
    ]
