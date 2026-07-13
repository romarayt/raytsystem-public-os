from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.derived import assert_safe_sqlite_family

PLATFORM_DB_RELATIVE = Path("ops/platform.sqlite")
PLATFORM_SCHEMA_VERSION = 1
_SCHEMA_STATEMENTS = (
    "CREATE TABLE platform_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
    """CREATE TABLE migration_journal (
        migration_id TEXT PRIMARY KEY,
        migration_sha256 TEXT NOT NULL,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL,
        state TEXT NOT NULL,
        applied_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE audit_events (
        event_id TEXT PRIMARY KEY,
        stream_id TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        payload_schema TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        causation_id TEXT,
        correlation_id TEXT,
        previous_event_sha256 TEXT,
        recorded_at TEXT NOT NULL,
        UNIQUE(stream_id, sequence)
    ) STRICT""",
    """CREATE TABLE records (
        kind TEXT NOT NULL,
        record_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(kind, record_id, revision)
    ) STRICT""",
    """CREATE TABLE record_heads (
        kind TEXT NOT NULL,
        record_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        payload_sha256 TEXT NOT NULL,
        PRIMARY KEY(kind, record_id),
        FOREIGN KEY(kind, record_id, revision)
            REFERENCES records(kind, record_id, revision)
    ) STRICT""",
    """CREATE TABLE idempotency_receipts (
        scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(scope, idempotency_key)
    ) STRICT""",
    "CREATE INDEX audit_events_stream_idx ON audit_events(stream_id, sequence)",
    "CREATE INDEX records_kind_state_idx ON records(kind, state, created_at)",
)


class PlatformStoreError(RuntimeError):
    """The isolated operational store is unavailable or fails integrity checks."""


class PlatformStoreUnavailable(PlatformStoreError):
    """The store has not been initialized or cannot be opened read-only."""


@dataclass(frozen=True)
class StoredRecord:
    kind: str
    record_id: str
    revision: int
    payload: dict[str, Any]
    payload_sha256: str
    state: str
    created_at: str


