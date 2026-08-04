"""Django system checks for SIGEDON deployment contracts.

Settings import validates configuration shape only. These checks verify that
production private media is usable (filesystem volume or R2 structural config).
They run under ``manage.py check --deploy`` and perform no network I/O.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Tags, register

from core.media_paths import paths_overlap
from core.private_storage import (
    PRIVATE_STORAGE_FILESYSTEM,
    PRIVATE_STORAGE_R2,
)

MEDIA_ROOT_MISSING = 'sigedon.E001'
MEDIA_ROOT_NOT_DIRECTORY = 'sigedon.E002'
MEDIA_ROOT_NOT_READABLE = 'sigedon.E003'
MEDIA_ROOT_NOT_WRITABLE = 'sigedon.E004'
MEDIA_ROOT_OVERLAPS_STATIC = 'sigedon.E005'
MEDIA_ROOT_WRITE_PROBE_FAILED = 'sigedon.E006'

R2_STORAGE_NOT_CONFIGURED = 'sigedon.E010'
R2_STATIC_BACKEND_INVALID = 'sigedon.E011'
R2_PUBLIC_DOMAIN_CONFIGURED = 'sigedon.E012'
R2_QUERYSTRING_AUTH_DISABLED = 'sigedon.E013'
R2_DEFAULT_ACL_PUBLIC = 'sigedon.E014'
UNKNOWN_PRIVATE_STORAGE_MODE = 'sigedon.E015'
R2_CUSTOM_ENDPOINT_ENABLED = 'sigedon.E016'

_PROBE_PREFIX = '.sigedon-media-write-probe-'


def _media_error(message: str, *, error_id: str) -> Error:
    return Error(
        message,
        hint=(
            'Mount a persistent private-media volume, set SIGEDON_MEDIA_ROOT '
            'to that absolute path, and ensure the application process can '
            'read and write it. Do not expose the volume publicly.'
        ),
        id=error_id,
    )


def _r2_error(message: str, *, error_id: str) -> Error:
    return Error(
        message,
        hint=(
            'Set SIGEDON_PRIVATE_STORAGE=r2 with complete R2_* variables for a '
            'private bucket. Do not enable public ACLs, r2.dev, or custom '
            'public domains. See docs/runbooks/CLOUDFLARE_R2.md.'
        ),
        id=error_id,
    )


def _check_filesystem_media_root() -> list[Error]:
    media_root = Path(settings.MEDIA_ROOT)
    static_root = Path(settings.STATIC_ROOT)

    if paths_overlap(media_root, static_root):
        return [
            _media_error(
                'MEDIA_ROOT must not equal or overlap STATIC_ROOT.',
                error_id=MEDIA_ROOT_OVERLAPS_STATIC,
            )
        ]

    if not media_root.exists():
        return [
            _media_error(
                'MEDIA_ROOT does not exist. Provision and mount the persistent '
                'private-media directory before starting traffic.',
                error_id=MEDIA_ROOT_MISSING,
            )
        ]

    if not media_root.is_dir():
        return [
            _media_error(
                'MEDIA_ROOT exists but is not a directory.',
                error_id=MEDIA_ROOT_NOT_DIRECTORY,
            )
        ]

    media_path = str(media_root)
    if not os.access(media_path, os.R_OK):
        return [
            _media_error(
                'MEDIA_ROOT is not readable by the application process.',
                error_id=MEDIA_ROOT_NOT_READABLE,
            )
        ]

    if not os.access(media_path, os.W_OK):
        return [
            _media_error(
                'MEDIA_ROOT is not writable by the application process.',
                error_id=MEDIA_ROOT_NOT_WRITABLE,
            )
        ]

    probe_path = media_root / f'{_PROBE_PREFIX}{uuid.uuid4().hex}'
    try:
        with open(probe_path, 'xb') as handle:
            handle.write(b'')
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return [
            _media_error(
                'MEDIA_ROOT write probe collided with an existing name; retry check.',
                error_id=MEDIA_ROOT_WRITE_PROBE_FAILED,
            )
        ]
    except OSError:
        return [
            _media_error(
                'MEDIA_ROOT write probe failed; the process cannot create files '
                'in the private-media directory.',
                error_id=MEDIA_ROOT_WRITE_PROBE_FAILED,
            )
        ]
    else:
        try:
            probe_path.unlink()
        except OSError:
            return [
                _media_error(
                    'MEDIA_ROOT write probe could not be removed after creation.',
                    error_id=MEDIA_ROOT_WRITE_PROBE_FAILED,
                )
            ]

    return []


def _check_r2_private_storage() -> list[Error]:
    """
    PRE: SIGEDON_PRIVATE_STORAGE=r2 and settings already loaded.
    POST: structural Errors only; never contacts Cloudflare or opens sockets.
    """
    errors: list[Error] = []
    r2 = getattr(settings, 'SIGEDON_R2_CONFIG', None)
    if r2 is None:
        errors.append(
            _r2_error(
                'SIGEDON_PRIVATE_STORAGE=r2 but R2 configuration is missing.',
                error_id=R2_STORAGE_NOT_CONFIGURED,
            )
        )
        return errors

    storages = getattr(settings, 'STORAGES', {}) or {}
    default_cfg = storages.get('default') or {}
    backend = default_cfg.get('BACKEND', '')
    if backend != 'storages.backends.s3.S3Storage':
        errors.append(
            _r2_error(
                'R2 mode requires STORAGES["default"] = storages.backends.s3.S3Storage.',
                error_id=R2_STORAGE_NOT_CONFIGURED,
            )
        )

    options = default_cfg.get('OPTIONS') or {}
    if options.get('querystring_auth') is False:
        errors.append(
            _r2_error(
                'querystring_auth must remain True for private R2 objects.',
                error_id=R2_QUERYSTRING_AUTH_DISABLED,
            )
        )
    default_acl = options.get('default_acl', None)
    if default_acl in ('public-read', 'public-read-write'):
        errors.append(
            _r2_error(
                'default_acl must not grant public-read on private documents.',
                error_id=R2_DEFAULT_ACL_PUBLIC,
            )
        )
    if options.get('custom_domain') or options.get('custom_domain') == '':
        # Empty string would still be wrong intent; only reject truthy domains.
        pass
    if options.get('custom_domain'):
        errors.append(
            _r2_error(
                'custom_domain is not allowed; keep the R2 bucket private.',
                error_id=R2_PUBLIC_DOMAIN_CONFIGURED,
            )
        )

    static_cfg = storages.get('staticfiles') or {}
    static_backend = static_cfg.get('BACKEND', '')
    if 'storages.backends.s3' in static_backend or static_backend.endswith('.S3Storage'):
        errors.append(
            _r2_error(
                'STORAGES["staticfiles"] must remain WhiteNoise; static assets '
                'must not use R2.',
                error_id=R2_STATIC_BACKEND_INVALID,
            )
        )
    elif 'whitenoise' not in static_backend.lower():
        errors.append(
            _r2_error(
                'STORAGES["staticfiles"] must remain WhiteNoise; static assets '
                'must not use R2.',
                error_id=R2_STATIC_BACKEND_INVALID,
            )
        )

    if getattr(r2, 'allow_custom_endpoint', False) or getattr(
        r2, 'endpoint_is_custom', False
    ):
        errors.append(
            Error(
                'R2_ALLOW_CUSTOM_ENDPOINT enables a nonstandard S3-compatible '
                'endpoint. Canonical deployments must use Cloudflare R2 '
                '(<account-id>.r2.cloudflarestorage.com).',
                hint=(
                    'Set R2_ALLOW_CUSTOM_ENDPOINT=False and use the derived '
                    'Cloudflare endpoint, or accept this as a nonstandard '
                    'deployment outside the Render R2 contract.'
                ),
                id=R2_CUSTOM_ENDPOINT_ENABLED,
            )
        )

    return errors


@register(Tags.security, deploy=True)
def check_persistent_media_root(app_configs, **kwargs):
    """
    PRE: settings are loaded; DEBUG may be True or False.
    POST: filesystem mode — deploy Errors for unusable MEDIA_ROOT when
          DEBUG=False; R2 mode — structural private-storage Errors without
          requiring SIGEDON_MEDIA_ROOT or network I/O. Empty when DEBUG=True
          for filesystem volume probes; R2 structural checks still run under
          --deploy when DEBUG=False.
    """
    if settings.DEBUG:
        return []

    mode = getattr(settings, 'SIGEDON_PRIVATE_STORAGE', PRIVATE_STORAGE_FILESYSTEM)
    if mode == PRIVATE_STORAGE_FILESYSTEM:
        return _check_filesystem_media_root()
    if mode == PRIVATE_STORAGE_R2:
        return _check_r2_private_storage()
    return [
        Error(
            f'SIGEDON_PRIVATE_STORAGE desconocido: {mode!r}.',
            hint='Use filesystem o r2.',
            id=UNKNOWN_PRIVATE_STORAGE_MODE,
        )
    ]


@register(Tags.compatibility)
def check_private_storage_mode(app_configs, **kwargs):
    """
    PRE: settings loaded in any environment (including Django's test runner,
         which forces DEBUG=False while STORAGES may still reflect import-time
         DEBUG=True static backend).
    POST: Errors when private storage mode/backend contract is inconsistent.
          Does not require WhiteNoise here — that is a deploy-time concern —
          but rejects staticfiles on S3/R2. No network I/O.
    """
    mode = getattr(settings, 'SIGEDON_PRIVATE_STORAGE', PRIVATE_STORAGE_FILESYSTEM)
    if mode not in (PRIVATE_STORAGE_FILESYSTEM, PRIVATE_STORAGE_R2):
        return [
            Error(
                f'SIGEDON_PRIVATE_STORAGE desconocido: {mode!r}.',
                hint='Use filesystem o r2.',
                id=UNKNOWN_PRIVATE_STORAGE_MODE,
            )
        ]

    storages = getattr(settings, 'STORAGES', {}) or {}
    default_backend = (storages.get('default') or {}).get('BACKEND', '')
    static_backend = (storages.get('staticfiles') or {}).get('BACKEND', '')

    errors: list[Error] = []
    if mode == PRIVATE_STORAGE_FILESYSTEM:
        if 'FileSystemStorage' not in default_backend:
            errors.append(
                Error(
                    'Filesystem mode requires FileSystemStorage as default storage.',
                    id=UNKNOWN_PRIVATE_STORAGE_MODE,
                )
            )
    elif mode == PRIVATE_STORAGE_R2:
        if default_backend != 'storages.backends.s3.S3Storage':
            errors.append(
                _r2_error(
                    'R2 mode requires storages.backends.s3.S3Storage.',
                    error_id=R2_STORAGE_NOT_CONFIGURED,
                )
            )
        if getattr(settings, 'SIGEDON_R2_CONFIG', None) is None:
            errors.append(
                _r2_error(
                    'R2 configuration object missing from settings.',
                    error_id=R2_STORAGE_NOT_CONFIGURED,
                )
            )

    # Static assets must never share the private R2/S3 backend.
    if 'storages.backends.s3' in static_backend or static_backend.endswith('.S3Storage'):
        errors.append(
            _r2_error(
                'STORAGES["staticfiles"] must not use S3/R2; keep WhiteNoise.',
                error_id=R2_STATIC_BACKEND_INVALID,
            )
        )
    return errors


@register(Tags.security, deploy=True)
def check_production_staticfiles_backend(app_configs, **kwargs):
    """
    PRE: --deploy checks; DEBUG may be True or False.
    POST: when DEBUG=False, require WhiteNoise for staticfiles. No network I/O.
    """
    if settings.DEBUG:
        return []
    storages = getattr(settings, 'STORAGES', {}) or {}
    static_backend = (storages.get('staticfiles') or {}).get('BACKEND', '')
    if 'whitenoise' not in static_backend.lower():
        return [
            _r2_error(
                'Static files must use WhiteNoise in production; not R2.',
                error_id=R2_STATIC_BACKEND_INVALID,
            )
        ]
    return []
