"""Django system checks for SIGEDON deployment contracts.

Settings import validates configuration shape only. These checks verify that
the production private-media volume exists and is usable. They run under
``manage.py check --deploy`` and never create production directories.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Tags, register

from core.media_paths import paths_overlap

MEDIA_ROOT_MISSING = 'sigedon.E001'
MEDIA_ROOT_NOT_DIRECTORY = 'sigedon.E002'
MEDIA_ROOT_NOT_READABLE = 'sigedon.E003'
MEDIA_ROOT_NOT_WRITABLE = 'sigedon.E004'
MEDIA_ROOT_OVERLAPS_STATIC = 'sigedon.E005'
MEDIA_ROOT_WRITE_PROBE_FAILED = 'sigedon.E006'

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


@register(Tags.security, deploy=True)
def check_persistent_media_root(app_configs, **kwargs):
    """
    PRE: settings are loaded; DEBUG may be True or False.
    POST: returns deploy Errors for unusable production MEDIA_ROOT; empty when
          DEBUG=True. Never lists directory contents. Removes write probes.
    """
    if settings.DEBUG:
        return []

    media_root = Path(settings.MEDIA_ROOT)
    static_root = Path(settings.STATIC_ROOT)
    errors: list[Error] = []

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

    return errors
