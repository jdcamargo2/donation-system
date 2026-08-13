from django.db import migrations


EXPENSE_REQUEST_NAMESPACE = 'expense_request'
EXPENSE_REQUEST_PREFIX = 'SGS'


def seed_expense_request_sequence(apps, schema_editor):
    """
    PRE: OperationalCodeSequence exists and expense_request namespace is canonical.
    POST: ensures one SGS sequence row starting at next_value=1 when absent.
    """
    OperationalCodeSequence = apps.get_model('operations', 'OperationalCodeSequence')
    OperationalCodeSequence.objects.get_or_create(
        namespace=EXPENSE_REQUEST_NAMESPACE,
        defaults={
            'prefix': EXPENSE_REQUEST_PREFIX,
            'next_value': 1,
        },
    )


def remove_expense_request_sequence(apps, schema_editor):
    """
    PRE: reverse migration targets only the expense_request / SGS sequence row.
    POST: deletes that matching sequence row when present; leaves other sequences intact.
    """
    OperationalCodeSequence = apps.get_model('operations', 'OperationalCodeSequence')
    OperationalCodeSequence.objects.filter(
        namespace=EXPENSE_REQUEST_NAMESPACE,
        prefix=EXPENSE_REQUEST_PREFIX,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0027_expense_request_constraints'),
    ]

    operations = [
        migrations.RunPython(
            seed_expense_request_sequence,
            remove_expense_request_sequence,
        ),
    ]
