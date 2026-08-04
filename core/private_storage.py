"""Private storage mode contract for SIGEDON (filesystem or Cloudflare R2).

PRE: callers pass raw environment values; no network I/O occurs here.
POST: returns validated configuration or raises ImproperlyConfigured without
      embedding secrets in messages. R2 mode never auto-falls back to filesystem.
      Explicit endpoints default to Cloudflare R2 host matching; custom endpoints
      require R2_ALLOW_CUSTOM_ENDPOINT=True (nonstandard).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

PRIVATE_STORAGE_FILESYSTEM = 'filesystem'
PRIVATE_STORAGE_R2 = 'r2'
ALLOWED_PRIVATE_STORAGE_MODES = frozenset(
    {PRIVATE_STORAGE_FILESYSTEM, PRIVATE_STORAGE_R2}
)

PRIVATE_FILE_DELIVERY_STREAM = 'stream'
PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT = 'signed_redirect'
ALLOWED_PRIVATE_FILE_DELIVERY_MODES = frozenset(
    {PRIVATE_FILE_DELIVERY_STREAM, PRIVATE_FILE_DELIVERY_SIGNED_REDIRECT}
)

DEFAULT_SIGNED_URL_EXPIRY_SECONDS = 300
MIN_SIGNED_URL_EXPIRY_SECONDS = 60
MAX_SIGNED_URL_EXPIRY_SECONDS = 900
DEFAULT_R2_REGION_NAME = 'auto'
DEFAULT_R2_ADDRESSING_STYLE = 'path'

_R2_REQUIRED_VARS = (
    'R2_ACCOUNT_ID',
    'R2_ACCESS_KEY_ID',
    'R2_SECRET_ACCESS_KEY',
    'R2_BUCKET_NAME',
)

_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})

# Obvious metadata / link-local metadata hosts rejected without DNS lookups.
_BLOCKED_ENDPOINT_HOSTS = frozenset(
    {
        'localhost',
        'metadata.google.internal',
        'metadata.google.com',
        'instance-data',
    }
)

# S3-compatible bucket naming (DNS-label subset; length 3–63).
_BUCKET_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')
_ACCOUNT_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')

# Unused local path when R2 is the default storage; never a public mount.
R2_UNUSED_MEDIA_DIRNAME = '.sigedon-unused-media-r2'


@dataclass(frozen=True)
class R2StorageConfig:
    """Validated Cloudflare R2 / S3-compatible settings (secrets stay out of logs)."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    endpoint_url: str
    region_name: str
    signed_url_expiry_seconds: int
    addressing_style: str
    allow_custom_endpoint: bool
    endpoint_is_custom: bool


@dataclass(frozen=True)
class PrivateStorageSettings:
    """Resolved private-storage contract used by Django settings and checks."""

    mode: str
    delivery_mode: str
    media_root: Path
    r2: R2StorageConfig | None
    storages_default: dict


def resolve_private_storage_mode(raw: str | None) -> str:
    """
    PRE: raw is the SIGEDON_PRIVATE_STORAGE env value (may be unset).
    POST: returns filesystem|r2; unknown values raise ImproperlyConfigured.
    """
    value = (raw or '').strip().lower() or PRIVATE_STORAGE_FILESYSTEM
    if value not in ALLOWED_PRIVATE_STORAGE_MODES:
        raise ImproperlyConfigured(
            'SIGEDON_PRIVATE_STORAGE debe ser filesystem o r2.'
        )
    return value


def resolve_private_file_delivery_mode(raw: str | None) -> str:
    """
    PRE: raw is SIGEDON_PRIVATE_FILE_DELIVERY (may be unset).
    POST: returns stream|signed_redirect; unknown values raise ImproperlyConfigured.
    """
    value = (raw or '').strip().lower() or PRIVATE_FILE_DELIVERY_STREAM
    if value not in ALLOWED_PRIVATE_FILE_DELIVERY_MODES:
        raise ImproperlyConfigured(
            'SIGEDON_PRIVATE_FILE_DELIVERY debe ser stream o signed_redirect.'
        )
    return value


