from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from raytsystem.contracts import (
    ApprovalRecord,
    PromotionState,
    PromotionTxn,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.ingestion import ApprovalRequired, IngestPipeline, InjectedFault, IntegrityError


class _RecoveryApprovalVerifier:
    def __init__(self, allowed: bytes) -> None:
        self.allowed = allowed

    def verify(self, payload: bytes) -> ApprovalRecord:
        if payload != self.allowed:
            raise ApprovalRequired("unverified recovery approval")
        return ApprovalRecord.model_validate(json.loads(payload))


def test_resume_before_pointer_swap_commits_once(project_root: Path) -> None:
    source = project_root / "inbox" / "source.md"
    source.write_text("# Resume before commit\n", encoding="utf-8")

    with pytest.raises(InjectedFault, match="after_generation_publish"):
        IngestPipeline(project_root, fail_at="after_generation_publish").ingest(
            source,
            fixture=True,
        )
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == "genesis"

    resumed = IngestPipeline(project_root).ingest(source, fixture=True)

    assert resumed.status == "succeeded"
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1


def test_resume_after_pointer_swap_reconciles_without_second_commit(project_root: Path) -> None:
    source = project_root / "inbox" / "source.md"
    source.write_text("# Resume after commit\n", encoding="utf-8")

    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(project_root, fail_at="after_current_swap").ingest(
            source,
            fixture=True,
        )
    committed_generation = (project_root / "ledger" / "CURRENT").read_text().strip()
    assert committed_generation != "genesis"

    resumed = IngestPipeline(project_root).ingest(source, fixture=True)

    assert resumed.status == "succeeded"
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == committed_generation
    assert len(list((project_root / "ledger" / "generations").glob("*.json"))) == 2
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1


def test_resume_after_pointer_swap_without_staging_uses_durable_wal(project_root: Path) -> None:
    source = project_root / "inbox" / "source.md"
    source.write_text("# Durable WAL recovery\n", encoding="utf-8")

    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(project_root, fail_at="after_current_swap").ingest(
            source,
            fixture=True,
        )
    committed_generation = (project_root / "ledger" / "CURRENT").read_text().strip()
    staging = next((project_root / "ops" / "staging").iterdir())
    for path in staging.iterdir():
        path.unlink()
    staging.rmdir()

    resumed = IngestPipeline(project_root).ingest(source, fixture=True)

    assert resumed.status == "succeeded"
    assert resumed.generation_id == committed_generation
    assert len(list((project_root / "ledger" / "generations").glob("*.json"))) == 2


def test_two_prepared_runs_rebase_and_preserve_both_claims(project_root: Path) -> None:
    first = project_root / "inbox" / "first.md"
    second = project_root / "inbox" / "second.md"
    first.write_text("First staged claim.\n", encoding="utf-8")
    second.write_text("Second staged claim.\n", encoding="utf-8")

    for source in (first, second):
        with pytest.raises(InjectedFault, match="after_proposal_validation"):
            IngestPipeline(project_root, fail_at="after_proposal_validation").ingest(
                source,
                fixture=True,
            )

    IngestPipeline(project_root).ingest(first, fixture=True)
    IngestPipeline(project_root).ingest(second, fixture=True)

    current = (project_root / "ledger" / "CURRENT").read_text().strip()
    generation = (project_root / "ledger" / "generations" / f"{current}.json").read_text()
    assert generation.count('"kind":"claim"') == 2
    index = (project_root / "knowledge" / "index.md").read_text()
    assert "First staged claim" in index
    assert "Second staged claim" in index


@pytest.mark.parametrize(
    "checkpoint",
    [
        "after_raw_capture",
        "after_normalization_publish",
        "after_proposal_validation",
        "after_generation_publish",
        "after_promotion_wal",
        "after_current_swap",
        "after_db_committed",
        "after_event_publish",
        "after_materialization",
        "after_git_checkpoint",
        "before_succeeded",
    ],
)
def test_named_fault_boundaries_resume_idempotently(
    project_root: Path,
    checkpoint: str,
) -> None:
    source = project_root / "inbox" / "boundary.md"
    source.write_text("Boundary evidence.\n", encoding="utf-8")

    with pytest.raises(InjectedFault, match=checkpoint):
        IngestPipeline(project_root, fail_at=checkpoint).ingest(source, fixture=True)

    resumed = IngestPipeline(project_root).ingest(source, fixture=True)

    assert resumed.status == "succeeded"
    assert len(list((project_root / "ledger" / "generations").glob("*.json"))) == 2
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1


def test_post_swap_recovery_needs_neither_staging_nor_inbox(project_root: Path) -> None:
    source = project_root / "inbox" / "durable-only.md"
    source.write_text("Durable recovery fact.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(project_root, fail_at="after_current_swap").ingest(
            source,
            fixture=True,
        )
    run_dir = next((project_root / "ops" / "runs").iterdir())
    staging = project_root / "ops" / "staging" / run_dir.name
    for path in staging.iterdir():
        path.unlink()
    staging.rmdir()
    source.unlink()

    resumed = IngestPipeline(project_root).promote_run(run_dir.name, fixture=True)

    assert resumed.status == "succeeded"
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1


def test_wal_loser_aborts_old_attempt_and_rebases_after_concurrent_promotion(
    project_root: Path,
) -> None:
    first = project_root / "inbox" / "wal-first.md"
    second = project_root / "inbox" / "wal-second.md"
    first.write_text("First WAL claim.\n", encoding="utf-8")
    second.write_text("Second concurrent claim.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match="after_promotion_wal"):
        IngestPipeline(project_root, fail_at="after_promotion_wal").ingest(
            first,
            fixture=True,
        )
    first_run = next((project_root / "ops" / "runs").iterdir()).name
    IngestPipeline(project_root).ingest(second, fixture=True)

    resumed = IngestPipeline(project_root).promote_run(first_run, fixture=True)

    assert resumed.status == "succeeded"
    index = (project_root / "knowledge" / "index.md").read_text()
    assert "First WAL claim" in index
    assert "Second concurrent claim" in index


def test_postcommit_recovery_ignores_rotated_fixture_policy(project_root: Path) -> None:
    source = project_root / "inbox" / "historical-fixture.md"
    source.write_text("Historically authorized fixture.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(project_root, fail_at="after_current_swap").ingest(
            source,
            fixture=True,
        )
    committed = (project_root / "ledger" / "CURRENT").read_text().strip()
    run_id = next((project_root / "ops" / "runs").iterdir()).name
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace('root = "inbox"', 'root = "tests/fixtures"'),
        encoding="utf-8",
    )
    source.unlink()
    staging = project_root / "ops" / "staging" / run_id
    for artifact in staging.iterdir():
        artifact.unlink()
    staging.rmdir()

    recovered = IngestPipeline(project_root).promote_run(run_id)

    assert recovered.status == "succeeded"
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == committed
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1


def test_real_postcommit_recovery_uses_accepted_record_after_expiry(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = project_root / "inbox" / "approved-crash.md"
    source.write_text("Approved exact candidate.\n", encoding="utf-8")
    prepared = IngestPipeline(project_root).ingest(source, prepare_only=True)
    txn = PromotionTxn.model_validate(
        json.loads(
            (project_root / "ops" / "staging" / prepared.run_id / "promotion_txn.json").read_text()
        )
    )
    assert txn.candidate_manifest_sha256 is not None
    now = datetime.now(UTC)
    approval = ApprovalRecord.create(
        action="promote",
        target_id=txn.txn_id,
        artifact_sha256=txn.candidate_manifest_sha256,
        policy_version="1.0.0",
        approver="recovery_test_reviewer",
        approved_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=5),
        scope=("real_corpus",),
        policy_sha256=sha256_hex((project_root / "config" / "policies.yaml").read_bytes()),
    )
    approval_bytes = canonical_json_bytes(approval)
    approval_path = project_root / "ops" / "approvals" / "incoming" / "approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_bytes(approval_bytes)
    verifier = _RecoveryApprovalVerifier(approval_bytes)

    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(
            project_root,
            fail_at="after_current_swap",
            approval_verifier=verifier,
        ).promote_run(prepared.run_id, approval_path=approval_path)
    committed = (project_root / "ledger" / "CURRENT").read_text().strip()
    approval_path.unlink()
    source.unlink()

    real_datetime = datetime

    class _FutureDateTime(real_datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return real_datetime.now(tz) + timedelta(days=1)  # type: ignore[arg-type]

    monkeypatch.setattr("raytsystem.ingestion.datetime", _FutureDateTime)
    (project_root / "config" / "policies.yaml").write_text(
        "version: 1.0.1\ndefault: deny\n",
        encoding="utf-8",
    )

    recovered = IngestPipeline(project_root).promote_run(prepared.run_id)

    assert recovered.status == "succeeded"
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == committed


def test_missing_accepted_approval_blocks_postcommit_reconciliation(
    project_root: Path,
) -> None:
    source = project_root / "inbox" / "missing-accepted.md"
    source.write_text("Approved candidate with lost audit.\n", encoding="utf-8")
    prepared = IngestPipeline(project_root).ingest(source, prepare_only=True)
    txn = PromotionTxn.model_validate(
        json.loads(
            (project_root / "ops" / "staging" / prepared.run_id / "promotion_txn.json").read_text()
        )
    )
    assert txn.candidate_manifest_sha256 is not None
    now = datetime.now(UTC)
    approval = ApprovalRecord.create(
        action="promote",
        target_id=txn.txn_id,
        artifact_sha256=txn.candidate_manifest_sha256,
        policy_version="1.0.0",
        approver="recovery_test_reviewer",
        approved_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1),
        scope=("real_corpus",),
        policy_sha256=sha256_hex((project_root / "config" / "policies.yaml").read_bytes()),
    )
    approval_bytes = canonical_json_bytes(approval)
    approval_path = project_root / "ops" / "approvals" / "incoming" / "approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_bytes(approval_bytes)
    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(
            project_root,
            fail_at="after_current_swap",
            approval_verifier=_RecoveryApprovalVerifier(approval_bytes),
        ).promote_run(prepared.run_id, approval_path=approval_path)
    accepted = project_root / "ops" / "approvals" / "accepted" / f"{approval.approval_id}.json"
    accepted.unlink()

    with pytest.raises(IntegrityError, match="approval"):
        IngestPipeline(project_root).promote_run(prepared.run_id)


def test_rebase_bundle_crash_resumes_from_nonaborted_old_wal(project_root: Path) -> None:
    first = project_root / "inbox" / "rebase-crash-first.md"
    second = project_root / "inbox" / "rebase-crash-second.md"
    first.write_text("First old WAL candidate.\n", encoding="utf-8")
    second.write_text("Concurrent committed candidate.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match="after_promotion_wal"):
        IngestPipeline(project_root, fail_at="after_promotion_wal").ingest(
            first,
            fixture=True,
        )
    first_run = next((project_root / "ops" / "runs").iterdir()).name
    IngestPipeline(project_root).ingest(second, fixture=True)

    with pytest.raises(InjectedFault, match="after_staging_bundle_first_file"):
        IngestPipeline(
            project_root,
            fail_at="after_staging_bundle_first_file",
        ).promote_run(first_run, fixture=True)

    recovered = IngestPipeline(project_root).promote_run(first_run, fixture=True)
    assert recovered.status == "succeeded"
    index = (project_root / "knowledge" / "index.md").read_text()
    assert "First old WAL candidate" in index
    assert "Concurrent committed candidate" in index


def test_succeeded_operation_still_finishes_wal_and_manifest(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = project_root / "inbox" / "finish-wal.md"
    source.write_text("Finalize every durable gate.\n", encoding="utf-8")
    pipeline = IngestPipeline(project_root)
    original_advance = pipeline._advance_promotion_state

    def crash_before_completed(txn_id: str, target: PromotionState) -> None:
        if target is PromotionState.COMPLETED:
            raise InjectedFault("before_wal_completed")
        original_advance(txn_id, target)

    monkeypatch.setattr(pipeline, "_advance_promotion_state", crash_before_completed)
    with pytest.raises(InjectedFault, match="before_wal_completed"):
        pipeline.ingest(source, fixture=True)
    run_id = next((project_root / "ops" / "runs").iterdir()).name

    recovered = IngestPipeline(project_root).promote_run(run_id)

    assert recovered.status == "succeeded"
    manifest = json.loads((project_root / "ops" / "runs" / run_id / "manifest.json").read_text())
    assert manifest["state"] == "succeeded"


def test_identical_ingest_finishes_succeeded_operation_reconciliation(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = project_root / "inbox" / "identical-finish-wal.md"
    source.write_text("Identical retry must finish every durable gate.\n", encoding="utf-8")
    pipeline = IngestPipeline(project_root)
    original_advance = pipeline._advance_promotion_state

    def crash_before_completed(txn_id: str, target: PromotionState) -> None:
        if target is PromotionState.COMPLETED:
            raise InjectedFault("before_wal_completed")
        original_advance(txn_id, target)

    monkeypatch.setattr(pipeline, "_advance_promotion_state", crash_before_completed)
    with pytest.raises(InjectedFault, match="before_wal_completed"):
        pipeline.ingest(source, fixture=True)
    run_dir = next((project_root / "ops" / "runs").iterdir())
    before = json.loads((run_dir / "manifest.json").read_text())
    wal_before = pipeline.control.promotion_for_operation(before["operation_key"])
    assert wal_before is not None
    assert wal_before["state"] == PromotionState.RECONCILING.value
    assert before["state"] != "succeeded"

    recovered = IngestPipeline(project_root).ingest(source, fixture=True)

    after = json.loads((run_dir / "manifest.json").read_text())
    verifier = IngestPipeline(project_root)
    wal_after = verifier.control.promotion_for_operation(after["operation_key"])
    assert recovered.status == "succeeded"
    assert wal_after is not None
    assert wal_after["state"] == PromotionState.COMPLETED.value
    assert after["state"] == "succeeded"


def test_identical_ingest_finishes_semantic_noop_manifest_after_crash(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = project_root / "inbox" / "semantic-noop-manifest.md"
    source.write_text("Semantic no-op terminal state must converge.\n", encoding="utf-8")
    first_pipeline = IngestPipeline(project_root)
    first_pipeline.ingest(source, fixture=True)
    first_pipeline.control.close()
    for suffix in ("", "-shm", "-wal"):
        (project_root / "ops" / f"control.sqlite{suffix}").unlink(missing_ok=True)
    previous_runs = {path.name for path in (project_root / "ops" / "runs").iterdir()}

    crashing = IngestPipeline(project_root)
    original_update = crashing._update_run_manifest

    def crash_before_noop_manifest(
        run_id: str,
        *,
        state: str,
        **updates: object,
    ) -> None:
        if state == "succeeded" and updates.get("semantic_noop") is True:
            raise InjectedFault("before_semantic_noop_manifest")
        original_update(run_id, state=state, **updates)

    monkeypatch.setattr(crashing, "_update_run_manifest", crash_before_noop_manifest)
    with pytest.raises(InjectedFault, match="before_semantic_noop_manifest"):
        crashing.ingest(source, fixture=True)
    new_runs = [
        path for path in (project_root / "ops" / "runs").iterdir() if path.name not in previous_runs
    ]
    assert len(new_runs) == 1
    run_dir = new_runs[0]
    before = json.loads((run_dir / "manifest.json").read_text())
    operation = crashing.control.connection.execute(
        "SELECT state, result_json FROM operations WHERE operation_key = ?",
        (before["operation_key"],),
    ).fetchone()
    assert operation is not None
    assert operation["state"] == "succeeded"
    assert operation["result_json"] is not None
    assert crashing.control.promotion_for_operation(before["operation_key"]) is None
    assert before["state"] != "succeeded"

    recovered = IngestPipeline(project_root).ingest(source, fixture=True)

    after = json.loads((run_dir / "manifest.json").read_text())
    assert recovered.status == "succeeded"
    assert recovered.noop is True
    assert after["state"] == "succeeded"
    assert after["semantic_noop"] is True


def test_new_promotion_waits_for_active_parent_reconciliation(project_root: Path) -> None:
    first = project_root / "inbox" / "parent-needs-recovery.md"
    second = project_root / "inbox" / "child-must-wait.md"
    first.write_text("Committed parent awaiting views.\n", encoding="utf-8")
    second.write_text("Child must not overtake recovery.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match="after_current_swap"):
        IngestPipeline(project_root, fail_at="after_current_swap").ingest(
            first,
            fixture=True,
        )
    parent_generation = (project_root / "ledger" / "CURRENT").read_text().strip()
    parent_run = next((project_root / "ops" / "runs").iterdir()).name

    with pytest.raises(IntegrityError, match="incomplete reconciliation"):
        IngestPipeline(project_root).ingest(second, fixture=True)
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == parent_generation

    IngestPipeline(project_root).promote_run(parent_run)
    child = IngestPipeline(project_root).ingest(second, fixture=True)
    assert child.status == "succeeded"


@pytest.mark.parametrize(
    "checkpoint",
    ("after_materialization", "after_git_checkpoint", "before_succeeded"),
)
def test_materialized_parent_must_reach_terminal_state_before_child(
    project_root: Path,
    checkpoint: str,
) -> None:
    first = project_root / "inbox" / f"terminal-parent-{checkpoint}.md"
    second = project_root / "inbox" / f"terminal-child-{checkpoint}.md"
    first.write_text("Parent views alone are not terminal.\n", encoding="utf-8")
    second.write_text("Child waits for terminal WAL and run state.\n", encoding="utf-8")
    with pytest.raises(InjectedFault, match=checkpoint):
        IngestPipeline(project_root, fail_at=checkpoint).ingest(first, fixture=True)
    parent_run = next((project_root / "ops" / "runs").iterdir()).name

    with pytest.raises(IntegrityError, match=r"durable state|terminal convergence"):
        IngestPipeline(project_root).ingest(second, fixture=True)

    IngestPipeline(project_root).promote_run(parent_run)
    assert IngestPipeline(project_root).ingest(second, fixture=True).status == "succeeded"
