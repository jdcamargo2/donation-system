# Gunicorn configuration for SIGEDON production (WSGI).
#
# Canonical command:
#   gunicorn core.wsgi:application --config deploy/gunicorn.conf.py
#
# WSGI is the production contract via core.wsgi:application.
# ASGI (core.asgi:application) may remain available for future use but is not
# the production startup path for this checkpoint.
#
# Workers never run migrations, collectstatic, or role synchronization.
# Those steps belong to the explicit release sequence (see docs/DEPLOYMENT.md).
#
# Database capacity:
#   total potential DB connections ≈ workers × threads
# Keep workers×threads below the runtime PostgreSQL connection allowance,
# leaving capacity for release commands, backup/restore verification,
# administration, Kobo management commands, and monitoring.
# Do not use (2 × CPU) + 1 as an unconditional default.
#
# Graceful shutdown:
#   SIGTERM → Gunicorn stops accepting new work and waits up to graceful_timeout.
#   Platform TimeoutStopSec / stop grace must exceed graceful_timeout.
#   Hard worker timeout kills stuck workers; in-flight DB transactions may roll back.
#
# Environment overrides (non-secret):
#   PORT, GUNICORN_BIND, GUNICORN_WORKERS, GUNICORN_THREADS,
#   GUNICORN_TIMEOUT, GUNICORN_GRACEFUL_TIMEOUT, GUNICORN_KEEPALIVE,
#   GUNICORN_LOG_LEVEL, GUNICORN_ACCESS_LOG, GUNICORN_ERROR_LOG

from __future__ import annotations

import sys
from pathlib import Path

_DEPLOY_DIR = Path(__file__).resolve().parent
if str(_DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_DIR))

import runtime_config as _rt  # noqa: E402

_settings = _rt.load_from_environ()

bind = _settings['bind']
workers = _settings['workers']
threads = _settings['threads']
worker_class = _settings['worker_class']
timeout = _settings['timeout']
graceful_timeout = _settings['graceful_timeout']
keepalive = _settings['keepalive']
loglevel = _settings['loglevel']
accesslog = _settings['accesslog']
errorlog = _settings['errorlog']
capture_output = _settings['capture_output']
daemon = _settings['daemon']
reload = _settings['reload']
preload_app = _settings['preload_app']

# Explicit production defaults: no PID file, no daemon, no auto-reload, no preload.
# preload_app remains False unless proven safe for process-local state.
# Logging contract (OPS-LOGGING-OBSERVABILITY):
#   - Gunicorn accesslog → stdout; errorlog → stderr; capture_output=True
#   - Django owns application/request-error logs with X-Request-ID correlation
#   - django.request is WARNING+ to avoid duplicating ordinary access lines
#   - Generated request IDs appear in Django logs and response headers;
#     inbound trusted-proxy IDs may appear in both layers when valid
pidfile = None
