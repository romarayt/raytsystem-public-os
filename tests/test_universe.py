from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from raytsystem.catalog import CatalogService
from raytsystem.codegraph.projection import CodeGraphProjection
from raytsystem.contracts import TaskPriority, canonical_json_bytes
from raytsystem.corpus import ActiveCorpus
from raytsystem.ingestion import IngestPipeline
from raytsystem.readmodel import ReadModelError, load_run_summaries
from raytsystem.tasking import TaskService
from raytsystem.universe import UniverseService, graph_logical_sha256

NOW = datetime(2026, 7, 11, 14, tzinfo=UTC)


def _write_minimal_catalog(root: Path) -> None:
    (root / "config" / "runtime-adapters.yaml").write_text(
        """version: \"1.0.0\"
adapters:
  - adapter_id: adapter_disabled
    name: Catalog only
    version: \"1.0.0\"
    state: disabled
    isolation_mode: none
    reason: Execution is unavailable.
""",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Safe routing\n", encoding="utf-8")
    (root / "packs" / "core").mkdir(parents=True)
    (root / "packs" / "core" / "pack.yaml").write_text(
        """pack_id: pack_core
name: Core
version: \"1.0.0\"
description: Core test pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: []
skill_ids: []
context_paths: [AGENTS.md]
optional: false
""",
        encoding="utf-8",
    )


def _ingest_fixture(project_root: Path) -> object:
    source = project_root / "inbox" / "universe.md"
    source.write_text("# Verified universe\n\nEvery factual node has evidence.\n", encoding="utf-8")
    return IngestPipeline(project_root).ingest(source, fixture=True)


def test_universe_connects_evidence_knowledge_work_and_catalog(project_root: Path) -> None:
    _write_minimal_catalog(project_root)
    result = _ingest_fixture(project_root)
    task = TaskService(project_root).create_task(
        title="Inspect the evidence path",
        priority=TaskPriority.HIGH,
        actor="user:local",
        idempotency_key="universe-task",
        expected_generation_id=None,
        now=NOW,
    )

    first = UniverseService(project_root).snapshot()
    second = UniverseService(project_root).snapshot()
    claim_id = next(iter(ActiveCorpus.load(project_root).claims))
    node_ids = {node.node_id for node in first.nodes}
    edges = {(edge.source, edge.target, edge.kind) for edge in first.edges}

    assert first.verify_id()
    assert first == second
    assert graph_logical_sha256(first) == graph_logical_sha256(second)
    assert result.source_id in node_ids  # type: ignore[attr-defined]
    assert result.segment_id in node_ids  # type: ignore[attr-defined]
    assert claim_id in node_ids
    assert task.task.task_id in node_ids
    assert (result.source_id, result.segment_id, "contains") in edges  # type: ignore[attr-defined]
    assert (result.segment_id, claim_id, "supports") in edges  # type: ignore[attr-defined]
    assert "instruction_agents" in node_ids
    assert "adapter_disabled" in node_ids
    rendered = canonical_json_bytes(first)
    assert str(project_root).encode() not in rendered
    assert b"_raw/" not in rendered
    assert b"normalized/" not in rendered


def test_run_summary_omits_private_manifest_fields(project_root: Path) -> None:
    _write_minimal_catalog(project_root)
    result = _ingest_fixture(project_root)
    corpus = ActiveCorpus.load(project_root)

    summaries = load_run_summaries(project_root, corpus.run_manifests)
    payload = summaries[0].model_dump(mode="json")

    assert summaries[0].run_id == result.run_id  # type: ignore[attr-defined]
    assert set(payload) == {
        "schema_name",
        "schema_version",
        "id_scheme_version",
        "extensions",
        "run_id",
        "operation_type",
        "state",
        "generation_id",
        "semantic_noop",
        "created_at",
        "updated_at",
        "manifest_sha256",
    }
    assert "input_path" not in payload
    assert "raw_path" not in payload


def test_invalid_committed_run_state_fails_closed(project_root: Path) -> None:
    _write_minimal_catalog(project_root)
    result = _ingest_fixture(project_root)
    manifest_path = project_root / "ops" / "runs" / result.run_id / "manifest.json"  # type: ignore[attr-defined]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["state"] = "invented_success"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    corpus = ActiveCorpus.load(project_root)

    with pytest.raises(ReadModelError, match="state is invalid"):
        load_run_summaries(project_root, corpus.run_manifests)


def test_catalog_snapshot_can_be_injected_without_get_time_writes(project_root: Path) -> None:
    _write_minimal_catalog(project_root)
    _ingest_fixture(project_root)
    corpus = ActiveCorpus.load(project_root)
    catalog = CatalogService(project_root).load()
    tasks = TaskService(project_root).snapshot()
    before = {path.relative_to(project_root) for path in project_root.rglob("*")}

    graph = UniverseService(project_root).snapshot(
        corpus=corpus,
        catalog=catalog,
        tasks=tasks,
    )

    after = {path.relative_to(project_root) for path in project_root.rglob("*")}
    assert after - before == set()
    assert before - after <= {
        Path("ops/control.sqlite-shm"),
        Path("ops/control.sqlite-wal"),
    }
    assert graph.catalog_sha256 == catalog.catalog_sha256


def test_universe_includes_a_bounded_verified_code_plane(project_root: Path) -> None:
    _write_minimal_catalog(project_root)
    _ingest_fixture(project_root)
    source = project_root / "src" / "universe_service.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("class UniverseAdapter:\n    pass\n", encoding="utf-8")
    test_source = project_root / "tests" / "test_universe_adapter.py"
    test_source.parent.mkdir(exist_ok=True)
    test_source.write_text(
        "from universe_service import UniverseAdapter\n\n"
        "def test_adapter():\n"
        "    assert UniverseAdapter()\n",
        encoding="utf-8",
    )
    adr = project_root / "ops" / "decisions" / "ADR-test-code.md"
    adr.parent.mkdir(parents=True, exist_ok=True)
    adr.write_text(
        "# ADR test code\n\n[Implementation](../../src/universe_service.py)\n",
        encoding="utf-8",
    )
    projection = CodeGraphProjection(project_root)
    projection.rebuild()
    code, object_sha256 = projection.current_snapshot_with_sha256()

    graph = UniverseService(project_root).snapshot(
        code_snapshot=code,
        code_snapshot_sha256=object_sha256,
        code_graph_state="current",
    )
    code_nodes = [node for node in graph.nodes if node.ring == "code"]
    node_ids = {node.node_id for node in graph.nodes}

    assert graph.code_snapshot_id == code.snapshot_id
    assert graph.code_snapshot_sha256 == object_sha256
    assert graph.code_snapshot_fingerprint == code.logical_fingerprint
    assert graph.code_view_node_count == len(code_nodes)
    assert "code" in {lens.value for lens in graph.supported_lenses}
    assert any(node.kind == "class" for node in code_nodes)
    assert any(edge.kind == "verifies" for edge in graph.edges)
    assert any(edge.kind == "explains" for edge in graph.edges)
    assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)
    rendered = canonical_json_bytes(graph)
    assert str(project_root).encode() not in rendered
    assert b"class UniverseAdapter" not in rendered
