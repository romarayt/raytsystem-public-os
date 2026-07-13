from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar, cast
from urllib.parse import quote

from pydantic import ValidationError

from raytsystem.contracts import PolicyDecision
from raytsystem.contracts.base import VersionedModel, canonical_json_bytes, sha256_hex
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    DigitalEmployee,
    ExecutionApproval,
    ExecutionComment,
    ExecutionInvocation,
    ExecutionRun,
    ExecutionSession,
    TaskAssignment,
    TaskGraphScope,
    TaskLease,
    TaskWorkspace,
    TranscriptEvent,
)
from raytsystem.derived import assert_safe_sqlite_family
from raytsystem.io import ensure_safe_directory
from raytsystem.security import SecretScanner

StoreMode = Literal["read_only", "read_write"]
ModelT = TypeVar("ModelT", bound=VersionedModel)

_SCHEMA_VERSION = "1"
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_:.@/-]{1,255}$")
_DANGEROUS_RUNTIME_MARKERS = (
    b"--dangerously-bypass-approvals-and-sandbox",
    b"--dangerously-skip-permissions",
    b"bypassPermissions",
)
_RECORD_TYPES: dict[str, tuple[type[VersionedModel], str, bool]] = {
    "assignment": (TaskAssignment, "assignment_id", False),
    "approval": (ExecutionApproval, "approval_id", True),
    "budget_policy": (BudgetPolicy, "budget_policy_id", True),
    "budget_usage": (BudgetUsage, "budget_policy_id", False),
    "comment": (ExecutionComment, "comment_id", True),
    "employee": (DigitalEmployee, "employee_id", False),
    "graph_scope": (TaskGraphScope, "graph_scope_id", True),
    "invocation": (ExecutionInvocation, "invocation_id", True),
    "lease": (TaskLease, "lease_id", False),
    "policy_decision": (PolicyDecision, "policy_decision_id", True),
    "run": (ExecutionRun, "run_id", False),
    "session": (ExecutionSession, "session_id", False),
    "workspace": (TaskWorkspace, "workspace_id", False),
}


class ExecutionStoreError(RuntimeError):
    """Execution operational state is unavailable or fails integrity checks."""


class ExecutionStoreConflict(ExecutionStoreError):
    """A caller attempted a stale, colliding, or non-idempotent write."""


class ExecutionStoreUnavailable(ExecutionStoreError):
    """The execution schema has not been initialized."""


