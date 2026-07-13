from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raytsystem.contracts.execution import FilesystemPolicy, RuntimeHealthStatus, WorkspaceMode
from raytsystem.execution.adapters import (
    AdapterDisabledError,
    AdapterLaunchError,
    ClaudeLocalAdapter,
    CodexLocalAdapter,
    FakeAdapter,
    InvalidRuntimeRequest,
    ManagedCwd,
    ProcessSupervisor,
    RuntimeRequest,
    controlled_runtime_environment,
)


def _task_request(
    tmp_path: Path,
    *,
    prompt: str = "Implement the reviewed task.",
    session_id: str | None = None,
    model: str | None = None,
) -> RuntimeRequest:
    root = tmp_path / "repo"
    repo = root / ".raytsystem" / "workspaces" / "workspace_one" / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return RuntimeRequest(
        prompt=prompt,
        cwd=ManagedCwd.task_workspace(
            root,
            ".raytsystem/workspaces/workspace_one/repo",
        ),
        filesystem_policy=FilesystemPolicy(),
        model=model,
        provider_session_id=session_id,
    )


def _readonly_request(tmp_path: Path) -> RuntimeRequest:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    return RuntimeRequest(
        prompt="Review only.",
        cwd=ManagedCwd.readonly_project_root(root),
        filesystem_policy=FilesystemPolicy(
            mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
            allow_staged_write=False,
        ),
    )


def test_managed_cwd_rejects_escape_external_and_mismatched_policy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    with pytest.raises(InvalidRuntimeRequest, match="raytsystem/workspaces"):
        ManagedCwd.task_workspace(root, "elsewhere/workspace_one")
    with pytest.raises(InvalidRuntimeRequest, match="normalized relative"):
        ManagedCwd.task_workspace(root, ".raytsystem/workspaces/workspace_one/../other")

    cwd = ManagedCwd.task_workspace(root, ".raytsystem/workspaces/workspace_one/repo")
    with pytest.raises(InvalidRuntimeRequest, match="modes do not match"):
        RuntimeRequest(
            prompt="Review.",
            cwd=cwd,
            filesystem_policy=FilesystemPolicy(
                mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
                allow_staged_write=False,
            ),
        )


def test_managed_cwd_fails_closed_if_component_becomes_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    workspaces = root / ".raytsystem" / "workspaces"
    workspaces.mkdir(parents=True)
    (workspaces / "workspace_one").symlink_to(outside, target_is_directory=True)
    cwd = ManagedCwd.task_workspace(root, ".raytsystem/workspaces/workspace_one")

    with pytest.raises(AdapterLaunchError, match="symlink"):
        cwd.assert_safe_for_launch()


def test_runtime_request_rejects_flag_and_nul_injection(tmp_path: Path) -> None:
    request = _task_request(tmp_path)
    with pytest.raises(InvalidRuntimeRequest, match="model"):
        RuntimeRequest(
            prompt=request.prompt,
            cwd=request.cwd,
            filesystem_policy=request.filesystem_policy,
            model="--danger-full-access",
        )
    with pytest.raises(InvalidRuntimeRequest, match="session"):
        RuntimeRequest(
            prompt=request.prompt,
            cwd=request.cwd,
            filesystem_policy=request.filesystem_policy,
            provider_session_id="--dangerously-bypass-approvals-and-sandbox",
        )
    with pytest.raises(InvalidRuntimeRequest, match="NUL"):
        RuntimeRequest(
            prompt="unsafe\x00prompt",
            cwd=request.cwd,
            filesystem_policy=request.filesystem_policy,
        )


def test_codex_health_is_typed_and_does_not_probe_while_disabled() -> None:
    resolver_calls = 0

    def forbidden_resolver(_name: str) -> str | None:
        nonlocal resolver_calls
        resolver_calls += 1
        raise AssertionError("disabled adapters must not inspect the host")

    checked_at = datetime(2026, 7, 12, 1, 2, tzinfo=UTC)
    health = CodexLocalAdapter(executable_resolver=forbidden_resolver).health_check(
        checked_at=checked_at
    )

    assert health.status is RuntimeHealthStatus.DISABLED
    assert health.reason_code == "feature_disabled"
    assert health.checked_at == checked_at
    assert resolver_calls == 0


