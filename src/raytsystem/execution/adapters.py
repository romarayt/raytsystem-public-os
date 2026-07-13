from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from raytsystem.contracts.execution import (
    FilesystemPolicy,
    RuntimeHealth,
    RuntimeHealthStatus,
    WorkspaceMode,
)
from raytsystem.security import SecretScanner

_MAX_PROMPT_BYTES = 1024 * 1024
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WORKSPACE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_RUNTIME_FLAGS = frozenset(
    {
        "--allow-dangerously-skip-permissions",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--dangerously-skip-permissions",
    }
)
_EXPECTED_EXECUTABLES = {
    "adapter_claude_code": "claude",
    "adapter_codex_local": "codex",
    "adapter_fake": "raytsystem-fake-runtime",
}
_RUNTIME_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSL_CERT_FILE",
)

ExecutableResolver = Callable[[str], str | None]
VersionProbe = Callable[[tuple[str, ...]], str | None]
Redactor = Callable[[str], str]
TerminationReason = Literal["completed", "timeout", "output_limit", "cancelled"]


class AdapterError(RuntimeError):
    """Base error for a denied or invalid runtime operation."""


class AdapterDisabledError(AdapterError):
    """Raised when an adapter has not passed its feature and policy gates."""


class InvalidRuntimeRequest(AdapterError):
    """Raised when runtime input cannot be represented safely."""


class AdapterLaunchError(AdapterError):
    """Raised when a reviewed command plan cannot be started."""


@dataclass(frozen=True)
class ManagedCwd:
    """Capability token for one reviewed runtime working directory.

    Callers can select the project root in read-only mode or a repository path
    below ``.raytsystem/workspaces``. Arbitrary and external paths are deliberately
    not representable.
    """

    project_root: Path
    token: str
    mode: WorkspaceMode

    def __post_init__(self) -> None:
        project_root = Path(os.path.abspath(self.project_root))
        object.__setattr__(self, "project_root", project_root)
        if self.mode is WorkspaceMode.WORKSPACE_ROOT_READONLY and self.token == ".":
            return
        if self.mode not in {
            WorkspaceMode.TASK_WORKTREE,
            WorkspaceMode.WORKSPACE_ROOT_READONLY,
        }:
            raise InvalidRuntimeRequest(
                "External runtime roots require a separate approved adapter"
            )

        pure = _safe_relative_path(self.token)
        parts = pure.parts
        if len(parts) < 3 or parts[:2] != (".raytsystem", "workspaces"):
            raise InvalidRuntimeRequest("Task cwd must stay below .raytsystem/workspaces")
        if not _WORKSPACE_COMPONENT_PATTERN.fullmatch(parts[2]):
            raise InvalidRuntimeRequest("Workspace ID in cwd token is invalid")
        candidate = Path(os.path.abspath(project_root / Path(*parts)))
        managed_root = Path(os.path.abspath(project_root / ".raytsystem" / "workspaces"))
        try:
            candidate.relative_to(managed_root)
        except ValueError as error:
            raise InvalidRuntimeRequest("Task cwd escapes the managed workspace root") from error

    @classmethod
    def readonly_project_root(cls, project_root: Path) -> ManagedCwd:
        return cls(
            project_root=project_root,
            token=".",
            mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
        )

    @classmethod
    def task_workspace(cls, project_root: Path, cwd_token: str) -> ManagedCwd:
        return cls.managed_workspace(
            project_root,
            cwd_token,
            mode=WorkspaceMode.TASK_WORKTREE,
        )

    @classmethod
    def managed_workspace(
        cls,
        project_root: Path,
        cwd_token: str,
        *,
        mode: WorkspaceMode,
    ) -> ManagedCwd:
        return cls(
            project_root=project_root,
            token=cwd_token,
            mode=mode,
        )

    @property
    def path(self) -> Path:
        if self.token == ".":
            return self.project_root
        return self.project_root / Path(*PurePosixPath(self.token).parts)

    def assert_safe_for_launch(self) -> None:
        """Revalidate directory shape immediately before process creation."""

        root = self.project_root
        candidate = self.path
        if not root.is_dir() or not candidate.is_dir():
            raise AdapterLaunchError("Managed runtime cwd is missing or not a directory")
        relative = candidate.relative_to(root)
        current = root
        for component in relative.parts:
            current = current / component
            try:
                mode = current.lstat().st_mode
            except OSError as error:
                raise AdapterLaunchError("Managed runtime cwd changed before launch") from error
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise AdapterLaunchError("Managed runtime cwd contains a symlink or non-directory")


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise InvalidRuntimeRequest("Runtime cwd token is malformed")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise InvalidRuntimeRequest("Runtime cwd token must be a normalized relative path")
    if pure.as_posix() != value:
        raise InvalidRuntimeRequest("Runtime cwd token must use canonical POSIX form")
    return pure