class ExecutionStore:
    """Typed operational records in raytsystem's existing local control database."""

    def __init__(
        self,
        path: Path,
        *,
        mode: StoreMode,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.path = path.absolute()
        self.mode = mode
        self.scanner = scanner or SecretScanner()
        if mode == "read_only":
            if not self.path.is_file():
                raise ExecutionStoreUnavailable("Control database is not initialized")
            assert_safe_sqlite_family(self.path)
            encoded = quote(self.path.as_posix(), safe="/")
            connection = sqlite3.connect(
                f"file:{encoded}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
                timeout=5.0,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                self.connection = connection
                self._verify_schema()
                if any(
                    sidecar.is_file() and sidecar.stat().st_size > 0
                    for sidecar in (
                        Path(f"{self.path}-wal"),
                        Path(f"{self.path}-journal"),
                    )
                ):
                    raise ExecutionStoreError(
                        "Control database has uncheckpointed writes; refusing an immutable read"
                    )
            except BaseException:
                connection.close()
                raise
            return
        if mode != "read_write":
            raise ValueError("Unsupported execution store mode")
        ensure_safe_directory(self.path.parent, mode=0o700)
        assert_safe_sqlite_family(self.path)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            self.connection = connection
            self._configure_writer()
            self._initialize_schema()
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_for_read(
        cls,
        path: Path,
        *,
        scanner: SecretScanner | None = None,
    ) -> ExecutionStore | None:
        try:
            return cls(path, mode="read_only", scanner=scanner)
        except ExecutionStoreUnavailable:
            return None
        except sqlite3.Error as error:
            raise ExecutionStoreError(
                "Control database failed read-only integrity checks"
            ) from error

    @classmethod
    def open_for_write(
        cls,
        path: Path,
        *,
        scanner: SecretScanner | None = None,
    ) -> ExecutionStore:
        return cls(path, mode="read_write", scanner=scanner)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ExecutionStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _configure_writer(self) -> None:
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA trusted_schema=OFF")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if sys.platform == "darwin":
            self.connection.execute("PRAGMA fullfsync=ON")
            self.connection.execute("PRAGMA checkpoint_fullfsync=ON")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self.mode != "read_write":
            raise ExecutionStoreError("Read-only execution stores cannot write")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def _initialize_schema(self) -> None:
        prefixed = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'execution_%'"
            ).fetchall()
        }
        if prefixed:
            if "execution_meta" not in prefixed:
                raise ExecutionStoreUnavailable("Partial execution schema requires recovery")
            marker = self.connection.execute(
                "SELECT value FROM execution_meta WHERE key='schema_version'"
            ).fetchone()
            if marker is None or str(marker["value"]) != _SCHEMA_VERSION:
                raise ExecutionStoreUnavailable("Execution schema requires migration")
        self.connection.executescript(
            f"""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS execution_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) STRICT;
                CREATE TABLE IF NOT EXISTS execution_records (
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    task_id TEXT,
                    employee_id TEXT,
                    run_id TEXT,
                    workspace_id TEXT,
                    scope_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(kind, record_id, revision)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS execution_record_heads (
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    PRIMARY KEY(kind, record_id),
                    FOREIGN KEY(kind, record_id, revision)
                        REFERENCES execution_records(kind, record_id, revision)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS execution_transcript_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    transcript_event_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS execution_idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(scope, idempotency_key)
                ) STRICT;
                CREATE INDEX IF NOT EXISTS execution_records_kind_state_idx
                    ON execution_records(kind, state, created_at);
                CREATE INDEX IF NOT EXISTS execution_records_task_idx
                    ON execution_records(task_id, kind, created_at);
                CREATE INDEX IF NOT EXISTS execution_records_employee_idx
                    ON execution_records(employee_id, kind, created_at);
                CREATE INDEX IF NOT EXISTS execution_records_run_idx
                    ON execution_records(run_id, kind, created_at);
                CREATE INDEX IF NOT EXISTS execution_records_workspace_idx
                    ON execution_records(workspace_id, kind, created_at);
                CREATE INDEX IF NOT EXISTS execution_transcript_run_idx
                    ON execution_transcript_events(run_id, sequence);
                INSERT OR IGNORE INTO execution_meta(key, value)
                    VALUES ('schema_version', '{_SCHEMA_VERSION}');
                COMMIT;
                """
        )
        row = self.connection.execute(
            "SELECT value FROM execution_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None or str(row["value"]) != _SCHEMA_VERSION:
            raise ExecutionStoreUnavailable("Execution schema requires migration")
        self._verify_schema()

    def _verify_schema(self) -> None:
        required = {
            "execution_meta",
            "execution_records",
            "execution_record_heads",
            "execution_transcript_events",
            "execution_idempotency",
        }
        observed = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if not required.issubset(observed):
            raise ExecutionStoreUnavailable("Execution schema is not initialized")
        row = self.connection.execute(
            "SELECT value FROM execution_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None or str(row["value"]) != _SCHEMA_VERSION:
            raise ExecutionStoreUnavailable("Execution schema version is unsupported")

    def put(
        self,
        record: VersionedModel,
        *,
        expected_revision: int | None,
    ) -> int:
        kind, record_id, immutable = self._record_identity(record)
        self._validate_record_shape(record)
        rendered = canonical_json_bytes(record)
        self._validate_persisted_payload(rendered)
        digest = sha256_hex(rendered)
        state = self._state(record)
        created_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._transaction():
            head = self.connection.execute(
                "SELECT h.revision, h.payload_sha256 AS head_sha256, r.record_id, "
                "r.payload_json, r.payload_sha256, r.task_id, r.employee_id, r.run_id, "
                "r.workspace_id, r.scope_id FROM execution_record_heads h "
                "LEFT JOIN execution_records r ON r.kind=h.kind AND r.record_id=h.record_id "
                "AND r.revision=h.revision WHERE h.kind=? AND h.record_id=?",
                (kind, record_id),
            ).fetchone()
            if head is not None:
                if head["payload_json"] is None or head["payload_sha256"] is None:
                    raise ExecutionStoreError("Execution record head target is missing")
                if str(head["head_sha256"]) != str(head["payload_sha256"]):
                    raise ExecutionStoreError("Execution record head hash is inconsistent")
                self._decode(type(record), head)
                if str(head["payload_sha256"]) == digest:
                    return int(head["revision"])
            observed_revision = None if head is None else int(head["revision"])
            if immutable and head is not None:
                raise ExecutionStoreConflict("Immutable execution record identity collision")
            if expected_revision != observed_revision:
                raise ExecutionStoreConflict("Execution record revision changed")
            revision = 1 if observed_revision is None else observed_revision + 1
            if isinstance(record, TaskAssignment) and record.revision != revision:
                raise ExecutionStoreConflict("Assignment revision must advance exactly once")
            task_id, employee_id, run_id, workspace_id, scope_id = self._associations(record)
            self.connection.execute(
                "INSERT INTO execution_records "
                "(kind, record_id, revision, payload_json, payload_sha256, state, "
                "task_id, employee_id, run_id, workspace_id, scope_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    record_id,
                    revision,
                    rendered.decode("utf-8"),
                    digest,
                    state,
                    task_id,
                    employee_id,
                    run_id,
                    workspace_id,
                    scope_id,
                    created_at,
                ),
            )
            self.connection.execute(
                "INSERT INTO execution_record_heads "
                "(kind, record_id, revision, payload_sha256) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, record_id) DO UPDATE SET "
                "revision=excluded.revision, payload_sha256=excluded.payload_sha256",
                (kind, record_id, revision, digest),
            )
        return revision

    def get(self, model: type[ModelT], record_id: str) -> ModelT | None:
        self._validate_key(record_id, label="record ID")
        kind = self._kind_for_model(model)
        row = self.connection.execute(
            "SELECT r.record_id, r.payload_json, r.payload_sha256, r.task_id, "
            "r.employee_id, r.run_id, r.workspace_id, r.scope_id "
            "FROM execution_record_heads h "
            "JOIN execution_records r ON r.kind=h.kind AND r.record_id=h.record_id "
            "AND r.revision=h.revision WHERE h.kind=? AND h.record_id=?",
            (kind, record_id),
        ).fetchone()
        return None if row is None else self._decode(model, row)

    def list(
        self,
        model: type[ModelT],
        *,
        state: str | None = None,
        task_id: str | None = None,
        employee_id: str | None = None,
        run_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ModelT, ...]:
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise ValueError("Execution record pagination is out of bounds")
        kind = self._kind_for_model(model)
        for value, label in (
            (state, "state"),
            (task_id, "task ID"),
            (employee_id, "employee ID"),
            (run_id, "run ID"),
            (workspace_id, "workspace ID"),
            (scope_id, "scope ID"),
        ):
            if value is not None:
                self._validate_key(value, label=label)
        clauses = ["h.kind=?"]
        parameters: list[object] = [kind]
        for column, value in (
            ("state", state),
            ("task_id", task_id),
            ("employee_id", employee_id),
            ("run_id", run_id),
            ("workspace_id", workspace_id),
            ("scope_id", scope_id),
        ):
            if value is not None:
                clauses.append(f"r.{column}=?")
                parameters.append(value)
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            "SELECT r.record_id, r.payload_json, r.payload_sha256, r.task_id, "
            "r.employee_id, r.run_id, r.workspace_id, r.scope_id "
            "FROM execution_record_heads h "
            "JOIN execution_records r ON r.kind=h.kind AND r.record_id=h.record_id "
            "AND r.revision=h.revision WHERE "
            + " AND ".join(clauses)
            + " ORDER BY r.created_at DESC, r.record_id LIMIT ? OFFSET ?",
            tuple(parameters),
        ).fetchall()
        return tuple(self._decode(model, row) for row in rows)

    def head_revision(self, model: type[VersionedModel], record_id: str) -> int | None:
        self._validate_key(record_id, label="record ID")
        kind = self._kind_for_model(model)
        row = self.connection.execute(
            "SELECT revision FROM execution_record_heads WHERE kind=? AND record_id=?",
            (kind, record_id),
        ).fetchone()
        return None if row is None else int(row["revision"])

    def append_transcript(self, event: TranscriptEvent) -> bool:
        rendered = canonical_json_bytes(event)
        self._validate_persisted_payload(rendered)
        digest = sha256_hex(rendered)
        with self._transaction():
            identity_collision = self.connection.execute(
                "SELECT run_id, sequence, payload_sha256 FROM execution_transcript_events "
                "WHERE transcript_event_id=?",
                (event.transcript_event_id,),
            ).fetchone()
            if identity_collision is not None and (
                str(identity_collision["run_id"]) != event.run_id
                or int(identity_collision["sequence"]) != event.sequence
                or str(identity_collision["payload_sha256"]) != digest
            ):
                raise ExecutionStoreConflict("Transcript event identity collision")
            existing = self.connection.execute(
                "SELECT transcript_event_id, payload_sha256 FROM execution_transcript_events "
                "WHERE run_id=? AND sequence=?",
                (event.run_id, event.sequence),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["transcript_event_id"]) == event.transcript_event_id
                    and str(existing["payload_sha256"]) == digest
                ):
                    return False
                raise ExecutionStoreConflict("Transcript sequence collision")
            last = self.connection.execute(
                "SELECT MAX(sequence) AS sequence FROM execution_transcript_events WHERE run_id=?",
                (event.run_id,),
            ).fetchone()
            expected = 0 if last is None or last["sequence"] is None else int(last["sequence"]) + 1
            if event.sequence != expected:
                raise ExecutionStoreConflict("Transcript sequence must be contiguous")
            self.connection.execute(
                "INSERT INTO execution_transcript_events "
                "(run_id, sequence, transcript_event_id, payload_json, payload_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    event.sequence,
                    event.transcript_event_id,
                    rendered.decode("utf-8"),
                    digest,
                    event.created_at.isoformat(timespec="milliseconds"),
                ),
            )
        return True

    def list_transcript(
        self,
        run_id: str,
        *,
        after_sequence: int = -1,
        limit: int = 500,
    ) -> tuple[TranscriptEvent, ...]:
        self._validate_key(run_id, label="run ID")
        if not -1 <= after_sequence <= 10_000_000 or not 1 <= limit <= 1_000:
            raise ValueError("Transcript pagination is out of bounds")
        rows = self.connection.execute(
            "SELECT run_id, sequence, transcript_event_id, payload_json, payload_sha256 "
            "FROM execution_transcript_events "
            "WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit),
        ).fetchall()
        events: list[TranscriptEvent] = []
        for row in rows:
            event = self._decode(TranscriptEvent, row)
            if (
                event.run_id != str(row["run_id"])
                or event.sequence != int(row["sequence"])
                or event.transcript_event_id != str(row["transcript_event_id"])
            ):
                raise ExecutionStoreError("Transcript index does not match its payload")
            events.append(event)
        return tuple(events)

    def store_receipt(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request: dict[str, object],
        receipt: dict[str, object],
    ) -> bool:
        self._validate_key(scope, label="idempotency scope")
        self._validate_key(idempotency_key, label="idempotency key")
        request_bytes = canonical_json_bytes(request)
        receipt_bytes = canonical_json_bytes(receipt)
        self._validate_persisted_payload(request_bytes)
        self._validate_persisted_payload(receipt_bytes)
        request_sha256 = sha256_hex(request_bytes)
        receipt_sha256 = sha256_hex(receipt_bytes)
        receipt_json = receipt_bytes.decode("utf-8")
        with self._transaction():
            existing = self.connection.execute(
                "SELECT request_sha256, receipt_json, receipt_sha256 "
                "FROM execution_idempotency "
                "WHERE scope=? AND idempotency_key=?",
                (scope, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["request_sha256"]) == request_sha256
                    and str(existing["receipt_json"]) == receipt_json
                    and str(existing["receipt_sha256"]) == receipt_sha256
                ):
                    return False
                raise ExecutionStoreConflict("Idempotency key was reused for another request")
            self.connection.execute(
                "INSERT INTO execution_idempotency VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scope,
                    idempotency_key,
                    request_sha256,
                    receipt_json,
                    receipt_sha256,
                    datetime.now(UTC).isoformat(timespec="milliseconds"),
                ),
            )
        return True

    def receipt(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request: dict[str, object],
    ) -> dict[str, object] | None:
        self._validate_key(scope, label="idempotency scope")
        self._validate_key(idempotency_key, label="idempotency key")
        request_bytes = canonical_json_bytes(request)
        self._validate_persisted_payload(request_bytes)
        request_sha256 = sha256_hex(request_bytes)
        row = self.connection.execute(
            "SELECT request_sha256, receipt_json, receipt_sha256 "
            "FROM execution_idempotency "
            "WHERE scope=? AND idempotency_key=?",
            (scope, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_sha256"]) != request_sha256:
            raise ExecutionStoreConflict("Idempotency key belongs to another request")
        receipt_json = str(row["receipt_json"])
        if sha256_hex(receipt_json.encode("utf-8")) != str(row["receipt_sha256"]):
            raise ExecutionStoreError("Stored receipt hash is invalid")
        payload = json.loads(receipt_json)
        if not isinstance(payload, dict):
            raise ExecutionStoreError("Stored receipt is malformed")
        return cast(dict[str, object], payload)

    @staticmethod
    def _kind_for_model(model: type[VersionedModel]) -> str:
        for kind, (known_model, _id_field, _immutable) in _RECORD_TYPES.items():
            if model is known_model:
                return kind
        raise TypeError(f"Unsupported execution record model: {model.__name__}")

    @classmethod
    def _record_identity(cls, record: VersionedModel) -> tuple[str, str, bool]:
        kind = cls._kind_for_model(type(record))
        _model, id_field, immutable = _RECORD_TYPES[kind]
        record_id = getattr(record, id_field)
        if not isinstance(record_id, str):
            raise TypeError("Execution record identity is invalid")
        verifier = getattr(record, "verify_id", None)
        if callable(verifier) and not verifier():
            raise ExecutionStoreConflict("Content-derived execution record ID is invalid")
        return kind, record_id, immutable

    @staticmethod
    def _state(record: VersionedModel) -> str:
        for field in ("status", "state", "kind", "scope_kind"):
            value = getattr(record, field, None)
            if value is not None:
                return str(getattr(value, "value", value))
        return "active"

    def _decode(self, model: type[ModelT], row: sqlite3.Row) -> ModelT:
        payload_json = str(row["payload_json"])
        if sha256_hex(payload_json.encode("utf-8")) != str(row["payload_sha256"]):
            raise ExecutionStoreError("Stored execution record hash is invalid")
        try:
            result = model.model_validate_json(payload_json)
        except ValidationError as error:
            raise ExecutionStoreError("Stored execution record is malformed") from error
        verifier = getattr(result, "verify_id", None)
        if callable(verifier) and not verifier():
            raise ExecutionStoreError("Stored execution record identity is invalid")
        keys = set(row.keys())
        if "record_id" in keys and model in {
            known_model for known_model, _id_field, _immutable in _RECORD_TYPES.values()
        }:
            kind = self._kind_for_model(model)
            _known_model, id_field, _immutable = _RECORD_TYPES[kind]
            if getattr(result, id_field) != str(row["record_id"]):
                raise ExecutionStoreError("Stored execution record index is inconsistent")
            indexed = ("task_id", "employee_id", "run_id", "workspace_id", "scope_id")
            expected = self._associations(result)
            if any(row[column] != value for column, value in zip(indexed, expected, strict=True)):
                raise ExecutionStoreError("Stored execution association index is inconsistent")
        return result

    @staticmethod
    def _associations(
        record: VersionedModel,
    ) -> tuple[str | None, str | None, str | None, str | None, str | None]:
        task_id = getattr(record, "task_id", None)
        if task_id is None:
            task_id = getattr(record, "current_task_id", None)
        employee_id = getattr(record, "employee_id", None)
        run_id = getattr(record, "run_id", None)
        workspace_id = getattr(record, "workspace_id", None)
        scope_id = getattr(record, "scope_id", None)
        if scope_id is None and isinstance(record, BudgetUsage):
            scope_id = record.budget_policy_id
        values = (task_id, employee_id, run_id, workspace_id, scope_id)
        if any(value is not None and not isinstance(value, str) for value in values):
            raise TypeError("Execution association identity is invalid")
        return cast(
            tuple[str | None, str | None, str | None, str | None, str | None],
            values,
        )

    @staticmethod
    def _validate_record_shape(record: VersionedModel) -> None:
        forbidden_keys = {
            "api_key",
            "args",
            "argv",
            "command",
            "cwd",
            "env",
            "environment",
            "password",
            "raw_command",
            "secret",
            "shell",
            "token",
        }

        def inspect(value: object) -> bool:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).casefold().replace("-", "_")
                    if normalized in forbidden_keys or inspect(child):
                        return True
            elif isinstance(value, list | tuple | set):
                return any(inspect(child) for child in value)
            return False

        if inspect(record.extensions):
            raise ExecutionStoreError("Execution extensions contain forbidden authority keys")
        if isinstance(record, ExecutionRun):
            from raytsystem.execution.adapters import (
                InvalidRuntimeRequest,
                validate_safe_command_argv,
            )

            try:
                validate_safe_command_argv(record.runtime_adapter_id, record.safe_command)
            except InvalidRuntimeRequest as error:
                raise ExecutionStoreError("Execution run command is not allowlisted") from error
        if (
            isinstance(record, ExecutionApproval)
            and record.action == "execute_runtime"
            and None
            in (
                record.employee_id,
                record.task_id,
                record.run_id,
                record.workspace_id,
                record.destination,
            )
        ):
            raise ExecutionStoreError("Runtime approval is missing exact scope bindings")

    def _validate_persisted_payload(self, rendered: bytes) -> None:
        if any(marker in rendered for marker in _DANGEROUS_RUNTIME_MARKERS):
            raise ExecutionStoreError("Dangerous runtime authority cannot be persisted")
        decision = self.scanner.scan(rendered)
        if decision.blocks_processing:
            raise ExecutionStoreError("Sensitive execution content must be redacted first")

    @staticmethod
    def _validate_key(value: str, *, label: str) -> None:
        if _KEY.fullmatch(value) is None:
            raise ValueError(f"{label} is malformed")