def resolve_r2_allow_custom_endpoint(raw: str | None) -> bool:
    """
    PRE: raw is R2_ALLOW_CUSTOM_ENDPOINT or empty for default False.
    POST: returns a strict boolean; malformed values fail closed.
    """
    if raw is None or not str(raw).strip():
        return False
    normalized = str(raw).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ImproperlyConfigured(
        'R2_ALLOW_CUSTOM_ENDPOINT debe ser un valor booleano válido.'
    )


def cloudflare_r2_endpoint_for_account(account_id: str) -> str:
    """
    PRE: account_id is a non-empty validated Cloudflare account identifier.
    POST: returns https://<account-id>.r2.cloudflarestorage.com
    """
    aid = (account_id or '').strip()
    if not aid or not _ACCOUNT_ID_RE.match(aid):
        raise ImproperlyConfigured('R2_ACCOUNT_ID tiene un formato inválido.')
    if '/' in aid or ':' in aid or '@' in aid or ' ' in aid:
        raise ImproperlyConfigured('R2_ACCOUNT_ID tiene un formato inválido.')
    return f'https://{aid}.r2.cloudflarestorage.com'


def _endpoint_hostname_blocked(hostname: str) -> bool:
    """
    PRE: hostname is a URL host without credentials (may be a literal IP).
    POST: True when the host is localhost, private/link-local/loopback IP,
          .local, or an obvious cloud metadata endpoint. Performs no DNS.
    """
    host = (hostname or '').strip().lower().rstrip('.')
    if not host:
        return True
    if host in _BLOCKED_ENDPOINT_HOSTS or host.endswith('.local'):
        return True
    if host == '169.254.169.254':
        return True
    # Strip IPv6 brackets if present.
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_endpoint_shape(endpoint_url: str) -> tuple[str, str]:
    """
    PRE: endpoint_url is a candidate HTTPS endpoint string.
    POST: returns (normalized_url_without_trailing_slash, hostname) or raises.
          Rejects credentials, query, fragment, and non-root paths.
    """
    value = (endpoint_url or '').strip()
    if not value:
        raise ImproperlyConfigured('R2_ENDPOINT_URL no puede estar vacío.')
    parsed = urlparse(value)
    if parsed.scheme != 'https':
        raise ImproperlyConfigured('R2_ENDPOINT_URL debe usar HTTPS.')
    if not parsed.netloc:
        raise ImproperlyConfigured('R2_ENDPOINT_URL debe incluir un host válido.')
    if parsed.username is not None or parsed.password is not None:
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no debe incluir usuario ni contraseña.'
        )
    if '@' in parsed.netloc:
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no debe incluir usuario ni contraseña.'
        )
    if parsed.query:
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no debe incluir parámetros de consulta.'
        )
    if parsed.fragment:
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no debe incluir fragmento.'
        )
    path = parsed.path or ''
    if path not in ('', '/'):
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no debe incluir una ruta distinta de /.'
        )
    hostname = parsed.hostname
    if not hostname:
        raise ImproperlyConfigured('R2_ENDPOINT_URL debe incluir un host válido.')
    return value.rstrip('/'), hostname


def validate_r2_endpoint_url(
    endpoint_url: str,
    *,
    account_id: str = '',
    allow_custom: bool = False,
) -> str:
    """
    PRE: endpoint_url is a candidate R2/S3 endpoint; account_id is required for
         strict Cloudflare matching when allow_custom is False.
    POST: returns the stripped URL when policy checks pass. No DNS or network.
    """
    normalized, hostname = _validate_endpoint_shape(endpoint_url)
    aid = (account_id or '').strip()

    if not allow_custom:
        if not aid:
            raise ImproperlyConfigured(
                'R2_ACCOUNT_ID es obligatorio para validar R2_ENDPOINT_URL.'
            )
        expected_host = f'{aid}.r2.cloudflarestorage.com'.lower()
        if hostname.lower() != expected_host:
            raise ImproperlyConfigured(
                'R2_ENDPOINT_URL debe coincidir con el host Cloudflare R2 '
                'derivado de R2_ACCOUNT_ID '
                '(<account-id>.r2.cloudflarestorage.com), o bien '
                'R2_ALLOW_CUSTOM_ENDPOINT=True para un endpoint S3-compatible '
                'no estándar.'
            )
        return normalized

    # Custom / nonstandard S3-compatible endpoint (explicit operator override).
    if _endpoint_hostname_blocked(hostname):
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL personalizado rechazado: host no permitido.'
        )
    return normalized


