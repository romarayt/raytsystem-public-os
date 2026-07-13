from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.contracts import (
    AgentTask,
    Sensitivity,
    TaskBoardGeneration,
    TaskEvent,
    TaskEventKind,
    TaskPriority,
    TaskStatus,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.io import UnsafeWritePath, ensure_safe_directory, write_bytes_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import IntegrityError, publish_immutable


class TaskLedgerError(IntegrityError):
    """The task command or append-only board ledger is invalid."""


class TaskConflict(TaskLedgerError):
    """The caller acted on a stale task-board generation."""


class TaskTransitionRejected(TaskLedgerError):
    """A requested task state transition violates the deterministic state machine."""


_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.INBOX: frozenset({TaskStatus.PLANNED, TaskStatus.CANCELLED}),
    TaskStatus.PLANNED: frozenset({TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.REVIEW, TaskStatus.BLOCKED, TaskStatus.CANCELLED}),
    TaskStatus.REVIEW: frozenset(
        {TaskStatus.DONE, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.BLOCKED: frozenset(
        {TaskStatus.PLANNED, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.CANCELLED}
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}
_TRANSITION_MUTABLE_FIELDS = frozenset({"status", "blocked_reason", "revision", "updated_at"})


@dataclass(frozen=True)
class TaskBoardSnapshot:
    generation_id: str | None
    generation_sha256: str | None
    head_event_id: str | None
    tasks: tuple[AgentTask, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "generation_sha256": self.generation_sha256,
            "head_event_id": self.head_event_id,
            "tasks": [task.model_dump(mode="json") for task in self.tasks],
        }


@dataclass(frozen=True)
class TaskCommandResult:
    generation_id: str
    generation_sha256: str
    event_id: str
    task: AgentTask
    no_op: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task"] = self.task.model_dump(mode="json")
        return payload


class TaskService:
    version = "1.0.0"
    max_events = 10_000
    max_tasks = 2_500
    max_generation_bytes = 16 * 1024 * 1024

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.ledger_root = self.root / "ops" / "task-ledger"
        self.objects_root = self.ledger_root / "objects" / "sha256"
        self.events_root = self.ledger_root / "events"
        self.generations_root = self.ledger_root / "generations"
        self.pointer_path = self.ledger_root / "CURRENT"
        self.lock_path = self.root / "ops" / "locks" / "task-ledger.lock"
        self.scanner = scanner or SecretScanner()

    def snapshot(self) -> TaskBoardSnapshot:
        generation = self._read_current_generation()
        if generation is None:
            return TaskBoardSnapshot(None, None, None, ())
        tasks = tuple(
            self._read_task(task_id, task_sha256)
            for task_id, task_sha256 in sorted(generation.task_hashes.items())
        )
        return TaskBoardSnapshot(
            generation_id=generation.generation_id,
            generation_sha256=generation.manifest_sha256(),
            head_event_id=generation.head_event_id,
            tasks=tasks,
        )

    def history(
        self,
        task_id: str,
        *,
        expected_generation_id: str | None = None,
    ) -> tuple[TaskEvent, ...]:
        generation = self._read_current_generation()
        if expected_generation_id is not None and (
            generation is None or generation.generation_id != expected_generation_id
        ):
            raise TaskConflict("Task board changed before history could be read")
        if generation is None or task_id not in generation.task_hashes:
            raise TaskTransitionRejected("Unknown task")
        events: list[TaskEvent] = []
        event_id = generation.head_event_id
        visited: set[str] = set()
        while event_id is not None:
            if event_id in visited or len(visited) >= self.max_events:
                raise TaskLedgerError("Task event history is cyclic or exceeds limits")
            visited.add(event_id)
            event = self._read_event(event_id)
            if event.task_id == task_id:
                events.append(event)
            event_id = event.previous_event_id
        return tuple(reversed(events))

    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        project_id: str = "project_default",
        mission_id: str | None = None,
        assignee_ids: tuple[str, ...] = (),
        skill_ids: tuple[str, ...] = (),
        dependency_ids: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        actor: str = "user:local",
        idempotency_key: str,
        expected_generation_id: str | None = None,
        now: datetime | None = None,
    ) -> TaskCommandResult:
        timestamp = self._timestamp(now)
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
            "project_id": project_id,
            "mission_id": mission_id,
            "assignee_ids": tuple(sorted(set(assignee_ids))),
            "skill_ids": tuple(sorted(set(skill_ids))),
            "dependency_ids": tuple(sorted(set(dependency_ids))),
            "tags": tuple(sorted(set(tags))),
            "actor": actor,
        }
        sensitivity = self._scan_task_payload(payload)
        normalized_idempotency = self._idempotency_id(idempotency_key, actor=actor)
        operation_key = derive_id(
            "op",
            {
                "service": self.version,
                "command": "create_task",
                "idempotency_key": normalized_idempotency,
                "payload": payload,
            },
        )
        with self._writer_lock():
            generation = self._read_current_generation()
            existing = self._find_operation(
                generation,
                operation_key=operation_key,
                idempotency_key=normalized_idempotency,
            )
            if existing is not None:
                return self._result_for_event(generation, existing, no_op=True)
            self._assert_expected(generation, expected_generation_id)
            known_tasks = self._load_tasks(generation)
            if len(known_tasks) >= self.max_tasks:
                raise TaskTransitionRejected("Task board reached its configured task limit")
            missing_dependencies = sorted(set(dependency_ids) - set(known_tasks))
            if missing_dependencies:
                raise TaskTransitionRejected("Task dependencies must already exist")
            task_id = derive_id("task", {"operation_key": operation_key})
            try:
                task = AgentTask(
                    task_id=task_id,
                    title=title,
                    description=description,
                    priority=priority,
                    project_id=project_id,
                    mission_id=mission_id,
                    assignee_ids=tuple(sorted(set(assignee_ids))),
                    skill_ids=tuple(sorted(set(skill_ids))),
                    dependency_ids=tuple(sorted(set(dependency_ids))),
                    tags=tuple(sorted(set(tags))),
                    sensitivity=sensitivity,
                    created_by=actor,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            except ValidationError as error:
                raise TaskTransitionRejected("Task payload is invalid") from error
            return self._commit(
                generation,
                task,
                event_kind=TaskEventKind.CREATED,
                operation_key=operation_key,
                idempotency_key=normalized_idempotency,
                actor=actor,
                timestamp=timestamp,
                previous_task_sha256=None,
                from_status=None,
            )

    def transition_task(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        actor: str = "user:local",
        idempotency_key: str,
        expected_generation_id: str,
        blocked_reason: str | None = None,
        now: datetime | None = None,
    ) -> TaskCommandResult:
        timestamp = self._timestamp(now)
        normalized_idempotency = self._idempotency_id(idempotency_key, actor=actor)
        self._scan_task_payload(
            {
                "actor": actor,
                "blocked_reason": blocked_reason,
                "task_id": task_id,
                "target": target,
            }
        )
        operation_key = derive_id(
            "op",
            {
                "service": self.version,
                "command": "transition_task",
                "task_id": task_id,
                "target": target,
                "blocked_reason": blocked_reason,
                "actor": actor,
                "idempotency_key": normalized_idempotency,
            },
        )
        with self._writer_lock():
            generation = self._read_current_generation()
            existing = self._find_operation(
                generation,
                operation_key=operation_key,
                idempotency_key=normalized_idempotency,
            )
            if existing is not None:
                return self._result_for_event(generation, existing, no_op=True)
            self._assert_expected(generation, expected_generation_id)
            tasks = self._load_tasks(generation)
            try:
                current = tasks[task_id]
            except KeyError as error:
                raise TaskTransitionRejected("Unknown task") from error
            if target not in _TRANSITIONS[current.status]:
                raise TaskTransitionRejected(
                    f"Illegal task transition: {current.status.value} -> {target.value}"
                )
            if target in {TaskStatus.READY, TaskStatus.RUNNING}:
                try:
                    incomplete = [
                        dependency_id
                        for dependency_id in current.dependency_ids
                        if tasks[dependency_id].status is not TaskStatus.DONE
                    ]
                except KeyError as error:
                    raise TaskLedgerError("Task dependency index is inconsistent") from error
                if incomplete:
                    raise TaskTransitionRejected("Task dependencies are not complete")
            if timestamp < current.updated_at:
                raise TaskTransitionRejected("Task transition timestamp cannot move backwards")
            try:
                next_task = current.model_copy(
                    update={
                        "status": target,
                        "blocked_reason": blocked_reason if target is TaskStatus.BLOCKED else None,
                        "revision": current.revision + 1,
                        "updated_at": timestamp,
                    }
                )
                next_task = AgentTask.model_validate(next_task.model_dump(mode="python"))
            except ValidationError as error:
                raise TaskTransitionRejected("Task transition payload is invalid") from error
            previous_hash = generation.task_hashes[task_id] if generation is not None else None
            event_kind = (
                TaskEventKind.CANCELLED
                if target is TaskStatus.CANCELLED
                else TaskEventKind.TRANSITIONED
            )
            return self._commit(
                generation,
                next_task,
                event_kind=event_kind,
                operation_key=operation_key,
                idempotency_key=normalized_idempotency,
                actor=actor,
                timestamp=timestamp,
                previous_task_sha256=previous_hash,
                from_status=current.status,
            )

    def _commit(
        self,
        generation: TaskBoardGeneration | None,
        task: AgentTask,
        *,
        event_kind: TaskEventKind,
        operation_key: str,
        idempotency_key: str,
        actor: str,
        timestamp: datetime,
        previous_task_sha256: str | None,
        from_status: TaskStatus | None,
    ) -> TaskCommandResult:
        task_bytes = canonical_json_bytes(task)
        task_sha256 = sha256_hex(task_bytes)
        task_path = self.objects_root / task_sha256[:2] / f"{task_sha256}.json"
        self._publish_private(task_path, task_bytes)
        event_material: dict[str, Any] = {
            "event_kind": event_kind,
            "task_id": task.task_id,
            "operation_key": operation_key,
            "idempotency_key": idempotency_key,
            "previous_event_id": None if generation is None else generation.head_event_id,
            "previous_task_sha256": previous_task_sha256,
            "task_sha256": task_sha256,
            "from_status": from_status,
            "to_status": task.status,
            "actor": actor,
            "created_at": timestamp,
        }
        event_seed = TaskEvent(event_id="tevt_pending", **event_material)
        event = event_seed.model_copy(
            update={"event_id": derive_id("tevt", event_seed.identity_payload())}
        )
        self._publish_private(
            self.events_root / f"{event.event_id}.json",
            canonical_json_bytes(event),
        )
        task_hashes = {} if generation is None else dict(generation.task_hashes)
        latest_event_ids = {} if generation is None else dict(generation.latest_event_ids)
        task_hashes[task.task_id] = task_sha256
        latest_event_ids[task.task_id] = event.event_id
        next_event_count = 1 if generation is None else generation.event_count + 1
        if next_event_count > self.max_events:
            raise TaskTransitionRejected("Task board reached its configured event limit")
        generation_material: dict[str, Any] = {
            "parent_generation_id": None if generation is None else generation.generation_id,
            "parent_generation_sha256": (
                None if generation is None else generation.manifest_sha256()
            ),
            "task_hashes": task_hashes,
            "latest_event_ids": latest_event_ids,
            "head_event_id": event.event_id,
            "event_count": next_event_count,
            "created_at": timestamp,
        }
        generation_seed = TaskBoardGeneration(
            generation_id="tgen_pending",
            **generation_material,
        )
        next_generation = generation_seed.model_copy(
            update={
                "generation_id": derive_id("tgen", generation_seed.identity_payload()),
            }
        )
        generation_bytes = canonical_json_bytes(next_generation)
        if len(generation_bytes) > self.max_generation_bytes:
            raise TaskTransitionRejected("Task board generation exceeds its safe size limit")
        self._publish_private(
            self.generations_root / f"{next_generation.generation_id}.json",
            generation_bytes,
        )
        self._validate_generation_closure(next_generation)
        if self._read_current_generation() != generation:
            raise TaskConflict("Task board changed before pointer commit")
        try:
            self._ensure_private_directory(self.ledger_root)
            write_bytes_atomic(
                self.pointer_path,
                f"{next_generation.generation_id}\n".encode("ascii"),
                mode=0o600,
            )
        except (OSError, UnsafeWritePath) as error:
            raise TaskLedgerError("Task board pointer commit failed") from error
        return TaskCommandResult(
            generation_id=next_generation.generation_id,
            generation_sha256=next_generation.manifest_sha256(),
            event_id=event.event_id,
            task=task,
            no_op=False,
        )

    def _read_current_generation(self) -> TaskBoardGeneration | None:
        if not self.pointer_path.exists() and not self.pointer_path.is_symlink():
            return None
        try:
            pointer = read_regular_file(
                self.root,
                self.pointer_path.relative_to(self.root).as_posix(),
                max_bytes=512,
            ).data.decode("ascii")
        except (UnicodeDecodeError, OSError, PathPolicyError) as error:
            raise TaskLedgerError("Task board pointer is unsafe or invalid") from error
        generation_id = pointer.strip()
        if pointer != f"{generation_id}\n" or not generation_id.startswith("tgen_"):
            raise TaskLedgerError("Task board pointer is malformed")
        generation = self._read_generation(generation_id)
        self._validate_generation_closure(generation)
        return generation

    def _read_generation(self, generation_id: str) -> TaskBoardGeneration:
        relative = (self.generations_root / f"{generation_id}.json").relative_to(self.root)
        try:
            data = read_regular_file(
                self.root,
                relative.as_posix(),
                max_bytes=16 * 1024 * 1024,
            ).data
            generation = TaskBoardGeneration.model_validate(json.loads(data))
        except (
            OSError,
            PathPolicyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise TaskLedgerError("Task board generation is unsafe or invalid") from error
        if (
            data != canonical_json_bytes(generation)
            or len(data) > self.max_generation_bytes
            or not generation.verify_id()
        ):
            raise TaskLedgerError("Task board generation integrity failed")
        if generation.generation_id != generation_id:
            raise TaskLedgerError("Task board generation identity mismatch")
        return generation

    def _validate_generation_closure(self, generation: TaskBoardGeneration) -> None:
        current = generation
        visited_generations: set[str] = set()
        visited_events: set[str] = set()
        operation_keys: set[str] = set()
        idempotency_keys: set[str] = set()
        steps = 0

        self._validate_task_graph(self._load_tasks(generation))

        for task_id, event_id in current.latest_event_ids.items():
            event = self._read_event(event_id)
            if event.task_id != task_id or event.task_sha256 != current.task_hashes[task_id]:
                raise TaskLedgerError("Task board latest-event index is inconsistent")
            task = self._read_task(task_id, current.task_hashes[task_id])
            if event.to_status is not task.status:
                raise TaskLedgerError("Task board latest event disagrees with task status")

        while True:
            if current.generation_id in visited_generations or steps >= self.max_events:
                raise TaskLedgerError("Task generation ancestry is cyclic or exceeds limits")
            visited_generations.add(current.generation_id)
            steps += 1
            if current.head_event_id is None:
                raise TaskLedgerError("Task generation has no head event")
            if current.head_event_id in visited_events:
                raise TaskLedgerError("Task event history is cyclic")
            visited_events.add(current.head_event_id)
            head = self._read_event(current.head_event_id)
            if head.operation_key in operation_keys or head.idempotency_key in idempotency_keys:
                raise TaskLedgerError("Task event command identity is duplicated")
            operation_keys.add(head.operation_key)
            idempotency_keys.add(head.idempotency_key)
            if current.latest_event_ids.get(head.task_id) != head.event_id:
                raise TaskLedgerError("Task generation head is not the latest task event")
            if current.task_hashes.get(head.task_id) != head.task_sha256:
                raise TaskLedgerError("Task generation head references the wrong task object")
            task = self._read_task(head.task_id, head.task_sha256)
            if task.status is not head.to_status or task.updated_at != head.created_at:
                raise TaskLedgerError("Task event and task revision disagree")
            if current.created_at != head.created_at:
                raise TaskLedgerError("Task generation and head event timestamps disagree")

            if current.parent_generation_id is None:
                if (
                    current.parent_generation_sha256 is not None
                    or current.event_count != 1
                    or len(current.task_hashes) != 1
                    or head.previous_event_id is not None
                    or head.event_kind is not TaskEventKind.CREATED
                    or task.revision != 1
                ):
                    raise TaskLedgerError("Root task generation closure is invalid")
                self._validate_created_event(head, task, parent=None)
                break

            parent = self._read_generation(current.parent_generation_id)
            if parent.manifest_sha256() != current.parent_generation_sha256:
                raise TaskLedgerError("Task parent generation hash mismatch")
            if current.event_count != parent.event_count + 1:
                raise TaskLedgerError("Task generation event count is inconsistent")
            if current.created_at < parent.created_at:
                raise TaskLedgerError("Task generation timestamp moved backwards")
            if head.previous_event_id != parent.head_event_id:
                raise TaskLedgerError("Task event history does not follow generation ancestry")
            removed_tasks = set(parent.task_hashes) - set(current.task_hashes)
            changed_tasks = {
                task_id
                for task_id, task_sha256 in current.task_hashes.items()
                if parent.task_hashes.get(task_id) != task_sha256
            }
            changed_events = {
                task_id
                for task_id, event_id in current.latest_event_ids.items()
                if parent.latest_event_ids.get(task_id) != event_id
            }
            if removed_tasks or changed_tasks != {head.task_id} or changed_events != {head.task_id}:
                raise TaskLedgerError("Task generation must change exactly its head task")
            if head.event_kind is TaskEventKind.CREATED:
                if head.task_id in parent.task_hashes or task.revision != 1:
                    raise TaskLedgerError("Created task event already exists in its parent")
                self._validate_created_event(head, task, parent=parent)
            else:
                parent_sha256 = parent.task_hashes.get(head.task_id)
                if parent_sha256 is None or head.previous_task_sha256 != parent_sha256:
                    raise TaskLedgerError("Task transition prior object is inconsistent")
                previous = self._read_task(head.task_id, parent_sha256)
                if (
                    head.from_status is not previous.status
                    or task.revision != previous.revision + 1
                    or task.created_at != previous.created_at
                    or task.updated_at < previous.updated_at
                ):
                    raise TaskLedgerError("Task revision history is inconsistent")
                self._validate_transition_event(head, previous, task, current)
            current = parent

        if steps != generation.event_count:
            raise TaskLedgerError("Task generation ancestry length is inconsistent")

    def _validate_created_event(
        self,
        event: TaskEvent,
        task: AgentTask,
        *,
        parent: TaskBoardGeneration | None,
    ) -> None:
        payload = {
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "project_id": task.project_id,
            "mission_id": task.mission_id,
            "assignee_ids": task.assignee_ids,
            "skill_ids": task.skill_ids,
            "dependency_ids": task.dependency_ids,
            "tags": task.tags,
            "actor": event.actor,
        }
        recovered_sensitivity = self._scan_task_payload(payload)
        self._scan_task_payload(task.model_dump(mode="python"))
        expected_operation = derive_id(
            "op",
            {
                "service": self.version,
                "command": "create_task",
                "idempotency_key": event.idempotency_key,
                "payload": payload,
            },
        )
        known_parent_tasks = set() if parent is None else set(parent.task_hashes)
        if (
            event.operation_key != expected_operation
            or task.task_id != derive_id("task", {"operation_key": event.operation_key})
            or task.status is not TaskStatus.INBOX
            or task.created_by != event.actor
            or task.created_at != event.created_at
            or task.updated_at != event.created_at
            or task.sensitivity is not recovered_sensitivity
            or task.artifact_ids
            or task.extensions
            or not set(task.dependency_ids).issubset(known_parent_tasks)
        ):
            raise TaskLedgerError("Created task event semantics are invalid")

    def _validate_transition_event(
        self,
        event: TaskEvent,
        previous: AgentTask,
        task: AgentTask,
        generation: TaskBoardGeneration,
    ) -> None:
        self._scan_task_payload(
            {
                "actor": event.actor,
                "blocked_reason": task.blocked_reason,
                "task_id": task.task_id,
                "target": task.status,
            }
        )
        self._scan_task_payload(task.model_dump(mode="python"))
        expected_operation = derive_id(
            "op",
            {
                "service": self.version,
                "command": "transition_task",
                "task_id": task.task_id,
                "target": task.status,
                "blocked_reason": task.blocked_reason,
                "actor": event.actor,
                "idempotency_key": event.idempotency_key,
            },
        )
        immutable_changed = {
            field
            for field in AgentTask.model_fields
            if field not in _TRANSITION_MUTABLE_FIELDS
            and getattr(previous, field) != getattr(task, field)
        }
        if (
            task.status not in _TRANSITIONS[previous.status]
            or event.operation_key != expected_operation
            or immutable_changed
        ):
            raise TaskLedgerError("Task transition semantics are invalid")
        if task.status in {TaskStatus.READY, TaskStatus.RUNNING}:
            for dependency_id in task.dependency_ids:
                dependency_sha256 = generation.task_hashes.get(dependency_id)
                if dependency_sha256 is None:
                    raise TaskLedgerError("Task transition dependency is missing")
                dependency = self._read_task(dependency_id, dependency_sha256)
                if dependency.status is not TaskStatus.DONE:
                    raise TaskLedgerError("Task transition dependency was incomplete")

    def _validate_task_graph(self, tasks: dict[str, AgentTask]) -> None:
        for task in tasks.values():
            if set(task.dependency_ids) - set(tasks):
                raise TaskLedgerError("Task dependency index is inconsistent")
            if task.status in {TaskStatus.READY, TaskStatus.RUNNING} and any(
                tasks[dependency_id].status is not TaskStatus.DONE
                for dependency_id in task.dependency_ids
            ):
                raise TaskLedgerError("Ready task has incomplete dependencies")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TaskLedgerError("Task dependency graph is cyclic")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency_id in tasks[task_id].dependency_ids:
                visit(dependency_id)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in sorted(tasks):
            visit(task_id)

    def _read_task(self, task_id: str, task_sha256: str) -> AgentTask:
        relative = (self.objects_root / task_sha256[:2] / f"{task_sha256}.json").relative_to(
            self.root
        )
        try:
            data = read_regular_file(self.root, relative.as_posix(), max_bytes=1024 * 1024).data
            task = AgentTask.model_validate(json.loads(data))
        except (
            OSError,
            PathPolicyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise TaskLedgerError("Task object is unsafe or invalid") from error
        if (
            sha256_hex(data) != task_sha256
            or data != canonical_json_bytes(task)
            or task.task_id != task_id
        ):
            raise TaskLedgerError("Task object integrity failed")
        return task

    def _read_event(self, event_id: str) -> TaskEvent:
        relative = (self.events_root / f"{event_id}.json").relative_to(self.root)
        try:
            data = read_regular_file(self.root, relative.as_posix(), max_bytes=1024 * 1024).data
            event = TaskEvent.model_validate(json.loads(data))
        except (
            OSError,
            PathPolicyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise TaskLedgerError("Task event is unsafe or invalid") from error
        if (
            data != canonical_json_bytes(event)
            or event.event_id != event_id
            or not event.verify_id()
        ):
            raise TaskLedgerError("Task event integrity failed")
        return event

    def _load_tasks(self, generation: TaskBoardGeneration | None) -> dict[str, AgentTask]:
        if generation is None:
            return {}
        return {
            task_id: self._read_task(task_id, task_sha256)
            for task_id, task_sha256 in generation.task_hashes.items()
        }

    def _find_operation(
        self,
        generation: TaskBoardGeneration | None,
        *,
        operation_key: str,
        idempotency_key: str,
    ) -> TaskEvent | None:
        event_id = None if generation is None else generation.head_event_id
        visited: set[str] = set()
        while event_id is not None:
            if event_id in visited or len(visited) >= 100_000:
                raise TaskLedgerError("Task event chain is cyclic or exceeds limits")
            visited.add(event_id)
            event = self._read_event(event_id)
            if event.operation_key == operation_key:
                return event
            if event.idempotency_key == idempotency_key:
                raise TaskConflict("Idempotency key was already used for another command")
            event_id = event.previous_event_id
        return None

    @staticmethod
    def _idempotency_id(value: str, *, actor: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned.encode("utf-8")) > 512 or "\x00" in cleaned:
            raise TaskTransitionRejected("Idempotency key is invalid")
        return derive_id("idem", {"actor": actor, "value": cleaned})

    @staticmethod
    def _timestamp(value: datetime | None) -> datetime:
        timestamp = value or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise TaskTransitionRejected("Task timestamps must include a timezone")
        normalized = timestamp.astimezone(UTC)
        return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)

    def _scan_task_payload(self, payload: dict[str, Any]) -> Sensitivity:
        try:
            decision = self.scanner.scan(
                canonical_json_bytes(payload),
                path="ops/task-ledger/task.json",
            )
        except Exception as error:
            raise TaskTransitionRejected("Task sensitivity scanner failed closed") from error
        if decision.blocks_processing or decision.sensitivity not in {"internal", "public"}:
            raise TaskTransitionRejected("Task payload failed the sensitivity gate")
        return Sensitivity(decision.sensitivity)

    @staticmethod
    def _assert_expected(
        generation: TaskBoardGeneration | None,
        expected_generation_id: str | None,
    ) -> None:
        current = None if generation is None else generation.generation_id
        if expected_generation_id != current:
            raise TaskConflict("Task board generation changed")

    def _result_for_event(
        self,
        generation: TaskBoardGeneration | None,
        event: TaskEvent,
        *,
        no_op: bool,
    ) -> TaskCommandResult:
        if generation is None:
            raise TaskLedgerError("Reachable event requires a task generation")
        result_generation = self._generation_for_event(generation, event.event_id)
        task_sha256 = result_generation.task_hashes.get(event.task_id)
        if task_sha256 != event.task_sha256:
            raise TaskLedgerError("Idempotent task result is inconsistent")
        task = self._read_task(event.task_id, task_sha256)
        return TaskCommandResult(
            generation_id=result_generation.generation_id,
            generation_sha256=result_generation.manifest_sha256(),
            event_id=event.event_id,
            task=task,
            no_op=no_op,
        )

    def _generation_for_event(
        self,
        generation: TaskBoardGeneration,
        event_id: str,
    ) -> TaskBoardGeneration:
        current = generation
        for _ in range(self.max_events):
            if current.head_event_id == event_id:
                return current
            if current.parent_generation_id is None:
                break
            current = self._read_generation(current.parent_generation_id)
        raise TaskLedgerError("Idempotent result generation is not reachable")

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        ensure_safe_directory(path, mode=0o700)
        os.chmod(path, 0o700, follow_symlinks=False)

    def _publish_private(self, path: Path, data: bytes) -> None:
        try:
            self._ensure_private_directory(path.parent)
            publish_immutable(path, data, mode=0o600)
        except TaskLedgerError:
            raise
        except (IntegrityError, OSError, UnsafeWritePath) as error:
            raise TaskLedgerError("Task immutable object publication failed") from error

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        try:
            ensure_safe_directory(self.lock_path.parent, mode=0o700)
            descriptor = self._open_lock_file()
        except (OSError, UnsafeWritePath) as error:
            raise TaskLedgerError("Task writer lock is unavailable") from error
        locked = False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise UnsafeWritePath("Task writer lock is unsafe")
            self._lock_descriptor(descriptor)
            locked = True
            yield
        finally:
            if locked:
                self._unlock_descriptor(descriptor)
            os.close(descriptor)

    def _open_lock_file(self) -> int:
        common_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        try:
            return os.open(self.lock_path, common_flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        before = os.lstat(self.lock_path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise UnsafeWritePath("Task writer lock is unsafe")
        descriptor = os.open(
            self.lock_path,
            common_flags | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            os.close(descriptor)
            raise UnsafeWritePath("Task writer lock changed during open")
        return descriptor

    @staticmethod
    def _lock_descriptor(descriptor: int) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)

    @staticmethod
    def _unlock_descriptor(descriptor: int) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
