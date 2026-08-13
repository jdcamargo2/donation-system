"""Fake private storage without .path for offline remote-storage tests.

PRE: location is a temporary directory owned by the test.
POST: behaves like a remote object store: save/open/exists/size/delete/url
      without implementing .path. URL generation is counted for authorization tests.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


class StorageReadError(OSError):
    """Simulated provider read failure."""


class StorageWriteError(OSError):
    """Simulated provider write failure."""


@deconstructible
class NoPathPrivateStorage(Storage):
    """
    Filesystem-backed test double that deliberately omits .path.

    Objects live under ``location`` but application code must use storage APIs.
    """

    def __init__(self, location=None, *, base_url='https://fake-private-storage.test/'):
        self.location = Path(location) if location is not None else Path('.')
        self.location.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip('/') + '/'
        self.url_generation_count = 0
        self.last_url_name = None
        self.last_url_parameters = None
        self.reject_url_parameters = False
        self.fail_open_names: set[str] = set()
        self.fail_save = False
        self.fail_exists = False
        self.signed_url_expiry = 300
        self.provider_unavailable_open = False
        self.provider_unavailable_exists = False
        self.provider_unavailable_url = False

    def _path(self, name: str) -> Path:
        # Internal only — never expose via .path property.
        clean = str(name).replace('\\', '/').lstrip('/')
        if '..' in PurePosixSegments(clean):
            raise ValueError('unsafe storage key')
        return self.location / clean

    def _open(self, name, mode='rb'):
        if getattr(self, 'provider_unavailable_open', False):
            raise ConnectionError('simulated provider outage')
        if name in self.fail_open_names:
            raise StorageReadError('simulated storage read failure')
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return File(path.open(mode), name=name)

    def _save(self, name, content):
        if self.fail_save:
            raise StorageWriteError('simulated storage write failure')
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # file_overwrite=False semantics: reject differing existing content.
        if path.exists():
            existing = path.read_bytes()
            incoming = content.read() if hasattr(content, 'read') else bytes(content)
            if hasattr(content, 'seek'):
                try:
                    content.seek(0)
                except Exception:  # noqa: BLE001
                    pass
            if existing != incoming:
                raise FileExistsError(name)
            return name
        with path.open('wb') as handle:
            for chunk in content.chunks() if hasattr(content, 'chunks') else [content.read()]:
                handle.write(chunk)
        return name

    def delete(self, name):
        path = self._path(name)
        if path.is_file():
            path.unlink()

    def exists(self, name):
        if getattr(self, 'provider_unavailable_exists', False):
            raise ConnectionError('simulated provider outage')
        if self.fail_exists:
            raise StorageReadError('simulated exists failure')
        return self._path(name).is_file()

    def size(self, name):
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.stat().st_size

    def url(self, name, parameters=None, expire=None):
        if getattr(self, 'provider_unavailable_url', False):
            raise ConnectionError('simulated provider outage')
        if getattr(self, 'reject_url_parameters', False) and parameters is not None:
            raise TypeError('url() got an unexpected keyword argument parameters')
        self.url_generation_count += 1
        self.last_url_name = name
        self.last_url_parameters = parameters
        expiry = expire if expire is not None else self.signed_url_expiry
        # Fake signed URL — never a real credential.
        qs = f'X-Amz-Expires={expiry}&X-Amz-Signature=fake-test-signature'
        if parameters:
            for key, value in parameters.items():
                qs += f'&{key}={value}'
        return f'{self.base_url}{name}?{qs}'

    def get_available_name(self, name, max_length=None):
        # Remote-like: keep the exact object key (no suffix renaming).
        # Differing existing content is rejected in _save.
        return name

    def path(self, name):  # type: ignore[override]
        raise NotImplementedError(
            'NoPathPrivateStorage deliberately does not implement .path'
        )

    def save_bytes(self, name: str, data: bytes) -> str:
        return self.save(name, ContentFile(data))


@deconstructible
class BareUrlPrivateStorage(NoPathPrivateStorage):
    """Test double whose url() cannot accept response override parameters."""

    def url(self, name):  # type: ignore[override]
        self.url_generation_count += 1
        self.last_url_name = name
        self.last_url_parameters = None
        qs = (
            f'X-Amz-Expires={self.signed_url_expiry}'
            f'&X-Amz-Signature=fake-test-signature'
        )
        return f'{self.base_url}{name}?{qs}'


def PurePosixSegments(name: str) -> list[str]:
    return [part for part in name.split('/') if part]


def guess_content_type(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or 'application/octet-stream'
