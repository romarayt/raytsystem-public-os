from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raytsystem.derived import assert_safe_sqlite_family


class LeaseBusy(RuntimeError):
    """Raised when a live fenced lease belongs to another run."""


@dataclass(frozen=True)
class OperationClaim:
    operation_key: str
    run_id: str
    state: str
    created: bool
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class LeaseToken:
    partition_key: str
    control_epoch: str
    owner_run_id: str
    fencing_token: int
    expires_at_ms: int


class ControlDB:
    """Durable local coordination plane; canonical knowledge remains on the filesystem."""

    def __init__(self, path: Path) -> None:
        self.path = path
        transient = {"database is busy", "database is locked", "disk i/o error"}
        for attempt, delay in enumerate((0.01, 0.05, 0.0), 1):
            connection: sqlite3.Connection | None = None
            try:
                assert_safe_sqlite_family(path)
                connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
                connection.row_factory = sqlite3.Row
                self.connection = connection
                self._configure(connection)
                self._create_schema()
                return
            except sqlite3.OperationalError as error:
                if connection is not None:
                    connection.close()
                if str(error).casefold() not in transient or attempt == 3:
                    raise
                time.sleep(delay)
        raise RuntimeError("Control database retry loop did not terminate")

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=5000")
        if sys.platform == "darwin":
            connection.execute("PRAGMA fullfsync=ON")
            connection.execute("PRAGMA checkpoint_fullfsync=ON")

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS operations (
                operation_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                state TEXT NOT NULL,
                result_json TEXT,
                error_code TEXT
            ) STRICT;
            CREATE TABLE IF NOT EXISTS leases (
                partition_key TEXT PRIMARY KEY,
                control_epoch TEXT NOT NULL,
                owner_run_id TEXT,
                fencing_token INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                renewed_at_ms INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS promotion_txns (
                txn_id TEXT PRIMARY KEY,
                operation_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                control_epoch TEXT NOT NULL,
                partition_fencing_token INTEGER NOT NULL,
                global_fencing_token INTEGER NOT NULL,
                parent_generation_id TEXT NOT NULL,
                next_generation_id TEXT NOT NULL UNIQUE,
                manifest_sha256 TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                approval_hash TEXT,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS event_outbox (
                event_id TEXT PRIMARY KEY,
                txn_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                state TEXT NOT NULL
            ) STRICT;
            """
        )
        self._migrate_promotion_attempts()
        with self._transaction():
            self.connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('control_epoch', ?)",
                (f"epoch_{uuid.uuid4().hex}",),
            )

    def _migrate_promotion_attempts(self) -> None:
        indexes = self.connection.execute("PRAGMA index_list('promotion_txns')").fetchall()
        operation_unique = False
        for index in indexes:
            if not bool(index["unique"]):
                continue
            columns = self.connection.execute(f"PRAGMA index_info('{index['name']}')").fetchall()
            if [str(column["name"]) for column in columns] == ["operation_key"]:
                operation_unique = True
                break
        if not operation_unique:
            return
        self.connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE promotion_txns RENAME TO promotion_txns_v1;
            CREATE TABLE promotion_txns (
                txn_id TEXT PRIMARY KEY,
                operation_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                control_epoch TEXT NOT NULL,
                partition_fencing_token INTEGER NOT NULL,
                global_fencing_token INTEGER NOT NULL,
                parent_generation_id TEXT NOT NULL,
                next_generation_id TEXT NOT NULL UNIQUE,
                manifest_sha256 TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                approval_hash TEXT,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            ) STRICT;
            INSERT INTO promotion_txns SELECT * FROM promotion_txns_v1;
            DROP TABLE promotion_txns_v1;
            COMMIT;
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    @property
    def control_epoch(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = 'control_epoch'"
        ).fetchone()
        if row is None:
            raise RuntimeError("control_epoch is missing")
        return str(row["value"])

    def claim_operation(
        self,
        *,
        operation_key: str,
        run_id: str,
        stage: str,
        partition_key: str,
        now_ms: int,
    ) -> OperationClaim:
        with self._transaction():
            row = self.connection.execute(
                "SELECT operation_key, run_id, state, result_json FROM operations "
                "WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            created = row is None
            if created:
                self.connection.execute(
                    "INSERT INTO runs(run_id, state, created_at_ms, updated_at_ms) "
                    "VALUES (?, 'running', ?, ?)",
                    (run_id, now_ms, now_ms),
                )
                self.connection.execute(
                    "INSERT INTO operations"
                    "(operation_key, run_id, stage, partition_key, state) "
                    "VALUES (?, ?, ?, ?, 'running')",
                    (operation_key, run_id, stage, partition_key),
                )
                row = self.connection.execute(
                    "SELECT operation_key, run_id, state, result_json FROM operations "
                    "WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
        if row is None:
            raise RuntimeError("Operation claim disappeared")
        result = None if row["result_json"] is None else json.loads(row["result_json"])
        return OperationClaim(
            operation_key=str(row["operation_key"]),
            run_id=str(row["run_id"]),
            state=str(row["state"]),
            created=created,
            result=result,
        )

    def update_operation(
        self,
        operation_key: str,
        *,
        state: str,
        now_ms: int,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        result_json = None if result is None else json.dumps(result, sort_keys=True)
        with self._transaction():
            row = self.connection.execute(
                "SELECT run_id FROM operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if row is None:
                raise KeyError(operation_key)
            self.connection.execute(
                "UPDATE operations SET state = ?, result_json = ?, error_code = ? "
                "WHERE operation_key = ?",
                (state, result_json, error_code, operation_key),
            )
            self.connection.execute(
                "UPDATE runs SET state = ?, updated_at_ms = ? WHERE run_id = ?",
                (state, now_ms, row["run_id"]),
            )

    def acquire_lease(
        self,
        partition_key: str,
        owner_run_id: str,
        *,
        ttl_ms: int,
        now_ms: int,
    ) -> LeaseToken:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        epoch = self.control_epoch
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM leases WHERE partition_key = ?",
                (partition_key,),
            ).fetchone()
            if row is None:
                fencing_token = 1
            elif row["owner_run_id"] == owner_run_id and row["expires_at_ms"] > now_ms:
                fencing_token = int(row["fencing_token"])
            elif row["owner_run_id"] is None or row["expires_at_ms"] <= now_ms:
                fencing_token = int(row["fencing_token"]) + 1
            else:
                raise LeaseBusy(f"Live lease for {partition_key} belongs to another run")
            expires_at_ms = now_ms + ttl_ms
            self.connection.execute(
                "INSERT INTO leases(partition_key, control_epoch, owner_run_id, fencing_token, "
                "expires_at_ms, renewed_at_ms) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(partition_key) DO UPDATE SET control_epoch=excluded.control_epoch, "
                "owner_run_id=excluded.owner_run_id, fencing_token=excluded.fencing_token, "
                "expires_at_ms=excluded.expires_at_ms, renewed_at_ms=excluded.renewed_at_ms",
                (partition_key, epoch, owner_run_id, fencing_token, expires_at_ms, now_ms),
            )
        return LeaseToken(partition_key, epoch, owner_run_id, fencing_token, expires_at_ms)

    def verify_lease(self, lease: LeaseToken, *, now_ms: int) -> bool:
        row = self.connection.execute(
            "SELECT * FROM leases WHERE partition_key = ?",
            (lease.partition_key,),
        ).fetchone()
        return bool(
            row is not None
            and row["control_epoch"] == lease.control_epoch
            and row["owner_run_id"] == lease.owner_run_id
            and row["fencing_token"] == lease.fencing_token
            and row["expires_at_ms"] > now_ms
        )

    def renew_lease(self, lease: LeaseToken, *, ttl_ms: int, now_ms: int) -> LeaseToken:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        expires_at_ms = now_ms + ttl_ms
        with self._transaction():
            cursor = self.connection.execute(
                "UPDATE leases SET expires_at_ms = ?, renewed_at_ms = ? "
                "WHERE partition_key = ? AND control_epoch = ? AND owner_run_id = ? "
                "AND fencing_token = ? AND expires_at_ms > ?",
                (
                    expires_at_ms,
                    now_ms,
                    lease.partition_key,
                    lease.control_epoch,
                    lease.owner_run_id,
                    lease.fencing_token,
                    now_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseBusy(f"Lease {lease.partition_key} cannot be renewed")
        return LeaseToken(
            lease.partition_key,
            lease.control_epoch,
            lease.owner_run_id,
            lease.fencing_token,
            expires_at_ms,
        )

    @contextmanager
    def hold_valid_leases(
        self,
        leases: tuple[LeaseToken, ...],
        *,
        now_ms: int,
        renew_ttl_ms: int | None = None,
    ) -> Iterator[tuple[LeaseToken, ...]]:
        """Hold SQLite's writer lock while a fenced filesystem commit occurs."""

        if not leases:
            raise ValueError("At least one lease is required")
        if renew_ttl_ms is not None and renew_ttl_ms <= 0:
            raise ValueError("renew_ttl_ms must be positive")
        with self._transaction():
            renewed: list[LeaseToken] = []
            for lease in leases:
                row = self.connection.execute(
                    "SELECT * FROM leases WHERE partition_key = ?",
                    (lease.partition_key,),
                ).fetchone()
                valid = bool(
                    row is not None
                    and row["control_epoch"] == lease.control_epoch
                    and row["owner_run_id"] == lease.owner_run_id
                    and row["fencing_token"] == lease.fencing_token
                    and row["expires_at_ms"] > now_ms
                )
                if not valid:
                    raise LeaseBusy(f"Lease {lease.partition_key} is stale or expired")
                expires_at_ms = int(row["expires_at_ms"])
                if renew_ttl_ms is not None:
                    expires_at_ms = now_ms + renew_ttl_ms
                    self.connection.execute(
                        "UPDATE leases SET expires_at_ms = ?, renewed_at_ms = ? "
                        "WHERE partition_key = ? AND control_epoch = ? "
                        "AND owner_run_id = ? AND fencing_token = ?",
                        (
                            expires_at_ms,
                            now_ms,
                            lease.partition_key,
                            lease.control_epoch,
                            lease.owner_run_id,
                            lease.fencing_token,
                        ),
                    )
                renewed.append(
                    LeaseToken(
                        lease.partition_key,
                        lease.control_epoch,
                        lease.owner_run_id,
                        lease.fencing_token,
                        expires_at_ms,
                    )
                )
            yield tuple(renewed)

    def renew_held_leases(
        self,
        leases: tuple[LeaseToken, ...],
        *,
        ttl_ms: int,
        now_ms: int,
    ) -> tuple[LeaseToken, ...]:
        """Extend owned fences inside an already-held SQLite writer transaction."""

        if not self.connection.in_transaction:
            raise RuntimeError("Held lease renewal requires the writer transaction")
        if not leases or ttl_ms <= 0:
            raise ValueError("Held lease renewal requires leases and a positive TTL")
        expires_at_ms = now_ms + ttl_ms
        renewed: list[LeaseToken] = []
        for lease in leases:
            cursor = self.connection.execute(
                "UPDATE leases SET expires_at_ms = ?, renewed_at_ms = ? "
                "WHERE partition_key = ? AND control_epoch = ? "
                "AND owner_run_id = ? AND fencing_token = ?",
                (
                    expires_at_ms,
                    now_ms,
                    lease.partition_key,
                    lease.control_epoch,
                    lease.owner_run_id,
                    lease.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseBusy(f"Lease {lease.partition_key} changed under writer lock")
            renewed.append(
                LeaseToken(
                    lease.partition_key,
                    lease.control_epoch,
                    lease.owner_run_id,
                    lease.fencing_token,
                    expires_at_ms,
                )
            )
        return tuple(renewed)

    def release_lease(self, lease: LeaseToken) -> bool:
        with self._transaction():
            cursor = self.connection.execute(
                "UPDATE leases SET owner_run_id = NULL, expires_at_ms = 0 "
                "WHERE partition_key = ? AND control_epoch = ? AND owner_run_id = ? "
                "AND fencing_token = ?",
                (
                    lease.partition_key,
                    lease.control_epoch,
                    lease.owner_run_id,
                    lease.fencing_token,
                ),
            )
        return cursor.rowcount == 1

    def store_promotion(
        self,
        *,
        txn_id: str,
        operation_key: str,
        run_id: str,
        partition_fencing_token: int,
        global_fencing_token: int,
        parent_generation_id: str,
        next_generation_id: str,
        manifest_sha256: str,
        event_id: str,
        approval_hash: str | None,
        payload_json: str,
        state: str,
        now_ms: int,
    ) -> None:
        with self._transaction():
            existing = self.connection.execute(
                "SELECT * FROM promotion_txns WHERE txn_id = ?",
                (txn_id,),
            ).fetchone()
            immutable = (
                operation_key,
                run_id,
                parent_generation_id,
                next_generation_id,
                manifest_sha256,
                event_id,
            )
            if existing is not None:
                existing_immutable = (
                    str(existing["operation_key"]),
                    str(existing["run_id"]),
                    str(existing["parent_generation_id"]),
                    str(existing["next_generation_id"]),
                    str(existing["manifest_sha256"]),
                    str(existing["event_id"]),
                )
                if str(existing["txn_id"]) != txn_id or existing_immutable != immutable:
                    raise RuntimeError("Promotion WAL identity collision")
                existing_approval_hash = (
                    None if existing["approval_hash"] is None else str(existing["approval_hash"])
                )
                if existing_approval_hash != approval_hash and str(existing["state"]) not in {
                    "prepared",
                    "committing",
                }:
                    raise RuntimeError("Committed promotion authority cannot be replaced")
                self.connection.execute(
                    "UPDATE promotion_txns SET control_epoch = ?, "
                    "partition_fencing_token = ?, global_fencing_token = ?, approval_hash = ?, "
                    "payload_json = ?, "
                    "updated_at_ms = ? WHERE txn_id = ?",
                    (
                        self.control_epoch,
                        partition_fencing_token,
                        global_fencing_token,
                        approval_hash,
                        payload_json,
                        now_ms,
                        txn_id,
                    ),
                )
                return
            self.connection.execute(
                "INSERT INTO promotion_txns(txn_id, operation_key, run_id, control_epoch, "
                "partition_fencing_token, global_fencing_token, parent_generation_id, "
                "next_generation_id, manifest_sha256, event_id, state, payload_json, "
                "approval_hash, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    txn_id,
                    operation_key,
                    run_id,
                    self.control_epoch,
                    partition_fencing_token,
                    global_fencing_token,
                    parent_generation_id,
                    next_generation_id,
                    manifest_sha256,
                    event_id,
                    state,
                    payload_json,
                    approval_hash,
                    now_ms,
                    now_ms,
                ),
            )

    def update_promotion_state(self, txn_id: str, state: str, *, now_ms: int) -> None:
        with self._transaction():
            row = self.connection.execute(
                "SELECT state FROM promotion_txns WHERE txn_id = ?",
                (txn_id,),
            ).fetchone()
            if row is None:
                raise KeyError(txn_id)
            current = str(row["state"])
            legal: dict[str, frozenset[str]] = {
                "prepared": frozenset({"committing", "aborted"}),
                "committing": frozenset({"committed", "aborted"}),
                "committed": frozenset({"reconciling"}),
                "reconciling": frozenset({"completed"}),
                "completed": frozenset(),
                "aborted": frozenset(),
            }
            if state != current and state not in legal.get(current, frozenset()):
                raise RuntimeError(f"Illegal promotion state transition: {current} -> {state}")
            cursor = self.connection.execute(
                "UPDATE promotion_txns SET state = ?, updated_at_ms = ? WHERE txn_id = ?",
                (state, now_ms, txn_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(txn_id)

    def promotion_for_operation(self, operation_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM promotion_txns WHERE operation_key = ? AND state != 'aborted' "
            "ORDER BY created_at_ms DESC, rowid DESC LIMIT 1",
            (operation_key,),
        ).fetchone()
        return None if row is None else dict(row)

    def event_outbox_record(self, event_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM event_outbox WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def store_event_outbox(
        self,
        *,
        event_id: str,
        txn_id: str,
        payload_json: str,
        payload_sha256: str,
    ) -> None:
        with self._transaction():
            existing = self.connection.execute(
                "SELECT * FROM event_outbox WHERE event_id = ? OR txn_id = ?",
                (event_id, txn_id),
            ).fetchone()
            if existing is not None:
                identity = (
                    str(existing["event_id"]),
                    str(existing["txn_id"]),
                    str(existing["payload_sha256"]),
                    str(existing["payload_json"]),
                )
                if identity != (event_id, txn_id, payload_sha256, payload_json):
                    raise RuntimeError("Event outbox identity collision")
                return
            self.connection.execute(
                "INSERT INTO event_outbox"
                "(event_id, txn_id, payload_json, payload_sha256, state) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (event_id, txn_id, payload_json, payload_sha256),
            )

    def mark_event_appended(self, event_id: str) -> None:
        with self._transaction():
            self.connection.execute(
                "UPDATE event_outbox SET state = 'appended' WHERE event_id = ?",
                (event_id,),
            )
