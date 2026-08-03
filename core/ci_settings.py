"""CI-only Django settings: isolate STATIC_ROOT to a runner temp directory.

PRE: SIGEDON_CI_STATIC_ROOT is set to an absolute writable temp path.
POST: imports the production settings module, overrides STATIC_ROOT, and forces
      WhiteNoise CompressedManifestStaticFilesStorage so CI artifact gates
      exercise the production static contract regardless of DJANGO_DEBUG.
Do not use this module in production or staging runtimes.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from core.settings import *  # noqa: F401, F403

_raw = os.environ.get('SIGEDON_CI_STATIC_ROOT', '').strip()
if not _raw:
    raise ImproperlyConfigured(
        'SIGEDON_CI_STATIC_ROOT must be set when using core.ci_settings.'
    )
if not os.path.isabs(_raw):
    raise ImproperlyConfigured(
        'SIGEDON_CI_STATIC_ROOT must be an absolute path.'
    )

STATIC_ROOT = Path(_raw)

# Always exercise production WhiteNoise manifest storage in CI collectstatic /
# verify_deployment_assets gates (independent of DJANGO_DEBUG inherited above).
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}

WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
