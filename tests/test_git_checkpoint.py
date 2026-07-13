from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raytsystem.contracts import (
    LedgerGeneration,
    PromotionEvent,
    PromotionState,
    PromotionTxn,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.control import ControlDB
from raytsystem.git_checkpoint import CheckpointRejected, GitCheckpoint
from raytsystem.ingestion import IngestPipeline


def _git(root: Path, *arguments: str) -> str:
    return (
        subprocess.run(
            ("git", "-C", str(root), *arguments),
            capture_output=True,
            check=True,
            timeout=10,
        )
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "baseline.txt")
    _git(
        tmp_path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    return tmp_path


def _write_provenance(root: Path, seed: str) -> tuple[str, str]:
    run_id = derive_id("run", {"seed": seed})
    operation_key = derive_id("op", {"seed": seed})
    txn_id = derive_id("ptxn", {"seed": seed})
    event_id = derive_id("evt", {"txn_id": txn_id})
    created_at = datetime(2026, 7, 10, tzinfo=UTC)
    generation_seed = LedgerGeneration(
        generation_id="gen_pending",
        parent_generation_id="genesis",
        records={},
        created_at=created_at,
        promotion_txn_id=txn_id,
        promotion_event_id=event_id,
    )
    generation_id = derive_id("gen", generation_seed.identity_payload())
    generation = generation_seed.model_copy(update={"generation_id": generation_id})
    event = PromotionEvent(
        event_id=event_id,
        txn_id=txn_id,
        run_id=run_id,
        operation_key=operation_key,
        parent_generation_id="genesis",
        new_generation_id=generation_id,
        committed_at=created_at,
    )
    generation_bytes = canonical_json_bytes(generation)
    txn = PromotionTxn(
        txn_id=txn_id,
        run_id=run_id,
        operation_key=operation_key,
        parent_generation_id="genesis",
        next_generation_id=generation_id,
        candidate_manifest_sha256=sha256_hex(generation_bytes),
        event_id=event_id,
        partition_fencing_token=1,
        global_fencing_token=1,
        output_hashes={},
        state=PromotionState.PREPARED,
        created_at=created_at,
        updated_at=created_at,
    )
    generation_path = root / "ledger" / "generations" / f"{generation_id}.json"
    event_path = root / "ops" / "events" / f"{event_id}.json"
    generation_path.parent.mkdir(parents=True)
    event_path.parent.mkdir(parents=True)
    generation_path.write_bytes(generation_bytes)
    event_path.write_bytes(canonical_json_bytes(event))
    run_path = root / "ops" / "runs" / run_id / "manifest.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_bytes(
        canonical_json_bytes(
            {
                "run_id": run_id,
                "operation_key": operation_key,
                "txn_id": txn_id,
                "event_id": event_id,
                "generation_id": generation_id,
                "created_at": created_at,
            }
        )
    )
    control = ControlDB(root / "ops" / "control.sqlite")
    control.claim_operation(
        operation_key=operation_key,
        run_id=run_id,
        stage="ingest",
        partition_key="ledger:current",
        now_ms=1,
    )
    control.store_promotion(
        txn_id=txn_id,
        operation_key=operation_key,
        run_id=run_id,
        partition_fencing_token=1,
        global_fencing_token=1,
        parent_generation_id="genesis",
        next_generation_id=generation_id,
        manifest_sha256=sha256_hex(generation_bytes),
        event_id=event_id,
        approval_hash=None,
        payload_json=canonical_json_bytes(txn).decode(),
        state="committed",
        now_ms=1,
    )
    control.store_event_outbox(
        event_id=event_id,
        txn_id=txn_id,
        payload_json=canonical_json_bytes(event).decode(),
        payload_sha256=sha256_hex(canonical_json_bytes(event)),
    )
    control.mark_event_appended(event_id)
    control.close()
    return event_id, generation_id


def test_checkpoint_uses_isolated_index_and_preserves_dirty_user_state(git_project: Path) -> None:
    user_file = git_project / "user-notes.txt"
    user_file.write_text("unstaged user work\n", encoding="utf-8")
    generated = git_project / "knowledge" / "index.md"
    generated.parent.mkdir()
    generated.write_text("# Generated\n", encoding="utf-8")
    head_before = _git(git_project, "rev-parse", "HEAD")
    index_before = (git_project / ".git" / "index").read_bytes()
    event_id, generation_id = _write_provenance(git_project, "first")

    result = GitCheckpoint(git_project).create(
        event_id=event_id,
        generation_id=generation_id,
        paths=("knowledge/index.md",),
    )

    assert _git(git_project, "rev-parse", "HEAD") == head_before
    assert (git_project / ".git" / "index").read_bytes() == index_before
    assert user_file.read_text() == "unstaged user work\n"
    assert _git(git_project, "show", f"{result.commit_sha}:knowledge/index.md") == "# Generated"
    assert _git(git_project, "rev-parse", result.reference) == result.commit_sha


def test_checkpoint_rejects_forbidden_or_secret_like_path(git_project: Path) -> None:
    (git_project / "inbox").mkdir()
    (git_project / "inbox" / "secret.md").write_text("TOKEN=" + "x" * 32)

    event_id, generation_id = _write_provenance(git_project, "second")
    with pytest.raises(CheckpointRejected, match="policy-forbidden"):
        GitCheckpoint(git_project).create(
            event_id=event_id,
            generation_id=generation_id,
            paths=("inbox/secret.md",),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("parent_generation_id", "genesis_changed"),
        ("run_id", derive_id("run", {"mutated": True})),
        ("operation_key", derive_id("op", {"mutated": True})),
        ("committed_at", "2026-07-10T00:00:01Z"),
    ),
)
def test_checkpoint_rejects_mutated_event_fields(
    git_project: Path,
    field: str,
    replacement: str,
) -> None:
    event_id, generation_id = _write_provenance(git_project, f"mutated-{field}")
    event_path = git_project / "ops" / "events" / f"{event_id}.json"
    payload = json.loads(event_path.read_text())
    payload[field] = replacement
    event_path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        CheckpointRejected,
        match=r"provenance|canonical|disagree|missing|invalid",
    ):
        GitCheckpoint(git_project).create(
            event_id=event_id,
            generation_id=generation_id,
            paths=(),
        )


