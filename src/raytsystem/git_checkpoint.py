from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from raytsystem.contracts import (
    LedgerGeneration,
    PromotionEvent,
    PromotionTxn,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.base import validate_relative_path
from raytsystem.security.paths import read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import IntegrityError, publish_immutable, read_json


class CheckpointRejected(RuntimeError):
    """A local Git checkpoint failed an exact-path or secret gate."""


@dataclass(frozen=True)
class GitCheckpointResult:
    event_id: str
    generation_id: str
    commit_sha: str
    reference: str
    paths: tuple[str, ...]


class GitCheckpoint:
    _forbidden_roots = (
        "inbox",
        "_raw/blobs",
        "_raw/restricted",
        "ops/staging",
        "artifacts/drafts",
        "artifacts/outbox",
        ".raytsystem",
        ".qmd",
        ".git",
        "ops/control.sqlite",
        "ops/control.sqlite-shm",
        "ops/control.sqlite-wal",
        "ops/locks",
    )

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def create(
        self,
        *,
        event_id: str,
        generation_id: str,
        paths: tuple[str, ...],
    ) -> GitCheckpointResult:
        if re.fullmatch(r"evt_[0-9a-f]{64}", event_id) is None:
            raise CheckpointRejected("Malformed promotion event ID")
        if re.fullmatch(r"gen_[0-9a-f]{64}", generation_id) is None:
            raise CheckpointRejected("Malformed generation ID")
        event = self._verify_provenance(event_id, generation_id)
        required_paths = (
            f"ledger/generations/{generation_id}.json",
            f"ops/events/{event_id}.json",
            f"ops/runs/{event.run_id}/manifest.json",
        )
        normalized = tuple(
            sorted({self._validate_path(path) for path in (*paths, *required_paths)})
        )
        if not normalized:
            raise CheckpointRejected("Checkpoint allowlist is empty")
        reference = f"refs/raytsystem/checkpoints/{event_id}"
        record_path = self.root / "ops" / "checkpoints" / f"{event_id}.json"
        if record_path.is_file():
            payload = read_json(record_path)
            self._verify_checkpoint_record(payload)
            if (
                payload.get("state") not in {None, "completed"}
                or payload.get("event_id") != event_id
                or payload.get("generation_id") != generation_id
                or payload.get("reference") != reference
                or tuple(payload.get("paths", ())) != normalized
            ):
                raise CheckpointRejected("Existing checkpoint record has different provenance")
            commit = str(payload.get("commit_sha", ""))
            self._ensure_reference(reference, commit)
            return GitCheckpointResult(event_id, generation_id, commit, reference, normalized)

        pending_path = self.root / "ops" / "checkpoints" / "pending" / f"{event_id}.json"
        if pending_path.is_file():
            pending = read_json(pending_path)
            self._verify_checkpoint_record(pending)
            if (
                pending.get("state") != "prepared"
                or pending.get("event_id") != event_id
                or pending.get("generation_id") != generation_id
                or pending.get("reference") != reference
                or tuple(pending.get("paths", ())) != normalized
            ):
                raise CheckpointRejected("Pending checkpoint has different provenance")
            commit = str(pending.get("commit_sha", ""))
        else:
            with tempfile.TemporaryDirectory(prefix="raytsystem-git-") as temporary:
                index = Path(temporary) / "index"
                environment = self._git_environment(index, Path(temporary))
                timestamp = event.committed_at.isoformat()
                environment["GIT_AUTHOR_DATE"] = timestamp
                environment["GIT_COMMITTER_DATE"] = timestamp
                parent = self._git(("rev-parse", "HEAD"), environment=environment).strip()
                self._git(("read-tree", parent), environment=environment)
                for relative in normalized:
                    read = read_regular_file(self.root, relative, max_bytes=25 * 1024 * 1024)
                    decision = self.scanner.scan(read.data, path=relative)
                    if (
                        not isinstance(decision, SensitivityDecision)
                        or decision.disposition != "allow"
                    ):
                        raise CheckpointRejected(f"Secret gate rejected staged path: {relative}")
                    blob = self._git(
                        ("hash-object", "-w", "--stdin"),
                        environment=environment,
                        input_bytes=read.data,
                    ).strip()
                    self._git(
                        ("update-index", "--add", "--cacheinfo", f"100644,{blob},{relative}"),
                        environment=environment,
                    )
                tree = self._git(("write-tree",), environment=environment).strip()
                message = (
                    f"checkpoint: raytsystem generation {generation_id}\n\n"
                    f"raytsystem-Event: {event_id}\n"
                    f"raytsystem-Generation: {generation_id}\n"
                ).encode()
                commit = self._git(
                    ("-c", "commit.gpgsign=false", "commit-tree", tree, "-p", parent),
                    environment=environment,
                    input_bytes=message,
                ).strip()
            pending = self._checkpoint_record(
                state="prepared",
                event_id=event_id,
                generation_id=generation_id,
                commit=commit,
                reference=reference,
                paths=normalized,
                created_at=event.committed_at.isoformat().replace("+00:00", "Z"),
            )
            publish_immutable(pending_path, canonical_json_bytes(pending))

        self._ensure_reference(reference, commit)

        payload = self._checkpoint_record(
            state="completed",
            event_id=event_id,
            generation_id=generation_id,
            commit=commit,
            reference=reference,
            paths=normalized,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        publish_immutable(record_path, canonical_json_bytes(payload))
        return GitCheckpointResult(event_id, generation_id, commit, reference, normalized)

    def verify(self, *, event_id: str, generation_id: str) -> GitCheckpointResult:
        """Validate a completed record, commit object and ref without repairing state."""

        if re.fullmatch(r"evt_[0-9a-f]{64}", event_id) is None:
            raise CheckpointRejected("Malformed promotion event ID")
        if re.fullmatch(r"gen_[0-9a-f]{64}", generation_id) is None:
            raise CheckpointRejected("Malformed generation ID")
        event = self._verify_provenance(event_id, generation_id, require_control=False)
        reference = f"refs/raytsystem/checkpoints/{event_id}"
        record_path = self.root / "ops" / "checkpoints" / f"{event_id}.json"
        if not record_path.is_file():
            raise CheckpointRejected("Completed checkpoint record is missing")
        payload = read_json(record_path)
        self._verify_checkpoint_record(payload)
        paths_value = payload.get("paths")
        if not isinstance(paths_value, (list, tuple)) or not all(
            isinstance(path, str) for path in paths_value
        ):
            raise CheckpointRejected("Checkpoint record path list is malformed")
        normalized = tuple(sorted({self._validate_path(path) for path in paths_value}))
        required_paths = {
            f"ledger/generations/{generation_id}.json",
            f"ops/events/{event_id}.json",
            f"ops/runs/{event.run_id}/manifest.json",
        }
        if (
            payload.get("state") not in {None, "completed"}
            or payload.get("event_id") != event_id
            or payload.get("generation_id") != generation_id
            or payload.get("reference") != reference
            or tuple(paths_value) != normalized
            or not required_paths.issubset(normalized)
        ):
            raise CheckpointRejected("Completed checkpoint record provenance disagrees")
        commit = str(payload.get("commit_sha", ""))
        self._verify_commit_object(commit)
        if self._try_resolve_reference(reference) != commit:
            raise CheckpointRejected("Checkpoint ref and record disagree")
        return GitCheckpointResult(event_id, generation_id, commit, reference, normalized)

    def _verify_provenance(
        self,
        event_id: str,
        generation_id: str,
        *,
        require_control: bool = True,
    ) -> PromotionEvent:
        generation_path = self.root / "ledger" / "generations" / f"{generation_id}.json"
        event_path = self.root / "ops" / "events" / f"{event_id}.json"
        try:
            generation_bytes = read_regular_file(
                self.root,
                generation_path.relative_to(self.root).as_posix(),
                max_bytes=25 * 1024 * 1024,
            ).data
            event_bytes = read_regular_file(
                self.root,
                event_path.relative_to(self.root).as_posix(),
                max_bytes=4 * 1024 * 1024,
            ).data
            generation = LedgerGeneration.model_validate(json.loads(generation_bytes))
            event = PromotionEvent.model_validate(json.loads(event_bytes))
        except (OSError, ValueError) as error:
            raise CheckpointRejected(
                "Checkpoint provenance objects are missing or invalid"
            ) from error
        if generation_bytes != canonical_json_bytes(
            generation
        ) or event_bytes != canonical_json_bytes(event):
            raise CheckpointRejected("Checkpoint provenance objects are not canonical")
        if (
            not generation.verify_id()
            or generation.generation_id != generation_id
            or generation.promotion_event_id != event_id
            or event.event_id != event_id
            or event.new_generation_id != generation_id
            or event.txn_id != generation.promotion_txn_id
            or event.event_id != derive_id("evt", {"txn_id": event.txn_id})
            or event.parent_generation_id != generation.parent_generation_id
            or event.committed_at != generation.created_at
        ):
            raise CheckpointRejected("Checkpoint event and generation provenance disagree")

        run_path = self.root / "ops" / "runs" / event.run_id / "manifest.json"
        try:
            run = read_json(run_path)
        except (OSError, ValueError, IntegrityError) as error:
            raise CheckpointRejected("Checkpoint run manifest is missing or invalid") from error
        required_run = {
            "run_id": event.run_id,
            "operation_key": event.operation_key,
            "txn_id": event.txn_id,
            "event_id": event.event_id,
            "generation_id": generation_id,
        }
        if any(run.get(key) != value for key, value in required_run.items()):
            raise CheckpointRejected("Checkpoint run manifest provenance disagrees")
        try:
            run_created_at = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise CheckpointRejected("Checkpoint run timestamp is invalid") from error
        if int(run_created_at.timestamp() * 1000) != int(event.committed_at.timestamp() * 1000):
            raise CheckpointRejected("Checkpoint event timestamp disagrees with its run")

        if not require_control:
            return event

        database_path = self.root / "ops" / "control.sqlite"
        if not database_path.is_file() or database_path.is_symlink():
            raise CheckpointRejected("Checkpoint durable control database is unavailable")
        try:
            connection = sqlite3.connect(
                f"{database_path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=2,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            outbox = connection.execute(
                "SELECT * FROM event_outbox WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            wal = connection.execute(
                "SELECT * FROM promotion_txns WHERE txn_id = ?",
                (event.txn_id,),
            ).fetchone()
            operation = connection.execute(
                "SELECT run_id FROM operations WHERE operation_key = ?",
                (event.operation_key,),
            ).fetchone()
        except sqlite3.Error as error:
            raise CheckpointRejected("Checkpoint durable provenance query failed") from error
        finally:
            if "connection" in locals():
                connection.close()
        if outbox is None or wal is None or operation is None:
            raise CheckpointRejected("Checkpoint durable provenance row is missing")
        outbox_json = str(outbox["payload_json"]).encode()
        if (
            str(outbox["event_id"]) != event_id
            or str(outbox["txn_id"]) != event.txn_id
            or str(outbox["state"]) != "appended"
            or outbox_json != event_bytes
            or str(outbox["payload_sha256"]) != sha256_hex(event_bytes)
            or str(operation["run_id"]) != event.run_id
        ):
            raise CheckpointRejected("Checkpoint event outbox provenance disagrees")
        try:
            txn_bytes = str(wal["payload_json"]).encode()
            txn = PromotionTxn.model_validate(json.loads(txn_bytes))
        except (json.JSONDecodeError, ValueError) as error:
            raise CheckpointRejected("Checkpoint promotion WAL payload is invalid") from error
        if txn_bytes != canonical_json_bytes(txn):
            raise CheckpointRejected("Checkpoint promotion WAL payload is not canonical")
        if (
            txn.txn_id != event.txn_id
            or txn.run_id != event.run_id
            or txn.operation_key != event.operation_key
            or txn.parent_generation_id != event.parent_generation_id
            or txn.next_generation_id != generation_id
            or txn.event_id != event_id
            or txn.candidate_manifest_sha256 != sha256_hex(generation_bytes)
            or str(wal["run_id"]) != event.run_id
            or str(wal["operation_key"]) != event.operation_key
            or str(wal["parent_generation_id"]) != event.parent_generation_id
            or str(wal["next_generation_id"]) != generation_id
            or str(wal["manifest_sha256"]) != sha256_hex(generation_bytes)
            or str(wal["event_id"]) != event_id
            or int(wal["partition_fencing_token"]) != txn.partition_fencing_token
            or int(wal["global_fencing_token"]) != txn.global_fencing_token
        ):
            raise CheckpointRejected("Checkpoint promotion WAL provenance disagrees")
        return event

    @staticmethod
    def _checkpoint_record(
        *,
        state: str,
        event_id: str,
        generation_id: str,
        commit: str,
        reference: str,
        paths: tuple[str, ...],
        created_at: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "1.0.0",
            "state": state,
            "event_id": event_id,
            "generation_id": generation_id,
            "commit_sha": commit,
            "reference": reference,
            "paths": paths,
            "created_at": created_at,
        }
        payload["payload_sha256"] = sha256_hex(canonical_json_bytes(payload))
        return payload

    @staticmethod
    def _verify_checkpoint_record(payload: dict[str, object]) -> None:
        recorded = payload.get("payload_sha256")
        material = dict(payload)
        material.pop("payload_sha256", None)
        if not isinstance(recorded, str) or recorded != sha256_hex(canonical_json_bytes(material)):
            raise CheckpointRejected("Checkpoint record payload hash mismatch")

    def _try_resolve_reference(self, reference: str) -> str | None:
        completed = subprocess.run(
            ("git", "-C", str(self.root), "rev-parse", "--verify", reference),
            capture_output=True,
            env=self._git_environment(None, None),
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.decode("ascii").strip()

    def _verify_commit_object(self, commit: str) -> None:
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
            raise CheckpointRejected("Checkpoint record has a malformed commit ID")
        resolved = self._git(
            ("rev-parse", "--verify", f"{commit}^{{commit}}"),
            environment=self._git_environment(None, None),
        ).strip()
        if resolved != commit:
            raise CheckpointRejected("Checkpoint commit object identity disagrees")

    def _ensure_reference(self, reference: str, commit: str) -> None:
        self._verify_commit_object(commit)
        resolved = self._try_resolve_reference(reference)
        if resolved is None:
            try:
                self._git(
                    ("update-ref", reference, commit, "0" * 40),
                    environment=self._git_environment(None, None),
                )
            except CheckpointRejected:
                if self._try_resolve_reference(reference) != commit:
                    raise
            resolved = self._try_resolve_reference(reference)
        if resolved != commit:
            raise CheckpointRejected("Checkpoint reference already targets a different commit")

    def _validate_path(self, value: str) -> str:
        try:
            relative = validate_relative_path(value)
        except ValueError as error:
            raise CheckpointRejected("Checkpoint path escapes the workspace") from error
        pure = PurePosixPath(relative)
        if any(
            pure == PurePosixPath(prefix) or PurePosixPath(prefix) in pure.parents
            for prefix in self._forbidden_roots
        ):
            raise CheckpointRejected(f"Checkpoint path is policy-forbidden: {relative}")
        return relative

    def _git_environment(self, index: Path | None, home: Path | None) -> dict[str, str]:
        environment = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "raytsystem",
            "GIT_AUTHOR_EMAIL": "raytsystem@local.invalid",
            "GIT_COMMITTER_NAME": "raytsystem",
            "GIT_COMMITTER_EMAIL": "raytsystem@local.invalid",
        }
        if index is not None:
            environment["GIT_INDEX_FILE"] = str(index)
        if home is not None:
            environment["HOME"] = str(home)
        return environment

    def _git(
        self,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        input_bytes: bytes | None = None,
    ) -> str:
        completed = subprocess.run(
            ("git", "-C", str(self.root), *arguments),
            input=input_bytes,
            capture_output=True,
            env=environment,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise CheckpointRejected("Git checkpoint plumbing command failed")
        return completed.stdout.decode("ascii").strip()