@dataclass(frozen=True)
class RuntimeRequest:
    prompt: str
    cwd: ManagedCwd
    filesystem_policy: FilesystemPolicy
    model: str | None = None
    provider_session_id: str | None = None

    def __post_init__(self) -> None:
        prompt_bytes = self.prompt.encode("utf-8")
        if not self.prompt.strip() or "\x00" in self.prompt:
            raise InvalidRuntimeRequest("Runtime prompt must be non-empty and contain no NUL")
        if len(prompt_bytes) > _MAX_PROMPT_BYTES:
            raise InvalidRuntimeRequest("Runtime prompt exceeds the bounded stdin limit")
        if self.cwd.mode is not self.filesystem_policy.mode:
            raise InvalidRuntimeRequest("Runtime cwd and filesystem policy modes do not match")
        if self.filesystem_policy.mode is WorkspaceMode.APPROVED_EXTERNAL_ROOT:
            raise InvalidRuntimeRequest("External roots are unsupported by local CLI adapters")
        if self.model is not None and not _MODEL_PATTERN.fullmatch(self.model):
            raise InvalidRuntimeRequest("Runtime model identifier is invalid")
        if self.provider_session_id is not None and not _SESSION_PATTERN.fullmatch(
            self.provider_session_id
        ):
            raise InvalidRuntimeRequest("Provider session identifier is invalid")

    @property
    def stdin_bytes(self) -> bytes:
        return self.prompt.encode("utf-8")


@dataclass(frozen=True)
class CommandPlan:
    """Reviewed argv plus capability-bound cwd and stdin.

    There is intentionally no shell string or caller-supplied environment.
    """

    adapter_id: str
    argv: tuple[str, ...]
    cwd: ManagedCwd
    stdin: bytes

    def __post_init__(self) -> None:
        validate_safe_command_argv(self.adapter_id, self.argv)
        if len(self.stdin) > _MAX_PROMPT_BYTES:
            raise InvalidRuntimeRequest("Runtime stdin exceeds the configured limit")

    @property
    def safe_command(self) -> tuple[str, ...]:
        return self.argv


def validate_safe_command_argv(adapter_id: str, argv: tuple[str, ...]) -> None:
    """Validate the complete fixed argv grammar at preparation and persistence boundaries."""

    expected = _EXPECTED_EXECUTABLES.get(adapter_id)
    if expected is None:
        raise InvalidRuntimeRequest("Unknown runtime adapter command plan")
    if not argv or Path(argv[0]).name != expected:
        raise InvalidRuntimeRequest("Runtime executable does not match the adapter")
    if any(not item or "\x00" in item or len(item) > 4_096 for item in argv):
        raise InvalidRuntimeRequest("Runtime argv contains an invalid item")
    if _FORBIDDEN_RUNTIME_FLAGS.intersection(argv):
        raise InvalidRuntimeRequest("Dangerous runtime flags are forbidden")
    if adapter_id == FakeAdapter.adapter_id:
        if argv != (argv[0], "--json"):
            raise InvalidRuntimeRequest("Fake runtime argv is not allowlisted")
        return
    if adapter_id == CodexLocalAdapter.adapter_id:
        _validate_codex_argv(argv)
        return
    if adapter_id == ClaudeLocalAdapter.adapter_id:
        _validate_claude_argv(argv)
        return
    raise InvalidRuntimeRequest("Runtime adapter has no command grammar")


