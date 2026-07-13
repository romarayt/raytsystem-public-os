from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from raytsystem.codegraph.contracts import CodeGraphSnapshot, CodeGraphState
from raytsystem.codegraph.detect import CodeGraphConfig, load_code_graph_config
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryError, CodeGraphQueryService
from raytsystem.codegraph.security import (
    CodeGraphSecurityError,
    safe_code_read_result,
    validate_code_path,
)
from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.io import UnsafeWritePath, write_bytes_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file

BENCHMARK_VERSION = "1.0.0"
_QUESTION_SET_VERSION = "1.0.0"
_MAX_QUESTIONS_BYTES = 1024 * 1024
_MAX_LINE_CHARS = 512
_QUESTION_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_LEXICAL_WORD = re.compile(r"[A-Za-z0-9_]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "before",
        "by",
        "does",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "when",
        "where",
        "which",
        "with",
    }
)


class GraphFirstBenchmarkError(RuntimeError):
    """Base error for invalid or unavailable graph-first benchmark inputs."""


class GraphFirstBenchmarkInputError(GraphFirstBenchmarkError):
    """Raised when the labeled question set is malformed or out of scope."""


class GraphFirstBenchmarkStale(GraphFirstBenchmarkError):
    """Raised when the benchmark cannot bind one verified current graph."""


@dataclass(frozen=True)
class BenchmarkQuestion:
    question_id: str
    question: str
    required_sources: tuple[str, ...]
    required_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.question_id,
            "question": self.question,
            "required_sources": list(self.required_sources),
            "required_terms": list(self.required_terms),
        }


@dataclass(frozen=True)
class BenchmarkQuestionSet:
    source_path: str
    source_sha256: str
    questions: tuple[BenchmarkQuestion, ...]


@dataclass(frozen=True)
class Retrieval:
    context: bytes
    source_references: tuple[str, ...]
    search_operations: int
    fallback: bool


@dataclass(frozen=True)
class _LexicalFileHit:
    path: str
    matched_terms: int
    occurrences: int
    lines: tuple[dict[str, Any], ...]


def load_questions(root: Path, path: Path) -> BenchmarkQuestionSet:
    root = root.resolve()
    relative = _root_relative(root, path)
    try:
        data = read_regular_file(root, relative, max_bytes=_MAX_QUESTIONS_BYTES).data
        payload = json.loads(data)
    except (OSError, PathPolicyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GraphFirstBenchmarkInputError(
            "Benchmark questions are unavailable or invalid JSON"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "questions"}:
        raise GraphFirstBenchmarkInputError("Benchmark question set has an invalid shape")
    if payload["schema_version"] != _QUESTION_SET_VERSION:
        raise GraphFirstBenchmarkInputError("Benchmark question set version is unsupported")
    raw_questions = payload["questions"]
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 100:
        raise GraphFirstBenchmarkInputError("Benchmark requires 1..100 labeled questions")
    questions = tuple(sorted((_parse_question(item) for item in raw_questions), key=_question_key))
    identifiers = [item.question_id for item in questions]
    if len(identifiers) != len(set(identifiers)):
        raise GraphFirstBenchmarkInputError("Benchmark question IDs must be unique")
    return BenchmarkQuestionSet(
        source_path=relative,
        source_sha256=sha256_hex(data),
        questions=questions,
    )


def _root_relative(root: Path, path: Path) -> str:
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as error:
        raise GraphFirstBenchmarkInputError(
            "Benchmark questions must stay inside the workspace"
        ) from error
    if not relative:
        raise GraphFirstBenchmarkInputError("Benchmark questions must name a file")
    return relative


def _question_key(question: BenchmarkQuestion) -> str:
    return question.question_id


def _parse_question(payload: object) -> BenchmarkQuestion:
    required_keys = {"id", "question", "required_sources", "required_terms"}
    if not isinstance(payload, dict) or set(payload) != required_keys:
        raise GraphFirstBenchmarkInputError("Benchmark question has an invalid shape")
    identifier = payload["id"]
    question = payload["question"]
    raw_sources = payload["required_sources"]
    raw_terms = payload["required_terms"]
    if not isinstance(identifier, str) or _QUESTION_ID.fullmatch(identifier) is None:
        raise GraphFirstBenchmarkInputError("Benchmark question ID is invalid")
    if (
        not isinstance(question, str)
        or not 1 <= len(question) <= 512
        or question != question.strip()
        or "\x00" in question
        or not lexical_tokens(question)
    ):
        raise GraphFirstBenchmarkInputError("Benchmark question text is invalid")
    if (
        not isinstance(raw_sources, list)
        or not raw_sources
        or not all(isinstance(item, str) for item in raw_sources)
        or len(raw_sources) != len(set(raw_sources))
    ):
        raise GraphFirstBenchmarkInputError("Required source references are invalid")
    try:
        sources = tuple(sorted(validate_code_path(item) for item in raw_sources))
    except CodeGraphSecurityError as error:
        raise GraphFirstBenchmarkInputError("Required source reference is unsafe") from error
    if (
        not isinstance(raw_terms, list)
        or not raw_terms
        or not all(isinstance(item, str) for item in raw_terms)
        or any(
            not 1 <= len(item) <= 128 or item != item.strip() or "\x00" in item
            for item in raw_terms
        )
        or len({item.casefold() for item in raw_terms}) != len(raw_terms)
    ):
        raise GraphFirstBenchmarkInputError("Required factual terms are invalid")
    return BenchmarkQuestion(
        question_id=identifier,
        question=question,
        required_sources=sources,
        required_terms=tuple(sorted(raw_terms, key=lambda item: item.casefold())),
    )


def lexical_tokens(question: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                token.casefold()
                for token in _LEXICAL_WORD.findall(question)
                if len(token) >= 2 and token.casefold() not in _STOP_WORDS
            }
        )
    )


