from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raytsystem.contracts import (
    SCHEMA_VERSION,
    MigrationPlan,
    MigrationRecord,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.lifecycle import MigrationState
from raytsystem.io import write_bytes_atomic
from raytsystem.platform_store import (
    PlatformStore,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file

_SCHEMA_LINE = re.compile(rb'(?m)^schema_version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
_CONFIG_RELATIVE = "config/raytsystem.toml"


class MigrationError(RuntimeError):
    """Workspace migration is stale, incompatible, unbacked, or not explicitly confirmed."""


@dataclass(frozen=True)
class Migration:
    """One deterministic schema step; ``apply`` mutates the workspace and returns its report."""

    migration_id: str
    from_version: str
    to_version: str
    reversible: bool
    apply: Callable[[Path], dict[str, Any]]


def _read_config(root: Path) -> bytes:
    try:
        return read_regular_file(root, _CONFIG_RELATIVE, max_bytes=256 * 1024).data
    except (OSError, PathPolicyError) as error:
        raise MigrationError("raytsystem configuration is unavailable") from error


def _config_version_bump(to_version: str, *, notes: str) -> Callable[[Path], dict[str, Any]]:
    def apply(root: Path) -> dict[str, Any]:
        data = _read_config(root)
        replacement = f'schema_version = "{to_version}"'.encode("ascii")
        migrated, count = _SCHEMA_LINE.subn(replacement, data, count=1)
        if count != 1:
            raise MigrationError("Migration could not update the schema version")
        write_bytes_atomic(root / "config" / "raytsystem.toml", migrated)
        return {
            "changed_files": [_CONFIG_RELATIVE],
            "before_sha256": sha256_hex(data),
            "after_sha256": sha256_hex(migrated),
            "data_changes": "none",
            "notes": notes,
        }

    return apply


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="schema_1_3_0_to_1_4_0",
        from_version="1.3.0",
        to_version="1.4.0",
        reversible=True,
        apply=_config_version_bump(
            "1.4.0",
            notes="The 1.4.0 schemas are additive; only the configuration version advances.",
        ),
    ),
)


class MigrationService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def current_version(self) -> str:
        match = _SCHEMA_LINE.search(_read_config(self.root))
        if match is None:
            raise MigrationError("raytsystem schema version is missing")
        return match.group(1).decode("ascii")

    def plan(self, target_version: str = SCHEMA_VERSION) -> MigrationPlan:
        current = self.current_version()
        if target_version != SCHEMA_VERSION:
            raise MigrationError("Migrations may only target this raytsystem schema release")
        if current.split(".")[0] != target_version.split(".")[0]:
            raise MigrationError("Cross-major migration requires a separate compatibility release")
        if _version_tuple(current) > _version_tuple(target_version):
            raise MigrationError("Workspace schema is newer than this raytsystem build")
        steps = _registry_chain(current, target_version)
        payload = _plan_payload(current, target_version, steps)
        plan_hash = sha256_hex(canonical_json_bytes(payload))
        return MigrationPlan(
            migration_plan_id=derive_id("mplan", payload | {"plan_sha256": plan_hash}),
            from_version=current,
            to_version=target_version,
            migration_ids=payload["migration_ids"],
            backup_required=payload["backup_required"],
            reversible=payload["reversible"],
            plan_sha256=plan_hash,
            created_at=datetime.now(UTC),
        )

    def apply(
        self,
        plan: MigrationPlan,
        *,
        backup_id: str,
        actor_id: str,
        confirm: bool,
    ) -> MigrationRecord | None:
        if not confirm:
            raise MigrationError("Migration requires explicit confirmation")
        steps = self._verify_plan(plan)
        if not steps:
            return None
        with initialize_platform_store(self.root) as store:
            pending = tuple(
                step for step in steps if _journal_state(store, step.migration_id) != "applied"
            )
            current = self.current_version()
            if not pending:
                if current != plan.to_version:
                    raise MigrationError("Applied migration record disagrees with configuration")
                head = store.head("migration", steps[-1].migration_id)
                if head is None:
                    raise MigrationError("Applied migration record disagrees with configuration")
                clean = {
                    key: value
                    for key, value in head.payload.items()
                    if key != "before_bytes_sha256"
                }
                return MigrationRecord.model_validate(clean)
            if current != pending[0].from_version:
                raise MigrationError("Migration plan is stale")
            if not backup_id:
                raise MigrationError("Migration requires a verified backup")
            backup = store.head("backup", backup_id)
            if backup is None or backup.state != "created":
                raise MigrationError("Migration backup is missing or unverified")
            record: MigrationRecord | None = None
            for step in pending:
                record = self._apply_step(store, step, backup_id=backup_id, actor_id=actor_id)
            if self.current_version() != plan.to_version:
                raise MigrationError("Migration finished on an unexpected schema version")
            return record

    def status(self) -> dict[str, Any]:
        plan = self.plan()
        pending = list(plan.migration_ids)
        if pending:
            store = open_platform_store_read_only(self.root)
            if store is not None:
                with store:
                    pending = [
                        migration_id
                        for migration_id in pending
                        if _journal_state(store, migration_id) != "applied"
                    ]
        return {
            "current_version": plan.from_version,
            "target_version": plan.to_version,
            "migration_required": bool(pending),
            "migration_ids": list(plan.migration_ids),
            "pending_migration_ids": pending,
            "plan_sha256": plan.plan_sha256,
        }

    def _apply_step(
        self,
        store: PlatformStore,
        step: Migration,
        *,
        backup_id: str,
        actor_id: str,
    ) -> MigrationRecord:
        started = datetime.now(UTC)
        before = _read_config(self.root)
        try:
            report = dict(step.apply(self.root))
        except BaseException as error:
            write_bytes_atomic(self.root / "config" / "raytsystem.toml", before)
            with suppress(Exception):
                _journal(store, step, _failure_sha256(step), "failed")
            if isinstance(error, MigrationError):
                raise
            raise MigrationError(f"Migration step {step.migration_id} failed") from error
        report_payload: dict[str, Any] = {
            "migration_id": step.migration_id,
            "from_version": step.from_version,
            "to_version": step.to_version,
            **report,
        }
        report_sha256 = sha256_hex(canonical_json_bytes(report_payload))
        migration_sha256 = sha256_hex(
            canonical_json_bytes(
                {
                    "migration_id": step.migration_id,
                    "from_version": step.from_version,
                    "to_version": step.to_version,
                    "report_sha256": report_sha256,
                }
            )
        )
        existing = store.head("migration", step.migration_id)
        report_record_id = derive_id(
            "mreport", {"migration_id": step.migration_id, "report_sha256": report_sha256}
        )
        existing_report = store.head("migration_report", report_record_id)
        record = MigrationRecord(
            migration_record_id=derive_id(
                "mrec", {"migration_id": step.migration_id, "migration_sha256": migration_sha256}
            ),
            migration_id=step.migration_id,
            migration_sha256=migration_sha256,
            from_version=step.from_version,
            to_version=step.to_version,
            state=MigrationState.APPLIED,
            attempt=1 if existing is None else existing.revision + 1,
            backup_id=backup_id,
            report_sha256=report_sha256,
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        try:
            with store.transaction():
                _journal(store, step, migration_sha256, "applied")
                store.append_record(
                    kind="migration",
                    record_id=step.migration_id,
                    payload=record.model_dump(mode="json")
                    | {"before_bytes_sha256": sha256_hex(before)},
                    state="applied",
                    expected_revision=None if existing is None else existing.revision,
                )
                store.append_record(
                    kind="migration_report",
                    record_id=report_record_id,
                    payload=report_payload,
                    state="applied",
                    expected_revision=(
                        None if existing_report is None else existing_report.revision
                    ),
                )
                store.append_event(
                    stream_id="workspace_migrations",
                    aggregate_id=step.migration_id,
                    event_type="migration_applied",
                    actor_id=actor_id,
                    payload_schema="migration_record_v1",
                    payload={
                        "migration_id": step.migration_id,
                        "from_version": step.from_version,
                        "to_version": step.to_version,
                        "backup_id": backup_id,
                        "report_sha256": report_sha256,
                    },
                )
        except BaseException:
            write_bytes_atomic(self.root / "config" / "raytsystem.toml", before)
            with suppress(Exception):
                _journal(store, step, _failure_sha256(step), "failed")
            raise
        return record

    @staticmethod
    def _verify_plan(plan: MigrationPlan) -> tuple[Migration, ...]:
        if plan.to_version != SCHEMA_VERSION:
            raise MigrationError("Migration target does not match this raytsystem build")
        steps = _registry_chain(plan.from_version, plan.to_version)
        payload = _plan_payload(plan.from_version, plan.to_version, steps)
        plan_hash = sha256_hex(canonical_json_bytes(payload))
        expected_id = derive_id("mplan", payload | {"plan_sha256": plan_hash})
        if (
            plan.migration_ids != payload["migration_ids"]
            or plan.backup_required != payload["backup_required"]
            or plan.reversible != payload["reversible"]
            or plan.plan_sha256 != plan_hash
            or plan.migration_plan_id != expected_id
        ):
            raise MigrationError("Migration plan is forged or corrupted")
        return steps


def _registry_chain(current: str, target: str) -> tuple[Migration, ...]:
    if current == target:
        return ()
    by_from: dict[str, Migration] = {}
    for step in MIGRATIONS:
        if step.from_version in by_from:
            raise MigrationError("Migration registry declares duplicate steps")
        by_from[step.from_version] = step
    steps: list[Migration] = []
    cursor = current
    while cursor != target:
        next_step = by_from.get(cursor)
        if next_step is None or len(steps) >= len(MIGRATIONS):
            raise MigrationError("No registered migration path reaches the target schema")
        steps.append(next_step)
        cursor = next_step.to_version
    return tuple(steps)


def _plan_payload(current: str, target: str, steps: tuple[Migration, ...]) -> dict[str, Any]:
    return {
        "from_version": current,
        "to_version": target,
        "migration_ids": tuple(step.migration_id for step in steps),
        "backup_required": bool(steps),
        "reversible": all(step.reversible for step in steps),
    }


def _journal(store: PlatformStore, step: Migration, migration_sha256: str, state: str) -> None:
    with store.transaction():
        store.connection.execute(
            "INSERT INTO migration_journal VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(migration_id) DO UPDATE SET "
            "migration_sha256=excluded.migration_sha256, state=excluded.state, "
            "applied_at=excluded.applied_at",
            (
                step.migration_id,
                migration_sha256,
                _version_int(step.from_version),
                _version_int(step.to_version),
                state,
                datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            ),
        )


def _journal_state(store: PlatformStore, migration_id: str) -> str | None:
    row = store.connection.execute(
        "SELECT state FROM migration_journal WHERE migration_id = ?", (migration_id,)
    ).fetchone()
    return None if row is None else str(row["state"])


def _failure_sha256(step: Migration) -> str:
    return sha256_hex(canonical_json_bytes({"migration_id": step.migration_id, "state": "failed"}))


def _version_int(value: str) -> int:
    major, minor, patch = _version_tuple(value)
    if not all(0 <= part < 1000 for part in (major, minor, patch)):
        raise MigrationError("Schema version is invalid")
    return major * 1_000_000 + minor * 1_000 + patch


def _version_tuple(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as error:
        raise MigrationError("Schema version is invalid") from error
    if len(parts) != 3:
        raise MigrationError("Schema version is invalid")
    return parts
