from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    overflowed: bool = False
    timed_out: bool = False


def hardened_git_environment() -> dict[str, str]:
    if os.name == "nt":
        # os.defpath on Windows is ".;C:\bin" — it would put the current
        # directory on PATH and lose git.exe; keep the parent PATH and the
        # SystemRoot needed for system DLL resolution instead.
        path = os.environ.get("PATH", os.defpath)
    else:
        path = os.defpath
    environment = {
        "PATH": path,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT")
        if system_root:
            environment["SYSTEMROOT"] = system_root
    return environment


def hardened_git_arguments(root: Path) -> tuple[str, ...]:
    return (
        "git",
        "-C",
        str(root),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.pager=cat",
        "-c",
        "pager.log=false",
    )


def run_bounded(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    stdout_limit: int,
    stderr_limit: int = 64 * 1024,
    timeout_seconds: float = 20.0,
) -> BoundedProcessResult:
    """Run a local command without ever collecting more than the declared byte caps."""

    if stdout_limit < 0 or stderr_limit < 0 or timeout_seconds <= 0:
        raise ValueError("Subprocess limits must be positive")
    if os.name == "nt":
        # selectors on Windows only accept sockets, never pipes; drain the
        # child through bounded reader threads instead.
        return _run_bounded_windows(
            arguments,
            environment=environment,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            timeout_seconds=timeout_seconds,
        )
    process = subprocess.Popen(
        tuple(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
        close_fds=True,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        raise OSError("Subprocess pipes are unavailable")
    stdout_descriptor = process.stdout.fileno()
    stderr_descriptor = process.stderr.fileno()
    streams = {
        stdout_descriptor: (process.stdout, bytearray(), stdout_limit),
        stderr_descriptor: (process.stderr, bytearray(), stderr_limit),
    }
    selector = selectors.DefaultSelector()
    for descriptor, (stream, _buffer, _limit) in streams.items():
        os.set_blocking(descriptor, False)
        selector.register(stream, selectors.EVENT_READ, descriptor)
    deadline = time.monotonic() + timeout_seconds
    overflowed = False
    timed_out = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _events in selector.select(min(remaining, 0.1)):
                descriptor = int(key.data)
                stream, buffer, limit = streams[descriptor]
                try:
                    chunk = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                available = limit - len(buffer)
                if len(chunk) > available:
                    if available > 0:
                        buffer.extend(chunk[:available])
                    overflowed = True
                    break
                buffer.extend(chunk)
            if overflowed:
                break
            if process.poll() is not None and not selector.get_map():
                break
        if overflowed or timed_out:
            _stop_process(process)
        else:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process(process)
        return BoundedProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=bytes(streams[stdout_descriptor][1]),
            stderr=bytes(streams[stderr_descriptor][1]),
            overflowed=overflowed,
            timed_out=timed_out,
        )
    finally:
        selector.close()
        for stream, _buffer, _limit in streams.values():
            if not stream.closed:
                stream.close()
        if process.poll() is None:
            _stop_process(process)


def _run_bounded_windows(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str],
    stdout_limit: int,
    stderr_limit: int,
    timeout_seconds: float,
) -> BoundedProcessResult:
    process = subprocess.Popen(
        tuple(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
        close_fds=True,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        raise OSError("Subprocess pipes are unavailable")
    overflow = threading.Event()
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()

    def _drain(stream, buffer: bytearray, limit: int) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                available = limit - len(buffer)
                if len(chunk) > available:
                    if available > 0:
                        buffer.extend(chunk[:available])
                    overflow.set()
                    return
                buffer.extend(chunk)
        except (OSError, ValueError):
            return

    readers = (
        threading.Thread(
            target=_drain, args=(process.stdout, stdout_buffer, stdout_limit), daemon=True
        ),
        threading.Thread(
            target=_drain, args=(process.stderr, stderr_buffer, stderr_limit), daemon=True
        ),
    )
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while True:
            if overflow.is_set():
                _stop_process(process)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_process(process)
                break
            try:
                process.wait(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
        for reader in readers:
            reader.join(timeout=1.0)
        return BoundedProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=bytes(stdout_buffer),
            stderr=bytes(stderr_buffer),
            overflowed=overflow.is_set(),
            timed_out=timed_out,
        )
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        if process.poll() is None:
            _stop_process(process)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
    else:
        process.kill()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
