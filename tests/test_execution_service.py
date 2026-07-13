from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from raytsystem.contracts import TaskStatus, derive_id, sha256_hex
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    DigitalEmployee,
    EmployeeStatus,
    ExecutionApproval,
    ExecutionInvocation,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionSession,
    ExecutionSessionStatus,
    FilesystemPolicy,
    GraphPolicy,
    HeartbeatPolicy,
    RuntimeHealth,
    RuntimeHealthStatus,
    TaskAssignment,
    TaskGraphScope,
    TaskLease,
    TaskLeaseStatus,
    TaskWorkspace,
    TokenBudget,
    WorkspaceMode,
)
from raytsystem.execution.adapters import (
    CommandPlan,
    FakeAdapter,
    ProcessOutcome,
    ProcessSupervisor,
    RuntimeRequest,
)
from raytsystem.execution.config import ExecutionConfig, FeatureFlags
from raytsystem.execution.employees import EmployeeCatalogSnapshot
from raytsystem.execution.service import ExecutionService
from raytsystem.execution.workspace import WorkspaceGraphError, WorkspacePreparation
from raytsystem.tasking import TaskService

NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)
HASH = "a" * 64
COMMIT = "b" * 40


class FixtureEmployees:
    def __init__(self, employee: DigitalEmployee) -> None:
        self.employee = employee

    def load(self) -> EmployeeCatalogSnapshot:
        return EmployeeCatalogSnapshot(HASH, (self.employee,))


class FixtureEmergency:
    def assert_runtime_allowed(self) -> None:
        return None


class FixtureWorkspaces:
    def __init__(self, root: Path, tasks: TaskService, *, stale: bool = False) -> None:
        self.root = root
        self.tasks = tasks
        self.stale = stale

    def prepare(
        self,
        task_id: str,
        agent_id: str,
        *,
        run_id: str | None = None,
        fixture_mode: bool = False,
        fixture_git_commit: str | None = None,
    ) -> WorkspacePreparation:
        del agent_id, fixture_mode, fixture_git_commit
        if self.stale:
            raise WorkspaceGraphError("fixture graph is stale")
        snapshot = self.tasks.snapshot()
        task = next(item for item in snapshot.tasks if item.task_id == task_id)
        assert snapshot.generation_id is not None
        assert snapshot.generation_sha256 is not None
        assert run_id is not None
        workspace_id = derive_id("workspace", {"run_id": run_id})
        relative_root = f".raytsystem/workspaces/{workspace_id}"
        repo_path = f"{relative_root}/repo"
        (self.root / repo_path).mkdir(parents=True, exist_ok=True)
        seed = TaskGraphScope(
            graph_scope_id="gscope_pending",
            task_id=task_id,
            graph_snapshot_id="graph_disabled",
            graph_fingerprint=HASH,
            generation_fingerprint=snapshot.generation_sha256,
            created_at=task.updated_at,
        )
        graph_scope = seed.model_copy(
            update={"graph_scope_id": derive_id("gscope", seed.identity_payload())}
        )
        workspace = TaskWorkspace(
            workspace_id=workspace_id,
            task_id=task_id,
            mode=WorkspaceMode.TASK_WORKTREE,
            relative_root=relative_root,
            repo_path=repo_path,
            context_path=f"{relative_root}/context",
            artifacts_path=f"{relative_root}/artifacts",
            logs_path=f"{relative_root}/logs",
            git_commit=COMMIT,
            graph_snapshot_id="graph_disabled",
            graph_fingerprint=HASH,
            manifest_sha256=sha256_hex(run_id.encode()),
            created_at=task.updated_at,
            updated_at=task.updated_at,
        )
        return WorkspacePreparation(
            workspace=workspace,
            graph_scope=graph_scope,
            context_snapshot_sha256=HASH,
            task_generation_id=snapshot.generation_id,
            task_revision=task.revision,
            employee_configuration_revision=HASH,
            catalog_sha256=HASH,
            git_commit=COMMIT,
            no_op=False,
        )


class CountingFakeAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.build_count = 0

    def build_command(self, request: RuntimeRequest) -> CommandPlan:
        self.build_count += 1
        return super().build_command(request)