def _validate_codex_argv(argv: tuple[str, ...]) -> None:
    required = (
        "exec",
        "--json",
        "--sandbox",
    )
    if argv[1:4] != required or len(argv) < 11:
        raise InvalidRuntimeRequest("Codex argv prefix is not allowlisted")
    if argv[4] not in {"read-only", "workspace-write"}:
        raise InvalidRuntimeRequest("Codex sandbox mode is not allowlisted")
    if argv[5] != "-C" or not Path(argv[6]).is_absolute():
        raise InvalidRuntimeRequest("Codex cwd binding is invalid")
    if argv[7:10] != (
        "--ignore-user-config",
        "-c",
        "shell_environment_policy.inherit=none",
    ):
        raise InvalidRuntimeRequest("Codex safety configuration is incomplete")
    tail = argv[10:]
    if tail[:1] == ("--model",):
        if len(tail) < 3 or _MODEL_PATTERN.fullmatch(tail[1]) is None:
            raise InvalidRuntimeRequest("Codex model argument is invalid")
        tail = tail[2:]
    if tail == ("-",):
        return
    if (
        len(tail) == 3
        and tail[0] == "resume"
        and _SESSION_PATTERN.fullmatch(tail[1]) is not None
        and tail[2] == "-"
    ):
        return
    raise InvalidRuntimeRequest("Codex invocation tail is not allowlisted")


def _validate_claude_argv(argv: tuple[str, ...]) -> None:
    required = (
        "--print",
        "--bare",
        "--output-format",
        "stream-json",
        "--input-format",
        "text",
        "--permission-mode",
        "dontAsk",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--tools",
    )
    if argv[1:14] != required or len(argv) < 15:
        raise InvalidRuntimeRequest("Claude argv prefix is not allowlisted")
    if argv[14] not in {"Read,Glob,Grep", "Read,Glob,Grep,Edit,Write"}:
        raise InvalidRuntimeRequest("Claude tool allowlist is invalid")
    tail = argv[15:]
    if tail[:1] == ("--model",):
        if len(tail) < 2 or _MODEL_PATTERN.fullmatch(tail[1]) is None:
            raise InvalidRuntimeRequest("Claude model argument is invalid")
        tail = tail[2:]
    if tail[:1] == ("--resume",):
        if len(tail) != 2 or _SESSION_PATTERN.fullmatch(tail[1]) is None:
            raise InvalidRuntimeRequest("Claude session argument is invalid")
        tail = ()
    if tail:
        raise InvalidRuntimeRequest("Claude invocation tail is not allowlisted")


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    termination_reason: TerminationReason
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.termination_reason == "completed" and self.exit_code == 0


