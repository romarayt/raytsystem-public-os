from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class PathPolicyError(ValueError):
    """Raised when an input path violates the workspace read policy."""


@dataclass(frozen=True)
class ReadResult:
    relative_path: str
    data: bytes
    mode: int
    size: int
    mtime_ns: int


def _lexical_relative(root: Path, candidate: str | Path) -> PurePosixPath:
    raw = os.fspath(candidate)
    if not raw or "\x00" in raw or "\\" in raw:
        raise PathPolicyError("Input path is malformed or non-POSIX")
    if len(raw) >= 2 and raw[1] == ":":
        raise PathPolicyError("Windows drive paths are forbidden")

    root_abs = Path(os.path.abspath(root))
    candidate_path = Path(raw)
    if candidate_path.is_absolute():
        candidate_abs = Path(os.path.abspath(candidate_path))
        try:
            relative = candidate_abs.relative_to(root_abs)
        except ValueError as error:
            raise PathPolicyError("Input path escapes the workspace") from error
    else:
        relative = candidate_path

    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise PathPolicyError("Input path escapes the workspace")
    return pure


def read_regular_file(root: Path, candidate: str | Path, *, max_bytes: int) -> ReadResult:
    """Read through directory file descriptors without following symlinks."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    root = Path(os.path.abspath(root))
    relative = _lexical_relative(root, candidate)
    if os.name == "nt":
        return _read_regular_file_windows(root, relative, max_bytes=max_bytes)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root, os.O_RDONLY | directory_flag | cloexec)
    parent_fd = root_fd
    opened_parents: list[int] = []
    file_fd: int | None = None
    try:
        for component in relative.parts[:-1]:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory_flag | nofollow | cloexec,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise PathPolicyError(
                    "Path parent is missing, non-directory or a symlink"
                ) from error
            opened_parents.append(next_fd)
            parent_fd = next_fd

        try:
            file_fd = os.open(
                relative.parts[-1],
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise PathPolicyError("Input is missing or a symlink") from error

        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise PathPolicyError("Input must be a regular file")
        if before.st_nlink != 1:
            raise PathPolicyError("Hard-linked inputs are not accepted")
        if before.st_size > max_bytes:
            raise PathPolicyError("Input exceeds the configured size limit")

        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > max_bytes:
                raise PathPolicyError("Input exceeds the configured size limit")

        after = os.fstat(file_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise PathPolicyError("Input changed while it was being read")
        return ReadResult(
            relative_path=relative.as_posix(),
            data=b"".join(chunks),
            mode=before.st_mode,
            size=before.st_size,
            mtime_ns=before.st_mtime_ns,
        )
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(opened_parents):
            os.close(descriptor)
        os.close(root_fd)


def _read_regular_file_windows(
    root: Path, relative: PurePosixPath, *, max_bytes: int
) -> ReadResult:
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise PathPolicyError(
                "Path parent is missing, non-directory or a symlink"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PathPolicyError("Path parent is missing, non-directory or a symlink")

    path = current / relative.parts[-1]
    try:
        before = os.lstat(path)
    except OSError as error:
        raise PathPolicyError("Input is missing or a symlink") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PathPolicyError("Input must be a regular file")
    if before.st_nlink != 1:
        raise PathPolicyError("Hard-linked inputs are not accepted")
    if before.st_size > max_bytes:
        raise PathPolicyError("Input exceeds the configured size limit")

    chunks: list[bytes] = []
    consumed = 0
    with open(path, "rb") as handle:
        after_open = os.fstat(handle.fileno())
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after_open, field) for field in stable_fields):
            raise PathPolicyError("Input changed while it was being read")
        while True:
            chunk = handle.read(min(1024 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > max_bytes:
                raise PathPolicyError("Input exceeds the configured size limit")
        after = os.fstat(handle.fileno())
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise PathPolicyError("Input changed while it was being read")
    return ReadResult(
        relative_path=relative.as_posix(),
        data=b"".join(chunks),
        mode=before.st_mode,
        size=before.st_size,
        mtime_ns=before.st_mtime_ns,
    )
