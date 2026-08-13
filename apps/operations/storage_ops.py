"""Streaming helpers for private object migration and backup (no .path)."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

SHA256_CHUNK_SIZE = 1024 * 1024
STORAGE_PROBE_PREFIX = '_internal/storage-probes/'
UNSAFE_KEY_RE = re.compile(r'(?:^|/)\.\.(?:/|$)')


def is_safe_relative_object_key(name: str) -> bool:
    """
    PRE: name is a candidate storage key.
    POST: True when the key is a non-empty relative POSIX path without '..'.
    """
    if not name or not isinstance(name, str):
        return False
    normalized = name.replace('\\', '/')
    if normalized.startswith('/') or normalized.startswith('~'):
        return False
    if UNSAFE_KEY_RE.search(normalized):
        return False
    if PurePosixPath(normalized).is_absolute():
        return False
    return bool(normalized.strip('/'))


def stream_sha256(fileobj, *, chunk_size: int = SHA256_CHUNK_SIZE) -> tuple[str, int]:
    """
    PRE: fileobj is a binary readable stream positioned at start.
    POST: returns (hex_digest, byte_count) without loading the whole file.
    """
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = fileobj.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def storage_object_sha256(storage, name: str) -> tuple[str, int]:
    """
    PRE: storage implements open/exists; name is a safe relative key.
    POST: returns (sha256, size) via streaming read.
    """
    handle = storage.open(name, 'rb')
    try:
        return stream_sha256(handle)
    finally:
        handle.close()
