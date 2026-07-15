"""
Compatibility shim between the operations_auditlog append-only PostgreSQL
trigger (see migration 0018) and Django's `flush` management command.

PROBLEM: `TransactionTestCase._fixture_teardown()` calls `flush`, which issues
one combined `TRUNCATE table_a, table_b, ..., operations_auditlog, ...;`
statement covering every Django-managed table. PostgreSQL executes that
statement atomically, so the append-only trigger firing for
`operations_auditlog` aborts the whole statement and breaks test isolation
for every TransactionTestCase in the project, not just AuditLog's own tests.

FIX: `flush` is an explicit, whole-database wipe never issued by application
runtime code; it is the kind of administrative operation the append-only
design already expects a superuser to be able to perform deliberately (see
docs/SECURITY.md). We scope a PostgreSQL-only bypass to exactly that single
administrative transaction using `SET LOCAL session_replication_role =
'replica'`, which disables ORIGIN-mode triggers (ours included) only for the
current transaction and only on PostgreSQL. Ordinary application mutations
never call this code path, so runtime protection is unaffected.
"""

from django.db.backends.base.operations import BaseDatabaseOperations

_ORIGINAL_EXECUTE_SQL_FLUSH = BaseDatabaseOperations.execute_sql_flush
_BYPASS_MARKER = "SET LOCAL session_replication_role = replica;"


def _execute_sql_flush_with_auditlog_bypass(self, sql_list):
    """
    PRE: sql_list is the SQL django's flush command is about to execute.
    POST: on PostgreSQL, prepends a transaction-scoped trigger bypass so the
    combined TRUNCATE succeeds; delegates unchanged on every other backend.
    """
    if sql_list and self.connection.vendor == "postgresql":
        sql_list = [_BYPASS_MARKER, *sql_list]
    return _ORIGINAL_EXECUTE_SQL_FLUSH(self, sql_list)


def apply_flush_trigger_bypass():
    """
    PRE: called at most once, during app startup.
    POST: BaseDatabaseOperations.execute_sql_flush is patched idempotently.
    """
    if (
        BaseDatabaseOperations.execute_sql_flush
        is _execute_sql_flush_with_auditlog_bypass
    ):
        return
    BaseDatabaseOperations.execute_sql_flush = _execute_sql_flush_with_auditlog_bypass
