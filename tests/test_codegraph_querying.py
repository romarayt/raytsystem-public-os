from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.codegraph.contracts import CodeRelation
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryService


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


def _build_query_graph(root: Path) -> None:
    _configure(root)
    (root / "src" / "storage.py").write_text(
        "def persist():\n    return True\n",
        encoding="utf-8",
    )
    (root / "src" / "service.py").write_text(
        "from storage import persist\n\ndef save():\n    return persist()\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "from service import save\n\ndef test_save():\n    assert save()\n",
        encoding="utf-8",
    )
    CodeGraphProjection(root).rebuild()


def test_graph_query_is_bounded_and_reports_snapshot_and_locations(project_root: Path) -> None:
    _build_query_graph(project_root)

    result = CodeGraphQueryService(project_root).query("save service", depth=2)

    assert result.snapshot_id.startswith("cgraph_")
    assert result.snapshot_fingerprint
    assert result.estimated_context_bytes <= 24_000
    assert len(result.nodes) <= 40
    assert len(result.edges) <= 100
    assert all(node.path is None or not node.path.startswith("/") for node in result.nodes)
    assert any(node.location is not None for node in result.nodes)


def test_shortest_path_and_impact_are_closed(project_root: Path) -> None:
    _build_query_graph(project_root)
    service = CodeGraphQueryService(project_root)

    path = service.path("test_save", "persist")
    impact = service.impact("src/storage.py")

    assert path.operation == "path"
    assert path.seed_node_ids
    assert path.ordered_node_ids[0] == path.seed_node_ids[0]
    assert path.ordered_node_ids[-1] == path.seed_node_ids[-1]
    depth_by_id = {node.node_id: node.depth for node in path.nodes}
    assert depth_by_id[path.ordered_node_ids[-1]] == len(path.ordered_node_ids) - 1
    assert all(edge.source in {node.node_id for node in path.nodes} for edge in path.edges)
    assert impact.operation == "impact"
    assert any(node.path == "src/service.py" for node in impact.nodes)
    assert any(edge.relation in {CodeRelation.CALLS, CodeRelation.IMPORTS} for edge in impact.edges)


def test_stale_graph_is_rejected_before_query(project_root: Path) -> None:
    _build_query_graph(project_root)
    (project_root / "src" / "service.py").write_text(
        "def changed():\n    return False\n",
        encoding="utf-8",
    )

    with pytest.raises(CodeGraphUnavailable, match="not current"):
        CodeGraphQueryService(project_root).query("service")