def lexical_search(
    root: Path,
    snapshot: CodeGraphSnapshot,
    config: CodeGraphConfig,
    question: str,
) -> Retrieval:
    """Run the fixed lexical baseline without consulting benchmark labels."""

    tokens = lexical_tokens(question)
    corpus: dict[str, str] = {}
    for entry in snapshot.files:
        try:
            result = safe_code_read_result(root, entry.path, max_bytes=config.max_file_bytes)
        except CodeGraphSecurityError as error:
            raise GraphFirstBenchmarkStale(
                "Code graph source changed or became unsafe during lexical search"
            ) from error
        if sha256_hex(result.data) != entry.content_sha256:
            raise GraphFirstBenchmarkStale("Code graph source hash changed during lexical search")
        corpus[entry.path] = result.data.decode("utf-8", errors="replace")

    hits: list[_LexicalFileHit] = []
    for path, text in sorted(corpus.items()):
        folded = text.casefold()
        matched = tuple(token for token in tokens if token in folded)
        if not matched:
            continue
        occurrences = sum(folded.count(token) for token in matched)
        lines: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            folded_line = line.casefold()
            line_matches = tuple(token for token in matched if token in folded_line)
            if not line_matches:
                continue
            clipped = line[:_MAX_LINE_CHARS]
            lines.append(
                {
                    "line": line_number,
                    "text": clipped,
                    "line_truncated": len(clipped) != len(line),
                }
            )
        hits.append(
            _LexicalFileHit(
                path=path,
                matched_terms=len(matched),
                occurrences=occurrences,
                lines=tuple(lines),
            )
        )
    hits.sort(key=lambda item: (-item.matched_terms, -item.occurrences, item.path))
    context, sources = _bounded_lexical_context(hits, max_bytes=config.query_max_bytes)
    return Retrieval(
        context=context,
        source_references=sources,
        search_operations=len(tokens),
        fallback=not sources,
    )


