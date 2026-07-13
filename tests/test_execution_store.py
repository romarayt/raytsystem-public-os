from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raytsystem.contracts.base import derive_id
from raytsystem.contracts.execution import (
    CommentKind,
    ExecutionComment,
    TaskAssignment,
    TranscriptEvent,
)
from raytsystem.control import ControlDB
from raytsystem.execution.store import (
    ExecutionStore,
    ExecutionStoreConflict,
    ExecutionStoreError,
    ExecutionStoreUnavailable,
)

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _comment(body: str = "Work started") -> ExecutionComment:
    seed = ExecutionComment(
        comment_id="comment_pending",
        task_id="task_example",
        kind=CommentKind.PROGRESS,
        actor="employee_example",
        body=body,
        created_at=NOW,
    )
    return seed.model_copy(update={"comment_id": derive_id("comment", seed.identity_payload())})


def _assignment(*, revision: int = 1, employee_id: str = "employee_example") -> TaskAssignment:
    return TaskAssignment(
        assignment_id="assignment_example",
        task_id="task_example",
        task_generation_id="tgen_example",
        task_revision=2,
        employee_id=employee_id,
        runtime_adapter_id="adapter_fake",
        revision=revision,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(sequence: int, text: str = "safe") -> TranscriptEvent:
    return TranscriptEvent(
        transcript_event_id=f"transcript_event_{sequence}",
        run_id="xrun_example",
        sequence=sequence,
        stream="stdout",
        event_type="runtime_output",
        text=text,
        created_at=NOW,
    )


def test_read_only_open_does_not_create_database_or_parent(tmp_path: Path) -> None:
    path = tmp_path / "ops" / "control.sqlite"

    store = ExecutionStore.open_for_read(path)

    assert store is None
    assert not path.parent.exists()


def test_writer_initializes_only_execution_tables_in_existing_control_db(tmp_path: Path) -> None:
    path = tmp_path / "ops" / "control.sqlite"
    control = ControlDB(path)
    try:
        observed_before = {
            str(row[0])
            for row in control.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        assert ExecutionStore.open_for_read(path) is None
        observed_after_read = {
            str(row[0])
            for row in control.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        assert observed_after_read == observed_before
        with ExecutionStore.open_for_write(path) as writer:
            assert writer.put(_comment(), expected_revision=None) == 1
        lease = control.acquire_lease(
            "task:example",
            "xrun_example",
            ttl_ms=1_000,
            now_ms=1,
        )
        assert lease.fencing_token == 1
    finally:
        control.close()

    with ExecutionStore.open_for_read(path) as reader:  # type: ignore[attr-defined]
        assert reader is not None
        assert reader.get(ExecutionComment, _comment().comment_id) == _comment()


def test_immutable_append_is_idempotent_and_collision_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        comment = _comment()
        assert store.put(comment, expected_revision=None) == 1
        assert store.put(comment, expected_revision=None) == 1
        forged = comment.model_copy(update={"body": "different"})
        with pytest.raises(ExecutionStoreConflict, match="ID is invalid"):
            store.put(forged, expected_revision=1)


def test_mutable_records_require_exact_revision_and_assignment_revision(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        assert store.put(_assignment(), expected_revision=None) == 1
        with pytest.raises(ExecutionStoreConflict, match="revision changed"):
            store.put(_assignment(revision=2, employee_id="employee_other"), expected_revision=0)
        assert (
            store.put(
                _assignment(revision=2, employee_id="employee_other"),
                expected_revision=1,
            )
            == 2
        )


def test_transcript_is_contiguous_idempotent_and_secret_safe(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        first = _event(0)
        assert store.append_transcript(first)
        assert not store.append_transcript(first)
        with pytest.raises(ExecutionStoreConflict, match="contiguous"):
            store.append_transcript(_event(2))
        with pytest.raises(ExecutionStoreError, match="redacted"):
            store.append_transcript(_event(1, "sk-proj-abcdefghijklmnopqrstuvwxyz123456"))
        redacted = _event(1, "[REDACTED: sensitive runtime output]").model_copy(
            update={"redacted": True}
        )
        assert store.append_transcript(redacted)
        assert store.list_transcript("xrun_example") == (first, redacted)


def test_idempotency_key_is_bound_to_request_and_receipt(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        assert store.store_receipt(
            scope="heartbeat",
            idempotency_key="idem_example",
            request={"task_id": "task_example"},
            receipt={"run_id": "xrun_example"},
        )
        assert not store.store_receipt(
            scope="heartbeat",
            idempotency_key="idem_example",
            request={"task_id": "task_example"},
            receipt={"run_id": "xrun_example"},
        )
        with pytest.raises(ExecutionStoreConflict, match="another request"):
            store.receipt(
                scope="heartbeat",
                idempotency_key="idem_example",
                request={"task_id": "task_other"},
            )


def test_future_schema_is_rejected_before_any_v1_table_is_created(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE execution_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO execution_meta VALUES ('schema_version', '2')")
    connection.commit()
    connection.close()

    with pytest.raises(ExecutionStoreUnavailable, match="requires migration"):
        ExecutionStore.open_for_write(path)

    connection = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert tables == {"execution_meta"}


def test_receipts_reject_secrets_and_verify_integrity(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        with pytest.raises(ExecutionStoreError, match="redacted"):
            store.store_receipt(
                scope="heartbeat",
                idempotency_key="idem_secret",
                request={"task_id": "task_example"},
                receipt={"token": "sk-proj-abcdefghijklmnopqrstuvwxyz123456"},
            )
        store.store_receipt(
            scope="heartbeat",
            idempotency_key="idem_safe",
            request={"task_id": "task_example"},
            receipt={"run_id": "xrun_example"},
        )
        store.connection.execute(
            "UPDATE execution_idempotency SET receipt_json='{}' WHERE idempotency_key='idem_safe'"
        )
        with pytest.raises(ExecutionStoreError, match="receipt hash"):
            store.receipt(
                scope="heartbeat",
                idempotency_key="idem_safe",
                request={"task_id": "task_example"},
            )


def test_corrupt_head_and_transcript_identity_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    with ExecutionStore.open_for_write(path) as store:
        comment = _comment()
        store.put(comment, expected_revision=None)
        store.connection.execute(
            "UPDATE execution_record_heads SET payload_sha256=? WHERE record_id=?",
            ("0" * 64, comment.comment_id),
        )
        with pytest.raises(ExecutionStoreError, match="head hash"):
            store.put(comment, expected_revision=1)

        first = _event(0)
        store.append_transcript(first)
        collision = _event(0).model_copy(
            update={
                "run_id": "xrun_other",
                "transcript_event_id": first.transcript_event_id,
            }
        )
        with pytest.raises(ExecutionStoreConflict, match="identity collision"):
            store.append_transcript(collision)


def test_two_writers_cannot_overwrite_same_expected_revision(tmp_path: Path) -> None:
    path = tmp_path / "control.sqlite"
    first = ExecutionStore.open_for_write(path)
    second = ExecutionStore.open_for_write(path)
    try:
        first.put(_assignment(), expected_revision=None)
        with pytest.raises(ExecutionStoreConflict, match="revision changed"):
            second.put(
                _assignment(revision=2, employee_id="employee_other"),
                expected_revision=None,
            )
    finally:
        first.close()
        second.close()
