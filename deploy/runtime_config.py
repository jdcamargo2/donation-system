"""
Pure helpers for SIGEDON Gunicorn runtime configuration.

PRE: callers pass environment-derived strings (or None) without logging secrets.
POST: returns validated bind/worker/timeout settings or raises RuntimeConfigError.
"""

from __future__ import annotations

import os


class RuntimeConfigError(ValueError):
    """Invalid Gunicorn runtime environment value (message never includes secrets)."""


DEFAULT_PORT = 8000
DEFAULT_WORKERS = 2
DEFAULT_THREADS = 1
DEFAULT_TIMEOUT = 60
DEFAULT_GRACEFUL_TIMEOUT = 30
DEFAULT_KEEPALIVE = 5
DEFAULT_LOG_LEVEL = 'info'
WORKER_CLASS = 'sync'


def parse_positive_int(name: str, raw: str | None, default: int) -> int:
    """
    PRE: name identifies the variable; default is a positive integer.
    POST: returns a positive integer, or raises RuntimeConfigError without echoing raw.
    """
    if default < 1:
        raise RuntimeConfigError(f'{name} default must be a positive integer.')
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise RuntimeConfigError(f'{name} must be a positive integer.') from exc
    if value < 1:
        raise RuntimeConfigError(f'{name} must be a positive integer.')
    return value


def parse_non_negative_int(name: str, raw: str | None, default: int) -> int:
    """
    PRE: name identifies the variable; default is a non-negative integer.
    POST: returns a non-negative integer, or raises RuntimeConfigError without echoing raw.
    """
    if default < 0:
        raise RuntimeConfigError(f'{name} default must be a non-negative integer.')
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise RuntimeConfigError(f'{name} must be a non-negative integer.') from exc
    if value < 0:
        raise RuntimeConfigError(f'{name} must be a non-negative integer.')
    return value


def resolve_bind(
    *,
    bind_raw: str | None = None,
    port_raw: str | None = None,
    default_port: int = DEFAULT_PORT,
) -> str:
    """
    PRE: optional GUNICORN_BIND and PORT values; default_port is positive.
    POST: returns an explicit bind address. GUNICORN_BIND wins when set.
    """
    if bind_raw is not None and str(bind_raw).strip():
        return str(bind_raw).strip()
    port = parse_positive_int('PORT', port_raw, default_port)
    return f'0.0.0.0:{port}'


def resolve_log_level(raw: str | None, default: str = DEFAULT_LOG_LEVEL) -> str:
    """
    PRE: optional GUNICORN_LOG_LEVEL string.
    POST: returns a non-empty log level token without echoing secrets.
    """
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower()


def load_from_environ(environ: dict[str, str] | None = None) -> dict:
    """
    PRE: environ is os.environ-like mapping (defaults to os.environ).
    POST: returns a dict of Gunicorn settings suitable for assignment on a config module.
    """
    env = os.environ if environ is None else environ
    workers = parse_positive_int(
        'GUNICORN_WORKERS',
        env.get('GUNICORN_WORKERS'),
        DEFAULT_WORKERS,
    )
    threads = parse_positive_int(
        'GUNICORN_THREADS',
        env.get('GUNICORN_THREADS'),
        DEFAULT_THREADS,
    )
    timeout = parse_positive_int(
        'GUNICORN_TIMEOUT',
        env.get('GUNICORN_TIMEOUT'),
        DEFAULT_TIMEOUT,
    )
    graceful_timeout = parse_positive_int(
        'GUNICORN_GRACEFUL_TIMEOUT',
        env.get('GUNICORN_GRACEFUL_TIMEOUT'),
        DEFAULT_GRACEFUL_TIMEOUT,
    )
    keepalive = parse_non_negative_int(
        'GUNICORN_KEEPALIVE',
        env.get('GUNICORN_KEEPALIVE'),
        DEFAULT_KEEPALIVE,
    )
    return {
        'bind': resolve_bind(
            bind_raw=env.get('GUNICORN_BIND'),
            port_raw=env.get('PORT'),
        ),
        'workers': workers,
        'threads': threads,
        'worker_class': WORKER_CLASS,
        'timeout': timeout,
        'graceful_timeout': graceful_timeout,
        'keepalive': keepalive,
        'loglevel': resolve_log_level(env.get('GUNICORN_LOG_LEVEL')),
        'accesslog': env.get('GUNICORN_ACCESS_LOG', '-').strip() or '-',
        'errorlog': env.get('GUNICORN_ERROR_LOG', '-').strip() or '-',
        'capture_output': True,
        'daemon': False,
        'reload': False,
        'preload_app': False,
        # Potential DB connections ≈ workers × threads (plus CONN_MAX_AGE reuse).
        # Keep this product below the runtime PostgreSQL connection allowance.
        'potential_db_connections': workers * threads,
    }