class SlowFakeAdapter(CountingFakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def execute(
        self,
        request: RuntimeRequest,
        *,
        supervisor: ProcessSupervisor | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ProcessOutcome:
        del request, supervisor
        self.started.set()
        assert cancel_event is not None
        await cancel_event.wait()
        return ProcessOutcome(None, "", "", "cancelled", 1)


class FixtureCodexAdapter:
    adapter_id = "adapter_codex_local"

    def __init__(self) -> None:
        self.build_count = 0

    def health_check(self, *, checked_at: datetime | None = None) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_adapter_id=self.adapter_id,
            status=RuntimeHealthStatus.AVAILABLE,
            executable="/managed/bin/codex",
            version="fixture",
            capabilities=("staged_write",),
            checked_at=checked_at or NOW,
        )

    def build_command(self, request: RuntimeRequest) -> CommandPlan:
        self.build_count += 1
        return CommandPlan(
            adapter_id=self.adapter_id,
            argv=(
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
                "-",
            ),
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
        del supervisor, cancel_event
        self.build_command(request)
        return ProcessOutcome(
            0,
            '{"session_id":"codex_fixture_session"}\n',
            "",
            "completed",
            1,
        )


def _employee(adapter_id: str = "adapter_fake") -> DigitalEmployee:
    return DigitalEmployee(
        employee_id="employee_fixture",
        agent_definition_id="agent_fixture",
        agent_definition_sha256=HASH,
        name="Fixture employee",
        role="software_engineer",
        runtime_adapter_id=adapter_id,
        filesystem_policy=FilesystemPolicy(),
        graph_policy=GraphPolicy(),
        heartbeat_policy=HeartbeatPolicy(),
        status=EmployeeStatus.IDLE,
        configuration_revision=HASH,
    )


def _config() -> ExecutionConfig:
    return ExecutionConfig(
        features=FeatureFlags(
            code_graph_enabled=False,
            graph_first_query_enabled=False,
            runtime_execution_enabled=True,
        ),
        lease_ttl_seconds=10,
        lease_renewal_seconds=1,
    )


def _ready_task(root: Path) -> tuple[TaskService, str]:
    tasks = TaskService(root)
    created = tasks.create_task(
        title="Implement the execution slice",
        description="Use the deterministic fixture adapter.",
        actor="user:test",
        idempotency_key="create-service-task",
        now=NOW,
    )
    planned = tasks.transition_task(
        created.task.task_id,
        TaskStatus.PLANNED,
        actor="user:test",
        idempotency_key="plan-service-task",
        expected_generation_id=created.generation_id,
        now=NOW,
    )
    ready = tasks.transition_task(
        created.task.task_id,
        TaskStatus.READY,
        actor="user:test",
        idempotency_key="ready-service-task",
        expected_generation_id=planned.generation_id,
        now=NOW,
    )
    return tasks, ready.task.task_id


def _service(
    root: Path,
    *,
    adapter: CountingFakeAdapter | None = None,
    stale_workspace: bool = False,
) -> tuple[ExecutionService, TaskService, str, CountingFakeAdapter]:
    tasks, task_id = _ready_task(root)
    runtime = adapter or CountingFakeAdapter()
    service = ExecutionService(
        root,
        config=_config(),
        tasks=tasks,
        employees=FixtureEmployees(_employee()),
        workspaces=FixtureWorkspaces(root, tasks, stale=stale_workspace),
        adapters={"adapter_fake": runtime},
        emergency=FixtureEmergency(),
        clock=lambda: NOW,
    )
    return service, tasks, task_id, runtime


def _assign(service: ExecutionService, tasks: TaskService, task_id: str) -> str:
    generation = tasks.snapshot().generation_id
    assert generation is not None
    result = service.assign_task(
        task_id=task_id,
        employee_id="employee_fixture",
        expected_generation_id=generation,
        idempotency_key="assign-service-task",
        now=NOW,
    )
    return result.assignment.assignment_id


def test_fake_heartbeat_reaches_review_and_duplicate_never_reexecutes(tmp_path: Path) -> None:
    service, tasks, task_id, adapter = _service(tmp_path)
    try:
        assignment_id = _assign(service, tasks, task_id)
        before_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

        first = asyncio.run(
            service.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="heartbeat-service-task",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        duplicate = asyncio.run(
            service.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="heartbeat-service-task",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )

        assert first.status is ExecutionRunStatus.REVIEW
        assert duplicate.run_id == first.run_id
        assert duplicate.no_op
        assert adapter.build_count == 2  # one audit plan and one execute-time rebuild
        assert tasks.snapshot().tasks[0].status is TaskStatus.REVIEW
        assert len(service.store.list(ExecutionRun, task_id=task_id)) == 1
        assert len(service.store.list(ExecutionInvocation, task_id=task_id)) == 1
        transcript = service.store.list_transcript(first.run_id)
        assert [event.sequence for event in transcript] == list(range(len(transcript)))
        sessions = service.store.list(ExecutionSession, task_id=task_id)
        assert len(sessions) == 1
        assert sessions[0].status is ExecutionSessionStatus.ACTIVE
        assert sessions[0].provider_session_id is not None
        assert first.run is not None
        lease = service.store.get(TaskLease, first.run.task_lease_id)
        assert lease is not None and lease.status is TaskLeaseStatus.RELEASED
        after_paths = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
        new_top_levels = {path.parts[0] for path in after_paths - before_paths}
        assert new_top_levels <= {".raytsystem", "ops"}
    finally:
        service.close()


def test_budget_and_stale_graph_block_before_runtime_plan(tmp_path: Path) -> None:
    service, tasks, task_id, adapter = _service(tmp_path / "budget")
    try:
        policy = BudgetPolicy.create(
            scope_kind="task",
            scope_id=task_id,
            token_limit=TokenBudget(),
            run_limit=1,
            created_at=NOW,
        )
        service.store.put(policy, expected_revision=None)
        service.store.put(
            BudgetUsage(
                budget_policy_id=policy.budget_policy_id,
                run_count=1,
                updated_at=NOW,
            ),
            expected_revision=None,
        )
        generation = tasks.snapshot().generation_id
        assert generation is not None
        assignment = service.assign_task(
            task_id=task_id,
            employee_id="employee_fixture",
            expected_generation_id=generation,
            idempotency_key="budget-assignment",
            budget_policy_id=policy.budget_policy_id,
            now=NOW,
        ).assignment
        result = asyncio.run(
            service.manual_heartbeat(
                assignment_id=assignment.assignment_id,
                idempotency_key="budget-heartbeat",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        assert result.status is ExecutionRunStatus.BLOCKED
        assert result.error_code == "policy_denied"
        assert adapter.build_count == 0
        assert service.store.list(ExecutionInvocation, task_id=task_id) == ()
    finally:
        service.close()

    stale, stale_tasks, stale_task_id, stale_adapter = _service(
        tmp_path / "stale", stale_workspace=True
    )
    try:
        assignment_id = _assign(stale, stale_tasks, stale_task_id)
        result = asyncio.run(
            stale.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="stale-heartbeat",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        assert result.error_code == "workspace_preflight_failed"
        assert stale_adapter.build_count == 0
        assert stale.store.list(ExecutionInvocation, task_id=stale_task_id) == ()
    finally:
        stale.close()


def test_provider_approval_wait_retries_same_idempotency_without_early_plan(
    tmp_path: Path,
) -> None:
    tasks, task_id = _ready_task(tmp_path)
    adapter = FixtureCodexAdapter()
    config = ExecutionConfig(
        features=FeatureFlags(
            code_graph_enabled=False,
            graph_first_query_enabled=False,
            runtime_execution_enabled=True,
            codex_local_enabled=True,
        ),
        lease_ttl_seconds=10,
        lease_renewal_seconds=1,
    )
    service = ExecutionService(
        tmp_path,
        config=config,
        tasks=tasks,
        employees=FixtureEmployees(_employee("adapter_codex_local")),
        workspaces=FixtureWorkspaces(tmp_path, tasks),
        adapters={"adapter_codex_local": adapter},
        emergency=FixtureEmergency(),
        clock=lambda: NOW,
    )
    try:
        assignment_id = _assign(service, tasks, task_id)
        waiting = asyncio.run(
            service.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="approved-provider-heartbeat",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        assert waiting.approval_required
        assert waiting.run is None and waiting.invocation is None
        assert adapter.build_count == 0
        assert tasks.snapshot().tasks[0].status is TaskStatus.READY
        assert waiting.policy_decision is not None
        workspaces = service.store.list(TaskWorkspace, task_id=task_id)
        assert len(workspaces) == 1
        seed = ExecutionApproval(
            approval_id="xapr_pending",
            action="execute_runtime",
            payload_sha256=waiting.policy_decision.payload_sha256,
            employee_id="employee_fixture",
            task_id=task_id,
            run_id=waiting.run_id,
            workspace_id=workspaces[0].workspace_id,
            destination="provider:openai",
            scope=("private_corpus_egress", "provider_egress", "runtime_execution"),
            approved_by="user:test",
            approved_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
        )
        approval = seed.model_copy(
            update={"approval_id": derive_id("xapr", seed.identity_payload())}
        )
        service.store.put(approval, expected_revision=None)

        completed = asyncio.run(
            service.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="approved-provider-heartbeat",
                approval_id=approval.approval_id,
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        assert completed.status is ExecutionRunStatus.REVIEW
        assert completed.run_id == waiting.run_id
        assert completed.invocation is not None
        assert completed.invocation.approval_id == approval.approval_id
        assert adapter.build_count == 2
    finally:
        service.close()


def test_pause_cancels_process_and_blocks_task(tmp_path: Path) -> None:
    adapter = SlowFakeAdapter()
    service, tasks, task_id, _runtime = _service(tmp_path, adapter=adapter)

    async def scenario() -> None:
        assignment_id = _assign(service, tasks, task_id)
        heartbeat = asyncio.create_task(
            service.manual_heartbeat(
                assignment_id=assignment_id,
                idempotency_key="pause-heartbeat",
                fixture_mode=True,
                fixture_git_commit=COMMIT,
                now=NOW,
            )
        )
        await adapter.started.wait()
        active = service.store.list(
            ExecutionRun, state=ExecutionRunStatus.RUNNING.value, task_id=task_id
        )
        assert len(active) == 1
        await service.pause_run(active[0].run_id)
        result = await heartbeat
        assert result.status is ExecutionRunStatus.CANCELLED
        assert result.error_code == "paused_by_user"

    try:
        asyncio.run(scenario())
        assert tasks.snapshot().tasks[0].status is TaskStatus.BLOCKED
    finally:
        service.close()


def test_stale_recovery_terminalizes_run_without_canonical_knowledge_write(
    tmp_path: Path,
) -> None:
    service, tasks, task_id, _adapter = _service(tmp_path)
    try:
        assignment_id = _assign(service, tasks, task_id)
        assignment = service.store.get(TaskAssignment, assignment_id)
        assert assignment is not None
        # A crashed queued run with a missing lease is recoverable without a process.
        run = ExecutionRun(
            run_id="xrun_stale_fixture",
            invocation_id="xinv_stale_fixture",
            invocation_source="manual",
            employee_id="employee_fixture",
            task_id=task_id,
            runtime_adapter_id="adapter_fake",
            provider="fake",
            safe_command=("raytsystem-fake-runtime", "--json"),
            cwd_token=".raytsystem/workspaces/workspace_stale/repo",
            workspace_id="workspace_stale_fixture",
            graph_snapshot_id="graph_disabled",
            graph_scope_id="gscope_stale_fixture",
            policy_decision_id="pdec_stale_fixture",
            task_lease_id="tlease_missing_fixture",
            fencing_token=1,
            status=ExecutionRunStatus.RUNNING,
            started_at=NOW,
        )
        service.store.put(run, expected_revision=None)
        recovered = service.recover_stale_runs(now=NOW)
        assert len(recovered) == 1
        assert recovered[0].status is ExecutionRunStatus.FAILED
        assert recovered[0].error_code == "stale_lease_recovered"
        assert not (tmp_path / "ledger").exists()
        assert not (tmp_path / "obsidian").exists()
    finally:
        service.close()