class RuntimeAdapter(Protocol):
    adapter_id: str

    def health_check(self, *, checked_at: datetime | None = None) -> RuntimeHealth: ...

    def build_command(self, request: RuntimeRequest) -> CommandPlan: ...

    async def execute(
        self,
        request: RuntimeRequest,
        *,
        supervisor: ProcessSupervisor | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome: ...


def _default_version_probe(argv: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0][:256] if output else None


def _checked_at(value: datetime | None) -> datetime:
    return datetime.now(UTC) if value is None else value.astimezone(UTC)


def controlled_runtime_environment() -> dict[str, str]:
    """Return the fixed host subset required to start reviewed local CLIs."""

    environment = {
        key: value
        for key in _RUNTIME_ENV_ALLOWLIST
        if (value := os.environ.get(key)) is not None and "\x00" not in value
    }
    environment["NO_COLOR"] = "1"
    return environment


def _cli_health(
    *,
    adapter_id: str,
    executable_name: str,
    capabilities: tuple[str, ...],
    enabled: bool,
    disabled_reason: str,
    resolver: ExecutableResolver,
    version_probe: VersionProbe,
    checked_at: datetime | None,
) -> RuntimeHealth:
    timestamp = _checked_at(checked_at)
    if not enabled:
        return RuntimeHealth(
            runtime_adapter_id=adapter_id,
            status=RuntimeHealthStatus.DISABLED,
            executable=executable_name,
            capabilities=capabilities,
            reason_code=disabled_reason,
            checked_at=timestamp,
        )
    executable = resolver(executable_name)
    if executable is None:
        return RuntimeHealth(
            runtime_adapter_id=adapter_id,
            status=RuntimeHealthStatus.UNAVAILABLE,
            executable=executable_name,
            capabilities=capabilities,
            reason_code="executable_not_found",
            checked_at=timestamp,
        )
    version = version_probe((executable, "--version"))
    return RuntimeHealth(
        runtime_adapter_id=adapter_id,
        status=(
            RuntimeHealthStatus.AVAILABLE if version is not None else RuntimeHealthStatus.DEGRADED
        ),
        executable=executable,
        version=version,
        capabilities=capabilities,
        reason_code=None if version is not None else "version_probe_failed",
        checked_at=timestamp,
    )


class FakeAdapter:
    """Deterministic, no-egress adapter for orchestration and recovery tests."""

    adapter_id = "adapter_fake"
    _capabilities = ("read_workspace", "staged_write", "resume", "deterministic")

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def health_check(self, *, checked_at: datetime | None = None) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_adapter_id=self.adapter_id,
            status=(
                RuntimeHealthStatus.AVAILABLE if self.enabled else RuntimeHealthStatus.DISABLED
            ),
            executable="in_process",
            version="1.0.0",
            capabilities=self._capabilities,
            reason_code=None if self.enabled else "feature_disabled",
            checked_at=_checked_at(checked_at),
        )

    def build_command(self, request: RuntimeRequest) -> CommandPlan:
        if not self.enabled:
            raise AdapterDisabledError("Fake runtime adapter is disabled")
        return CommandPlan(
            adapter_id=self.adapter_id,
            argv=("raytsystem-fake-runtime", "--json"),
            cwd=request.cwd,
            stdin=request.stdin_bytes,
        )

    async def execute(
        self,
        request: RuntimeRequest,
        *,
        supervisor: ProcessSupervisor | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome:
        del supervisor
        self.build_command(request)
        if cancel_event is not None and cancel_event.is_set():
            return ProcessOutcome(
                exit_code=None,
                stdout="",
                stderr="",
                termination_reason="cancelled",
                duration_ms=0,
            )
        material = {
            "adapter_id": self.adapter_id,
            "cwd_token": request.cwd.token,
            "filesystem_policy": request.filesystem_policy.model_dump(mode="json"),
            "model": request.model,
            "prompt_sha256": hashlib.sha256(request.stdin_bytes).hexdigest(),
            "provider_session_id": request.provider_session_id,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        payload = {
            "adapter_id": self.adapter_id,
            "prompt_sha256": material["prompt_sha256"],
            "resumed": request.provider_session_id is not None,
            "session_id": f"fake_{digest[:32]}",
            "status": "completed",
        }
        return ProcessOutcome(
            exit_code=0,
            stdout=json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            stderr="",
            termination_reason="completed",
            duration_ms=0,
        )


class CodexLocalAdapter:
    adapter_id = "adapter_codex_local"
    _capabilities = ("read_workspace", "staged_write", "review", "resume", "jsonl")

    def __init__(
        self,
        *,
        enabled: bool = False,
        executable_resolver: ExecutableResolver = shutil.which,
        version_probe: VersionProbe = _default_version_probe,
    ) -> None:
        self.enabled = enabled
        self._executable_resolver = executable_resolver
        self._version_probe = version_probe

    def health_check(self, *, checked_at: datetime | None = None) -> RuntimeHealth:
        return _cli_health(
            adapter_id=self.adapter_id,
            executable_name="codex",
            capabilities=self._capabilities,
            enabled=self.enabled,
            disabled_reason="feature_disabled",
            resolver=self._executable_resolver,
            version_probe=self._version_probe,
            checked_at=checked_at,
        )

    def build_command(self, request: RuntimeRequest) -> CommandPlan:
        if not self.enabled:
            raise AdapterDisabledError("Codex local adapter is disabled")
        executable = self._executable_resolver("codex")
        if executable is None:
            raise AdapterLaunchError("Codex executable is unavailable")
        sandbox = (
            "workspace-write"
            if request.cwd.mode is WorkspaceMode.TASK_WORKTREE
            and request.filesystem_policy.allow_staged_write
            else "read-only"
        )
        argv = [
            executable,
            "exec",
            "--json",
            "--sandbox",
            sandbox,
            "-C",
            os.fspath(request.cwd.path),
            "--ignore-user-config",
            "-c",
            "shell_environment_policy.inherit=none",
        ]
        if request.model is not None:
            argv.extend(("--model", request.model))
        if request.provider_session_id is None:
            argv.append("-")
        else:
            argv.extend(("resume", request.provider_session_id, "-"))
        return CommandPlan(
            adapter_id=self.adapter_id,
            argv=tuple(argv),
            cwd=request.cwd,
            stdin=request.stdin_bytes,
        )

    async def execute(
        self,
        request: RuntimeRequest,
        *,
        supervisor: ProcessSupervisor | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome:
        runner = ProcessSupervisor() if supervisor is None else supervisor
        return await runner.run(self.build_command(request), cancel_event=cancel_event)


class ClaudeLocalAdapter:
    adapter_id = "adapter_claude_code"
    _capabilities = ("read_workspace", "staged_write", "review", "resume", "jsonl")

    def __init__(
        self,
        *,
        enabled: bool = False,
        egress_allowed: bool = False,
        executable_resolver: ExecutableResolver = shutil.which,
        version_probe: VersionProbe = _default_version_probe,
    ) -> None:
        self.enabled = enabled
        self.egress_allowed = egress_allowed
        self._executable_resolver = executable_resolver
        self._version_probe = version_probe

    @property
    def _gate_open(self) -> bool:
        return self.enabled and self.egress_allowed

    @property
    def _disabled_reason(self) -> str:
        return "feature_disabled" if not self.enabled else "egress_policy_denied"

    def health_check(self, *, checked_at: datetime | None = None) -> RuntimeHealth:
        return _cli_health(
            adapter_id=self.adapter_id,
            executable_name="claude",
            capabilities=self._capabilities,
            enabled=self._gate_open,
            disabled_reason=self._disabled_reason,
            resolver=self._executable_resolver,
            version_probe=self._version_probe,
            checked_at=checked_at,
        )

    def build_command(self, request: RuntimeRequest) -> CommandPlan:
        if not self.enabled:
            raise AdapterDisabledError("Claude local adapter feature is disabled")
        if not self.egress_allowed:
            raise AdapterDisabledError("Claude local adapter egress is not approved")
        executable = self._executable_resolver("claude")
        if executable is None:
            raise AdapterLaunchError("Claude executable is unavailable")
        argv = [
            executable,
            "--print",
            "--bare",
            "--output-format",
            "stream-json",
            "--input-format",
            "text",
            "--permission-mode",
            "dontAsk",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            (
                "Read,Glob,Grep,Edit,Write"
                if request.filesystem_policy.allow_staged_write
                else "Read,Glob,Grep"
            ),
        ]
        if request.model is not None:
            argv.extend(("--model", request.model))
        if request.provider_session_id is not None:
            argv.extend(("--resume", request.provider_session_id))
        return CommandPlan(
            adapter_id=self.adapter_id,
            argv=tuple(argv),
            cwd=request.cwd,
            stdin=request.stdin_bytes,
        )

    async def execute(
        self,
        request: RuntimeRequest,
        *,
        supervisor: ProcessSupervisor | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome:
        runner = ProcessSupervisor() if supervisor is None else supervisor
        return await runner.run(self.build_command(request), cancel_event=cancel_event)


def redact_sensitive_output(value: str) -> str:
    if not value:
        return value
    decision = SecretScanner().scan(value.encode("utf-8"))
    if decision.blocks_processing:
        return "[REDACTED: sensitive runtime output]"
    return value


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.consumed = 0
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.overflow = asyncio.Event()

    def append(self, stream: Literal["stdout", "stderr"], chunk: bytes) -> None:
        remaining = max(0, self.limit - self.consumed)
        target = self.stdout if stream == "stdout" else self.stderr
        target.extend(chunk[:remaining])
        self.consumed += min(remaining, len(chunk))
        if len(chunk) > remaining:
            self.overflow.set()


class ProcessSupervisor:
    """No-shell asyncio supervisor with output, time, and cancellation bounds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 3600.0,
        max_output_bytes: int = 4 * 1024 * 1024,
        terminate_grace_seconds: float = 3.0,
        redactor: Redactor = redact_sensitive_output,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 24 * 60 * 60:
            raise ValueError("Runtime timeout must be between zero and one day")
        if max_output_bytes <= 0 or max_output_bytes > 64 * 1024 * 1024:
            raise ValueError("Runtime output bound is invalid")
        if terminate_grace_seconds <= 0 or terminate_grace_seconds > 60:
            raise ValueError("Runtime termination grace is invalid")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.terminate_grace_seconds = terminate_grace_seconds
        self.redactor = redactor

    async def run(
        self,
        plan: CommandPlan,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome:
        if plan.adapter_id == FakeAdapter.adapter_id:
            raise AdapterLaunchError("The in-process fake adapter cannot be spawned")
        plan.cwd.assert_safe_for_launch()
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *plan.argv,
                cwd=os.fspath(plan.cwd.path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=controlled_runtime_environment(),
            )
        except OSError as error:
            raise AdapterLaunchError("Reviewed runtime executable could not be started") from error
        if process.stdin is None or process.stdout is None or process.stderr is None:
            await self._stop(process)
            raise AdapterLaunchError("Runtime process pipes were not created")

        output = _BoundedOutput(self.max_output_bytes)
        stdout_task = asyncio.create_task(self._drain(process.stdout, "stdout", output))
        stderr_task = asyncio.create_task(self._drain(process.stderr, "stderr", output))
        stdin_task = asyncio.create_task(self._write_stdin(process.stdin, plan.stdin))
        wait_task = asyncio.create_task(process.wait())
        overflow_task = asyncio.create_task(output.overflow.wait())
        cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        watcher_tasks: set[asyncio.Task[Any]] = {wait_task, overflow_task}
        if cancel_task is not None:
            watcher_tasks.add(cancel_task)
        reason: TerminationReason = "completed"
        try:
            done, _ = await asyncio.wait(
                watcher_tasks,
                timeout=self.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                reason = "timeout"
                await self._stop(process)
            elif output.overflow.is_set():
                reason = "output_limit"
                await self._stop(process)
            elif cancel_event is not None and cancel_event.is_set():
                reason = "cancelled"
                await self._stop(process)
            else:
                await wait_task
            await asyncio.gather(stdout_task, stderr_task, stdin_task, return_exceptions=True)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(process))
            raise
        finally:
            for task in (stdout_task, stderr_task, stdin_task, overflow_task, cancel_task):
                if task is not None and not task.done():
                    task.cancel()
            cleanup = tuple(
                task
                for task in (stdout_task, stderr_task, stdin_task, overflow_task, cancel_task)
                if task is not None
            )
            await asyncio.gather(*cleanup, return_exceptions=True)

        exit_code = process.returncode if reason == "completed" else None
        return ProcessOutcome(
            exit_code=exit_code,
            stdout=self._redact(bytes(output.stdout)),
            stderr=self._redact(bytes(output.stderr)),
            termination_reason=reason,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    async def _drain(
        self,
        reader: asyncio.StreamReader,
        stream: Literal["stdout", "stderr"],
        output: _BoundedOutput,
    ) -> None:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                return
            output.append(stream, chunk)

    @staticmethod
    async def _write_stdin(writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        self._signal(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=self.terminate_grace_seconds)
            return
        except TimeoutError:
            pass
        self._signal(process, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            await process.wait()

    @staticmethod
    def _signal(process: asyncio.subprocess.Process, requested: signal.Signals) -> None:
        process_id = getattr(process, "pid", None)
        if os.name == "posix" and isinstance(process_id, int) and process_id > 0:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process_id, requested)
            return
        with contextlib.suppress(ProcessLookupError):
            if requested is signal.SIGTERM:
                process.terminate()
            else:
                process.kill()

    def _redact(self, value: bytes) -> str:
        decoded = value.decode("utf-8", errors="replace")
        try:
            redacted = self.redactor(decoded)
        except Exception:
            return "[REDACTED: runtime redaction failure]"
        encoded = redacted.encode("utf-8")[: self.max_output_bytes]
        return encoded.decode("utf-8", errors="ignore")
