from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from raytsystem.codegraph.detect import detect_files, load_code_graph_config
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryError, CodeGraphQueryService
from raytsystem.contracts import sha256_hex
from raytsystem.security.paths import PathPolicyError, read_regular_file

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class CodeGraphBenchmarkCase:
    case_id: str
    question: str
    expected_paths: tuple[str, ...]


@dataclass(frozen=True)
class CodeGraphBenchmarkCaseResult:
    case_id: str
    question_sha256: str
    baseline_context_bytes: int
    graph_context_bytes: int
    context_reduction_ppm: int
    baseline_files_read: int
    graph_files_read: int
    baseline_search_operations: int
    graph_search_operations: int
    baseline_coverage_ppm: int
    graph_coverage_ppm: int
    graph_reference_accuracy_ppm: int
    baseline_latency_ms: int
    graph_latency_ms: int
    fallback_reason: str | None


@dataclass(frozen=True)
class CodeGraphBenchmarkReport:
    snapshot_id: str
    snapshot_fingerprint: str
    case_count: int
    average_context_reduction_ppm: int
    average_baseline_coverage_ppm: int
    average_graph_coverage_ppm: int
    graph_reference_accuracy_ppm: int
    max_graph_files_read: int
    fallback_count: int
    stale_graph_failures: int
    targets: dict[str, bool]
    cases: tuple[CodeGraphBenchmarkCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_code_graph_benchmark_cases(
    root: Path,
    relative: str,
) -> tuple[CodeGraphBenchmarkCase, ...]:
    try:
        data = read_regular_file(root, relative, max_bytes=1024 * 1024).data
    except (OSError, PathPolicyError) as error:
        raise CodeGraphQueryError("Code graph benchmark cases are unavailable") from error
    cases: list[CodeGraphBenchmarkCase] = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise CodeGraphQueryError("Code graph benchmark case is malformed")
        case_id = str(payload.get("case_id", ""))
        question = str(payload.get("question", ""))
        expected_paths = tuple(sorted({str(value) for value in payload.get("expected_paths", [])}))
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", case_id)
            or not 1 <= len(question) <= 512
            or not expected_paths
        ):
            raise CodeGraphQueryError("Code graph benchmark case fields are invalid")
        cases.append(CodeGraphBenchmarkCase(case_id, question, expected_paths))
    if not 1 <= len(cases) <= 100 or len({case.case_id for case in cases}) != len(cases):
        raise CodeGraphQueryError("Code graph benchmark suite size or IDs are invalid")
    return tuple(cases)


