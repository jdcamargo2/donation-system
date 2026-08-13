# Generated manually to mirror 0018_auditlog_append_only_trigger.

from django.db import migrations


EXPENSEREQUESTEVENT_TABLE = 'operations_expenserequestevent'
EXPENSEREQUESTEVENT_TRIGGER_FUNCTION = 'operations_expenserequestevent_reject_mutation'
EXPENSEREQUESTEVENT_TRIGGER_NAME = 'operations_expenserequestevent_append_only'

# PRE: statement fires BEFORE UPDATE/DELETE/TRUNCATE on operations_expenserequestevent.
# POST: raises a safe, generic error without leaking row contents; INSERT and
# SELECT are never intercepted because this trigger does not fire for them.
INSTALL_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {EXPENSEREQUESTEVENT_TRIGGER_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'operations_expenserequestevent es append-only: UPDATE, DELETE y TRUNCATE estan prohibidos.'
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

DROP_TRIGGER_SQL = (
    f'DROP TRIGGER IF EXISTS {EXPENSEREQUESTEVENT_TRIGGER_NAME} '
    f'ON {EXPENSEREQUESTEVENT_TABLE};'
)

# FOR EACH STATEMENT is required because TRUNCATE has no per-row semantics;
# combining UPDATE/DELETE/TRUNCATE in one statement-level trigger keeps a
# single, explicit defense point for every rejected mutation.
INSTALL_TRIGGER_SQL = f"""
CREATE TRIGGER {EXPENSEREQUESTEVENT_TRIGGER_NAME}
BEFORE UPDATE OR DELETE OR TRUNCATE ON {EXPENSEREQUESTEVENT_TABLE}
FOR EACH STATEMENT
EXECUTE FUNCTION {EXPENSEREQUESTEVENT_TRIGGER_FUNCTION}();
"""

DROP_FUNCTION_SQL = f'DROP FUNCTION IF EXISTS {EXPENSEREQUESTEVENT_TRIGGER_FUNCTION}();'


def install_append_only_trigger(apps, schema_editor):
    """
    PRE: operations_expenserequestevent exists in the target database.
    POST: on PostgreSQL, installs (idempotently) the function and trigger that
    reject UPDATE/DELETE/TRUNCATE. On other backends this is a deliberate
    no-op so `migrate` keeps working for SQLite local development.
    """
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(INSTALL_FUNCTION_SQL)
    schema_editor.execute(DROP_TRIGGER_SQL)
    schema_editor.execute(INSTALL_TRIGGER_SQL)


def remove_append_only_trigger(apps, schema_editor):
    """
    PRE: install_append_only_trigger may or may not have run on this backend.
    POST: on PostgreSQL, drops the trigger before the function it depends on.
    No-op on other backends.
    """
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(DROP_TRIGGER_SQL)
    schema_editor.execute(DROP_FUNCTION_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('operations', '0028_expense_request_code_sequence'),
    ]

    operations = [
        migrations.RunPython(
            install_append_only_trigger,
            remove_append_only_trigger,
            elidable=False,
        ),
    ]