def _bounded_lexical_context(
    hits: Sequence[_LexicalFileHit],
    *,
    max_bytes: int,
) -> tuple[bytes, tuple[str, ...]]:
    files: list[dict[str, Any]] = []
    truncated = False
    for hit in hits:
        accepted: list[dict[str, Any]] = []
        for line in hit.lines:
            candidate_files = [
                *files,
                {"path": hit.path, "lines": [*accepted, line]},
            ]
            candidate = {
                "backend": "deterministic_lexical_file_search_v1",
                "files": candidate_files,
                "truncated": False,
            }
            if len(canonical_json_bytes(candidate)) <= max_bytes:
                accepted.append(line)
            else:
                truncated = True
        if accepted:
            files.append({"path": hit.path, "lines": accepted})
        elif hit.lines:
            truncated = True
    payload = {
        "backend": "deterministic_lexical_file_search_v1",
        "files": files,
        "truncated": truncated,
    }
    context = canonical_json_bytes(payload)
    if len(context) > max_bytes:
        raise GraphFirstBenchmarkError("Lexical context exceeded the shared byte cap")
    return context, tuple(item["path"] for item in files)


def score_retrieval(
    question: BenchmarkQuestion,
    source_references: Sequence[str],
    context: bytes,
) -> dict[str, Any]:
    sources = tuple(sorted(set(source_references)))
    required_sources = set(question.required_sources)
    matched_sources = tuple(sorted(required_sources.intersection(sources)))
    folded_context = context.decode("utf-8", errors="replace").casefold()
    matched_terms = tuple(
        term for term in question.required_terms if term.casefold() in folded_context
    )
    matched_facts = len(matched_sources) + len(matched_terms)
    declared_facts = len(question.required_sources) + len(question.required_terms)
    precision_denominator = len(sources)
    precision = (
        Fraction(len(matched_sources), precision_denominator)
        if precision_denominator
        else Fraction(0)
    )
    return {
        "factual_coverage": {
            "matched": matched_facts,
            "declared": declared_facts,
            "ratio": _fraction_string(Fraction(matched_facts, declared_facts)),
            "matched_sources": list(matched_sources),
            "matched_terms": list(matched_terms),
        },
        "source_reference_precision": {
            "matched": len(matched_sources),
            "retrieved": precision_denominator,
            "ratio": _fraction_string(precision),
        },
    }


