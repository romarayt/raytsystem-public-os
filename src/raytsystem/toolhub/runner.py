from __future__ import annotations

import hashlib
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from raytsystem.toolhub.errors import (
    ToolDependencyError,
    ToolExecutionError,
    ToolTimeoutError,
)

CliName = Literal["ffprobe", "ffmpeg", "yt-dlp", "tesseract"]
CliOperation = Literal["probe", "download", "extract_audio", "extract_frame", "ocr"]


@dataclass(frozen=True)
class CliPolicy:
    name: CliName
    version_args: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class ExecutablePin:
    """Trusted launcher-supplied identity for one external media executable."""

    path: Path
    sha256: str
    exact_version: str
    platform: str
    machine: str

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("Executable pin SHA-256 must be lowercase hexadecimal")
        if not self.exact_version or "\n" in self.exact_version or "\r" in self.exact_version:
            raise ValueError("Executable pin version must be one exact output line")


CLI_POLICIES: dict[CliName, CliPolicy] = {
    "ffprobe": CliPolicy("ffprobe", ("-version",), 30, 2 * 1024 * 1024),
    "ffmpeg": CliPolicy("ffmpeg", ("-version",), 900, 2 * 1024 * 1024),
    "yt-dlp": CliPolicy("yt-dlp", ("--version",), 900, 2 * 1024 * 1024),
    "tesseract": CliPolicy("tesseract", ("--version",), 300, 4 * 1024 * 1024),
}


@dataclass(frozen=True)
class CliInvocation:
    """An internal typed invocation; callers cannot supply arbitrary argv."""

    executable: CliName
    operation: CliOperation
    arguments: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    max_output_bytes: int = 2 * 1024 * 1024
    stdin: bytes = b""


@dataclass(frozen=True)
class CliOutcome:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class CliRunner(Protocol):
    enforces_local_sandbox: bool

    def run(self, invocation: CliInvocation) -> CliOutcome: ...

    def version(self, executable: CliName) -> str: ...


def _controlled_environment(path: str) -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "PATH": path,
    }


