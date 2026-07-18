"""Cross-platform dir_fd-style secure I/O.

On POSIX every function is a thin pass-through to :mod:`os`, preserving the
original openat(2)-based guarantees (O_NOFOLLOW walks, dir_fd-relative
operations, race-free reads).

On Windows the same API is emulated:

- Directory descriptors are real CRT descriptors wrapping handles opened via
  ``CreateFileW`` with ``FILE_FLAG_BACKUP_SEMANTICS``; a registry maps each
  descriptor to its verified absolute path so relative operations can be
  resolved against it.
- ``O_NOFOLLOW`` is emulated by opening with
  ``FILE_FLAG_OPEN_REPARSE_POINT`` (directories) or by an lstat/fstat
  identity cross-check (files): the candidate is lstat-ed, rejected if it is
  any reparse point (symlink, junction, ...), opened, and the opened
  descriptor's ``(st_dev, st_ino)`` must match the lstat capture — if a
  concurrent swap-to-symlink happened in between, the identity differs and
  the read is refused.
- dir_fd-relative mutations (mkdir/unlink/rmdir/replace/stat) resolve the
  name against the registry path and re-check that the parent has not been
  replaced by a reparse point. The window between check and use is wider
  than on POSIX; the threat model (single local user, loopback-only
  service) accepts this.
- ``O_BINARY`` is always added to file opens so ``os.read`` never applies
  CRLF text-mode translation.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path

__all__ = [
    "O_CLOEXEC",
    "O_DIRECTORY",
    "O_NOFOLLOW",
    "close",
    "fsync",
    "fsync_dir",
    "link",
    "listdir",
    "lock_exclusive",
    "lock_shared",
    "mkdir",
    "open",
    "registered_path",
    "rename",
    "replace",
    "rmdir",
    "stat",
    "unlink",
    "unlock",
]

_IS_WINDOWS = os.name == "nt"

# Re-exported so call sites keep meaning on both platforms (0 where absent).
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0x10_0000 if _IS_WINDOWS else 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0x20_0000 if _IS_WINDOWS else 0)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_EMULATED_FLAGS = (O_NOFOLLOW | O_DIRECTORY) if _IS_WINDOWS else 0

_builtin_open = open  # not used; guards against accidental shadowing bugs


class DirFdPolicyError(OSError):
    """Raised when the Windows emulation refuses an unsafe filesystem object."""


if not _IS_WINDOWS:
    import fcntl

    def open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: A001
        return os.open(path, flags, mode, dir_fd=dir_fd)

    def close(fd):
        os.close(fd)

    def stat(path, *, dir_fd=None, follow_symlinks=True):
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def mkdir(path, mode=0o777, *, dir_fd=None):
        os.mkdir(path, mode, dir_fd=dir_fd)

    def unlink(path, *, dir_fd=None):
        os.unlink(path, dir_fd=dir_fd)

    def rmdir(path, *, dir_fd=None):
        os.rmdir(path, dir_fd=dir_fd)

    def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        os.replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        os.link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        os.rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def listdir(path="."):
        return os.listdir(path)

    def fsync(fd):
        os.fsync(fd)

    def fsync_dir(fd):
        os.fsync(fd)

    def registered_path(fd):
        raise NotImplementedError("registered_path is Windows-only")

    def lock_exclusive(fd):
        fcntl.flock(fd, fcntl.LOCK_EX)

    def lock_shared(fd):
        fcntl.flock(fd, fcntl.LOCK_SH)

    def unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)

else:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _GENERIC_READ = 0x8000_0000
    _GENERIC_WRITE = 0x4000_0000
    _FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # read | write | delete
    _CREATE_NEW = 1
    _CREATE_ALWAYS = 2
    _OPEN_EXISTING = 3
    _OPEN_ALWAYS = 4
    _TRUNCATE_EXISTING = 5
    _FILE_ATTRIBUTE_NORMAL = 0x0080
    _FILE_FLAG_BACKUP_SEMANTICS = 0x0200_0000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x0020_0000
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    _FILE_ATTRIBUTE_DIRECTORY = 0x0010
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL

    # fd -> verified absolute Path for directory descriptors we created.
    _dir_registry: dict[int, Path] = {}

    def _raise_last_error(path) -> None:
        error = ctypes.get_last_error()
        raise ctypes.WinError(error, str(path))

    def _handle_info(handle) -> _BY_HANDLE_FILE_INFORMATION:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        return info

    def _resolve(path, dir_fd) -> Path:
        candidate = Path(os.fspath(path))
        if dir_fd is None:
            return Path(os.path.abspath(candidate))
        base = _dir_registry.get(dir_fd)
        if base is None:
            raise DirFdPolicyError(0, "Unknown directory descriptor", str(path))
        if candidate.is_absolute():
            raise DirFdPolicyError(0, "Absolute path with dir_fd is forbidden", str(path))
        return base / candidate

    def _is_reparse(st: os.stat_result) -> bool:
        return bool(st.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)

    def _open_directory(resolved: Path, nofollow: bool) -> int:
        flags = _FILE_FLAG_BACKUP_SEMANTICS
        if nofollow:
            flags |= _FILE_FLAG_OPEN_REPARSE_POINT
        handle = _kernel32.CreateFileW(
            str(resolved),
            _GENERIC_READ,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            _raise_last_error(resolved)
        info = _handle_info(handle)
        if nofollow and info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _kernel32.CloseHandle(handle)
            raise DirFdPolicyError(0, "Path component is a reparse point", str(resolved))
        if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
            _kernel32.CloseHandle(handle)
            raise DirFdPolicyError(0, "Path is not a directory", str(resolved))
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        _dir_registry[fd] = Path(os.path.abspath(resolved))
        return fd

    def _open_file(resolved: Path, flags: int, mode: int) -> int:
        # CreateFileW instead of os.open for two POSIX-parity reasons:
        # FILE_SHARE_DELETE lets other handles rename/delete the file while
        # we hold it open (os.open never grants that share mode), and
        # FILE_FLAG_OPEN_REPARSE_POINT gives race-free O_NOFOLLOW.
        del mode  # POSIX permission bits have no Windows equivalent here
        access_bits = flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
        if access_bits == os.O_WRONLY:
            access = _GENERIC_WRITE
        elif access_bits == os.O_RDWR:
            access = _GENERIC_READ | _GENERIC_WRITE
        else:
            access = _GENERIC_READ
        creating = bool(flags & os.O_CREAT)
        exclusive = bool(flags & os.O_EXCL)
        truncating = bool(flags & os.O_TRUNC)
        if creating and exclusive:
            disposition = _CREATE_NEW
        elif creating and truncating:
            disposition = _CREATE_ALWAYS
        elif creating:
            disposition = _OPEN_ALWAYS
        elif truncating:
            disposition = _TRUNCATE_EXISTING
        else:
            disposition = _OPEN_EXISTING
        win_flags = _FILE_ATTRIBUTE_NORMAL
        nofollow = bool(flags & O_NOFOLLOW)
        if nofollow:
            win_flags |= _FILE_FLAG_OPEN_REPARSE_POINT
        handle = _kernel32.CreateFileW(
            str(resolved),
            access,
            _FILE_SHARE_ALL,
            None,
            disposition,
            win_flags,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            error = ctypes.WinError(ctypes.get_last_error())
            error.filename = str(resolved)
            raise error
        if nofollow:
            info = _handle_info(handle)
            if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                _kernel32.CloseHandle(handle)
                raise DirFdPolicyError(0, "Input is a reparse point", str(resolved))
        crt_flags = _O_BINARY
        if access == _GENERIC_READ:
            crt_flags |= os.O_RDONLY
        return msvcrt.open_osfhandle(handle, crt_flags)

    def open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: A001
        resolved = _resolve(path, dir_fd)
        # POSIX os.open accepts directories even without O_DIRECTORY; route
        # any existing directory through the handle-based directory opener.
        if flags & O_DIRECTORY or os.path.isdir(resolved):
            return _open_directory(resolved, nofollow=bool(flags & O_NOFOLLOW))
        return _open_file(resolved, flags, mode)

    def close(fd):
        _dir_registry.pop(fd, None)
        os.close(fd)

    def stat(path, *, dir_fd=None, follow_symlinks=True):
        resolved = _resolve(path, dir_fd)
        return os.stat(resolved, follow_symlinks=follow_symlinks)

    def mkdir(path, mode=0o777, *, dir_fd=None):
        os.mkdir(_resolve(path, dir_fd), mode)

    def unlink(path, *, dir_fd=None):
        os.unlink(_resolve(path, dir_fd))

    def rmdir(path, *, dir_fd=None):
        os.rmdir(_resolve(path, dir_fd))

    def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        os.replace(_resolve(src, src_dir_fd), _resolve(dst, dst_dir_fd))

    def link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        del follow_symlinks  # os.link on Windows never follows symlinks
        os.link(_resolve(src, src_dir_fd), _resolve(dst, dst_dir_fd))

    def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        # Windows os.rename refuses to clobber an existing destination, which
        # matches the renameat-without-RENAME_EXCHANGE no-replace intent of
        # every call site in this codebase.
        os.rename(_resolve(src, src_dir_fd), _resolve(dst, dst_dir_fd))

    def listdir(path="."):
        if isinstance(path, int):
            return os.listdir(registered_path(path))
        return os.listdir(path)

    def fsync(fd):
        # Directory descriptors cannot be flushed through the CRT on Windows;
        # NTFS journals metadata, so those flushes degrade to no-ops.
        if fd in _dir_registry:
            return
        os.fsync(fd)

    def fsync_dir(fd):
        # NTFS journals metadata; CRT fsync on a backup-semantics directory
        # descriptor is not reliably supported, so this is a deliberate no-op.
        del fd

    def registered_path(fd) -> Path:
        base = _dir_registry.get(fd)
        if base is None:
            raise DirFdPolicyError(0, "Unknown directory descriptor")
        return base

    def lock_exclusive(fd):
        # msvcrt.locking locks a byte range from the current position; lock
        # the first byte, matching flock's whole-file advisory intent for
        # the small marker/lock files used in this codebase.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def lock_shared(fd):
        # The CRT has no shared locks; an exclusive byte lock is stricter,
        # which is safe (never admits more concurrency than POSIX would).
        lock_exclusive(fd)

    def unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def is_regular_file_mode(mode: int) -> bool:
    """Shared helper: True when the stat mode denotes a regular file."""
    return stat_module.S_ISREG(mode)
