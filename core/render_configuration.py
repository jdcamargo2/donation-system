"""Render native-Python configuration verification (network-free).

PRE: Django settings already loaded for the process under test.
POST: returns structured findings without printing secret values. Suitable for
      ``manage.py verify_render_configuration`` and offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.urls import NoReverseMatch, reverse

from core.private_storage import (
    PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT,
    PRIVATE_FILE_DELIVERY_STREAM,
    PRIVATE_STORAGE_FILESYSTEM,
    PRIVATE_STORAGE_R2,
)

WHITENOISE_MANIFEST_BACKEND = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)

KNOWN_DEVELOPMENT_HOSTS = frozenset(
    {
        'localhost',
        '127.0.0.1',
        '0.0.0.0',
        '::1',
        'testserver',
    }
)

FORBIDDEN_PUBLIC_R2_ENV = (
    'R2_PUBLIC_URL',
    'AWS_S3_CUSTOM_DOMAIN',
    'AWS_S3_URL_PROTOCOL',
)

# Variables consumed by production runtime that the Render environment registry
# must document. Names must match settings / deploy scripts exactly.
RENDER_DOCUMENTED_VARIABLES = frozenset(
    {
        'DJANGO_DEBUG',
        'DJANGO_SECRET_KEY',
        'ALLOWED_HOSTS',
        'CSRF_TRUSTED_ORIGINS',
        'DATABASE_ENGINE',
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_HOST',
        'POSTGRES_PORT',
        'DATABASE_CONN_MAX_AGE',
        'POSTGRES_MIGRATOR_USER',
        'POSTGRES_MIGRATOR_PASSWORD',
        'SECURE_SSL_REDIRECT',
        'SECURE_HSTS_SECONDS',
        'SECURE_HSTS_INCLUDE_SUBDOMAINS',
        'SECURE_HSTS_PRELOAD',
        'SECURE_PROXY_SSL_HEADER_ENABLED',
        'SIGEDON_PRIVATE_STORAGE',
        'SIGEDON_PRIVATE_FILE_DELIVERY',
        'SIGEDON_MEDIA_ROOT',
        'R2_ACCOUNT_ID',
        'R2_ACCESS_KEY_ID',
        'R2_SECRET_ACCESS_KEY',
        'R2_BUCKET_NAME',
        'R2_ENDPOINT_URL',
        'R2_ALLOW_CUSTOM_ENDPOINT',
        'R2_REGION_NAME',
        'R2_SIGNED_URL_EXPIRY_SECONDS',
        'R2_ADDRESSING_STYLE',
        'KOBO_ENABLED',
        'KOBO_BASE_URL',
        'KOBO_API_TOKEN',
        'KOBO_WEBHOOK_USERNAME',
        'KOBO_WEBHOOK_SECRET',
        'KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER',
        'KOBO_HTTP_CONNECT_TIMEOUT',
        'KOBO_HTTP_READ_TIMEOUT',
        'KOBO_HTTP_MAX_ATTEMPTS',
        'KOBO_HTTP_RETRY_BASE_DELAY',
        'KOBO_HTTP_RETRY_MAX_DELAY',
        'KOBO_HTTP_RETRY_AFTER_MAX_DELAY',
        'KOBO_HTTP_MAX_PAGES',
        'KOBO_SYNC_OVERLAP_SECONDS',
        'KOBO_SYNC_LEASE_SECONDS',
        'KOBO_MAX_ATTACHMENT_BYTES',
        'KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS',
        'KOBO_WEBHOOK_MAX_BYTES',
        'SIGEDON_READINESS_MIGRATION_CACHE_SECONDS',
        'PORT',
        'GUNICORN_BIND',
        'GUNICORN_WORKERS',
        'GUNICORN_THREADS',
        'GUNICORN_TIMEOUT',
        'GUNICORN_GRACEFUL_TIMEOUT',
        'GUNICORN_KEEPALIVE',
        'GUNICORN_LOG_LEVEL',
        'GUNICORN_ACCESS_LOG',
        'GUNICORN_ERROR_LOG',
        'DJANGO_LOG_LEVEL',
        'SIGEDON_LOG_LEVEL',
        'KOBO_LOG_LEVEL',
        'SIGEDON_RENDER_INSTALL_DEPS',
        'SIGEDON_BUILD_MEDIA_ROOT',
        'PYTHON_BIN',
        'SIGEDON_PREFLIGHT_SHOW_MIGRATE_PLAN',
    }
)

OBSOLETE_GENERIC_ALIASES = frozenset(
    {
        'DATABASE_URL',
        'SECRET_KEY',
        'DEBUG',
        'WEB_CONCURRENCY',
        'DJANGO_ALLOWED_HOSTS',
        'REDIS_URL',
    }
)


@dataclass(frozen=True)
class RenderConfigFinding:
    """One configuration guarantee result (never embeds secret values)."""

    code: str
    message: str
    ok: bool


def _positive_int(raw: str | None, *, name: str) -> RenderConfigFinding | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return RenderConfigFinding(
            code='gunicorn_invalid',
            message=f'{name} must be a positive integer.',
            ok=False,
        )
    if value < 1:
        return RenderConfigFinding(
            code='gunicorn_invalid',
            message=f'{name} must be >= 1.',
            ok=False,
        )
    return None


def _https_origins(origins: Iterable[str]) -> list[str]:
    bad: list[str] = []
    for origin in origins:
        parsed = urlparse(origin)
        if parsed.scheme != 'https' or not parsed.netloc:
            bad.append(origin)
    return bad


def verify_render_configuration(
    *,
    environ: dict[str, str] | None = None,
    allow_development_hosts: bool = False,
) -> list[RenderConfigFinding]:
    """
    PRE: settings reflect the candidate Render runtime; environ defaults to
         process environment for Gunicorn/Kobo structural checks.
    POST: returns findings; all ok=True means the Render runtime contract passes.
          Performs no network I/O and never returns secret values.
    """
    import os

    env = environ if environ is not None else os.environ
    findings: list[RenderConfigFinding] = []

    if settings.DEBUG:
        findings.append(
            RenderConfigFinding(
                code='debug_enabled',
                message='DJANGO_DEBUG must be False for Render runtime.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='debug_enabled',
                message='DJANGO_DEBUG is False.',
                ok=True,
            )
        )

    engine = (getattr(settings, 'DATABASE_ENGINE', '') or '').strip().lower()
    if engine != 'postgresql':
        findings.append(
            RenderConfigFinding(
                code='database_engine',
                message='DATABASE_ENGINE must be postgresql on Render.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='database_engine',
                message='DATABASE_ENGINE is postgresql.',
                ok=True,
            )
        )

    hosts = list(getattr(settings, 'ALLOWED_HOSTS', []) or [])
    if not hosts:
        findings.append(
            RenderConfigFinding(
                code='allowed_hosts',
                message='ALLOWED_HOSTS must be non-empty.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='allowed_hosts',
                message='ALLOWED_HOSTS is present.',
                ok=True,
            )
        )

    if not allow_development_hosts:
        bad_hosts = [
            host for host in hosts if host.lower() in KNOWN_DEVELOPMENT_HOSTS
        ]
        if bad_hosts:
            findings.append(
                RenderConfigFinding(
                    code='development_hosts',
                    message=(
                        'ALLOWED_HOSTS includes development hosts; remove them '
                        'for Render runtime or pass an intentional override.'
                    ),
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='development_hosts',
                    message='ALLOWED_HOSTS has no known development hosts.',
                    ok=True,
                )
            )

    origins = list(getattr(settings, 'CSRF_TRUSTED_ORIGINS', []) or [])
    if not origins:
        findings.append(
            RenderConfigFinding(
                code='csrf_origins',
                message='CSRF_TRUSTED_ORIGINS must be non-empty for Render HTTPS.',
                ok=False,
            )
        )
    else:
        bad_origins = _https_origins(origins)
        if bad_origins:
            findings.append(
                RenderConfigFinding(
                    code='csrf_origins',
                    message='CSRF_TRUSTED_ORIGINS must be HTTPS-only.',
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='csrf_origins',
                    message='CSRF_TRUSTED_ORIGINS are HTTPS-only.',
                    ok=True,
                )
            )

    if not getattr(settings, 'SESSION_COOKIE_SECURE', False):
        findings.append(
            RenderConfigFinding(
                code='session_cookie_secure',
                message='SESSION_COOKIE_SECURE must be True.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='session_cookie_secure',
                message='SESSION_COOKIE_SECURE is True.',
                ok=True,
            )
        )

    if not getattr(settings, 'CSRF_COOKIE_SECURE', False):
        findings.append(
            RenderConfigFinding(
                code='csrf_cookie_secure',
                message='CSRF_COOKIE_SECURE must be True.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='csrf_cookie_secure',
                message='CSRF_COOKIE_SECURE is True.',
                ok=True,
            )
        )

    proxy_header = getattr(settings, 'SECURE_PROXY_SSL_HEADER', None)
    if proxy_header != ('HTTP_X_FORWARDED_PROTO', 'https'):
        findings.append(
            RenderConfigFinding(
                code='proxy_ssl_header',
                message=(
                    'SECURE_PROXY_SSL_HEADER_ENABLED must be True so '
                    'SECURE_PROXY_SSL_HEADER trusts X-Forwarded-Proto=https.'
                ),
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='proxy_ssl_header',
                message='SECURE_PROXY_SSL_HEADER is configured.',
                ok=True,
            )
        )

    storages = getattr(settings, 'STORAGES', {}) or {}
    static_backend = (storages.get('staticfiles') or {}).get('BACKEND', '')
    if static_backend != WHITENOISE_MANIFEST_BACKEND:
        findings.append(
            RenderConfigFinding(
                code='whitenoise',
                message=(
                    'staticfiles backend must be '
                    'whitenoise.storage.CompressedManifestStaticFilesStorage.'
                ),
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='whitenoise',
                message='WhiteNoise CompressedManifestStaticFilesStorage active.',
                ok=True,
            )
        )

    mode = getattr(settings, 'SIGEDON_PRIVATE_STORAGE', PRIVATE_STORAGE_FILESYSTEM)
    if mode == PRIVATE_STORAGE_FILESYSTEM:
        findings.append(
            RenderConfigFinding(
                code='private_storage_mode',
                message=(
                    'SIGEDON_PRIVATE_STORAGE=filesystem is not an accepted final '
                    'Render runtime mode; use r2 after probe and acceptance.'
                ),
                ok=False,
            )
        )
    elif mode == PRIVATE_STORAGE_R2:
        findings.append(
            RenderConfigFinding(
                code='private_storage_mode',
                message='SIGEDON_PRIVATE_STORAGE=r2.',
                ok=True,
            )
        )
        r2 = getattr(settings, 'SIGEDON_R2_CONFIG', None)
        if r2 is None:
            findings.append(
                RenderConfigFinding(
                    code='r2_config',
                    message='R2 configuration missing for r2 mode.',
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='r2_config',
                    message='R2 configuration present (structural).',
                    ok=True,
                )
            )
        default_options = (storages.get('default') or {}).get('OPTIONS') or {}
        if default_options.get('default_acl') in (
            'public-read',
            'public-read-write',
        ):
            findings.append(
                RenderConfigFinding(
                    code='r2_public_acl',
                    message='R2 default_acl must not be public.',
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='r2_public_acl',
                    message='R2 default_acl is not public.',
                    ok=True,
                )
            )
        if default_options.get('querystring_auth') is False:
            findings.append(
                RenderConfigFinding(
                    code='r2_querystring_auth',
                    message='R2 querystring_auth must remain True.',
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='r2_querystring_auth',
                    message='R2 querystring_auth is enabled.',
                    ok=True,
                )
            )
        if default_options.get('custom_domain'):
            findings.append(
                RenderConfigFinding(
                    code='r2_custom_domain',
                    message='R2 custom_domain is forbidden for private media.',
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='r2_custom_domain',
                    message='R2 custom_domain is unset.',
                    ok=True,
                )
            )
        # Canonical Render uses Cloudflare R2 endpoint only.
        if r2 is not None and getattr(r2, 'endpoint_is_custom', False):
            findings.append(
                RenderConfigFinding(
                    code='r2_custom_endpoint',
                    message=(
                        'R2_ALLOW_CUSTOM_ENDPOINT enables a nonstandard '
                        'S3-compatible endpoint; canonical Render requires '
                        'Cloudflare R2 (<account-id>.r2.cloudflarestorage.com).'
                    ),
                    ok=False,
                )
            )
        elif r2 is not None and getattr(r2, 'allow_custom_endpoint', False):
            findings.append(
                RenderConfigFinding(
                    code='r2_custom_endpoint',
                    message=(
                        'R2_ALLOW_CUSTOM_ENDPOINT is True; disable for '
                        'canonical Cloudflare R2 Render deployments.'
                    ),
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='r2_custom_endpoint',
                    message='R2 endpoint policy is Cloudflare-strict.',
                    ok=True,
                )
            )
    else:
        findings.append(
            RenderConfigFinding(
                code='private_storage_mode',
                message=f'Unknown SIGEDON_PRIVATE_STORAGE={mode!r}.',
                ok=False,
            )
        )

    delivery = getattr(
        settings, 'SIGEDON_PRIVATE_FILE_DELIVERY', PRIVATE_FILE_DELIVERY_STREAM
    )
    if delivery not in (
        PRIVATE_FILE_DELIVERY_STREAM,
        PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT,
    ):
        findings.append(
            RenderConfigFinding(
                code='private_file_delivery',
                message='SIGEDON_PRIVATE_FILE_DELIVERY must be stream or signed_redirect.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='private_file_delivery',
                message=(
                    'SIGEDON_PRIVATE_FILE_DELIVERY is valid '
                    '(inline previews always stream for CSP control).'
                ),
                ok=True,
            )
        )

    for forbidden in FORBIDDEN_PUBLIC_R2_ENV:
        if (env.get(forbidden) or '').strip():
            findings.append(
                RenderConfigFinding(
                    code='r2_public_env',
                    message=f'{forbidden} must not be set.',
                    ok=False,
                )
            )

    kobo_enabled = bool(getattr(settings, 'KOBO_ENABLED', False))
    if kobo_enabled:
        missing_kobo: list[str] = []
        for name in (
            'KOBO_BASE_URL',
            'KOBO_API_TOKEN',
            'KOBO_WEBHOOK_USERNAME',
            'KOBO_WEBHOOK_SECRET',
        ):
            if not (getattr(settings, name, None) or '').strip():
                missing_kobo.append(name)
        if missing_kobo:
            findings.append(
                RenderConfigFinding(
                    code='kobo_required',
                    message=(
                        'KOBO_ENABLED requires: ' + ', '.join(missing_kobo) + '.'
                    ),
                    ok=False,
                )
            )
        else:
            findings.append(
                RenderConfigFinding(
                    code='kobo_required',
                    message='Kobo required values are present.',
                    ok=True,
                )
            )
    else:
        findings.append(
            RenderConfigFinding(
                code='kobo_required',
                message='KOBO_ENABLED is False; Kobo secrets not required.',
                ok=True,
            )
        )

    if bool(getattr(settings, 'KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER', False)):
        findings.append(
            RenderConfigFinding(
                code='kobo_legacy_webhook_header',
                message=(
                    'KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER must be False for '
                    'normal production; Basic auth is canonical.'
                ),
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='kobo_legacy_webhook_header',
                message='Legacy Kobo webhook secret header is disabled.',
                ok=True,
            )
        )

    cache_seconds = getattr(
        settings, 'SIGEDON_READINESS_MIGRATION_CACHE_SECONDS', 15
    )
    try:
        cache_value = int(cache_seconds)
    except (TypeError, ValueError):
        cache_value = -1
    if cache_value < 0 or cache_value > 300:
        findings.append(
            RenderConfigFinding(
                code='readiness_migration_cache',
                message=(
                    'SIGEDON_READINESS_MIGRATION_CACHE_SECONDS must be 0–300.'
                ),
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='readiness_migration_cache',
                message='SIGEDON_READINESS_MIGRATION_CACHE_SECONDS is valid.',
                ok=True,
            )
        )

    for name in (
        'GUNICORN_WORKERS',
        'GUNICORN_THREADS',
        'GUNICORN_TIMEOUT',
        'GUNICORN_GRACEFUL_TIMEOUT',
        'GUNICORN_KEEPALIVE',
    ):
        finding = _positive_int(env.get(name), name=name)
        if finding is not None:
            findings.append(finding)

    try:
        reverse('readyz')
        findings.append(
            RenderConfigFinding(
                code='readyz_route',
                message='readyz route is registered.',
                ok=True,
            )
        )
    except NoReverseMatch:
        findings.append(
            RenderConfigFinding(
                code='readyz_route',
                message='readyz route is missing.',
                ok=False,
            )
        )

    try:
        reverse('healthz')
        findings.append(
            RenderConfigFinding(
                code='healthz_route',
                message='healthz route is registered.',
                ok=True,
            )
        )
    except NoReverseMatch:
        findings.append(
            RenderConfigFinding(
                code='healthz_route',
                message='healthz route is missing.',
                ok=False,
            )
        )

    # HSTS preload must stay off for initial launch.
    if getattr(settings, 'SECURE_HSTS_PRELOAD', False):
        findings.append(
            RenderConfigFinding(
                code='hsts_preload',
                message='SECURE_HSTS_PRELOAD must remain False for initial launch.',
                ok=False,
            )
        )
    else:
        findings.append(
            RenderConfigFinding(
                code='hsts_preload',
                message='SECURE_HSTS_PRELOAD is False.',
                ok=True,
            )
        )

    return findings


def configuration_is_healthy(findings: Iterable[RenderConfigFinding]) -> bool:
    """
    PRE: findings produced by verify_render_configuration.
    POST: True iff every finding reports ok=True.
    """
    return all(item.ok for item in findings)
