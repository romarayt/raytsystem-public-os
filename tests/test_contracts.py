from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from raytsystem.contracts import (
    SCHEMA_MODELS,
    SCHEMA_VERSION,
    ApprovalRecord,
    Claim,
    ClaimStatus,
    Lease,
    LedgerGeneration,
    Normalization,
    PromotionState,
    PromotionTxn,
    SourceRevision,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
PUBLISHED_REGISTRY_SHA256 = {
    "1.0.0": "e189883553fffbab9687e0b90fb6b3b382f3d583bce0c38cb199d3605218d13f",
    "1.1.0": "f3e83b85a596b0ab963966e07231edd5ce5c37b9c158527462508ca10a5334dc",
    "1.2.0": "4fb242b805379ac32d816cb773b90ad99b456a8595486c213c081dab9f0029a0",
    "1.3.0": "69ea8063ed47cf7b8ece3d7e8fb4bbb8c1735ce8ad6d8209c333a959c582ba85",
    "1.4.0": "797d5eefd3094c8e0282488cc8b147dda0e017497c28e9637b9e0f03495274ef",
}


def test_canonical_serialization_is_stable() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert derive_id("obj", left) == derive_id("obj", right)


def test_source_revision_id_binds_exact_content() -> None:
    revision = SourceRevision.create(
        source_id="src_example",
        content_sha256=HASH_A,
        raw_path="_raw/blobs/sha256/aa/" + HASH_A,
        retrieved_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert revision.source_revision_id.startswith("srev_")
    assert revision.content_sha256 == HASH_A


def test_invalid_sha_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceRevision.create(
            source_id="src_example",
            content_sha256="not-a-sha",
            raw_path="_raw/blobs/invalid",
            retrieved_at=datetime(2026, 7, 10, tzinfo=UTC),
        )


def test_normalization_id_binds_parser_and_config() -> None:
    first = Normalization.create(
        source_revision_id="srev_example",
        adapter="native_text",
        parser_version="1.0.0",
        config_sha256=HASH_A,
        document_sha256=HASH_B,
        created_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    second = Normalization.create(
        source_revision_id="srev_example",
        adapter="native_text",
        parser_version="1.0.1",
        config_sha256=HASH_A,
        document_sha256=HASH_B,
        created_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert first.normalization_id != second.normalization_id


def test_supported_claim_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Claim(
            claim_id="clm_example",
            statement="A supported fact",
            status=ClaimStatus.SUPPORTED,
            evidence_ids=[],
            recorded_at=datetime(2026, 7, 10, tzinfo=UTC),
        )


def test_approval_is_bound_to_exact_action_and_hash() -> None:
    approval = ApprovalRecord.create(
        action="promote_real_corpus",
        target_id="run_example",
        artifact_sha256=HASH_A,
        policy_version="1.0.0",
        approver="user:roman",
        approved_at=datetime(2026, 7, 10, tzinfo=UTC),
        expires_at=datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert approval.approval_id.startswith("apr_")
    assert approval.is_valid_for(
        action="promote_real_corpus",
        target_id="run_example",
        artifact_sha256=HASH_A,
        at=datetime(2026, 7, 10, 1, tzinfo=UTC),
    )
    assert not approval.is_valid_for(
        action="promote_real_corpus",
        target_id="run_example",
        artifact_sha256=HASH_B,
        at=datetime(2026, 7, 10, 1, tzinfo=UTC),
    )


def test_lease_rejects_expired_or_wrong_fence() -> None:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    lease = Lease(
        lease_id="lease_example",
        partition_key="ledger:current",
        owner_id="run_a",
        fencing_token=7,
        acquired_at=now,
        expires_at=now + timedelta(seconds=30),
    )

    assert lease.allows(owner_id="run_a", fencing_token=7, at=now)
    assert not lease.allows(owner_id="run_b", fencing_token=7, at=now)
    assert not lease.allows(owner_id="run_a", fencing_token=6, at=now)
    assert not lease.allows(owner_id="run_a", fencing_token=7, at=now + timedelta(seconds=31))


def test_promotion_state_transitions_are_strict() -> None:
    txn = PromotionTxn(
        txn_id="ptx_example",
        run_id="run_example",
        operation_key="op_example",
        parent_generation_id="genesis",
        next_generation_id="gen_next",
        event_id="evt_example",
        partition_fencing_token=2,
        global_fencing_token=3,
        output_hashes={"generation": HASH_A},
        state=PromotionState.PREPARED,
        created_at=datetime(2026, 7, 10, tzinfo=UTC),
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    committing = txn.transition(PromotionState.COMMITTING)
    committed = committing.transition(PromotionState.COMMITTED)
    assert committed.state is PromotionState.COMMITTED

    with pytest.raises(ValueError, match="Illegal promotion transition"):
        txn.transition(PromotionState.COMMITTED)


def test_every_registered_contract_has_an_exported_schema() -> None:
    root = Path(__file__).parents[1]
    schema_dir = root / "config" / "schemas" / f"v{SCHEMA_VERSION}"
    registry = json.loads((schema_dir / "registry.json").read_text(encoding="utf-8"))

    expected = {model.__name__ for model in SCHEMA_MODELS}
    assert set(registry["entries"]) == expected
    assert all((schema_dir / entry["path"]).is_file() for entry in registry["entries"].values())


def test_published_schema_registries_remain_complete_and_immutable() -> None:
    root = Path(__file__).parents[1]
    for version, expected_registry_sha256 in PUBLISHED_REGISTRY_SHA256.items():
        schema_dir = root / "config" / "schemas" / f"v{version}"
        registry = json.loads((schema_dir / "registry.json").read_text(encoding="utf-8"))
        assert registry["schema_version"] == version
        assert registry["registry_sha256"] == expected_registry_sha256
        assert (
            sha256_hex(
                canonical_json_bytes({"schema_version": version, "entries": registry["entries"]})
            )
            == expected_registry_sha256
        )
        for entry in registry["entries"].values():
            schema = json.loads((schema_dir / entry["path"]).read_text(encoding="utf-8"))
            compact = json.dumps(
                schema,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            assert sha256_hex(compact) == entry["sha256"]


def test_every_generation_keeps_a_resolvable_immutable_schema_registry() -> None:
    root = Path(__file__).parents[1]
    registry_hashes = {
        json.loads(path.read_text(encoding="utf-8"))["registry_sha256"]
        for path in (root / "config" / "schemas").glob("v*/registry.json")
    }
    generation_paths = sorted((root / "ledger" / "generations").glob("*.json"))
    assert "genesis" in {path.stem for path in generation_paths}
    for path in generation_paths:
        generation = LedgerGeneration.model_validate_json(path.read_text(encoding="utf-8"))
        assert generation.verify_id()
        assert generation.schema_registry_sha256 in registry_hashes