def derive_r2_endpoint_url(
    *,
    account_id: str,
    endpoint_url_raw: str,
    allow_custom: bool = False,
) -> tuple[str, bool]:
    """
    PRE: account_id is non-empty when endpoint is blank; endpoint_url_raw may
         be an explicit HTTPS endpoint; allow_custom gates non-Cloudflare hosts.
    POST: returns (validated_endpoint, endpoint_is_custom). No network I/O.
    """
    aid = (account_id or '').strip()
    if not aid:
        raise ImproperlyConfigured(
            'R2_ACCOUNT_ID es obligatorio cuando SIGEDON_PRIVATE_STORAGE=r2.'
        )
    if not _ACCOUNT_ID_RE.match(aid) or '/' in aid or ':' in aid or '@' in aid:
        raise ImproperlyConfigured('R2_ACCOUNT_ID tiene un formato inválido.')

    explicit = (endpoint_url_raw or '').strip()
    if not explicit:
        return cloudflare_r2_endpoint_for_account(aid), False

    expected = cloudflare_r2_endpoint_for_account(aid)
    _, hostname = _validate_endpoint_shape(explicit)
    expected_host = urlparse(expected).hostname or ''
    is_cloudflare = hostname.lower() == expected_host.lower()
    if is_cloudflare:
        return validate_r2_endpoint_url(
            explicit, account_id=aid, allow_custom=False
        ), False
    if not allow_custom:
        raise ImproperlyConfigured(
            'R2_ENDPOINT_URL no es el endpoint Cloudflare R2 del account ID. '
            'Active R2_ALLOW_CUSTOM_ENDPOINT=True solo para despliegues '
            'S3-compatible no estándar (no es conformidad Cloudflare R2).'
        )
    return validate_r2_endpoint_url(
        explicit, account_id=aid, allow_custom=True
    ), True

def validate_r2_bucket_name(bucket_name: str) -> str:
    """
    PRE: bucket_name is a candidate private-bucket identifier.
    POST: returns the trimmed name when syntactically plausible.
    """
    value = (bucket_name or '').strip()
    if not value:
        raise ImproperlyConfigured('R2_BUCKET_NAME es obligatorio.')
    if not _BUCKET_NAME_RE.match(value):
        raise ImproperlyConfigured('R2_BUCKET_NAME tiene un formato inválido.')
    if '..' in value or '.-' in value or '-.' in value:
        raise ImproperlyConfigured('R2_BUCKET_NAME tiene un formato inválido.')
    return value


