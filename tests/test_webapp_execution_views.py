from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from raytsystem.catalog import CatalogService
from raytsystem.contracts.base import derive_id
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    CommentKind,
    DigitalEmployee,
    EmployeeStatus,
    ExecutionApproval,
    ExecutionComment,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionSession,
    TaskAssignment,
    TaskGraphScope,
    TaskWorkspace,
    TokenBudget,
    TranscriptEvent,
    WorkspaceMode,
)
from raytsystem.execution.adapters import FakeAdapter
from raytsystem.execution.config import load_execution_config
from raytsystem.execution.employees import DigitalEmployeeCatalog
from raytsystem.execution.store import ExecutionStore
from raytsystem.webapp.execution_views import ExecutionViewProvider

NOW = datetime(2026, 7, 12, tzinfo=UTC)
TASK_ID = "task_example"
EMPLOYEE_ID = derive_id("employee", {"agent_definition_id": "agent_builder"})


def _prepare_catalog(root: Path, *, runtime_enabled: bool = False) -> None:
    agents = root / "packs" / "test" / "agents"
    agents.mkdir(parents=True)
    (root / "packs" / "test" / "pack.yaml").write_text(
        "\n".join(
            [
                'schema_name: "PackManifestV1"',
                'pack_id: "pack_test"',
                'name: "Test pack"',
                'version: "1.0.0"',
                'description: "Safe test employees."',
                'license_expression: "MIT"',
                'trust_class: "user"',
                'agent_ids: ["agent_builder"]',
                "skill_ids: []",
                "context_paths: []",
                "optional: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (agents / "agent_builder.yaml").write_text(
        "\n".join(
            [
                'schema_name: "AgentDefinitionV1"',
                'agent_id: "agent_builder"',
                'name: "Builder"',
                'role: "software_engineer"',
                'description: "Implements an approved task in a managed worktree."',
                'version: "1.0.0"',
                'pack_id: "pack_test"',
                'runtime_adapter_id: "adapter_fake"',
                "skill_ids: []",
                "context_paths: []",
                'capabilities: ["code_edit"]',
                'requested_filesystem_mode: "staging_only"',
                'egress_destination: "private-runtime-egress-endpoint"',
                'accent: "#123456"',
                "enabled: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "config" / "runtime-adapters.yaml").write_text(
        "\n".join(
            [
                'version: "1.0.0"',
                "adapters:",
                "  - adapter_id: adapter_fake",
                "    name: Deterministic fake runtime",
                '    version: "1.0.0"',
                "    state: available",
                "    isolation_mode: in_process_no_egress",
                "    capabilities: [read_workspace, staged_write, deterministic]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if runtime_enabled:
        config = root / "config" / "raytsystem.toml"
        config.write_text(
            config.read_text(encoding="utf-8") + "\n[features]\nruntime_execution_enabled = true\n",
            encoding="utf-8",
        )


def _graph_scope() -> TaskGraphScope:
    seed = TaskGraphScope(
        graph_scope_id="gscope_pending",
        task_id=TASK_ID,
        graph_snapshot_id="cgraph_example",
        graph_fingerprint="a" * 64,
        generation_fingerprint="b" * 64,
        roots=(".raytsystem/workspaces/workspace_example/repo",),
        seed_node_ids=("code_node",),
        include_relations=("imports",),
        created_at=NOW,
    )
    return seed.model_copy(update={"graph_scope_id": derive_id("gscope", seed.identity_payload())})


def _workspace() -> TaskWorkspace:
    root = ".raytsystem/workspaces/workspace_example"
    return TaskWorkspace(
        workspace_id="workspace_example",
        task_id=TASK_ID,
        mode=WorkspaceMode.TASK_WORKTREE,
        relative_root=root,
        repo_path=f"{root}/repo",
        context_path=f"{root}/context",
        artifacts_path=f"{root}/artifacts",
        logs_path=f"{root}/logs",
        git_commit="c" * 40,
        graph_snapshot_id="cgraph_example",
        graph_fingerprint="a" * 64,
        manifest_sha256="d" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def _assignment(
    task_id: str = TASK_ID,
    assignment_id: str = "assignment_example",
) -> TaskAssignment:
    return TaskAssignment(
        assignment_id=assignment_id,
        task_id=task_id,
        task_generation_id="tgen_example",
        task_revision=1,
        employee_id=EMPLOYEE_ID,
        runtime_adapter_id="adapter_fake",
        graph_scope_id=_graph_scope().graph_scope_id if task_id == TASK_ID else None,
        created_at=NOW,
        updated_at=NOW,
    )


def _run() -> ExecutionRun:
    return ExecutionRun(
        run_id="xrun_example",
        invocation_id="xinv_example",
        invocation_source="manual",
        employee_id=EMPLOYEE_ID,
        task_id=TASK_ID,
        runtime_adapter_id="adapter_fake",
        provider="fake",
        safe_command=("raytsystem-fake-runtime", "--json"),
        cwd_token=".raytsystem/workspaces/workspace_example/repo",
        workspace_id="workspace_example",
        graph_snapshot_id="cgraph_example",
        graph_scope_id=_graph_scope().graph_scope_id,
        session_id_after="xsession_example",
        policy_decision_id="policy_example",
        task_lease_id="tlease_example",
        fencing_token=1,
        status=ExecutionRunStatus.RUNNING,
        started_at=NOW,
        changed_files=("src/private_module.py",),
        summary="Managed execution is in progress.",
    )


def _session() -> ExecutionSession:
    return ExecutionSession(
        session_id="xsession_example",
        provider_session_id="private-session-reference",
        runtime_adapter_id="adapter_fake",
        provider="fake",
        task_id=TASK_ID,
        employee_id=EMPLOYEE_ID,
        workspace_id="workspace_example",
        graph_snapshot_id="cgraph_example",
        context_snapshot_sha256="e" * 64,
        compatibility_sha256="f" * 64,
        started_at=NOW,
        previous_run_id="xrun_example",
    )


def _approval() -> ExecutionApproval:
    seed = ExecutionApproval(
        approval_id="xapr_pending",
        action="review_execution",
        payload_sha256="1" * 64,
        employee_id=EMPLOYEE_ID,
        task_id=TASK_ID,
        run_id="xrun_example",
        workspace_id="workspace_example",
        destination="internal_review_queue",
        scope=("review",),
        approved_by="operator_local",
        approved_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return seed.model_copy(update={"approval_id": derive_id("xapr", seed.identity_payload())})


def _comment() -> ExecutionComment:
    seed = ExecutionComment(
        comment_id="comment_pending",
        task_id=TASK_ID,
        kind=CommentKind.PROGRESS,
        actor=EMPLOYEE_ID,
        run_id="xrun_example",
        body="Execution started safely.",
        created_at=NOW,
    )
    return seed.model_copy(update={"comment_id": derive_id("comment", seed.identity_payload())})


def _seed_execution(root: Path) -> None:
    policy = BudgetPolicy.create(
        scope_kind="task",
        scope_id=TASK_ID,
        token_limit=TokenBudget(input_tokens=1_000, output_tokens=500),
        created_at=NOW,
        run_limit=2,
    )
    records = (
        _assignment(),
        _assignment("task_other", "assignment_other"),
        _workspace(),
        _graph_scope(),
        _run(),
        _session(),
        policy,
        BudgetUsage(
            budget_policy_id=policy.budget_policy_id,
            tokens=TokenBudget(input_tokens=100, output_tokens=50),
            run_count=1,
            updated_at=NOW,
        ),
        _approval(),
        _comment(),
    )
    with ExecutionStore.open_for_write(root / "ops" / "control.sqlite") as store:
        for record in records:
            store.put(record, expected_revision=None)
        store.append_transcript(
            TranscriptEvent(
                transcript_event_id="transcript_event_zero",
                run_id="xrun_example",
                sequence=0,
                stream="stdout",
                event_type="runtime_output",
                text="[REDACTED: sensitive runtime output]",
                redacted=True,
                created_at=NOW,
            )
        )


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_all_keys(child))
    elif isinstance(value, list | tuple):
        for child in value:
            keys.update(_all_keys(child))
    return keys


def test_missing_execution_state_is_read_only_and_reports_disabled_features(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_catalog(project_root)
    before = {path.relative_to(project_root).as_posix() for path in project_root.rglob("*")}

    def forbidden_probe(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime health probes are forbidden in GET views")

    monkeypatch.setattr(FakeAdapter, "health_check", forbidden_probe)
    provider = ExecutionViewProvider(project_root)
    features = provider.features()
    employees = provider.employees()
    health = provider.runtime_health()
    assignments = provider.assignments()

    after = {path.relative_to(project_root).as_posix() for path in project_root.rglob("*")}
    assert before == after
    assert not (project_root / "ops").exists()
    assert features["storage_state"] == "uninitialized"
    assert employees["state"] == "catalog_only"
    assert employees["employees"][0]["status"] == "disabled"
    assert employees["employees"][0]["reason_code"] == "runtime_execution_disabled"
    assert assignments["state"] == "uninitialized"
    assert health["probe_performed"] is False
    assert health["adapters"][0]["status"] == "disabled"
    assert "executable" not in _all_keys(health)


def test_task_detail_uses_typed_links_and_omits_sensitive_runtime_fields(
    project_root: Path,
) -> None:
    _prepare_catalog(project_root, runtime_enabled=True)
    _seed_execution(project_root)
    database = project_root / "ops" / "control.sqlite"
    modified_before = database.stat().st_mtime_ns
    provider = ExecutionViewProvider(project_root)

    detail = provider.task_detail(TASK_ID)

    assert database.stat().st_mtime_ns == modified_before
    assert [item["assignment_id"] for item in detail["assignments"]] == ["assignment_example"]
    assert detail["workspaces"][0]["workspace_id"] == "workspace_example"
    assert detail["graph_scopes"][0]["task_id"] == TASK_ID
    assert detail["runs"][0]["run_id"] == "xrun_example"
    assert detail["sessions"][0]["session_id"] == "xsession_example"
    assert detail["budgets"][0]["usage"]["run_count"] == 1
    assert detail["approvals"][0]["destination_present"] is True
    assert detail["comments"][0]["body"] == "Execution started safely."

    forbidden_keys = {
        "safe_command",
        "cwd_token",
        "relative_root",
        "repo_path",
        "context_path",
        "artifacts_path",
        "logs_path",
        "provider_session_id",
        "destination",
        "executable",
        "env",
    }
    assert forbidden_keys.isdisjoint(_all_keys(detail))
    rendered = json.dumps(detail, sort_keys=True)
    assert ".raytsystem/workspaces" not in rendered
    assert "raytsystem-fake-runtime" not in rendered
    assert "private-session-reference" not in rendered
    assert "internal_review_queue" not in rendered
    assert "[REDACTED: sensitive runtime output]" not in rendered

    snapshots = (
        (provider.assignments(task_id=TASK_ID), "assignments"),
        (provider.workspaces(task_id=TASK_ID), "workspaces"),
        (provider.graph_scopes(task_id=TASK_ID), "graph_scopes"),
        (provider.runs(task_id=TASK_ID), "runs"),
        (provider.sessions(task_id=TASK_ID), "sessions"),
        (provider.budgets(scope_id=TASK_ID, scope_kind="task"), "budgets"),
        (provider.approvals(task_id=TASK_ID), "approvals"),
        (provider.comments(task_id=TASK_ID), "comments"),
    )
    assert all(snapshot[key] for snapshot, key in snapshots)
    assert all(json.dumps(snapshot) for snapshot, _key in snapshots)

    transcript = provider.transcript("xrun_example", limit=1)
    assert transcript["events"][0]["redacted"] is True
    assert transcript["events"][0]["text"] == "[REDACTED: sensitive runtime output]"


def test_persisted_running_employee_stays_effectively_disabled_behind_feature_gate(
    project_root: Path,
) -> None:
    _prepare_catalog(project_root)
    flags = load_execution_config(project_root).features
    derived = DigitalEmployeeCatalog(CatalogService(project_root), flags=flags).load().employees[0]
    stored = DigitalEmployee.model_validate(
        {
            **derived.model_dump(mode="python"),
            "status": EmployeeStatus.RUNNING,
            "current_task_id": TASK_ID,
            "current_session_id": "xsession_example",
        }
    )
    with ExecutionStore.open_for_write(project_root / "ops" / "control.sqlite") as store:
        store.put(stored, expected_revision=None)

    item = ExecutionViewProvider(project_root).employees()["employees"][0]

    assert item["status"] == "disabled"
    assert item["stored_status"] == "running"
    assert item["state_source"] == "feature_gate"
    assert item["reason_code"] == "runtime_execution_disabled"


def test_unified_agent_projection_uses_stable_ids_and_exposes_orphans_once(
    project_root: Path,
) -> None:
    _prepare_catalog(project_root, runtime_enabled=True)
    flags = load_execution_config(project_root).features
    derived = DigitalEmployeeCatalog(CatalogService(project_root), flags=flags).load().employees[0]
    duplicate = derived.model_copy(update={"employee_id": "employee_duplicate"})
    orphan = derived.model_copy(
        update={
            "employee_id": "employee_orphan",
            "agent_definition_id": "agent_missing",
            "name": "Missing definition",
        }
    )
    with ExecutionStore.open_for_write(project_root / "ops" / "control.sqlite") as store:
        store.put(derived, expected_revision=None)
        store.put(duplicate, expected_revision=None)
        store.put(orphan, expected_revision=None)

    payload = ExecutionViewProvider(project_root).agents()

    assert [agent["agent_id"] for agent in payload["agents"]] == [
        "agent_builder",
        "agent_missing",
    ]
    builder, missing = payload["agents"]
    assert builder["employee_id"] == EMPLOYEE_ID
    assert builder["duplicate_execution_record_count"] == 2
    assert builder["readiness"] == "degraded"
    assert builder["unavailable_reason"] == "duplicate_execution_records"
    assert missing["definition"] is None
    assert missing["definition_state"] == "missing"
    assert missing["readiness"] == "degraded"
    assert missing["unavailable_reason"] == "definition_missing"
    assert len({agent["agent_id"] for agent in payload["agents"]}) == 2
    detail = ExecutionViewProvider(project_root).agent_detail("agent_builder")
    rendered = json.dumps({"list": payload, "detail": detail}, sort_keys=True)
    assert "egress_destination" not in _all_keys(payload)
    assert "egress_destination" not in _all_keys(detail)
    assert builder["egress_declared"] is True
    assert builder["definition"]["egress_declared"] is True
    assert detail["access"]["network"]["egress_declared"] is True
    assert "private-runtime-egress-endpoint" not in rendered


def test_unified_agent_projection_pages_all_execution_records(
    project_root: Path,
) -> None:
    _prepare_catalog(project_root, runtime_enabled=True)
    flags = load_execution_config(project_root).features
    derived = DigitalEmployeeCatalog(CatalogService(project_root), flags=flags).load().employees[0]
    with ExecutionStore.open_for_write(project_root / "ops" / "control.sqlite") as store:
        store.put(derived, expected_revision=None)
        for index in range(500):
            store.put(
                derived.model_copy(update={"employee_id": f"employee_duplicate_{index:03}"}),
                expected_revision=None,
            )

    payload = ExecutionViewProvider(project_root).agents()

    assert payload["total_agents"] == 1
    assert payload["agents"][0]["employee_id"] == derived.employee_id
    assert payload["agents"][0]["duplicate_execution_record_count"] == 501
    assert payload["agents"][0]["unavailable_reason"] == "duplicate_execution_records"


def test_execution_views_reject_unbounded_pages_and_malformed_ids(
    project_root: Path,
) -> None:
    _prepare_catalog(project_root)
    provider = ExecutionViewProvider(project_root)

    with pytest.raises(ValueError, match="pagination"):
        provider.employees(limit=0)
    with pytest.raises(ValueError, match="pagination"):
        provider.assignments(limit=501)
    with pytest.raises(ValueError, match="pagination"):
        provider.comments(offset=-1)
    with pytest.raises(ValueError, match="malformed"):
        provider.task_detail("../../escape")
    with pytest.raises(ValueError, match="cursor"):
        provider.transcript("xrun_example", after_sequence=-2)
    with pytest.raises(ValueError, match="page size"):
        provider.transcript("xrun_example", limit=1_001)