class AllowlistedCliRunner:
    """Hash/version-pinned no-shell runner used inside a platform sandbox capability.

    This low-level runner deliberately does not claim filesystem or network confinement;
    ``VideoToolHub`` therefore refuses to use it directly for untrusted local media.
    """

    enforces_local_sandbox = False

    def __init__(
        self,
        *,
        pins: Mapping[CliName, ExecutablePin] | None = None,
    ) -> None:
        self._pins = dict(pins or {})
        self._resolved: dict[CliName, Path] = {}
        self._versions: dict[CliName, str] = {}
        self._fingerprints: dict[CliName, tuple[int, int, int, int]] = {}

    @classmethod
    def from_environment(cls) -> AllowlistedCliRunner:
        """Load complete pins injected by a trusted launcher; never discover via PATH."""

        pins: dict[CliName, ExecutablePin] = {}
        for name in CLI_POLICIES:
            prefix = f"RAYTSYSTEM_TOOLHUB_{name.replace('-', '_').upper()}"
            values = {
                "path": os.environ.get(f"{prefix}_PATH"),
                "sha256": os.environ.get(f"{prefix}_SHA256"),
                "exact_version": os.environ.get(f"{prefix}_VERSION"),
                "platform": os.environ.get(f"{prefix}_PLATFORM"),
                "machine": os.environ.get(f"{prefix}_MACHINE"),
            }
            if not any(values.values()):
                continue
            if not all(values.values()):
                raise ToolDependencyError(f"Pinned {name} configuration is incomplete")
            pins[name] = ExecutablePin(
                path=Path(str(values["path"])),
                sha256=str(values["sha256"]),
                exact_version=str(values["exact_version"]),
                platform=str(values["platform"]),
                machine=str(values["machine"]),
            )
        return cls(pins=pins)

    def run(self, invocation: CliInvocation) -> CliOutcome:
        policy = CLI_POLICIES.get(invocation.executable)
        if policy is None:
            raise ToolPolicyError("Executable is not in the Tool Hub allowlist")
        if invocation.timeout_seconds <= 0 or invocation.timeout_seconds > policy.timeout_seconds:
            raise ToolPolicyError("Invocation timeout exceeds the reviewed tool contract")
        if (
            invocation.max_output_bytes <= 0
            or invocation.max_output_bytes > policy.max_output_bytes
        ):
            raise ToolPolicyError("Invocation output bound exceeds the reviewed tool contract")
        if not invocation.cwd.is_dir():
            raise ToolExecutionError("Tool working directory is unavailable")
        _validate_invocation(invocation)

        executable = self._resolve(invocation.executable)
        self.version(invocation.executable)
        executable = self._resolve(invocation.executable)
        return self._execute(
            executable,
            invocation.arguments,
            cwd=invocation.cwd,
            timeout_seconds=invocation.timeout_seconds,
            max_output_bytes=invocation.max_output_bytes,
            label=invocation.executable,
            stdin=invocation.stdin,
            environment=self._environment(),
        )

    def version(self, executable: CliName) -> str:
        resolved = self._resolve(executable)
        if executable in self._versions:
            return self._versions[executable]
        policy = CLI_POLICIES[executable]
        try:
            completed = self._execute(
                resolved,
                policy.version_args,
                cwd=resolved.parent,
                timeout_seconds=15,
                max_output_bytes=64 * 1024,
                label=executable,
                stdin=b"",
                environment=self._environment(),
            )
        except (ToolExecutionError, ToolTimeoutError) as error:
            raise ToolDependencyError(f"Could not inspect pinned {executable} version") from error
        combined = completed.stdout or completed.stderr
        first_line = combined.decode("utf-8", errors="replace").splitlines()[:1]
        version = first_line[0].strip()[:512] if first_line else "unknown"
        if completed.exit_code != 0 or not version or version == "unknown":
            raise ToolDependencyError(f"Could not inspect pinned {executable} version")
        expected = self._pins[executable].exact_version
        if version != expected:
            raise ToolDependencyError(f"Pinned {executable} version does not match policy")
        self._versions[executable] = version
        return version

    def _resolve(self, executable: CliName) -> Path:
        pin = self._pins.get(executable)
        if pin is None:
            raise ToolDependencyError(f"Required executable pin is unavailable: {executable}")
        if pin.platform != sys.platform or pin.machine != platform.machine():
            raise ToolDependencyError(f"Pinned {executable} platform does not match this host")
        try:
            path = pin.path.expanduser().resolve(strict=True)
        except OSError as error:
            raise ToolDependencyError(
                f"Required allowlisted executable is unavailable: {executable}"
            ) from error
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ToolDependencyError(
                f"Required allowlisted executable is unavailable: {executable}"
            )
        stat = path.stat()
        fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        previous = self._fingerprints.get(executable)
        if previous is not None and previous != fingerprint:
            raise ToolDependencyError(f"Pinned {executable} file identity changed during session")
        if _sha256_file(path) != pin.sha256:
            raise ToolDependencyError(f"Pinned {executable} hash does not match policy")
        previous_path = self._resolved.get(executable)
        if previous_path is not None and previous_path != path:
            raise ToolDependencyError(f"Pinned {executable} real path changed during session")
        self._fingerprints[executable] = fingerprint
        self._resolved[executable] = path
        return path

    def _environment(self) -> dict[str, str]:
        directories = sorted({os.fspath(path.parent) for path in self._resolved.values()})
        return _controlled_environment(os.pathsep.join(directories))

    @staticmethod
    def _execute(
        executable: Path,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        max_output_bytes: int,
        label: str,
        stdin: bytes,
        environment: Mapping[str, str],
    ) -> CliOutcome:
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                (os.fspath(executable), *arguments),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            raise ToolExecutionError(f"Allowlisted {label} invocation could not start") from error
        if process.stdout is None or process.stderr is None:
            _stop_process(process)
            raise ToolExecutionError(f"Allowlisted {label} process pipes were not created")
        if stdin:
            if process.stdin is None:
                _stop_process(process)
                raise ToolExecutionError(f"Allowlisted {label} stdin pipe was not created")
            try:
                process.stdin.write(stdin)
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()
        collector = _BoundedCollector(max_output_bytes)
        readers = (
            threading.Thread(
                target=collector.read,
                args=(process.stdout, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=collector.read,
                args=(process.stderr, "stderr"),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        deadline = started + timeout_seconds
        failure: ToolExecutionError | ToolTimeoutError | None = None
        while process.poll() is None:
            if collector.overflow.is_set():
                failure = ToolExecutionError("Allowlisted process exceeded its output limit")
                _stop_process(process)
                break
            if time.monotonic() >= deadline:
                failure = ToolTimeoutError(f"Allowlisted {label} invocation timed out")
                _stop_process(process)
                break
            time.sleep(0.01)
        for reader in readers:
            reader.join(timeout=1)
        if collector.overflow.is_set() and failure is None:
            failure = ToolExecutionError("Allowlisted process exceeded its output limit")
        if failure is not None:
            raise failure
        return CliOutcome(
            exit_code=process.returncode or 0,
            stdout=bytes(collector.stdout),
            stderr=bytes(collector.stderr),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class _BoundedCollector:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.overflow = threading.Event()
        self._lock = threading.Lock()
        self._total = 0

    def read(self, stream: BinaryIO, target: Literal["stdout", "stderr"]) -> None:
        destination = self.stdout if target == "stdout" else self.stderr
        while chunk := stream.read(64 * 1024):
            with self._lock:
                remaining = self.max_bytes - self._total
                if remaining <= 0:
                    self.overflow.set()
                    return
                destination.extend(chunk[:remaining])
                self._total += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    self.overflow.set()
                    return


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=1)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return


class ToolPolicyError(ToolExecutionError):
    """The internal invocation violates its registered executable policy."""


def build_probe_invocation(source: Path, cwd: Path) -> CliInvocation:
    return CliInvocation(
        executable="ffprobe",
        operation="probe",
        arguments=(
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            _format_whitelist(source),
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            os.fspath(source),
        ),
        cwd=cwd,
        timeout_seconds=30,
    )


def build_download_invocation(
    source_url: str,
    output_template: Path,
    cwd: Path,
    *,
    max_file_bytes: int,
) -> CliInvocation:
    return CliInvocation(
        executable="yt-dlp",
        operation="download",
        arguments=(
            "--ignore-config",
            "--no-config-locations",
            "--no-plugin-dirs",
            "--no-exec",
            "--no-cache-dir",
            "--no-playlist",
            "--no-warnings",
            "--restrict-filenames",
            "--no-part",
            "--no-write-comments",
            "--no-write-info-json",
            "--no-cookies-from-browser",
            "--socket-timeout",
            "30",
            "--retries",
            "1",
            "--max-filesize",
            str(max_file_bytes),
            "--format",
            "best",
            "--output",
            os.fspath(output_template),
            "--batch-file",
            "-",
        ),
        cwd=cwd,
        timeout_seconds=900,
        stdin=f"{source_url}\n".encode(),
    )


def build_audio_invocation(
    source: Path,
    output: Path,
    cwd: Path,
    *,
    sample_rate_hz: int,
    channels: int,
) -> CliInvocation:
    return CliInvocation(
        executable="ffmpeg",
        operation="extract_audio",
        arguments=(
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            _format_whitelist(source),
            "-n",
            "-i",
            os.fspath(source),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate_hz),
            "-ac",
            str(channels),
            os.fspath(output),
        ),
        cwd=cwd,
        timeout_seconds=900,
    )


def build_frame_invocation(
    source: Path,
    output: Path,
    cwd: Path,
    *,
    timestamp_seconds: Decimal,
    width: int,
) -> CliInvocation:
    return CliInvocation(
        executable="ffmpeg",
        operation="extract_frame",
        arguments=(
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            _format_whitelist(source),
            "-n",
            "-ss",
            format(timestamp_seconds, "f"),
            "-i",
            os.fspath(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "3",
            os.fspath(output),
        ),
        cwd=cwd,
        timeout_seconds=120,
    )


def build_ocr_invocation(
    frame: Path,
    cwd: Path,
    *,
    language: Literal["eng", "rus", "eng+rus"],
) -> CliInvocation:
    return CliInvocation(
        executable="tesseract",
        operation="ocr",
        arguments=(
            os.fspath(frame),
            "stdout",
            "--dpi",
            "96",
            "-l",
            language,
            "--psm",
            "6",
        ),
        cwd=cwd,
        timeout_seconds=120,
        max_output_bytes=4 * 1024 * 1024,
    )


def _validate_invocation(invocation: CliInvocation) -> None:
    """Reject any argv not emitted by one of the reviewed typed builders."""

    arguments = invocation.arguments
    expected_executable: dict[CliOperation, CliName] = {
        "probe": "ffprobe",
        "download": "yt-dlp",
        "extract_audio": "ffmpeg",
        "extract_frame": "ffmpeg",
        "ocr": "tesseract",
    }
    if expected_executable[invocation.operation] != invocation.executable:
        raise ToolPolicyError("Typed media operation is bound to another executable")

    if invocation.operation == "probe":
        if len(arguments) != 11 or arguments[:5] != (
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
        ):
            raise ToolPolicyError("ffprobe arguments do not match the reviewed grammar")
        if arguments[5] not in _SAFE_FORMAT_WHITELISTS or arguments[6:10] != (
            "-show_format",
            "-show_streams",
            "-of",
            "json",
        ):
            raise ToolPolicyError("ffprobe arguments do not match the reviewed grammar")
        _require_absolute_input(arguments[-1])
        return

    if invocation.operation == "download":
        fixed = (
            "--ignore-config",
            "--no-config-locations",
            "--no-plugin-dirs",
            "--no-exec",
            "--no-cache-dir",
            "--no-playlist",
            "--no-warnings",
            "--restrict-filenames",
            "--no-part",
            "--no-write-comments",
            "--no-write-info-json",
            "--no-cookies-from-browser",
            "--socket-timeout",
            "30",
            "--retries",
            "1",
            "--max-filesize",
        )
        if len(arguments) != 24 or arguments[:17] != fixed:
            raise ToolPolicyError("yt-dlp arguments do not match the reviewed grammar")
        if not arguments[17].isdigit() or int(arguments[17]) <= 0:
            raise ToolPolicyError("yt-dlp byte limit is invalid")
        if arguments[18] != "--format" or arguments[19] != "best" or arguments[20] != "--output":
            raise ToolPolicyError("yt-dlp output arguments are invalid")
        _require_output_parent(arguments[21], invocation.cwd)
        if arguments[22:] != ("--batch-file", "-"):
            raise ToolPolicyError("yt-dlp URL must be supplied only through stdin")
        if (
            len(invocation.stdin) > 16 * 1024
            or invocation.stdin.count(b"\n") != 1
            or not invocation.stdin.startswith((b"http://", b"https://"))
        ):
            raise ToolPolicyError("yt-dlp stdin does not contain one bounded HTTP(S) URL")
        return

    if invocation.operation == "extract_audio":
        if len(arguments) != 18 or arguments[:5] != (
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
        ):
            raise ToolPolicyError("ffmpeg audio arguments do not match the reviewed grammar")
        if (
            arguments[5] != "-format_whitelist"
            or arguments[6] not in _SAFE_FORMAT_WHITELISTS
            or arguments[7:9] != ("-n", "-i")
        ):
            raise ToolPolicyError("ffmpeg audio input policy is invalid")
        _require_absolute_input(arguments[9])
        if arguments[10:14] != ("-vn", "-acodec", "pcm_s16le", "-ar"):
            raise ToolPolicyError("ffmpeg audio codec arguments are invalid")
        if (
            arguments[14] not in {"16000", "22050", "44100", "48000"}
            or arguments[15] != "-ac"
            or arguments[16] not in {"1", "2"}
        ):
            raise ToolPolicyError("ffmpeg audio channel settings are invalid")
        _require_output(arguments[17], invocation.cwd)
        return

    if invocation.operation == "extract_frame":
        if len(arguments) != 19 or arguments[:5] != (
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
        ):
            raise ToolPolicyError("ffmpeg frame arguments do not match the reviewed grammar")
        if (
            arguments[5] != "-format_whitelist"
            or arguments[6] not in _SAFE_FORMAT_WHITELISTS
            or arguments[7:9] != ("-n", "-ss")
        ):
            raise ToolPolicyError("ffmpeg frame input policy is invalid")
        try:
            timestamp = Decimal(arguments[9])
        except Exception as error:
            raise ToolPolicyError("ffmpeg frame timestamp is invalid") from error
        if not timestamp.is_finite() or timestamp < 0 or arguments[10] != "-i":
            raise ToolPolicyError("ffmpeg frame timestamp is invalid")
        _require_absolute_input(arguments[11])
        if arguments[12:15] != ("-frames:v", "1", "-vf") or not arguments[15].startswith("scale="):
            raise ToolPolicyError("ffmpeg frame selection arguments are invalid")
        scale = arguments[15].removeprefix("scale=").removesuffix(":-2")
        if (
            not scale.isdigit()
            or not 160 <= int(scale) <= 1920
            or arguments[16:18] != ("-q:v", "3")
        ):
            raise ToolPolicyError("ffmpeg frame scale arguments are invalid")
        _require_output(arguments[-1], invocation.cwd)
        return

    if (
        len(arguments) != 8
        or arguments[1:5] != ("stdout", "--dpi", "96", "-l")
        or arguments[5] not in {"eng", "rus", "eng+rus"}
        or arguments[6:] != ("--psm", "6")
    ):
        raise ToolPolicyError("tesseract arguments do not match the reviewed grammar")
    _require_absolute_input(arguments[0])


def _require_absolute_input(value: str) -> None:
    if not Path(value).is_absolute() or "\x00" in value:
        raise ToolPolicyError("Allowlisted input path is invalid")


def _require_output(value: str, cwd: Path) -> None:
    path = Path(value)
    if not path.is_absolute() or path.parent.resolve() != cwd.resolve():
        raise ToolPolicyError("Allowlisted output path escapes its stage")


def _require_output_parent(value: str, cwd: Path) -> None:
    _require_output(value, cwd)


_SAFE_FORMAT_WHITELISTS = frozenset(
    {"avi", "flac", "matroska,webm", "mov", "mp3", "mpeg,mpegvideo", "ogg", "wav"}
)


def _format_whitelist(source: Path) -> str:
    suffix = source.suffix.lower()
    formats = {
        ".avi": "avi",
        ".flac": "flac",
        ".m4a": "mov",
        ".m4v": "mov",
        ".mkv": "matroska,webm",
        ".mov": "mov",
        ".mp3": "mp3",
        ".mp4": "mov",
        ".mpeg": "mpeg,mpegvideo",
        ".mpg": "mpeg,mpegvideo",
        ".ogg": "ogg",
        ".opus": "ogg",
        ".wav": "wav",
        ".webm": "matroska,webm",
    }.get(suffix)
    if formats is None:
        raise ToolPolicyError("Media suffix has no reviewed demuxer policy")
    return formats