def validate_signed_url_expiry(raw: str | None) -> int:
    """
    PRE: raw is R2_SIGNED_URL_EXPIRY_SECONDS or empty for default.
    POST: returns an integer in [60, 900].
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_SIGNED_URL_EXPIRY_SECONDS
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ImproperlyConfigured(
            'R2_SIGNED_URL_EXPIRY_SECONDS debe ser un entero válido.'
        ) from exc
    if value < MIN_SIGNED_URL_EXPIRY_SECONDS or value > MAX_SIGNED_URL_EXPIRY_SECONDS:
        raise ImproperlyConfigured(
            'R2_SIGNED_URL_EXPIRY_SECONDS debe estar entre '
            f'{MIN_SIGNED_URL_EXPIRY_SECONDS} y {MAX_SIGNED_URL_EXPIRY_SECONDS}.'
        )
    return value


def build_r2_storage_config(env: Mapping[str, str]) -> R2StorageConfig:
    """
    PRE: env provides R2_* keys when SIGEDON_PRIVATE_STORAGE=r2.
    POST: returns a complete R2StorageConfig or raises ImproperlyConfigured.
          Performs no network I/O and never logs secret values.
    """
    missing = [name for name in _R2_REQUIRED_VARS if not (env.get(name) or '').strip()]
    if missing:
        raise ImproperlyConfigured(
            'Faltan variables R2 obligatorias: ' + ', '.join(missing) + '.'
        )

    account_id = env['R2_ACCOUNT_ID'].strip()
    access_key_id = env['R2_ACCESS_KEY_ID'].strip()
    secret_access_key = env['R2_SECRET_ACCESS_KEY'].strip()
    bucket_name = validate_r2_bucket_name(env['R2_BUCKET_NAME'])
    allow_custom = resolve_r2_allow_custom_endpoint(
        env.get('R2_ALLOW_CUSTOM_ENDPOINT')
    )
    endpoint_url, endpoint_is_custom = derive_r2_endpoint_url(
        account_id=account_id,
        endpoint_url_raw=env.get('R2_ENDPOINT_URL', ''),
        allow_custom=allow_custom,
    )
    region_name = (env.get('R2_REGION_NAME') or '').strip() or DEFAULT_R2_REGION_NAME
    addressing_style = (
        (env.get('R2_ADDRESSING_STYLE') or '').strip() or DEFAULT_R2_ADDRESSING_STYLE
    )
    if addressing_style not in {'path', 'virtual'}:
        raise ImproperlyConfigured(
            'R2_ADDRESSING_STYLE debe ser path o virtual.'
        )
    expiry = validate_signed_url_expiry(env.get('R2_SIGNED_URL_EXPIRY_SECONDS'))

    # Reject accidental public-domain configuration knobs if present.
    for forbidden in (
        'R2_PUBLIC_URL',
        'AWS_S3_CUSTOM_DOMAIN',
        'AWS_S3_URL_PROTOCOL',
    ):
        if (env.get(forbidden) or '').strip():
            raise ImproperlyConfigured(
                f'{forbidden} no está permitido; el bucket privado no usa dominio público.'
            )

    return R2StorageConfig(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        region_name=region_name,
        signed_url_expiry_seconds=expiry,
        addressing_style=addressing_style,
        allow_custom_endpoint=allow_custom,
        endpoint_is_custom=endpoint_is_custom,
    )


def build_r2_storages_default(config: R2StorageConfig) -> dict:
    """
    PRE: config is a validated R2StorageConfig.
    POST: returns STORAGES['default'] for private django-storages S3Storage.
          Bucket stays private: default_acl=None, querystring_auth=True,
          file_overwrite=False. No custom_domain / public-read.
    """
    return {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'access_key': config.access_key_id,
            'secret_key': config.secret_access_key,
            'bucket_name': config.bucket_name,
            'endpoint_url': config.endpoint_url,
            'region_name': config.region_name,
            'default_acl': None,
            'querystring_auth': True,
            'querystring_expire': config.signed_url_expiry_seconds,
            'file_overwrite': False,
            'addressing_style': config.addressing_style,
            'signature_version': 's3v4',
        },
    }


def build_filesystem_storages_default() -> dict:
    """
    PRE: none.
    POST: returns STORAGES['default'] for local FileSystemStorage.
    """
    return {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    }


def resolve_private_storage_settings(
    *,
    env: Mapping[str, str],
    debug: bool,
    base_dir: Path,
    static_root: Path,
    resolve_media_root,
) -> PrivateStorageSettings:
    """
    PRE: env is the process environment; resolve_media_root is the filesystem
         path resolver used when mode=filesystem.
    POST: returns PrivateStorageSettings. R2 never falls back to filesystem.
          MEDIA_ROOT is unused for R2 default storage but set to a non-public
          placeholder so Django settings remain valid.
    """
    mode = resolve_private_storage_mode(env.get('SIGEDON_PRIVATE_STORAGE'))
    delivery_mode = resolve_private_file_delivery_mode(
        env.get('SIGEDON_PRIVATE_FILE_DELIVERY')
    )

    if mode == PRIVATE_STORAGE_FILESYSTEM:
        media_root = resolve_media_root(
            debug=debug,
            media_root_raw=env.get('SIGEDON_MEDIA_ROOT', ''),
            base_dir=base_dir,
            static_root=static_root,
        )
        return PrivateStorageSettings(
            mode=mode,
            delivery_mode=delivery_mode,
            media_root=media_root,
            r2=None,
            storages_default=build_filesystem_storages_default(),
        )

    r2 = build_r2_storage_config(env)
    # Placeholder only — default storage is S3Storage; never serve this path.
    media_root = (base_dir / R2_UNUSED_MEDIA_DIRNAME).resolve(strict=False)
    return PrivateStorageSettings(
        mode=mode,
        delivery_mode=delivery_mode,
        media_root=media_root,
        r2=r2,
        storages_default=build_r2_storages_default(r2),
    )