def run_benchmark(
    root: Path,
    questions: BenchmarkQuestionSet,
    *,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    root = root.resolve()
    projection = CodeGraphProjection(root)
    status = projection.status(verify_hashes=True)
    if status.state is not CodeGraphState.CURRENT:
        raise GraphFirstBenchmarkStale(
            f"Code graph is not current: {status.reason or status.state.value}"
        )
    try:
        snapshot, snapshot_sha256 = projection.current_snapshot_with_sha256()
        config = load_code_graph_config(root)
    except (CodeGraphUnavailable, CodeGraphSecurityError) as error:
        raise GraphFirstBenchmarkStale("Current code graph cannot be verified") from error
    if (
        status.snapshot_id != snapshot.snapshot_id
        or status.snapshot_fingerprint != snapshot.logical_fingerprint
    ):
        raise GraphFirstBenchmarkStale("Code graph changed before benchmark execution")
    snapshot_paths = {entry.path for entry in snapshot.files}
    missing_ground_truth = sorted(
        {
            source
            for question in questions.questions
            for source in question.required_sources
            if source not in snapshot_paths
        }
    )
    if missing_ground_truth:
        raise GraphFirstBenchmarkInputError(
            "Required source references are outside the current graph snapshot"
        )

    graph_service = CodeGraphQueryService(root)
    cases: list[dict[str, Any]] = []
    for question in questions.questions:
        lexical_started = clock_ns()
        lexical = lexical_search(root, snapshot, config, question.question)
        lexical_latency = _elapsed(lexical_started, clock_ns())
        try:
            graph_started = clock_ns()
            graph_result = graph_service.query(question.question, depth=2)
            graph_latency = _elapsed(graph_started, clock_ns())
        except CodeGraphUnavailable as error:
            raise GraphFirstBenchmarkStale(
                "Code graph became stale during benchmark execution"
            ) from error
        except CodeGraphQueryError as error:
            raise GraphFirstBenchmarkError("Graph query failed for a benchmark case") from error
        if (
            graph_result.snapshot_id != snapshot.snapshot_id
            or graph_result.snapshot_fingerprint != snapshot.logical_fingerprint
        ):
            raise GraphFirstBenchmarkStale("Graph query result does not match the bound snapshot")
        graph_context = canonical_json_bytes(graph_result)
        if len(graph_context) > config.query_max_bytes:
            raise GraphFirstBenchmarkError("Graph context exceeded the shared byte cap")
        snapshot_edge_files = {edge.edge_id: edge.source_file for edge in snapshot.edges}
        graph_sources = tuple(
            sorted(
                {
                    *(
                        node.path
                        for node in graph_result.nodes
                        if node.path is not None and node.path in snapshot_paths
                    ),
                    *(
                        source_file
                        for source_file in (
                            snapshot_edge_files.get(edge.edge_id) for edge in graph_result.edges
                        )
                        if source_file is not None and source_file in snapshot_paths
                    ),
                }
            )
        )
        graph = Retrieval(
            context=graph_context,
            source_references=graph_sources,
            search_operations=1,
            fallback=graph_result.fallback_reason is not None or not graph_sources,
        )
        cases.append(
            {
                "question": question.to_dict(),
                "methods": {
                    "lexical": _method_report(question, lexical, lexical_latency),
                    "graph": _method_report(question, graph, graph_latency),
                },
            }
        )

    final_status = projection.status(verify_hashes=True)
    if (
        final_status.state is not CodeGraphState.CURRENT
        or final_status.snapshot_id != snapshot.snapshot_id
        or final_status.snapshot_fingerprint != snapshot.logical_fingerprint
    ):
        raise GraphFirstBenchmarkStale("Code graph changed during benchmark execution")
    return {
        "schema_name": "GraphFirstBenchmarkReportV1",
        "schema_version": "1.0.0",
        "benchmark_version": BENCHMARK_VERSION,
        "status": "ok",
        "question_set": {
            "path": questions.source_path,
            "sha256": questions.source_sha256,
            "case_count": len(questions.questions),
        },
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_fingerprint": snapshot.logical_fingerprint,
            "snapshot_sha256": snapshot_sha256,
        },
        "algorithms": {
            "lexical": "deterministic_lexical_file_search_v1",
            "graph": f"CodeGraphQueryService/{graph_service.version}",
            "shared_context_budget_bytes": config.query_max_bytes,
            "graph_depth": 2,
            "result_tuning": False,
            "network": False,
            "llm": False,
        },
        "metric_definitions": {
            "context_bytes": "Canonical UTF-8 retrieval-context bytes.",
            "unique_files": "Distinct current-snapshot source files referenced by context.",
            "search_operations": (
                "Lexical normalized-token searches; one graph query call for graph-first."
            ),
            "factual_coverage": (
                "Matched required source references plus matched required terms, divided by "
                "all declared source references and terms."
            ),
            "source_reference_precision": (
                "Required source references retrieved, divided by all unique source "
                "references retrieved."
            ),
            "latency_ns": "One monotonic-clock observation per method and question.",
            "fallback_rate": "Cases with no source references or an explicit fallback reason.",
            "stale_failures": "Graph-staleness failures; any nonzero run fails closed.",
        },
        "cases": cases,
        "summary": {
            "lexical": _aggregate(cases, "lexical"),
            "graph": _aggregate(cases, "graph"),
        },
    }


def _elapsed(started: int, finished: int) -> int:
    if finished < started:
        raise GraphFirstBenchmarkError("Benchmark clock moved backwards")
    return finished - started


def _method_report(
    question: BenchmarkQuestion,
    retrieval: Retrieval,
    latency_ns: int,
) -> dict[str, Any]:
    score = score_retrieval(
        question,
        retrieval.source_references,
        retrieval.context,
    )
    return {
        "context_bytes": len(retrieval.context),
        "context_sha256": sha256_hex(retrieval.context),
        "unique_files": len(retrieval.source_references),
        "source_references": list(retrieval.source_references),
        "search_operations": retrieval.search_operations,
        **score,
        "latency_ns": latency_ns,
        "fallback": retrieval.fallback,
        "stale_failures": 0,
    }