def test_checkpoint_rejects_tampered_durable_outbox(git_project: Path) -> None:
    event_id, generation_id = _write_provenance(git_project, "outbox-tamper")
    connection = sqlite3.connect(git_project / "ops" / "control.sqlite")
    connection.execute(
        "UPDATE event_outbox SET payload_sha256 = ? WHERE event_id = ?",
        ("f" * 64, event_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(CheckpointRejected, match="outbox"):
        GitCheckpoint(git_project).create(
            event_id=event_id,
            generation_id=generation_id,
            paths=(),
        )


def test_checkpoint_recovers_ref_created_before_final_record(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = git_project / "knowledge" / "index.md"
    generated.parent.mkdir()
    generated.write_text("# Generated\n", encoding="utf-8")
    event_id, generation_id = _write_provenance(git_project, "orphan-ref")
    from raytsystem import git_checkpoint as checkpoint_module

    original_publish = checkpoint_module.publish_immutable
    final_path = git_project / "ops" / "checkpoints" / f"{event_id}.json"

    def fail_final(path: Path, data: bytes, *, mode: int = 0o644) -> bool:
        if path == final_path:
            raise OSError("simulated crash after ref")
        return original_publish(path, data, mode=mode)

    monkeypatch.setattr(checkpoint_module, "publish_immutable", fail_final)
    with pytest.raises(OSError, match="simulated crash"):
        GitCheckpoint(git_project).create(
            event_id=event_id,
            generation_id=generation_id,
            paths=("knowledge/index.md",),
        )
    reference = f"refs/raytsystem/checkpoints/{event_id}"
    orphan_commit = _git(git_project, "rev-parse", reference)

    monkeypatch.setattr(checkpoint_module, "publish_immutable", original_publish)
    recovered = GitCheckpoint(git_project).create(
        event_id=event_id,
        generation_id=generation_id,
        paths=("knowledge/index.md",),
    )

    assert recovered.commit_sha == orphan_commit
    assert final_path.is_file()


def test_checkpoint_recovers_completed_record_when_ref_is_missing(git_project: Path) -> None:
    generated = git_project / "knowledge" / "index.md"
    generated.parent.mkdir()
    generated.write_text("# Generated\n", encoding="utf-8")
    event_id, generation_id = _write_provenance(git_project, "completed-record-orphan")
    first = GitCheckpoint(git_project).create(
        event_id=event_id,
        generation_id=generation_id,
        paths=("knowledge/index.md",),
    )
    _git(git_project, "update-ref", "-d", first.reference)

    recovered = GitCheckpoint(git_project).create(
        event_id=event_id,
        generation_id=generation_id,
        paths=("knowledge/index.md",),
    )

    assert recovered.commit_sha == first.commit_sha
    assert _git(git_project, "rev-parse", recovered.reference) == first.commit_sha


def test_ingest_reconcile_creates_provenance_checkpoint_ref(project_root: Path) -> None:
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace(
            "checkpoint_on_promotion = false",
            "checkpoint_on_promotion = true",
        ),
        encoding="utf-8",
    )
    _git(project_root, "init", "-q")
    _git(project_root, "add", "config", "ledger")
    _git(
        project_root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    head_before = _git(project_root, "rev-parse", "HEAD")
    source = project_root / "inbox" / "checkpoint.md"
    source.write_text("Checkpoint integration fact.\n", encoding="utf-8")

    result = IngestPipeline(project_root).ingest(source, fixture=True)

    generation = json.loads(
        (project_root / "ledger" / "generations" / f"{result.generation_id}.json").read_text()
    )
    event_id = generation["promotion_event_id"]
    reference = f"refs/raytsystem/checkpoints/{event_id}"
    assert _git(project_root, "rev-parse", reference)
    assert (project_root / "ops" / "checkpoints" / f"{event_id}.json").is_file()
    assert _git(project_root, "rev-parse", "HEAD") == head_before


def test_identical_ingest_repairs_missing_active_checkpoint_ref(project_root: Path) -> None:
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace(
            "checkpoint_on_promotion = false",
            "checkpoint_on_promotion = true",
        ),
        encoding="utf-8",
    )
    _git(project_root, "init", "-q")
    _git(project_root, "add", "config", "ledger")
    _git(
        project_root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    source = project_root / "inbox" / "checkpoint-repair.md"
    source.write_text("Checkpoint ref recovery remains idempotent.\n", encoding="utf-8")
    first = IngestPipeline(project_root).ingest(source, fixture=True)
    generation = json.loads(
        (project_root / "ledger" / "generations" / f"{first.generation_id}.json").read_text()
    )
    event_id = generation["promotion_event_id"]
    reference = f"refs/raytsystem/checkpoints/{event_id}"
    record = json.loads((project_root / "ops" / "checkpoints" / f"{event_id}.json").read_text())
    _git(project_root, "update-ref", "-d", reference)

    recovered = IngestPipeline(project_root).ingest(source, fixture=True)

    assert recovered.status == "succeeded"
    assert recovered.generation_id == first.generation_id
    assert _git(project_root, "rev-parse", reference) == record["commit_sha"]


def test_checkpointed_idempotency_survives_control_db_recreation(project_root: Path) -> None:
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace(
            "checkpoint_on_promotion = false",
            "checkpoint_on_promotion = true",
        ),
        encoding="utf-8",
    )
    _git(project_root, "init", "-q")
    _git(project_root, "add", "config", "ledger")
    _git(
        project_root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    source = project_root / "inbox" / "checkpoint-control-rebuild.md"
    source.write_text(
        "A checkpoint remains valid when coordination is rebuilt.\n", encoding="utf-8"
    )
    first_pipeline = IngestPipeline(project_root)
    first = first_pipeline.ingest(source, fixture=True)
    first_pipeline.control.close()
    for suffix in ("", "-shm", "-wal"):
        (project_root / "ops" / f"control.sqlite{suffix}").unlink(missing_ok=True)

    second = IngestPipeline(project_root).ingest(source, fixture=True)

    assert second.noop is True
    assert second.generation_id == first.generation_id
    generation = json.loads(
        (project_root / "ledger" / "generations" / f"{first.generation_id}.json").read_text()
    )
    event_id = generation["promotion_event_id"]
    reference = f"refs/raytsystem/checkpoints/{event_id}"
    assert _git(project_root, "rev-parse", "--verify", reference)
