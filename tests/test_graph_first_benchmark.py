from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.graph_first import (
    BenchmarkQuestion,
    GraphFirstBenchmarkStale,
    load_questions,
    main,
    render_report,
    run_benchmark,
    score_retrieval,
)

from raytsystem.codegraph.projection import CodeGraphProjection


class _StepClock:
    def __init__(self, step: int = 100) -> None:
        self.value = 0
        self.step = step

    def __call__(self) -> int:
        current = self.value
        self.value += self.step
        return current


def _build_fixture_graph(root: Path) -> None:
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
        '[project]\nname = "graph-first-fixture"\ndependencies = []\n',
        encoding="utf-8",
    )
    (root / "src" / "storage.py").write_text(
        "def persist(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    (root / "src" / "service.py").write_text(
        "from storage import persist\n\ndef save(value: str) -> str:\n    return persist(value)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "from service import save\n\ndef test_save() -> None:\n    assert save('x') == 'x'\n",
        encoding="utf-8",
    )
    CodeGraphProjection(root).rebuild()


def _write_questions(root: Path) -> Path:
    path = root / "questions.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "questions": [
                    {
                        "id": "save_to_storage",
                        "question": "How does save call persist in storage?",
                        "required_sources": [
                            "src/service.py",
                            "src/storage.py",
                        ],
                        "required_terms": ["persist", "save"],
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _fixture(root: Path) -> Path:
    _build_fixture_graph(root)
    return _write_questions(root)


def test_report_is_deterministic_with_injected_clock(project_root: Path) -> None:
    questions_path = _fixture(project_root)
    questions = load_questions(project_root, questions_path)

    first = run_benchmark(project_root, questions, clock_ns=_StepClock())
    second = run_benchmark(project_root, questions, clock_ns=_StepClock())

    assert first == second
    assert render_report(first) == render_report(second)
    assert json.loads(render_report(first))["status"] == "ok"
    assert first["algorithms"]["network"] is False
    assert first["algorithms"]["llm"] is False
    assert first["algorithms"]["result_tuning"] is False
    assert first["summary"]["lexical"]["latency_ns"] == {
        "mean": "100.000000",
        "total": 100,
    }
    assert first["summary"]["graph"]["latency_ns"] == {
        "mean": "100.000000",
        "total": 100,
    }
    for method in ("lexical", "graph"):
        metrics = first["cases"][0]["methods"][method]
        assert metrics["context_bytes"] <= first["algorithms"]["shared_context_budget_bytes"]
        assert metrics["stale_failures"] == 0


def test_metric_math_uses_declared_sources_and_terms() -> None:
    question = BenchmarkQuestion(
        question_id="metric_math",
        question="Find alpha architecture",
        required_sources=("src/a.py", "src/b.py"),
        required_terms=("alpha", "beta"),
    )

    score = score_retrieval(
        question,
        ("src/a.py", "src/noise.py"),
        b"alpha evidence only",
    )

    assert score["factual_coverage"] == {
        "matched": 2,
        "declared": 4,
        "ratio": "0.500000",
        "matched_sources": ["src/a.py"],
        "matched_terms": ["alpha"],
    }
    assert score["source_reference_precision"] == {
        "matched": 1,
        "retrieved": 2,
        "ratio": "0.500000",
    }


def test_cli_is_read_only_without_output_and_writes_only_when_explicit(
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    questions_path = _fixture(project_root)
    before = {path.relative_to(project_root) for path in project_root.rglob("*")}

    assert (
        main(
            (
                "--root",
                str(project_root),
                "--questions",
                str(questions_path),
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "ok"
    assert captured.err == ""
    assert {path.relative_to(project_root) for path in project_root.rglob("*")} == before

    output = project_root / "reports" / "graph-first.json"
    assert (
        main(
            (
                "--root",
                str(project_root),
                "--questions",
                str(questions_path),
                "--output",
                str(output),
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert json.loads(output.read_bytes())["status"] == "ok"


def test_stale_graph_fails_closed_and_cli_does_not_write_output(
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    questions_path = _fixture(project_root)
    questions = load_questions(project_root, questions_path)
    (project_root / "src" / "service.py").write_text(
        "def changed() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    with pytest.raises(GraphFirstBenchmarkStale, match="not current"):
        run_benchmark(project_root, questions, clock_ns=_StepClock())

    output = project_root / "should-not-exist.json"
    assert (
        main(
            (
                "--root",
                str(project_root),
                "--questions",
                str(questions_path),
                "--output",
                str(output),
            )
        )
        == 2
    )
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert captured.out == ""
    assert error["error"] == "stale_graph"
    assert error["stale_failures"] == 1
    assert not output.exists()
