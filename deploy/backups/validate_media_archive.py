#!/usr/bin/env python3
"""Validate a v1 media.tar.gz before extraction (path/type traversal defense).

PRE: argv[1] is a readable tar/tar.gz path; argv[2] is the intended restore root.
POST: exit 0 only when every member is a safe regular file or directory whose
      normalized path stays inside the restore root. Exit non-zero otherwise.
      Never extracts. Never prints member payloads.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath


def _die(message: str, code: int = 3) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(code)


def _is_windows_absolute(name: str) -> bool:
    windows = PureWindowsPath(name)
    if windows.is_absolute():
        return True
    # Drive / UNC style without relying solely on PureWindowsPath edge cases.
    if len(name) >= 2 and name[1] == ':':
        return True
    if name.startswith('\\\\') or name.startswith('//'):
        return True
    return False


def _reject_member_name(name: str, restore_root: Path) -> str | None:
    if not name or name.strip() == '':
        return 'empty member path'
    if '\x00' in name:
        return 'malformed member path'
    if name.startswith('/') or name.startswith('\\'):
        return 'absolute member path'
    if _is_windows_absolute(name):
        return 'absolute member path'
    posix = PurePosixPath(name)
    parts = posix.parts
    if '..' in parts:
        return 'path traversal component'
    if name == '..' or name.startswith('../') or '/../' in name or name.endswith('/..'):
        return 'path traversal component'
    # Normalize against restore root; reject escape.
    candidate = (restore_root / Path(*parts)).resolve()
    try:
        candidate.relative_to(restore_root.resolve())
    except ValueError:
        return 'path resolves outside restore root'
    return None


def _reject_member_type(member: tarfile.TarInfo) -> str | None:
    # FIFO reports isdev()=True on CPython; check specialized types first.
    if member.issym():
        return 'symlink member'
    if member.islnk():
        return 'hard-link member'
    if member.isfifo():
        return 'fifo member'
    if member.ischr() or member.isblk() or member.isdev():
        return 'device member'
    if member.isdir() or member.isfile():
        return None
    return 'unsupported member type'


def validate_archive(archive_path: Path, restore_root: Path) -> None:
    if not archive_path.is_file():
        _die(f'media archive not found: {archive_path.name}')
    if not restore_root.exists():
        _die('restore root does not exist')
    restore_root = restore_root.resolve()

    try:
        with tarfile.open(archive_path, mode='r:*') as archive:
            members = archive.getmembers()
            for member in members:
                type_error = _reject_member_type(member)
                if type_error:
                    _die(f'unsafe archive member ({type_error}): rejected')
                path_error = _reject_member_name(member.name, restore_root)
                if path_error:
                    _die(f'unsafe archive member ({path_error}): rejected')
                # Link name must not escape either (defensive; type already rejected).
                if member.linkname:
                    link_error = _reject_member_name(member.linkname, restore_root)
                    if link_error or member.linkname.startswith('/'):
                        _die('unsafe archive member (link target): rejected')
    except tarfile.TarError:
        _die('media archive is not a readable tar archive')


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        _die(f'uso: {argv[0]} <media.tar.gz> <restore-root>', code=2)
    validate_archive(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv))
    except BrokenPipeError:
        raise SystemExit(0)
