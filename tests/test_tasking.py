from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import raytsystem.tasking as tasking
from raytsystem.contracts import (
    AgentTask,
    TaskBoardGeneration,
    TaskEvent,
    TaskEventKind,
    TaskPriority,
    TaskStatus,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.tasking import TaskConflict, TaskLedgerError, TaskService, TaskTransitionRejected

NOW = datetime(2026, 7, 11, 12, tzinfo=UTC)


def _publish_forged_transition(
    service: TaskService,
    *,
    target: TaskStatus,
    title: str | None = None,
    idempotency_key: str | None = None,
    blocked_reason: str | None = None,
) -> None:
    parent = service._read_current_generation()
    assert parent is not None and parent.head_event_id is not None
    task_id = next(iter(parent.task_hashes))
    previous = service._read_task(task_id, parent.task_hashes[task_id])
    event_idempotency = idempotency_key or derive_id(
        "idem", {"actor": "user:forger", "key": "forged-transition"}
    )
    next_task = previous.model_copy(
        update={
            "title": previous.title if title is None else title,
            "status": target,
            "blocked_reason": blocked_reason,
            "revision": previous.revision + 1,
            "updated_at": NOW + timedelta(seconds=1),
        }
    )
    task_bytes = canonical_json_bytes(next_task)
    task_sha256 = sha256_hex(task_bytes)
    task_path = service.objects_root / task_sha256[:2] / f"{task_sha256}.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_bytes(task_bytes)
    operation_key = derive_id(
        "op",
        {
            "service": service.version,
            "command": "transition_task",
            "task_id": task_id,
            "target": target,
            "blocked_reason": blocked_reason,
            "actor": "user:forger",
            "idempotency_key": event_idempotency,
        },
    )
    seed = TaskEvent(
        event_id="tevt_pending",
        event_kind=TaskEventKind.TRANSITIONED,
        task_id=task_id,
        operation_key=operation_key,
        idempotency_key=event_idempotency,
        previous_event_id=parent.head_event_id,
        previous_task_sha256=parent.task_hashes[task_id],
        task_sha256=task_sha256,
        from_status=previous.status,
        to_status=target,
        actor="user:forger",
        created_at=NOW + timedelta(seconds=1),
    )
    event = seed.model_copy(update={"event_id": derive_id("tevt", seed.identity_payload())})
    event_path = service.events_root / f"{event.event_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_bytes(canonical_json_bytes(event))
    generation_seed = TaskBoardGeneration(
        generation_id="tgen_pending",
        parent_generation_id=parent.generation_id,
        parent_generation_sha256=parent.manifest_sha256(),
        task_hashes={task_id: task_sha256},
        latest_event_ids={task_id: event.event_id},
        head_event_id=event.event_id,
        event_count=parent.event_count + 1,
        created_at=event.created_at,
    )
    generation = generation_seed.model_copy(
        update={"generation_id": derive_id("tgen", generation_seed.identity_payload())}
    )
    generation_path = service.generations_root / f"{generation.generation_id}.json"
    generation_path.parent.mkdir(parents=True, exist_ok=True)
    generation_path.write_bytes(canonical_json_bytes(generation))
    service.pointer_path.write_text(generation.generation_id + "\n", encoding="ascii")


def _publish_forged_root(
    service: TaskService,
    *,
    artifact_ids: tuple[str, ...] = (),
    extensions: dict[str, str] | None = None,
) -> None:
    actor = "user:forger"
    idempotency_key = derive_id("idem", {"actor": actor, "value": "forged-root"})
    payload = {
        "title": "Forged root",
        "description": "",
        "priority": TaskPriority.NORMAL,
        "project_id": "project_default",
        "mission_id": None,
        "assignee_ids": (),
        "skill_ids": (),
        "dependency_ids": (),
        "tags": (),
        "actor": actor,
    }
    operation_key = derive_id(
        "op",
        {
            "service": service.version,
            "command": "create_task",
            "idempotency_key": idempotency_key,
            "payload": payload,
        },
    )
    task_id = derive_id("task", {"operation_key": operation_key})
    task = AgentTask(
        task_id=task_id,
        title="Forged root",
        artifact_ids=artifact_ids,
        extensions={} if extensions is None else extensions,
        created_by=actor,
        created_at=NOW,
        updated_at=NOW,
    )
    task_bytes = canonical_json_bytes(task)
    task_sha256 = sha256_hex(task_bytes)
    task_path = service.objects_root / task_sha256[:2] / f"{task_sha256}.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_bytes(task_bytes)
    event_seed = TaskEvent(
        event_id="tevt_pending",
        event_kind=TaskEventKind.CREATED,
        task_id=task_id,
        operation_key=operation_key,
        idempotency_key=idempotency_key,
        task_sha256=task_sha256,
        to_status=TaskStatus.INBOX,
        actor=actor,
        created_at=NOW,
    )
    event = event_seed.model_copy(
        update={"event_id": derive_id("tevt", event_seed.identity_payload())}
    )
    event_path = service.events_root / f"{event.event_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_bytes(canonical_json_bytes(event))
    generation_seed = TaskBoardGeneration(
        generation_id="tgen_pending",
        task_hashes={task_id: task_sha256},
        latest_event_ids={task_id: event.event_id},
        head_event_id=event.event_id,
        event_count=1,
        created_at=NOW,
    )
    generation = generation_seed.model_copy(
        update={"generation_id": derive_id("tgen", generation_seed.identity_payload())}
    )
    generation_path = service.generations_root / f"{generation.generation_id}.json"
    generation_path.parent.mkdir(parents=True, exist_ok=True)
    generation_path.write_bytes(canonical_json_bytes(generation))
    service.pointer_path.parent.mkdir(parents=True, exist_ok=True)
    service.pointer_path.write_text(generation.generation_id + "\n", encoding="ascii")


def test_empty_task_snapshot_is_read_only(tmp_path: Path) -> None:
    service = TaskService(tmp_path)

    snapshot = service.snapshot()

    assert snapshot.generation_id is None
    assert snapshot.tasks == ()
    assert not (tmp_path / "ops").exists()


def test_task_create_is_durable_and_idempotent(project_root: Path) -> None:
    service = TaskService(project_root)
    ledger_before = (project_root / "ledger" / "CURRENT").read_bytes()

    created = service.create_task(
        title="Design the universal control plane",
        description="Use typed local state.",
        priority=TaskPriority.HIGH,
        actor="user:local",
        idempotency_key="create-control-plane",
        expected_generation_id=None,
        now=NOW,
    )
    repeated = service.create_task(
        title="Design the universal control plane",
        description="Use typed local state.",
        priority=TaskPriority.HIGH,
        actor="user:local",
        idempotency_key="create-control-plane",
        expected_generation_id=None,
        now=NOW,
    )

    assert not created.no_op
    assert repeated.no_op
    assert repeated.generation_id == created.generation_id
    assert repeated.event_id == created.event_id
    assert service.snapshot().tasks == (created.task,)
    assert (project_root / "ledger" / "CURRENT").read_bytes() == ledger_before


def test_task_transition_requires_current_generation_and_valid_state(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Audit runtime adapters",
        actor="user:local",
        idempotency_key="create-adapter-audit",
        expected_generation_id=None,
        now=NOW,
    )

    with pytest.raises(TaskConflict, match="generation changed"):
        service.transition_task(
            created.task.task_id,
            TaskStatus.PLANNED,
            actor="user:local",
            idempotency_key="plan-adapter-audit-stale",
            expected_generation_id="tgen_" + "0" * 64,
            now=NOW + timedelta(seconds=1),
        )

    planned = service.transition_task(
        created.task.task_id,
        TaskStatus.PLANNED,
        actor="user:local",
        idempotency_key="plan-adapter-audit",
        expected_generation_id=created.generation_id,
        now=NOW + timedelta(seconds=1),
    )
    assert planned.task.status is TaskStatus.PLANNED
    assert planned.task.revision == 2

    with pytest.raises(TaskTransitionRejected, match="Illegal task transition"):
        service.transition_task(
            planned.task.task_id,
            TaskStatus.DONE,
            actor="user:local",
            idempotency_key="skip-review",
            expected_generation_id=planned.generation_id,
            now=NOW + timedelta(seconds=2),
        )


def test_reused_idempotency_key_with_different_payload_is_a_conflict(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Original title",
        actor="user:local",
        idempotency_key="one-logical-command",
        expected_generation_id=None,
        now=NOW,
    )

    with pytest.raises(TaskConflict, match="already used"):
        service.create_task(
            title="Changed payload",
            actor="user:local",
            idempotency_key="one-logical-command",
            expected_generation_id=created.generation_id,
            now=NOW + timedelta(seconds=1),
        )


def test_idempotent_replay_after_transition_returns_original_result(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Original command result",
        actor="user:local",
        idempotency_key="original-result",
        expected_generation_id=None,
        now=NOW,
    )
    planned = service.transition_task(
        created.task.task_id,
        TaskStatus.PLANNED,
        actor="user:local",
        idempotency_key="later-transition",
        expected_generation_id=created.generation_id,
        now=NOW + timedelta(seconds=1),
    )

    replayed = service.create_task(
        title="Original command result",
        actor="user:local",
        idempotency_key="original-result",
        expected_generation_id=None,
        now=NOW + timedelta(days=1),
    )

    assert replayed.no_op
    assert replayed.generation_id == created.generation_id
    assert replayed.task.status is TaskStatus.INBOX
    assert service.snapshot().generation_id == planned.generation_id
    assert service.snapshot().tasks[0].status is TaskStatus.PLANNED


def test_idempotency_keys_are_scoped_to_actor(project_root: Path) -> None:
    service = TaskService(project_root)
    first = service.create_task(
        title="Alice task",
        actor="user:alice",
        idempotency_key="shared-browser-key",
        expected_generation_id=None,
        now=NOW,
    )
    second = service.create_task(
        title="Bob task",
        actor="user:bob",
        idempotency_key="shared-browser-key",
        expected_generation_id=first.generation_id,
        now=NOW + timedelta(seconds=1),
    )

    assert second.task.task_id != first.task.task_id
    assert len(service.snapshot().tasks) == 2


def test_dependencies_gate_ready_and_running(project_root: Path) -> None:
    service = TaskService(project_root)
    dependency = service.create_task(
        title="Define contracts",
        actor="user:local",
        idempotency_key="contracts",
        expected_generation_id=None,
        now=NOW,
    )
    child = service.create_task(
        title="Build UI",
        dependency_ids=(dependency.task.task_id,),
        actor="user:local",
        idempotency_key="ui",
        expected_generation_id=dependency.generation_id,
        now=NOW + timedelta(seconds=1),
    )
    planned = service.transition_task(
        child.task.task_id,
        TaskStatus.PLANNED,
        actor="user:local",
        idempotency_key="ui-planned",
        expected_generation_id=child.generation_id,
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(TaskTransitionRejected, match="dependencies are not complete"):
        service.transition_task(
            child.task.task_id,
            TaskStatus.READY,
            actor="user:local",
            idempotency_key="ui-ready-too-soon",
            expected_generation_id=planned.generation_id,
            now=NOW + timedelta(seconds=3),
        )


def test_blocked_transition_requires_reason(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Blocked work",
        actor="user:local",
        idempotency_key="blocked-work",
        expected_generation_id=None,
        now=NOW,
    )
    planned = service.transition_task(
        created.task.task_id,
        TaskStatus.PLANNED,
        actor="user:local",
        idempotency_key="blocked-work-planned",
        expected_generation_id=created.generation_id,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(TaskTransitionRejected, match="payload is invalid"):
        service.transition_task(
            planned.task.task_id,
            TaskStatus.BLOCKED,
            actor="user:local",
            idempotency_key="blocked-no-reason",
            expected_generation_id=planned.generation_id,
            now=NOW + timedelta(seconds=2),
        )

    blocked = service.transition_task(
        planned.task.task_id,
        TaskStatus.BLOCKED,
        blocked_reason="Awaiting a reviewed runtime policy.",
        actor="user:local",
        idempotency_key="blocked-with-reason",
        expected_generation_id=planned.generation_id,
        now=NOW + timedelta(seconds=2),
    )
    assert blocked.task.blocked_reason == "Awaiting a reviewed runtime policy."


def test_pointer_failure_leaves_previous_board_visible(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TaskService(project_root)
    original_write = tasking.write_bytes_atomic

    def fail_pointer(path: Path, data: bytes, *, mode: int = 0o644) -> None:
        if path.name == "CURRENT":
            raise OSError("synthetic ENOSPC")
        original_write(path, data, mode=mode)

    monkeypatch.setattr("raytsystem.tasking.write_bytes_atomic", fail_pointer)
    with pytest.raises(TaskLedgerError, match="pointer commit failed"):
        service.create_task(
            title="Uncommitted task",
            actor="user:local",
            idempotency_key="uncommitted",
            expected_generation_id=None,
            now=NOW,
        )

    assert service.snapshot().tasks == ()

    monkeypatch.setattr("raytsystem.tasking.write_bytes_atomic", original_write)
    recovered = service.create_task(
        title="Uncommitted task",
        actor="user:local",
        idempotency_key="uncommitted",
        expected_generation_id=None,
        now=NOW,
    )
    assert not recovered.no_op
    assert len(service.snapshot().tasks) == 1


def test_task_object_tamper_fails_closed(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Integrity task",
        actor="user:local",
        idempotency_key="integrity-task",
        expected_generation_id=None,
        now=NOW,
    )
    generation = service._read_current_generation()
    assert generation is not None
    task_sha = generation.task_hashes[created.task.task_id]
    task_path = (
        project_root
        / "ops"
        / "task-ledger"
        / "objects"
        / "sha256"
        / task_sha[:2]
        / f"{task_sha}.json"
    )
    task_path.write_bytes(task_path.read_bytes() + b" ")

    with pytest.raises(TaskLedgerError, match="integrity failed"):
        service.snapshot()


def test_hash_valid_generation_with_missing_event_fails_closure(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Closure task",
        actor="user:local",
        idempotency_key="closure-task",
        expected_generation_id=None,
        now=NOW,
    )
    current = service._read_current_generation()
    assert current is not None
    missing_event_id = "tevt_" + "f" * 64
    seed = TaskBoardGeneration(
        generation_id="tgen_pending",
        task_hashes=current.task_hashes,
        latest_event_ids={created.task.task_id: missing_event_id},
        head_event_id=missing_event_id,
        event_count=1,
        created_at=NOW,
    )
    forged = seed.model_copy(update={"generation_id": derive_id("tgen", seed.identity_payload())})
    generation_path = (
        project_root / "ops" / "task-ledger" / "generations" / f"{forged.generation_id}.json"
    )
    generation_path.write_bytes(canonical_json_bytes(forged))
    (project_root / "ops" / "task-ledger" / "CURRENT").write_text(
        forged.generation_id + "\n",
        encoding="ascii",
    )

    with pytest.raises(TaskLedgerError, match="event is unsafe or invalid"):
        service.snapshot()


@pytest.mark.parametrize(
    ("target", "changed_title"),
    [(TaskStatus.DONE, None), (TaskStatus.PLANNED, "Silently changed title")],
)
def test_hash_valid_semantically_forged_transition_fails_closed(
    project_root: Path,
    target: TaskStatus,
    changed_title: str | None,
) -> None:
    service = TaskService(project_root)
    service.create_task(
        title="Semantic closure task",
        actor="user:local",
        idempotency_key="semantic-closure-task",
        expected_generation_id=None,
        now=NOW,
    )
    _publish_forged_transition(service, target=target, title=changed_title)

    with pytest.raises(TaskLedgerError, match="transition semantics"):
        service.snapshot()


def test_hash_valid_duplicate_idempotency_event_fails_closed(project_root: Path) -> None:
    service = TaskService(project_root)
    service.create_task(
        title="Unique command identity",
        actor="user:local",
        idempotency_key="unique-command",
        expected_generation_id=None,
        now=NOW,
    )
    parent = service._read_current_generation()
    assert parent is not None and parent.head_event_id is not None
    existing = service._read_event(parent.head_event_id)
    _publish_forged_transition(
        service,
        target=TaskStatus.PLANNED,
        idempotency_key=existing.idempotency_key,
    )

    with pytest.raises(TaskLedgerError, match="command identity is duplicated"):
        service.snapshot()


def test_hash_valid_forged_secret_transition_fails_recovery_gate(project_root: Path) -> None:
    service = TaskService(project_root)
    created = service.create_task(
        title="Recovery sensitivity",
        actor="user:local",
        idempotency_key="recovery-sensitivity",
        expected_generation_id=None,
        now=NOW,
    )
    service.transition_task(
        created.task.task_id,
        TaskStatus.PLANNED,
        actor="user:local",
        idempotency_key="recovery-sensitivity-plan",
        expected_generation_id=created.generation_id,
        now=NOW + timedelta(seconds=1),
    )
    _publish_forged_transition(
        service,
        target=TaskStatus.BLOCKED,
        blocked_reason="sk-proj-" + "z" * 32,
    )

    with pytest.raises(TaskTransitionRejected, match="sensitivity gate"):
        service.snapshot()


@pytest.mark.parametrize("field", ["artifact_ids", "extensions"])
def test_hash_valid_forged_root_scans_every_persisted_task_field(
    project_root: Path,
    field: str,
) -> None:
    secret = "sk-proj-" + "v" * 32
    service = TaskService(project_root)
    if field == "artifact_ids":
        _publish_forged_root(service, artifact_ids=(secret,))
    else:
        _publish_forged_root(service, extensions={"payload": secret})

    with pytest.raises(TaskTransitionRejected, match="sensitivity gate"):
        service.snapshot()


def test_task_text_secret_is_rejected_before_any_task_state(project_root: Path) -> None:
    service = TaskService(project_root)
    planted = "sk-proj-" + "s" * 32

    with pytest.raises(TaskTransitionRejected, match="sensitivity gate"):
        service.create_task(
            title="Do not persist this",
            description=planted,
            actor="user:local",
            idempotency_key="secret-task",
            expected_generation_id=None,
            now=NOW,
        )

    assert service.snapshot().generation_id is None
    assert not (project_root / "ops" / "task-ledger").exists()


def test_task_timestamps_require_timezone_and_monotonic_updates(project_root: Path) -> None:
    service = TaskService(project_root)
    with pytest.raises(TaskTransitionRejected, match="timezone"):
        service.create_task(
            title="Naive time",
            actor="user:local",
            idempotency_key="naive-time",
            expected_generation_id=None,
            now=datetime(2026, 7, 11, 12),
        )

    created = service.create_task(
        title="Monotonic time",
        actor="user:local",
        idempotency_key="monotonic-time",
        expected_generation_id=None,
        now=NOW,
    )
    with pytest.raises(TaskTransitionRejected, match="backwards"):
        service.transition_task(
            created.task.task_id,
            TaskStatus.PLANNED,
            actor="user:local",
            idempotency_key="backwards-time",
            expected_generation_id=created.generation_id,
            now=NOW - timedelta(seconds=1),
        )


def test_task_ledger_objects_are_private(project_root: Path) -> None:
    service = TaskService(project_root)
    service.create_task(
        title="Private operational state",
        actor="user:local",
        idempotency_key="private-mode",
        expected_generation_id=None,
        now=NOW,
    )

    ledger_root = project_root / "ops" / "task-ledger"
    assert stat.S_IMODE(ledger_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((ledger_root / "CURRENT").stat().st_mode) == 0o600
    for path in ledger_root.rglob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinked_writer_lock_is_rejected(project_root: Path) -> None:
    lock_root = project_root / "ops" / "locks"
    lock_root.mkdir(parents=True)
    target = project_root / "outside-lock-target"
    target.write_text("do not touch", encoding="utf-8")
    (lock_root / "task-ledger.lock").symlink_to(target)

    with pytest.raises(TaskLedgerError, match="lock is unavailable"):
        TaskService(project_root).create_task(
            title="Unsafe lock",
            actor="user:local",
            idempotency_key="unsafe-lock",
            expected_generation_id=None,
            now=NOW,
        )

    assert target.read_text(encoding="utf-8") == "do not touch"
