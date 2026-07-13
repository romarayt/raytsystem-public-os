from __future__ import annotations

import os
import stat
from pathlib import Path

from raytsystem.io import UnsafeWritePath, ensure_safe_parent


def assert_safe_replace_target(path: Path) -> None:
    """Reject symlink/non-regular/hardlinked targets before replacing derived state."""

    ensure_safe_parent(path)
    if not path.exists() and not path.is_symlink():
        return
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise UnsafeWritePath(f"Unsafe derived target: {path}")


def assert_safe_sqlite_family(path: Path) -> None:
    assert_safe_replace_target(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            metadata = os.lstat(sidecar)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise UnsafeWritePath(f"Unsafe SQLite sidecar: {sidecar}")
