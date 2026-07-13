from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.codegraph.benchmark import (
    CodeGraphBenchmarkCase,
    run_code_graph_benchmark,
)
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable


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
        '[project]\nname = "fixture"\ndependencies = []\n',
        encoding="utf-8",
    )


def test_code_graph_benchmark_is_bounded_reproducible_and_source_accurate(
    project_root: Path,
) -> None:
    _configure(project_root)
    (project_root / "src" / "universe.py").write_text(
        "class UniverseService:\n    def snapshot(self):\n        return True\n",
        encoding="utf-8",
    )
    CodeGraphProjection(project_root).rebuild()
    cases = (
        CodeGraphBenchmarkCase(
            "universe-service",
            "How does UniverseService build a snapshot?",
            ("src/universe.py",),
        ),
    )

    first = run_code_graph_benchmark(project_root, cases)
    second = run_code_graph_benchmark(project_root, cases)

    assert first.snapshot_id == second.snapshot_id
    assert first.case_count == 1
    assert first.max_graph_files_read == 0
    assert first.graph_reference_accuracy_ppm == 1_000_000
    assert first.cases[0].graph_context_bytes <= 24_000
    assert first.cases[0].question_sha256 == second.cases[0].question_sha256


def test_code_graph_benchmark_rejects_stale_snapshot(project_root: Path) -> None:
    _configure(project_root)
    source = project_root / "src" / "service.py"
    source.write_text("def service(): return 1\n", encoding="utf-8")
    CodeGraphProjection(project_root).rebuild()
    source.write_text("def service(): return 2\n", encoding="utf-8")

    with pytest.raises(CodeGraphUnavailable, match="current snapshot"):
        run_code_graph_benchmark(
            project_root,
            (
                CodeGraphBenchmarkCase(
                    "service-change",
                    "Where is service defined?",
                    ("src/service.py",),
                ),
            ),
        )