class PlatformStore:
    """Versioned operational records; canonical knowledge remains outside this database."""

    def __init__(
        self,
        root: Path,
        *,
        mode: Literal["read_only", "read_write"] = "read_only",
    ) -> None:
        self.root = root.resolve()
        self.path = self.root / PLATFORM_DB_RELATIVE
        self.mode = mode
        self._savepoint_counter = 0
        if mode == "read_only":
            if not self.path.is_file():
                raise PlatformStoreUnavailable("The platform store is not initialized")
            assert_safe_sqlite_family(self.path)
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("PRAGMA trusted_schema=OFF")
            self._verify_schema()
            return
        if mode != "read_write":
            raise ValueError("Unsupported platform store mode")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        assert_safe_sqlite_family(self.path)
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self._configure_writer()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> PlatformStore:
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
    def transaction(self) -> Iterator[None]:
        if self.mode != "read_write":
            raise PlatformStoreError("Read-only stores cannot start write transactions")
        if self.connection.in_transaction:
            self._savepoint_counter += 1
            name = f"platform_nested_{self._savepoint_counter}"
            self.connection.execute(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self.connection.execute(f"ROLLBACK TO {name}")
                self.connection.execute(f"RELEASE {name}")
                raise
            else:
                self.connection.execute(f"RELEASE {name}")
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def initialize(self) -> None:
        if self.mode != "read_write":
            raise PlatformStoreError("Initialization requires an explicit writer")
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > PLATFORM_SCHEMA_VERSION:
            raise PlatformStoreError("The platform store is newer than this raytsystem build")
        if version == 0:
            with self.transaction():
                for statement in _SCHEMA_STATEMENTS:
                    self.connection.execute(statement)
                migration_payload = {
                    "migration_id": "platform_schema_0001",
                    "from_version": 0,
                    "to_version": 1,
                }
                migration_sha256 = sha256_hex(canonical_json_bytes(migration_payload))
                now = _now()
                self.connection.execute(
                    "INSERT INTO migration_journal VALUES (?, ?, 0, 1, 'applied', ?)",
                    ("platform_schema_0001", migration_sha256, now),
                )
                self.connection.execute(
                    "INSERT INTO platform_meta(key, value) VALUES ('created_at', ?)", (now,)
                )
                self.connection.execute(f"PRAGMA user_version={PLATFORM_SCHEMA_VERSION}")
        self._verify_schema()

    def _verify_schema(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version != PLATFORM_SCHEMA_VERSION:
            raise PlatformStoreUnavailable(
                f"Platform schema {version} requires migration to {PLATFORM_SCHEMA_VERSION}"
            )
        required = {
            "platform_meta",
            "migration_journal",
            "audit_events",
            "records",
            "record_heads",
            "idempotency_receipts",
        }
        observed = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if not required.issubset(observed):
            raise PlatformStoreUnavailable("Platform schema is incomplete")

    def append_record(
        self,
        *,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        state: str,
        expected_revision: int | None = None,
    ) -> StoredRecord:
        _validate_token(kind, "record kind")
        _validate_token(record_id, "record ID")
        rendered = canonical_json_bytes(payload)
        payload_sha256 = sha256_hex(rendered)
        created_at = _now()
        with self.transaction():
            head = self.connection.execute(
                "SELECT revision, payload_sha256 FROM record_heads "
                "WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            ).fetchone()
            revision = 1 if head is None else int(head["revision"]) + 1
            prior_revision = None if head is None else int(head["revision"])
            if expected_revision != prior_revision:
                raise PlatformStoreError("Record revision changed")
            self.connection.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    record_id,
                    revision,
                    rendered.decode("utf-8"),
                    payload_sha256,
                    state,
                    created_at,
                ),
            )
            self.connection.execute(
                "INSERT INTO record_heads VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, record_id) DO UPDATE SET "
                "revision=excluded.revision, payload_sha256=excluded.payload_sha256",
                (kind, record_id, revision, payload_sha256),
            )
        return StoredRecord(
            kind=kind,
            record_id=record_id,
            revision=revision,
            payload=payload,
            payload_sha256=payload_sha256,
            state=state,
            created_at=created_at,
        )

    def head(self, kind: str, record_id: str) -> StoredRecord | None:
        _validate_token(kind, "record kind")
        _validate_token(record_id, "record ID")
        row = self.connection.execute(
            "SELECT r.* FROM record_heads h JOIN records r "
            "ON r.kind=h.kind AND r.record_id=h.record_id AND r.revision=h.revision "
            "WHERE h.kind=? AND h.record_id=?",
            (kind, record_id),
        ).fetchone()
        return None if row is None else _stored_record(row)

    def list_heads(
        self,
        kind: str,
        *,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[StoredRecord, ...]:
        _validate_token(kind, "record kind")
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise ValueError("Record pagination is out of bounds")
        if state is None:
            rows = self.connection.execute(
                "SELECT r.* FROM record_heads h JOIN records r "
                "ON r.kind=h.kind AND r.record_id=h.record_id AND r.revision=h.revision "
                "WHERE h.kind=? ORDER BY r.created_at DESC, r.record_id LIMIT ? OFFSET ?",
                (kind, limit, offset),
            ).fetchall()
        else:
            _validate_token(state, "record state")
            rows = self.connection.execute(
                "SELECT r.* FROM record_heads h JOIN records r "
                "ON r.kind=h.kind AND r.record_id=h.record_id AND r.revision=h.revision "
                "WHERE h.kind=? AND r.state=? "
                "ORDER BY r.created_at DESC, r.record_id LIMIT ? OFFSET ?",
                (kind, state, limit, offset),
            ).fetchall()
        return tuple(_stored_record(row) for row in rows)

    def append_event(
        self,
        *,
        stream_id: str,
        aggregate_id: str,
        event_type: str,
        actor_id: str,
        payload_schema: str,
        payload: dict[str, Any],
        sensitivity: str = "internal",
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        for value, label in (
            (stream_id, "stream ID"),
            (aggregate_id, "aggregate ID"),
            (event_type, "event type"),
            (actor_id, "actor ID"),
            (payload_schema, "payload schema"),
        ):
            _validate_token(value, label)
        rendered = canonical_json_bytes(payload)
        payload_sha256 = sha256_hex(rendered)
        with self.transaction():
            previous = self.connection.execute(
                "SELECT * FROM audit_events WHERE stream_id=? ORDER BY sequence DESC LIMIT 1",
                (stream_id,),
            ).fetchone()
            sequence = 1 if previous is None else int(previous["sequence"]) + 1
            previous_hash = None if previous is None else _event_row_hash(previous)
            recorded_at = _now()
            identity = {
                "stream_id": stream_id,
                "aggregate_id": aggregate_id,
                "sequence": sequence,
                "event_type": event_type,
                "actor_id": actor_id,
                "payload_schema": payload_schema,
                "payload_sha256": payload_sha256,
                "sensitivity": sensitivity,
                "causation_id": causation_id,
                "correlation_id": correlation_id,
                "previous_event_sha256": previous_hash,
                "recorded_at": recorded_at,
            }
            event_id = derive_id("aevt", identity)
            self.connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    stream_id,
                    aggregate_id,
                    sequence,
                    event_type,
                    actor_id,
                    payload_schema,
                    rendered.decode("utf-8"),
                    payload_sha256,
                    sensitivity,
                    causation_id,
                    correlation_id,
                    previous_hash,
                    recorded_at,
                ),
            )
        return {"event_id": event_id, **identity}

    def list_events(self, stream_id: str, *, limit: int = 200) -> tuple[dict[str, Any], ...]:
        _validate_token(stream_id, "stream ID")
        if not 1 <= limit <= 1_000:
            raise ValueError("Event limit is out of bounds")
        rows = self.connection.execute(
            "SELECT * FROM audit_events WHERE stream_id=? ORDER BY sequence LIMIT ?",
            (stream_id, limit),
        ).fetchall()
        return tuple(_event_payload(row) for row in rows)

    def event_count(self) -> int:
        """Return the total append-only audit backlog without exposing event payloads."""

        row = self.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
        return 0 if row is None else int(row[0])

    def verify_event_stream(self, stream_id: str) -> bool:
        rows = self.connection.execute(
            "SELECT * FROM audit_events WHERE stream_id=? ORDER BY sequence", (stream_id,)
        ).fetchall()
        previous_hash: str | None = None
        for expected_sequence, row in enumerate(rows, 1):
            if int(row["sequence"]) != expected_sequence:
                return False
            if row["previous_event_sha256"] != previous_hash:
                return False
            if sha256_hex(str(row["payload_json"]).encode("utf-8")) != row["payload_sha256"]:
                return False
            identity = _event_identity(row)
            if str(row["event_id"]) != derive_id("aevt", identity):
                return False
            previous_hash = _event_row_hash(row)
        return True

    def idempotent_receipt(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request: dict[str, Any],
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        _validate_token(scope, "idempotency scope")
        _validate_token(idempotency_key, "idempotency key")
        request_sha256 = sha256_hex(canonical_json_bytes(request))
        with self.transaction():
            existing = self.connection.execute(
                "SELECT request_sha256, receipt_json FROM idempotency_receipts "
                "WHERE scope=? AND idempotency_key=?",
                (scope, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise PlatformStoreError("Idempotency key was reused for another request")
                loaded = json.loads(str(existing["receipt_json"]))
                if not isinstance(loaded, dict):
                    raise PlatformStoreError("Idempotency receipt is not an object")
                return loaded
            if receipt is None:
                return None
            self.connection.execute(
                "INSERT INTO idempotency_receipts VALUES (?, ?, ?, ?, ?)",
                (
                    scope,
                    idempotency_key,
                    request_sha256,
                    canonical_json_bytes(receipt).decode("utf-8"),
                    _now(),
                ),
            )
        return receipt

    def snapshot_id(self) -> str:
        heads = [
            {
                "kind": str(row["kind"]),
                "record_id": str(row["record_id"]),
                "revision": int(row["revision"]),
                "payload_sha256": str(row["payload_sha256"]),
            }
            for row in self.connection.execute(
                "SELECT * FROM record_heads ORDER BY kind, record_id"
            ).fetchall()
        ]
        event_heads = [
            {"stream_id": str(row["stream_id"]), "sequence": int(row["sequence"])}
            for row in self.connection.execute(
                "SELECT stream_id, MAX(sequence) AS sequence FROM audit_events "
                "GROUP BY stream_id ORDER BY stream_id"
            ).fetchall()
        ]
        return derive_id("pview", {"heads": heads, "event_heads": event_heads})


def open_platform_store_read_only(root: Path) -> PlatformStore | None:
    try:
        return PlatformStore(root, mode="read_only")
    except (OSError, sqlite3.Error, PlatformStoreUnavailable):
        return None


def initialize_platform_store(root: Path) -> PlatformStore:
    store = PlatformStore(root, mode="read_write")
    try:
        store.initialize()
    except BaseException:
        store.close()
        raise
    return store


def _stored_record(row: sqlite3.Row) -> StoredRecord:
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise PlatformStoreError("Stored record payload is not an object")
    if sha256_hex(canonical_json_bytes(payload)) != row["payload_sha256"]:
        raise PlatformStoreError("Stored record payload hash is invalid")
    return StoredRecord(
        kind=str(row["kind"]),
        record_id=str(row["record_id"]),
        revision=int(row["revision"]),
        payload=payload,
        payload_sha256=str(row["payload_sha256"]),
        state=str(row["state"]),
        created_at=str(row["created_at"]),
    )


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise PlatformStoreError("Audit event payload is not an object")
    return {
        "event_id": str(row["event_id"]),
        "stream_id": str(row["stream_id"]),
        "aggregate_id": str(row["aggregate_id"]),
        "sequence": int(row["sequence"]),
        "event_type": str(row["event_type"]),
        "actor_id": str(row["actor_id"]),
        "payload_schema": str(row["payload_schema"]),
        "payload": payload,
        "payload_sha256": str(row["payload_sha256"]),
        "sensitivity": str(row["sensitivity"]),
        "causation_id": row["causation_id"],
        "correlation_id": row["correlation_id"],
        "previous_event_sha256": row["previous_event_sha256"],
        "recorded_at": str(row["recorded_at"]),
    }


def _event_row_hash(row: sqlite3.Row) -> str:
    material = {"event_id": str(row["event_id"]), **_event_identity(row)}
    return sha256_hex(canonical_json_bytes(material))


def _event_identity(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stream_id": str(row["stream_id"]),
        "aggregate_id": str(row["aggregate_id"]),
        "sequence": int(row["sequence"]),
        "event_type": str(row["event_type"]),
        "actor_id": str(row["actor_id"]),
        "payload_schema": str(row["payload_schema"]),
        "payload_sha256": str(row["payload_sha256"]),
        "sensitivity": str(row["sensitivity"]),
        "causation_id": row["causation_id"],
        "correlation_id": row["correlation_id"],
        "previous_event_sha256": row["previous_event_sha256"],
        "recorded_at": str(row["recorded_at"]),
    }


def _validate_token(value: str, label: str) -> None:
    if not value or len(value) > 256 or any(char.isspace() for char in value):
        raise ValueError(f"Invalid {label}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