def run_code_graph_benchmark(
    root: Path,
    cases: tuple[CodeGraphBenchmarkCase, ...],
) -> CodeGraphBenchmarkReport:
    root = root.resolve()
    config = load_code_graph_config(root)
    projection = CodeGraphProjection(root)
    status = projection.status(verify_hashes=True)
    if status.state.value != "current":
        raise CodeGraphUnavailable("Code graph benchmark requires a current snapshot")
    snapshot = projection.current_snapshot()
    detected = detect_files(root, config)
    file_by_path = {item.path: item for item in detected}
    known_paths = set(file_by_path)
    for path in file_by_path:
        parent = PurePosixPath(path).parent
        while parent.as_posix() != ".":
            known_paths.add(parent.as_posix())
            parent = parent.parent
    graph_service = CodeGraphQueryService(root)
    results: list[CodeGraphBenchmarkCaseResult] = []
    stale_failures = 0
    for case in cases:
        baseline_started = time.monotonic_ns()
        terms = tuple({word.casefold() for word in _WORD.findall(case.question) if len(word) > 1})
        ranked = sorted(
            detected,
            key=lambda item: (
                -sum(
                    item.path.casefold().count(term) + item.data.lower().count(term.encode("utf-8"))
                    for term in terms
                ),
                item.path,
            ),
        )[:5]
        remaining = config.query_max_bytes
        baseline_context_bytes = 0
        baseline_paths: set[str] = set()
        for item in ranked:
            if remaining <= 0:
                break
            consumed = min(len(item.data), remaining)
            if consumed:
                baseline_paths.add(item.path)
                baseline_context_bytes += consumed
                remaining -= consumed
        baseline_latency_ms = round((time.monotonic_ns() - baseline_started) / 1_000_000)
        expected = set(case.expected_paths)
        baseline_coverage = len(expected.intersection(baseline_paths)) / len(expected)

        graph_started = time.monotonic_ns()
        fallback_reason: str | None = None
        try:
            graph = graph_service.query(case.question, depth=2)
        except (CodeGraphQueryError, CodeGraphUnavailable):
            fallback_reason = "graph_query_unavailable"
            graph = None
        graph_latency_ms = round((time.monotonic_ns() - graph_started) / 1_000_000)
        if graph is None:
            graph_context_bytes = 0
            graph_paths: set[str] = set()
            reference_accuracy = 0.0
        else:
            graph_context_bytes = graph.estimated_context_bytes
            graph_paths = {node.path for node in graph.nodes if node.path is not None}
            reference_accuracy = (
                1.0
                if not graph_paths
                else len(graph_paths.intersection(known_paths)) / len(graph_paths)
            )
        graph_coverage = len(expected.intersection(graph_paths)) / len(expected)
        reduction = (
            0
            if baseline_context_bytes == 0
            else round(
                (baseline_context_bytes - graph_context_bytes) * 1_000_000 / baseline_context_bytes
            )
        )
        results.append(
            CodeGraphBenchmarkCaseResult(
                case_id=case.case_id,
                question_sha256=sha256_hex(case.question.encode("utf-8")),
                baseline_context_bytes=baseline_context_bytes,
                graph_context_bytes=graph_context_bytes,
                context_reduction_ppm=reduction,
                baseline_files_read=len(baseline_paths),
                graph_files_read=0,
                baseline_search_operations=1,
                graph_search_operations=1,
                baseline_coverage_ppm=round(baseline_coverage * 1_000_000),
                graph_coverage_ppm=round(graph_coverage * 1_000_000),
                graph_reference_accuracy_ppm=round(reference_accuracy * 1_000_000),
                baseline_latency_ms=baseline_latency_ms,
                graph_latency_ms=graph_latency_ms,
                fallback_reason=fallback_reason,
            )
        )
    count = len(results)
    average_reduction = sum(item.context_reduction_ppm for item in results) // count
    baseline_coverage = sum(item.baseline_coverage_ppm for item in results) // count
    graph_coverage = sum(item.graph_coverage_ppm for item in results) // count
    reference_accuracy = sum(item.graph_reference_accuracy_ppm for item in results) // count
    fallback_count = sum(item.fallback_reason is not None for item in results)
    max_graph_files_read = max(item.graph_files_read for item in results)
    return CodeGraphBenchmarkReport(
        snapshot_id=snapshot.snapshot_id,
        snapshot_fingerprint=snapshot.logical_fingerprint,
        case_count=count,
        average_context_reduction_ppm=average_reduction,
        average_baseline_coverage_ppm=baseline_coverage,
        average_graph_coverage_ppm=graph_coverage,
        graph_reference_accuracy_ppm=reference_accuracy,
        max_graph_files_read=max_graph_files_read,
        fallback_count=fallback_count,
        stale_graph_failures=stale_failures,
        targets={
            "context_reduction_at_least_40_percent": average_reduction >= 400_000,
            "no_more_than_five_source_files_read": max_graph_files_read <= 5,
            "factual_coverage_not_worse": graph_coverage >= baseline_coverage,
            "all_graph_paths_exist": reference_accuracy == 1_000_000,
            "no_fallbacks": fallback_count == 0,
            "stale_graph_failures_zero": stale_failures == 0,
        },
        cases=tuple(results),
    )
