"""Pure helpers for SIGEDON private-media filesystem path configuration.

PRE: callers pass Path-like configuration candidates and known roots.
POST: returns a normalized absolute Path or raises ImproperlyConfigured
      without embedding secret values in messages.
"""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def paths_overlap(path_a: Path, path_b: Path) -> bool:
    """
    PRE: path_a and path_b are Path instances.
    POST: True when the paths are equal or one is nested inside the other.
    """
    try:
        resolved_a = path_a.resolve(strict=False)
        resolved_b = path_b.resolve(strict=False)
    except OSError:
        return False
    if resolved_a == resolved_b:
        return True
    try:
        resolved_a.relative_to(resolved_b)
        return True
    except ValueError:
        pass
    try:
        resolved_b.relative_to(resolved_a)
        return True
    except ValueError:
        return False


def is_filesystem_root(path: Path) -> bool:
    """
    PRE: path is a Path instance.
    POST: True when path resolves to a filesystem-drive root (e.g. '/').
    """
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved == Path(resolved.anchor)


def is_under(child: Path, parent: Path) -> bool:
    """
    PRE: child and parent are Path instances.
    POST: True when child resolves strictly inside parent (not equal).
    """
    try:
        resolved_child = child.resolve(strict=False)
        resolved_parent = parent.resolve(strict=False)
    except OSError:
        return False
    if resolved_child == resolved_parent:
        return False
    try:
        resolved_child.relative_to(resolved_parent)
        return True
    except ValueError:
        return False


def validate_media_root_path(
    media_root: Path,
    *,
    static_root: Path,
    base_dir: Path,
    require_outside_repo: bool = False,
) -> Path:
    """
    PRE: media_root is a candidate path; static_root and base_dir are known roots.
    POST: returns a normalized absolute Path, or raises ImproperlyConfigured.
    Does not require the path to exist on disk.
    """
    configured = media_root.expanduser()
    if not configured.is_absolute():
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT must be an absolute persistent filesystem path.'
        )

    try:
        normalized = configured.resolve(strict=False)
    except OSError as exc:
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT is not a usable filesystem path.'
        ) from exc

    if is_filesystem_root(normalized):
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT must not be a filesystem root.'
        )

    try:
        base_resolved = base_dir.resolve(strict=False)
    except OSError as exc:
        raise ImproperlyConfigured(
            'BASE_DIR is not a usable filesystem path for media validation.'
        ) from exc

    if normalized == base_resolved:
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT must not be the application repository root.'
        )

    if require_outside_repo and (
        normalized == base_resolved or is_under(normalized, base_resolved)
    ):
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT must not be inside the application repository '
            'when DJANGO_DEBUG=False.'
        )

    try:
        static_normalized = static_root.resolve(strict=False)
    except OSError as exc:
        raise ImproperlyConfigured(
            'STATIC_ROOT is not a usable filesystem path for media validation.'
        ) from exc

    if paths_overlap(normalized, static_normalized):
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT must not equal or overlap STATIC_ROOT.'
        )

    return normalized


def resolve_media_root(
    *,
    debug: bool,
    media_root_raw: str,
    base_dir: Path,
    static_root: Path,
) -> Path:
    """
    PRE: debug reflects DJANGO_DEBUG; media_root_raw is the raw env value.
    POST: returns the configured MEDIA_ROOT Path or raises ImproperlyConfigured.
    Development keeps BASE_DIR/media when the env var is unset.
    Production requires an absolute path outside the repository.
    """
    raw = (media_root_raw or '').strip()

    if debug:
        if not raw:
            return base_dir / 'media'
        return validate_media_root_path(
            Path(raw),
            static_root=static_root,
            base_dir=base_dir,
            require_outside_repo=False,
        )

    if not raw:
        raise ImproperlyConfigured(
            'SIGEDON_MEDIA_ROOT is required when DJANGO_DEBUG=False.'
        )

    return validate_media_root_path(
        Path(raw),
        static_root=static_root,
        base_dir=base_dir,
        require_outside_repo=True,
    )
