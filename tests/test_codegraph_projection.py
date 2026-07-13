from __future__ import annotations

import json
from pathlib import Path

import pytest

from raytsystem.codegraph.contracts import CodeGraphManifest, CodeGraphState
from raytsystem.codegraph.projection import (
    CodeGraphBuildInterrupted,
    CodeGraphProjection,
    CodeGraphUnavailable,
)
from raytsystem.contracts import canonical_json_bytes, sha256_hex


def _configure(root: Path) -> None:
    config = root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + """

[code_graph]
path = ".raytsystem/graph"
roots = ["src", "tests"]
files = ["pyproject.toml"]
max_files = 100
max_file_bytes = 1048576
max_total_bytes = 8388608
max_nodes = 5000
max_edges = 20000
universe_max_nodes = 1000
universe_max_edges = 5000
query_max_nodes = 40
query_max_edges = 100
query_max_bytes = 24000
parser_timeout_seconds = 10
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\ndependencies = ["pydantic>=2"]\n',
        encoding="utf-8",
    )


def test_rebuild_update_noop_and_canonical_knowledge_is_untouched(project_root: Path) -> None:
    _configure(project_root)
    (project_root / "src" / "a.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    current_before = (project_root / "ledger" / "CURRENT").read_bytes()
    generation_before = (project_root / "ledger" / "generations" / "genesis.json").read_bytes()

    first = CodeGraphProjection(project_root).rebuild()
    second = CodeGraphProjection(project_root).update()

    assert not first.no_op
    assert second.no_op
    assert second.snapshot_id == first.snapshot_id
    assert second.snapshot_fingerprint == first.snapshot_fingerprint
    assert second.processed_files == 0
    assert second.cache_hits == 2
    assert CodeGraphProjection(project_root).status().state is CodeGraphState.CURRENT
    assert (
        CodeGraphProjection(project_root).status(verify_hashes=False).state
        is CodeGraphState.UNCHECKED
    )
    assert (project_root / "ledger" / "CURRENT").read_bytes() == current_before
    assert (
        project_root / "ledger" / "generations" / "genesis.json"
    ).read_bytes() == generation_before


def test_incremental_update_handles_change_delete_and_rename(project_root: Path) -> None:
    _configure(project_root)
    source = project_root / "src" / "a.py"
    stable = project_root / "src" / "stable.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    stable.write_text("def stable():\n    return 1\n", encoding="utf-8")
    CodeGraphProjection(project_root).rebuild()

    source.rename(project_root / "src" / "renamed.py")
    stable.write_text("def stable():\n    return 2\n", encoding="utf-8")
    status = CodeGraphProjection(project_root).status()
    updated = CodeGraphProjection(project_root).update()
    snapshot = CodeGraphProjection(project_root).current_snapshot()

    assert status.state is CodeGraphState.STALE
    assert status.changed_files == 2
    assert status.deleted_files == 1
    assert updated.deleted_files == 1
    assert updated.processed_files == 2
    assert {entry.path for entry in snapshot.files} == {
        "pyproject.toml",
        "src/renamed.py",
        "src/stable.py",
    }


def test_pointer_recovery_after_interruption_is_observably_atomic(project_root: Path) -> None:
    _configure(project_root)
    source = project_root / "src" / "a.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    baseline = CodeGraphProjection(project_root).rebuild()
    source.write_text("def alpha():\n    return 2\n", encoding="utf-8")

    with pytest.raises(CodeGraphBuildInterrupted, match="after_pointer"):
        CodeGraphProjection(project_root, fail_at="after_pointer").update()

    committed = CodeGraphProjection(project_root).current_snapshot()
    resumed = CodeGraphProjection(project_root).update()

    assert committed.snapshot_id != baseline.snapshot_id
    assert resumed.no_op
    assert CodeGraphProjection(project_root).status().state is CodeGraphState.CURRENT
    wal = json.loads((project_root / ".raytsystem" / "graph" / "WAL.json").read_text())
    assert wal["state"] == "committed"


def test_interruption_before_pointer_keeps_old_snapshot_and_resumes(project_root: Path) -> None:
    _configure(project_root)
    source = project_root / "src" / "a.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    baseline = CodeGraphProjection(project_root).rebuild()
    source.write_text("def alpha():\n    return 2\n", encoding="utf-8")

    with pytest.raises(CodeGraphBuildInterrupted, match="before_pointer"):
        CodeGraphProjection(project_root, fail_at="before_pointer").update()

    assert CodeGraphProjection(project_root).current_snapshot().snapshot_id == baseline.snapshot_id
    assert CodeGraphProjection(project_root).status().state is CodeGraphState.STALE
    resumed = CodeGraphProjection(project_root).update()
    assert resumed.snapshot_id != baseline.snapshot_id
    assert CodeGraphProjection(project_root).status().state is CodeGraphState.CURRENT


def test_forged_snapshot_is_rejected(project_root: Path) -> None:
    _configure(project_root)
    (project_root / "src" / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
    result = CodeGraphProjection(project_root).rebuild()
    snapshot_path = (
        project_root / ".raytsystem" / "graph" / "snapshots" / f"{result.snapshot_sha256}.json"
    )
    original = snapshot_path.read_bytes()
    snapshot_path.write_bytes(original + b" ")

    status = CodeGraphProjection(project_root).status()

    assert sha256_hex(snapshot_path.read_bytes()) != result.snapshot_sha256
    assert status.state is CodeGraphState.ERROR
    assert status.reason == "integrity_failed"


def test_rehashed_manifest_that_does_not_bind_snapshot_is_rejected(project_root: Path) -> None:
    _configure(project_root)
    (project_root / "src" / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
    projection = CodeGraphProjection(project_root)
    result = projection.rebuild()
    snapshot = projection.current_snapshot()
    forged = CodeGraphManifest.create(
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=result.snapshot_sha256,
        config_fingerprint=snapshot.config_fingerprint,
        extractor_fingerprint=snapshot.extractor_fingerprint,
        files=snapshot.files[:-1],
        created_at=snapshot.created_at,
    )
    manifests = project_root / ".raytsystem" / "graph" / "manifests"
    (manifests / f"{forged.manifest_id}.json").write_bytes(canonical_json_bytes(forged))
    pointer_path = project_root / ".raytsystem" / "graph" / "CURRENT"
    pointer = json.loads(pointer_path.read_bytes())
    pointer["manifest_id"] = forged.manifest_id
    pointer_path.write_bytes(canonical_json_bytes(pointer) + b"\n")

    status = projection.status()

    assert status.state is CodeGraphState.ERROR
    assert status.reason == "integrity_failed"


def test_forged_cache_is_discarded_and_reextracted(project_root: Path) -> None:
    _configure(project_root)
    (project_root / "src" / "a.py").write_text("def alpha(): pass\n", encoding="utf-8")
    projection = CodeGraphProjection(project_root)
    projection.rebuild()
    cache_path = next((project_root / ".raytsystem" / "graph" / "cache").glob("*.json"))
    payload = json.loads(cache_path.read_bytes())
    token = "ghp_" + "b" * 36
    payload["nodes"][0]["metadata"]["forged"] = token
    cache_path.write_bytes(canonical_json_bytes(payload))

    snapshot_before = projection.current_snapshot()
    with pytest.raises(CodeGraphUnavailable, match="failed closed"):
        projection.update()
    rendered = canonical_json_bytes(projection.current_snapshot())

    assert projection.current_snapshot().snapshot_id == snapshot_before.snapshot_id
    assert token.encode() not in rendered


def test_code_graph_can_be_disabled_without_touching_canonical_state(project_root: Path) -> None:
    _configure(project_root)
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[features]\ncode_graph_enabled = false\ngraph_first_query_enabled = false\n",
        encoding="utf-8",
    )
    canonical_before = (project_root / "ledger" / "CURRENT").read_bytes()

    status = CodeGraphProjection(project_root).status()

    assert status.state is CodeGraphState.MISSING
    assert status.reason == "disabled"
    with pytest.raises(CodeGraphUnavailable, match="disabled"):
        CodeGraphProjection(project_root).rebuild()
    assert (project_root / "ledger" / "CURRENT").read_bytes() == canonical_before