def test_codex_health_reports_available_and_degraded_without_running_in_tests() -> None:
    version_argv: list[tuple[str, ...]] = []

    def version_probe(argv: tuple[str, ...]) -> str | None:
        version_argv.append(argv)
        return "codex-cli 0.test"

    available = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
        version_probe=version_probe,
    ).health_check()
    degraded = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
        version_probe=lambda _argv: None,
    ).health_check()

    assert available.status is RuntimeHealthStatus.AVAILABLE
    assert available.version == "codex-cli 0.test"
    assert version_argv == [("/managed/bin/codex", "--version")]
    assert degraded.status is RuntimeHealthStatus.DEGRADED
    assert degraded.reason_code == "version_probe_failed"


def test_codex_command_is_fixed_sandboxed_and_prompt_is_stdin(tmp_path: Path) -> None:
    prompt = "Do work; $(touch /tmp/not-executed)"
    request = _task_request(tmp_path, prompt=prompt, model="gpt-5.4")
    adapter = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
    )
    plan = adapter.build_command(request)

    assert plan.argv == (
        "/managed/bin/codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "-C",
        str(request.cwd.path),
        "--ignore-user-config",
        "-c",
        "shell_environment_policy.inherit=none",
        "--model",
        "gpt-5.4",
        "-",
    )
    assert plan.stdin == prompt.encode()
    assert prompt not in plan.argv
    assert not any("dangerously" in item for item in plan.argv)


def test_codex_resume_and_readonly_commands_preserve_safety(tmp_path: Path) -> None:
    adapter = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
    )
    resumed = adapter.build_command(
        _task_request(tmp_path, session_id="019f5310-cb5e-70f0-aa07-18e5dc7851dc")
    )
    readonly = adapter.build_command(_readonly_request(tmp_path / "readonly"))

    assert resumed.argv[-3:] == (
        "resume",
        "019f5310-cb5e-70f0-aa07-18e5dc7851dc",
        "-",
    )
    sandbox_index = readonly.argv.index("--sandbox")
    assert readonly.argv[sandbox_index + 1] == "read-only"
    assert readonly.argv[readonly.argv.index("-C") + 1] == str(readonly.cwd.path)


def test_readonly_policy_can_use_managed_workspace_cwd(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    repo = root / ".raytsystem" / "workspaces" / "workspace_one" / "repo"
    repo.mkdir(parents=True)
    cwd = ManagedCwd.managed_workspace(
        root,
        ".raytsystem/workspaces/workspace_one/repo",
        mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
    )
    request = RuntimeRequest(
        prompt="Review the bounded context.",
        cwd=cwd,
        filesystem_policy=FilesystemPolicy(
            mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
            allow_staged_write=False,
        ),
    )
    plan = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
    ).build_command(request)

    assert plan.argv[plan.argv.index("--sandbox") + 1] == "read-only"
    assert plan.argv[plan.argv.index("-C") + 1] == str(repo)


def test_codex_is_disabled_until_explicitly_enabled(tmp_path: Path) -> None:
    with pytest.raises(AdapterDisabledError, match="disabled"):
        CodexLocalAdapter().build_command(_task_request(tmp_path))


def test_claude_requires_feature_and_egress_gates_before_host_probe(tmp_path: Path) -> None:
    probes = 0

    def forbidden_resolver(_name: str) -> str | None:
        nonlocal probes
        probes += 1
        raise AssertionError("closed gates must not inspect the host")

    request = _task_request(tmp_path)
    feature_disabled = ClaudeLocalAdapter(executable_resolver=forbidden_resolver)
    egress_denied = ClaudeLocalAdapter(
        enabled=True,
        executable_resolver=forbidden_resolver,
    )

    assert feature_disabled.health_check().reason_code == "feature_disabled"
    assert egress_denied.health_check().reason_code == "egress_policy_denied"
    assert probes == 0
    with pytest.raises(AdapterDisabledError, match="feature"):
        feature_disabled.build_command(request)
    with pytest.raises(AdapterDisabledError, match="egress"):
        egress_denied.build_command(request)


