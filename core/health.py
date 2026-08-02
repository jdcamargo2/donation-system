"""
Liveness and readiness probes for SIGEDON production runtime.

PRE: Django is loaded; probes must remain independent of business apps,
     Kobo, cache, media storage, and authentication.
POST: /healthz/ proves process responsiveness; /readyz/ proves default-DB
      connectivity and that required migrations are applied. Responses are
      minimal, non-cacheable, and never expose internal configuration.
"""

from __future__ import annotations

import logging
import threading
import time

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError, InterfaceError, OperationalError
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

logger = logging.getLogger('sigedon.health')

_DEFAULT_ALIAS = 'default'
_LOG_SUPPRESSION_SECONDS = 30.0
_failure_log_lock = threading.Lock()
_last_failure_log_at = 0.0


def health_response(payload: dict, *, status: int) -> JsonResponse:
    """
    PRE: payload is a minimal public status dict; status is the HTTP code.
    POST: returns a JsonResponse with no-store / nosniff hardening headers.
    """
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def check_database_connection(*, alias: str = _DEFAULT_ALIAS) -> None:
    """
    PRE: alias names a configured Django database (default in production).
    POST: returns after a successful SELECT 1; raises DatabaseError subclasses
          on connectivity failure. Does not mutate transaction state or
          business tables. Does not close healthy persistent connections;
          Django's request_started close_old_connections handles obsolescence.
          On failure outside an atomic block, closes the broken handle so the
          next request can reconnect cleanly.
    """
    connection = connections[alias]
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except (OperationalError, InterfaceError, DatabaseError):
        # Avoid close() inside atomic blocks (e.g. TestCase); it can leave a
        # half-closed wrapper that breaks the surrounding transaction.
        if not connection.in_atomic_block:
            connection.close()
        raise


def check_migrations_applied(*, alias: str = _DEFAULT_ALIAS) -> bool:
    """
    PRE: the default database connection is usable.
    POST: returns True when the migration plan for leaf nodes is empty;
          returns False when pending migrations exist. Never applies
          migrations or mutates django_migrations.
    """
    connection = connections[alias]
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return not plan


def _should_emit_failure_log() -> bool:
    """
    PRE: called under readiness failure paths.
    POST: returns True at most once per suppression window per process.
    """
    global _last_failure_log_at
    now = time.monotonic()
    with _failure_log_lock:
        if now - _last_failure_log_at < _LOG_SUPPRESSION_SECONDS:
            return False
        _last_failure_log_at = now
        return True


def _log_expected_unavailability() -> None:
    if _should_emit_failure_log():
        logger.warning('readiness check failed: database unavailable')


def _log_pending_migrations() -> None:
    if _should_emit_failure_log():
        logger.warning('readiness check failed: pending migrations')


def _log_unexpected_failure() -> None:
    if _should_emit_failure_log():
        logger.exception('readiness check failed: unexpected error')


@require_http_methods(['GET', 'HEAD'])
def healthz(request):
    """
    Liveness probe.

    PRE: Django can dispatch the view (no auth, no DB, no cache, no I/O).
    POST: returns HTTP 200 with {"status": "ok"} and hardening headers.
    """
    return health_response({'status': 'ok'}, status=200)


@require_http_methods(['GET', 'HEAD'])
def readyz(request):
    """
    Readiness probe.

    PRE: none beyond a dispatchable Django process.
    POST: 200 {"status": "ready"} when default DB is reachable and migrations
          are applied; otherwise 503 {"status": "not_ready"} with no internal
          details. Never depends on Kobo, cache, media, or auth.
    """
    try:
        check_database_connection()
        if not check_migrations_applied():
            _log_pending_migrations()
            return health_response({'status': 'not_ready'}, status=503)
    except (OperationalError, InterfaceError, DatabaseError):
        _log_expected_unavailability()
        return health_response({'status': 'not_ready'}, status=503)
    except Exception:
        _log_unexpected_failure()
        return health_response({'status': 'not_ready'}, status=503)

    return health_response({'status': 'ready'}, status=200)