def _aggregate(cases: Sequence[dict[str, Any]], method: str) -> dict[str, Any]:
    metrics = [item["methods"][method] for item in cases]
    count = len(metrics)
    context_total = sum(int(item["context_bytes"]) for item in metrics)
    files_total = sum(int(item["unique_files"]) for item in metrics)
    operations_total = sum(int(item["search_operations"]) for item in metrics)
    latency_total = sum(int(item["latency_ns"]) for item in metrics)
    fallback_count = sum(bool(item["fallback"]) for item in metrics)
    stale_failures = sum(int(item["stale_failures"]) for item in metrics)
    coverage = (
        sum(
            (
                Fraction(
                    int(item["factual_coverage"]["matched"]),
                    int(item["factual_coverage"]["declared"]),
                )
                for item in metrics
            ),
            start=Fraction(0),
        )
        / count
    )
    precision = (
        sum(
            (
                Fraction(
                    int(item["source_reference_precision"]["matched"]),
                    int(item["source_reference_precision"]["retrieved"]),
                )
                if int(item["source_reference_precision"]["retrieved"])
                else Fraction(0)
                for item in metrics
            ),
            start=Fraction(0),
        )
        / count
    )
    return {
        "case_count": count,
        "context_bytes": {
            "total": context_total,
            "mean": _fraction_string(Fraction(context_total, count)),
        },
        "unique_files": {
            "total": files_total,
            "mean": _fraction_string(Fraction(files_total, count)),
        },
        "search_operations": {
            "total": operations_total,
            "mean": _fraction_string(Fraction(operations_total, count)),
        },
        "factual_coverage_macro": _fraction_string(coverage),
        "source_reference_precision_macro": _fraction_string(precision),
        "latency_ns": {
            "total": latency_total,
            "mean": _fraction_string(Fraction(latency_total, count)),
        },
        "fallback_rate": {
            "fallbacks": fallback_count,
            "cases": count,
            "ratio": _fraction_string(Fraction(fallback_count, count)),
        },
        "stale_failures": stale_failures,
    }


def _fraction_string(value: Fraction) -> str:
    if value < 0:
        raise ValueError("Benchmark ratios cannot be negative")
    scale = 1_000_000
    scaled = (value.numerator * scale * 2 + value.denominator) // (value.denominator * 2)
    whole, fraction = divmod(scaled, scale)
    return f"{whole}.{fraction:06d}"


def render_report(report: dict[str, Any]) -> bytes:
    return canonical_json_bytes(report) + b"\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare deterministic lexical retrieval with graph-first code context."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="Workspace-local question JSON; defaults to benchmarks/graph_first_questions.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write canonical JSON only when this option is explicitly provided.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    questions_path = (
        Path(args.questions)
        if args.questions is not None
        else Path("benchmarks/graph_first_questions.json")
    )
    try:
        questions = load_questions(root, questions_path)
        report = run_benchmark(root, questions)
        data = render_report(report)
        if args.output is None:
            sys.stdout.buffer.write(data)
        else:
            output = Path(args.output)
            output = output if output.is_absolute() else root / output
            write_bytes_atomic(output, data)
    except GraphFirstBenchmarkStale as error:
        sys.stderr.buffer.write(
            render_report(
                {
                    "schema_name": "GraphFirstBenchmarkErrorV1",
                    "schema_version": "1.0.0",
                    "status": "error",
                    "error": "stale_graph",
                    "message": str(error),
                    "stale_failures": 1,
                }
            )
        )
        return 2
    except (
        GraphFirstBenchmarkError,
        UnsafeWritePath,
        OSError,
    ) as error:
        sys.stderr.buffer.write(
            render_report(
                {
                    "schema_name": "GraphFirstBenchmarkErrorV1",
                    "schema_version": "1.0.0",
                    "status": "error",
                    "error": "benchmark_failed",
                    "message": str(error),
                    "stale_failures": 0,
                }
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