def test_claude_command_is_bare_bounded_and_never_bypasses_permissions(
    tmp_path: Path,
) -> None:
    prompt = "Apply the approved patch."
    request = _task_request(
        tmp_path,
        prompt=prompt,
        session_id="019f5310-cb5e-70f0-aa07-18e5dc7851dc",
        model="claude-sonnet-4-6",
    )
    adapter = ClaudeLocalAdapter(
        enabled=True,
        egress_allowed=True,
        executable_resolver=lambda _name: "/managed/bin/claude",
    )
    plan = adapter.build_command(request)

    assert plan.argv[0] == "/managed/bin/claude"
    assert "--print" in plan.argv
    assert "--bare" in plan.argv
    assert plan.argv[plan.argv.index("--permission-mode") + 1] == "dontAsk"
    assert plan.argv[plan.argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert plan.argv[plan.argv.index("--resume") + 1] == request.provider_session_id
    assert plan.stdin == prompt.encode()
    assert prompt not in plan.argv
    assert not any("dangerously" in item for item in plan.argv)
    assert "bypassPermissions" not in plan.argv
    assert "Bash" not in plan.argv[plan.argv.index("--tools") + 1]


def test_fake_adapter_is_deterministic_and_no_egress(tmp_path: Path) -> None:
    request = _task_request(tmp_path, prompt="Stable fake request")
    adapter = FakeAdapter()

    first = asyncio.run(adapter.execute(request))
    second = asyncio.run(adapter.execute(request))
    payload = json.loads(first.stdout)

    assert first == second
    assert first.ok
    assert payload["adapter_id"] == "adapter_fake"
    assert payload["status"] == "completed"
    assert "Stable fake request" not in first.stdout
    assert adapter.build_command(request).argv == ("raytsystem-fake-runtime", "--json")


def test_process_supervisor_uses_exec_without_shell_and_redacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from raytsystem.execution import adapters as adapters_module

    request = _task_request(tmp_path, prompt="stdin payload")
    plan = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
    ).build_command(request)
    captured: dict[str, object] = {}

    class FakeStdin:
        def write(self, data: bytes) -> None:
            captured["stdin"] = data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"token=secret")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode: int | None = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    async def fake_spawn(*argv: str, **kwargs: object) -> FakeProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(adapters_module.asyncio, "create_subprocess_exec", fake_spawn)
    supervisor = ProcessSupervisor(redactor=lambda _value: "[filtered]")
    outcome = asyncio.run(supervisor.run(plan))

    assert outcome.ok
    assert outcome.stdout == "[filtered]"
    assert captured["argv"] == plan.argv
    assert captured["stdin"] == plan.stdin
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    assert kwargs["env"] == controlled_runtime_environment()
    assert kwargs["cwd"] == str(plan.cwd.path)


def test_process_supervisor_enforces_output_bound_and_terminates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from raytsystem.execution import adapters as adapters_module

    request = _task_request(tmp_path)
    plan = CodexLocalAdapter(
        enabled=True,
        executable_resolver=lambda _name: "/managed/bin/codex",
    ).build_command(request)
    terminated = False

    class FakeStdin:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"x" * 100)
            self.stderr.feed_eof()
            self.returncode: int | None = None
            self._stopped = asyncio.Event()

        async def wait(self) -> int:
            await self._stopped.wait()
            return self.returncode or 0

        def terminate(self) -> None:
            nonlocal terminated
            terminated = True
            self.returncode = -15
            self.stdout.feed_eof()
            self._stopped.set()

        def kill(self) -> None:
            self.returncode = -9
            self._stopped.set()

    async def fake_spawn(*_argv: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(adapters_module.asyncio, "create_subprocess_exec", fake_spawn)
    outcome = asyncio.run(
        ProcessSupervisor(max_output_bytes=16, redactor=lambda value: value).run(plan)
    )

    assert outcome.termination_reason == "output_limit"
    assert outcome.exit_code is None
    assert len(outcome.stdout.encode()) == 16
    assert terminated
