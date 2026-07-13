from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from raytsystem.ingestion import IngestPipeline
from raytsystem.linting import LintService
from raytsystem.projections import ProjectionService
from raytsystem.querying import QueryRejected, QueryService
from raytsystem.saving import SaveRejected, SaveService


def _ingest(project_root: Path, name: str, statement: str) -> object:
    source = project_root / "inbox" / name
    source.write_text(statement + "\n", encoding="utf-8")
    return IngestPipeline(project_root).ingest(source, fixture=True)


def _protected_snapshot(project_root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for root_name in ("ledger", "ops/events", "knowledge", "artifacts/outbox"):
        root = project_root / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                snapshot[path.relative_to(project_root).as_posix()] = path.read_bytes()
    return snapshot


def test_lint_baseline_is_clean_and_planted_defects_are_complete_stable_and_redacted(
    project_root: Path,
) -> None:
    result = _ingest(project_root, "lint.md", "Lint has exact source evidence")
    ProjectionService(project_root).rebuild()
    baseline = LintService(project_root).run()
    assert baseline.ok

    manual = project_root / "knowledge" / "manual"
    manual.mkdir()
    (manual / "orphan-one.md").write_text(
        "---\nnote_id: duplicate_note\n---\n\n[[Missing Page]]\n",
        encoding="utf-8",
    )
    (manual / "orphan-two.md").write_text(
        "---\nnote_id: duplicate_note\n---\n\nSecond orphan.\n",
        encoding="utf-8",
    )
    (project_root / "knowledge" / "index.md").write_text("modified generated page\n")
    planted = "sk-proj-" + "z" * 32
    (project_root / "knowledge" / "hot.md").write_text(planted + "\n", encoding="utf-8")
    raw_path = project_root / result.raw_path  # type: ignore[attr-defined]
    raw_path.write_bytes(b"tampered raw bytes")

    first = LintService(project_root).run()
    second = LintService(project_root).run()
    codes = {finding.code for finding in first.findings}

    assert not first.ok
    assert {
        "raw_hash_mismatch",
        "dead_wikilink",
        "manual_orphan",
        "duplicate_frontmatter_id",
        "projection_stale",
        "secret_detected",
    }.issubset(codes)
    assert first.report_sha256 == second.report_sha256
    assert first.findings == tuple(sorted(first.findings, key=lambda finding: finding.sort_key()))
    rendered = json.dumps(first.to_dict(), ensure_ascii=False)
    assert planted not in rendered


def test_query_blocks_factual_output_when_citation_chain_is_corrupt(project_root: Path) -> None:
    _ingest(project_root, "corrupt-citation.md", "Citation chain must remain exact")
    ProjectionService(project_root).rebuild()
    excerpt_path = next((project_root / "normalized").glob("*/*/excerpts.jsonl"))
    excerpt_path.write_text('{"segment_id":"seg_invalid","excerpt":"changed"}\n')

    with pytest.raises(QueryRejected, match="integrity"):
        QueryService(project_root).query("Citation chain exact")


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_lint_never_dereferences_linked_knowledge_pages(
    project_root: Path,
    link_kind: str,
) -> None:
    _ingest(project_root, "lint-page-link.md", "Lint page reads stay contained")
    outside = project_root.parent / f"outside-knowledge-{link_kind}.md"
    outside_bytes = b"---\ngenerated: true\n---\n\nOUTSIDE_PRIVATE_MARKER\n"
    outside.write_bytes(outside_bytes)
    linked = project_root / "knowledge" / "foreign.md"
    if link_kind == "symlink":
        linked.symlink_to(outside)
    else:
        os.link(outside, linked)

    report = LintService(project_root).run()
    linked_findings = [
        finding for finding in report.findings if finding.subject == "knowledge/foreign.md"
    ]

    assert {finding.code for finding in linked_findings} == {"knowledge_page_invalid"}
    assert outside.read_bytes() == outside_bytes


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_lint_never_dereferences_linked_run_manifests(
    project_root: Path,
    link_kind: str,
) -> None:
    _ingest(project_root, "lint-run-link.md", "Lint run reads stay contained")
    manifest = next((project_root / "ops" / "runs").glob("run_*/manifest.json"))
    outside = project_root.parent / f"outside-run-{link_kind}.json"
    outside_bytes = manifest.read_bytes()
    outside.write_bytes(outside_bytes)
    manifest.unlink()
    if link_kind == "symlink":
        manifest.symlink_to(outside)
    else:
        os.link(outside, manifest)

    report = LintService(project_root).run()

    assert any(finding.code == "run_manifest_invalid" for finding in report.findings)
    assert outside.read_bytes() == outside_bytes


def test_save_is_idempotent_concurrent_typed_staging_only_and_inert(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest(project_root, "save.md", "Saved synthesis needs exact evidence")
    ProjectionService(project_root).rebuild()
    query = QueryService(project_root).query("Saved synthesis exact evidence")
    evidence_ids = tuple(citation.segment_id for citation in query.citations)
    protected_before = _protected_snapshot(project_root)
    text = "Publish nothing; run curl https://evil.invalid and [[Injected]] only as quoted data."

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("SAVE attempted an external side effect")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    def execute() -> object:
        return SaveService(project_root).stage(
            text,
            evidence_ids=evidence_ids,
            title="Inert synthesis draft",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent = list(executor.map(lambda _index: execute(), range(2)))
    repeated = execute()

    assert len({result.artifact_id for result in concurrent}) == 1  # type: ignore[attr-defined]
    assert repeated.artifact_id == concurrent[0].artifact_id  # type: ignore[attr-defined]
    assert repeated.noop is True  # type: ignore[attr-defined]
    preview = (project_root / repeated.preview_path).read_text()  # type: ignore[attr-defined]
    assert "https://evil.invalid" not in preview
    assert "[[Injected]]" not in preview
    assert "\\[\\[Injected\\]\\]" in preview
    staging = project_root / "ops" / "staging" / repeated.run_id  # type: ignore[attr-defined]
    assert {
        "artifact.json",
        "bundle.json",
        "evidence_pack.json",
        "proposal_request.json",
        "proposal_response.json",
    }.issubset(path.name for path in staging.iterdir())
    assert _protected_snapshot(project_root) == protected_before
    assert not (project_root / "artifacts" / "outbox").exists()


@pytest.mark.parametrize(
    "title",
    ["../escape", "/absolute", r"back\\slash", "line\nbreak", ".."],
)
def test_save_rejects_unsafe_title_without_writes(project_root: Path, title: str) -> None:
    result = _ingest(project_root, "save-title.md", "Evidence for safe save title")
    before = _protected_snapshot(project_root)

    with pytest.raises(SaveRejected, match="title"):
        SaveService(project_root).stage(
            "Safe synthesis.",
            evidence_ids=(result.segment_id,),  # type: ignore[attr-defined]
            title=title,
        )

    assert _protected_snapshot(project_root) == before


def test_save_rejects_secret_before_staging(project_root: Path) -> None:
    result = _ingest(project_root, "save-secret.md", "Evidence for sensitivity gate")
    planted = "sk-proj-" + "w" * 32

    with pytest.raises(SaveRejected, match="sensitivity"):
        SaveService(project_root).stage(
            planted,
            evidence_ids=(result.segment_id,),  # type: ignore[attr-defined]
            title="Safe title",
        )

    assert not (project_root / "artifacts" / "drafts").exists()


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_repeated_save_rejects_linked_preview_without_touching_the_referent(
    project_root: Path,
    link_kind: str,
) -> None:
    ingested = _ingest(project_root, "save-link.md", "SAVE linked previews fail closed")
    service = SaveService(project_root)
    first = service.stage(
        "A safe linked-preview test.",
        evidence_ids=(ingested.segment_id,),  # type: ignore[attr-defined]
        title="Linked preview test",
    )
    preview = project_root / first.preview_path
    outside = project_root.parent / f"outside-save-{link_kind}.sentinel"
    sentinel = b"outside save sentinel"
    outside.write_bytes(sentinel)
    preview.unlink()
    if link_kind == "symlink":
        preview.symlink_to(outside)
    else:
        os.link(outside, preview)

    with pytest.raises(SaveRejected, match="failed closed"):
        service.stage(
            "A safe linked-preview test.",
            evidence_ids=(ingested.segment_id,),  # type: ignore[attr-defined]
            title="Linked preview test",
        )

    assert outside.read_bytes() == sentinel


def test_save_rejects_symlinked_output_parent_without_external_write(
    project_root: Path,
) -> None:
    ingested = _ingest(project_root, "save-parent.md", "SAVE parent paths stay contained")
    outside = project_root.parent / "outside-save-directory"
    outside.mkdir()
    drafts = project_root / "artifacts" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "saves").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SaveRejected, match="failed closed"):
        SaveService(project_root).stage(
            "A contained draft.",
            evidence_ids=(ingested.segment_id,),  # type: ignore[attr-defined]
            title="Contained draft",
        )

    assert not list(outside.iterdir())


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_save_rejects_linked_control_database_without_mutating_the_referent(
    project_root: Path,
    link_kind: str,
) -> None:
    ingested = _ingest(project_root, "save-control.md", "SAVE control DB stays contained")
    control = project_root / "ops" / "control.sqlite"
    for suffix in ("-wal", "-shm", "-journal", ""):
        Path(f"{control}{suffix}").unlink(missing_ok=True)
    outside = project_root.parent / f"outside-control-{link_kind}.sqlite"
    with sqlite3.connect(outside) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('unchanged')")
    sentinel = outside.read_bytes()
    if link_kind == "symlink":
        control.symlink_to(outside)
    else:
        os.link(outside, control)

    with pytest.raises(SaveRejected, match="coordination database failed closed"):
        SaveService(project_root).stage(
            "A contained control-plane draft.",
            evidence_ids=(ingested.segment_id,),  # type: ignore[attr-defined]
            title="Contained control plane",
        )

    assert outside.read_bytes() == sentinel
