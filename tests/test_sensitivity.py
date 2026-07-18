from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from raytsystem.contracts import ProposalItem, derive_id
from raytsystem.ingestion import IngestPipeline, QuarantinedInput
from raytsystem.security.sensitivity import SensitivityDecision


def test_secret_like_input_is_quarantined_before_normalization(project_root: Path) -> None:
    source = project_root / "inbox" / "secret.md"
    planted = "ghp_" + "a" * 36
    source.write_text(f"# Private\n\nToken: {planted}\n", encoding="utf-8")

    with pytest.raises(QuarantinedInput, match="classified restricted"):
        IngestPipeline(project_root).ingest(source, fixture=True)

    restricted_files = list((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert len(restricted_files) == 1
    assert planted.encode() in restricted_files[0].read_bytes()
    assert not (project_root / "normalized").exists()
    assert not (project_root / "ops" / "staging").exists()
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == "genesis"

    for path in project_root.rglob("*"):
        if not path.is_file() or "inbox" in path.parts or "restricted" in path.parts:
            continue
        assert planted.encode() not in path.read_bytes()


def test_secret_like_workspace_path_is_quarantined_before_metadata(project_root: Path) -> None:
    planted = "sk-proj-" + "p" * 32
    source = project_root / "inbox" / f"{planted}.md"
    raw = b"Benign bytes with a secret-shaped filename only.\n"
    source.write_bytes(raw)

    with pytest.raises(QuarantinedInput, match="classified restricted"):
        IngestPipeline(project_root).ingest(source, fixture=True)

    restricted = next((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert restricted.read_bytes() == raw
    assert not (project_root / "normalized").exists()
    assert not (project_root / "ops" / "runs").exists()
    assert not (project_root / "_raw" / "sources").exists()
    for path in project_root.rglob("*"):
        if not path.is_file() or "inbox" in path.parts or "restricted" in path.parts:
            continue
        assert planted.encode() not in path.read_bytes()


class _FailingScanner:
    name = "failing_test_scanner"
    version = "1"

    def scan(self, _data: bytes, *, path: str | None = None) -> SensitivityDecision:
        del path
        raise RuntimeError("scanner unavailable")


def test_scanner_failure_quarantines_fail_closed(project_root: Path) -> None:
    source = project_root / "inbox" / "unknown.md"
    source.write_text("# Not yet classified\n", encoding="utf-8")

    with pytest.raises(QuarantinedInput, match="scanner failed closed"):
        IngestPipeline(project_root, scanner=_FailingScanner()).ingest(source, fixture=True)

    assert list((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert not (project_root / "normalized").exists()
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == "genesis"


@pytest.mark.parametrize(
    "planted",
    [
        "postgresql://user:" + "p" * 16 + "@localhost/db",
        "sk-" + "proj-" + "o" * 32,
        "sk-" + "ant-" + "a" * 32,
        "github_" + "pat_" + "g" * 32,
        "Bearer " + "b" * 32,
        "TOKEN=" + "t" * 32,
    ],
)
def test_common_secret_shapes_are_quarantined(project_root: Path, planted: str) -> None:
    source = project_root / "inbox" / "candidate.md"
    source.write_text(planted + "\n", encoding="utf-8")

    with pytest.raises(QuarantinedInput):
        IngestPipeline(project_root).ingest(source, fixture=True)

    restricted = next((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert planted.encode() in restricted.read_bytes()
    if os.name != "nt":  # Windows has ACLs instead of POSIX mode bits
        assert restricted.stat().st_mode & 0o777 == 0o600
        assert restricted.parent.stat().st_mode & 0o777 == 0o700
    assert not (project_root / "normalized").exists()


class _UnsafeDecisionScanner:
    name = "unsafe_test_scanner"
    version = "1"

    def scan(self, _data: bytes, *, path: str | None = None) -> SensitivityDecision:
        del path
        return SensitivityDecision(
            sensitivity="secret",
            disposition="allow",
            reason_codes=(),
            scanner_name=self.name,
            scanner_version=self.version,
        )


class _AllowAllScanner:
    name = "allow_all_test_scanner"
    version = "0"

    def scan(self, _data: bytes, *, path: str | None = None) -> SensitivityDecision:
        del path
        return SensitivityDecision(
            sensitivity="internal",
            disposition="allow",
            reason_codes=(),
            scanner_name=self.name,
            scanner_version=self.version,
        )


def test_unsafe_scanner_decision_fails_closed(project_root: Path) -> None:
    source = project_root / "inbox" / "unsafe-decision.md"
    source.write_text("Unclassified bytes.\n", encoding="utf-8")

    with pytest.raises(QuarantinedInput, match="scanner failed closed"):
        IngestPipeline(project_root, scanner=_UnsafeDecisionScanner()).ingest(source, fixture=True)

    assert not (project_root / "normalized").exists()


def test_secret_revealed_by_json_unescape_is_quarantined(project_root: Path) -> None:
    source = project_root / "inbox" / "escaped.json"
    raw = ('{"\\u0054OKEN":"' + "q" * 32 + '"}\n').encode()
    assert b"TOKEN" not in raw
    source.write_bytes(raw)

    with pytest.raises(QuarantinedInput, match="Extracted content"):
        IngestPipeline(project_root).ingest(source, fixture=True)

    restricted = next((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert restricted.read_bytes() == raw
    assert not (project_root / "normalized").exists()


def test_secret_revealed_by_imported_proposal_unescape_is_quarantined(
    project_root: Path,
) -> None:
    source = project_root / "inbox" / "proposal-evidence.md"
    source.write_text("Safe evidence for a model-neutral proposal.\n", encoding="utf-8")
    pipeline = IngestPipeline(project_root)
    prepared = pipeline.ingest(source, fixture=True, prepare_only=True)
    exported = pipeline.export_proposal(prepared.run_id)
    response_path = project_root / exported["proposal_response.json"]
    payload = json.loads(response_path.read_text())
    planted = "sk-proj-" + "s" * 32
    payload["proposed_items"][0]["payload"]["statement"] = planted
    item = ProposalItem.model_validate(payload["proposed_items"][0])
    payload["proposal_response_id"] = derive_id(
        "pres",
        {
            "request_sha256": payload["request_ref"]["object_sha256"],
            "items": [item],
        },
    )
    raw = json.dumps(payload).replace("sk-proj-", r"\u0073k-proj-").encode()
    assert planted.encode() not in raw
    imported_path = project_root / "inbox" / "escaped-proposal-response.json"
    imported_path.write_bytes(raw)

    with pytest.raises(QuarantinedInput, match="Decoded proposal content"):
        pipeline.import_proposal(prepared.run_id, imported_path)

    restricted = next((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert restricted.read_bytes() == raw
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == "genesis"
    for path in project_root.rglob("*"):
        if not path.is_file() or "inbox" in path.parts or "restricted" in path.parts:
            continue
        assert planted.encode() not in path.read_bytes()


def test_promotion_rescans_claim_accepted_by_older_scanner(project_root: Path) -> None:
    source = project_root / "inbox" / "scanner-upgrade-evidence.md"
    source.write_text("Safe evidence before a scanner upgrade.\n", encoding="utf-8")
    old_pipeline = IngestPipeline(project_root, scanner=_AllowAllScanner())
    prepared = old_pipeline.ingest(source, fixture=True, prepare_only=True)
    exported = old_pipeline.export_proposal(prepared.run_id)
    response_path = project_root / exported["proposal_response.json"]
    payload = json.loads(response_path.read_text())
    planted = "sk-proj-" + "u" * 32
    payload["proposed_items"][0]["payload"]["statement"] = planted
    item = ProposalItem.model_validate(payload["proposed_items"][0])
    payload["proposal_response_id"] = derive_id(
        "pres",
        {
            "request_sha256": payload["request_ref"]["object_sha256"],
            "items": [item],
        },
    )
    imported_path = project_root / "inbox" / "older-scanner-response.json"
    imported_path.write_text(json.dumps(payload), encoding="utf-8")
    old_pipeline.import_proposal(prepared.run_id, imported_path)

    with pytest.raises(QuarantinedInput, match="Canonical claim candidate"):
        IngestPipeline(project_root).promote_run(prepared.run_id, fixture=True)

    restricted = next((project_root / "_raw" / "restricted").glob("*/raw.bin"))
    assert planted.encode() in restricted.read_bytes()
    assert (project_root / "ledger" / "CURRENT").read_text().strip() == "genesis"
    assert not list((project_root / "ledger" / "objects").glob("*/*.json"))
