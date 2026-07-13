from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path


class UnsafeWritePath(RuntimeError):
    """Raised when a managed write would traverse a symlink or non-directory."""


def ensure_safe_directory(path: Path, *, mode: int = 0o755) -> None:
    """Create/check a directory tree without accepting existing symlink components."""

    absolute = path.absolute()
    missing: list[Path] = []
    cursor = absolute
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    current = Path(absolute.anchor)
    for component in absolute.parts[1 : len(absolute.parts) - len(missing)]:
        current /= component
        metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeWritePath(f"Unsafe directory component: {current}")
    for directory in reversed(missing):
        with suppress(FileExistsError):
            os.mkdir(directory, mode=mode)
        metadata = os.lstat(directory)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeWritePath(f"Unsafe directory component: {directory}")


def ensure_safe_parent(path: Path) -> None:
    ensure_safe_directory(path.parent)
    if path.exists() or path.is_symlink():
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UnsafeWritePath(f"Unsafe write target: {path}")


def write_bytes_atomic(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write a regular file with same-directory replace and directory fsync."""

    ensure_safe_parent(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str, *, mode: int = 0o644) -> None:
    write_bytes_atomic(path, text.encode("utf-8"), mode=mode)
