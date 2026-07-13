from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import Literal

from raytsystem.codegraph.contracts import (
    CodeEdge,
    CodeGraphQueryEdge,
    CodeGraphQueryNode,
    CodeGraphSnapshot,
    CodeGraphState,
    CodeNode,
    CodeNodeKind,
    CodeRelation,
    EdgeConfidence,
    GraphQueryResult,
)
from raytsystem.codegraph.detect import load_code_graph_config
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision


class CodeGraphQueryError(RuntimeError):
    """A graph query was unsafe, stale, ambiguous or outside its hard budget."""


_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_GENERIC_TRANSLATIONS: dict[str, tuple[str, ...]] = {
    "архитектура": ("architecture", "module", "service"),
    "безопасность": ("security", "policy", "guard"),
    "граф": ("graph", "universe"),
    "зависимости": ("dependency", "depends", "import"),
    "зависимость": ("dependency", "depends", "import"),
    "задачи": ("task", "tasking"),
    "запрос": ("query", "search"),
    "код": ("code", "module"),
    "обновление": ("update", "projection"),
    "поиск": ("search", "query"),
    "проекция": ("projection", "snapshot"),
    "хранилище": ("storage", "ledger"),
}
_IMPACT_RELATIONS = frozenset(
    {
        CodeRelation.CALLS,
        CodeRelation.CONFIGURED_BY,
        CodeRelation.DEPENDS_ON,
        CodeRelation.IMPLEMENTS,
        CodeRelation.IMPORTS,
        CodeRelation.INHERITS,
        CodeRelation.REFERENCES,
        CodeRelation.TESTS,
        CodeRelation.VERIFIES,
    }
)


def _tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for word in _WORDS.findall(value):
        pieces = _CAMEL.findall(word) or [word]
        for piece in pieces:
            normalized = piece.casefold()
            if len(normalized) >= 2:
                tokens.append(normalized)
                tokens.extend(_GENERIC_TRANSLATIONS.get(normalized, ()))
    return tuple(dict.fromkeys(tokens))


def _node_terms(node: CodeNode) -> tuple[str, ...]:
    values = [node.label, node.qualified_name, node.kind.value]
    if node.path is not None:
        values.append(node.path)
    values.extend(node.metadata.values())
    return _tokens(" ".join(values))


class CodeGraphQueryService:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = Path(root).resolve()
        self.scanner = scanner or SecretScanner()
        self.config = load_code_graph_config(self.root)

    def query(self, question: str, *, depth: int = 2) -> GraphQueryResult:
        snapshot = self._snapshot(question, depth=depth)
        scores = self._relevance_scores(snapshot, question)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        seeds = tuple(node_id for node_id, _ in ranked[:3])
        if not seeds:
            seeds = tuple(
                node.node_id
                for node in sorted(
                    snapshot.nodes,
                    key=lambda item: (
                        not item.is_god,
                        not item.is_bridge,
                        item.kind in {CodeNodeKind.FILE, CodeNodeKind.DIRECTORY},
                        item.label.casefold(),
                        item.node_id,
                    ),
                )[:3]
            )
        # Relevance-first traversal roots: strong direct matches join the reported
        # seeds so a high-signal answer node outside the seed neighborhood is not lost.
        top_score = ranked[0][1] if ranked else 0
        traversal_roots = tuple(
            dict.fromkeys(
                (
                    *seeds,
                    *(node_id for node_id, score in ranked[:10] if score * 4 >= top_score),
                )
            )
        )
        return self._traverse(
            snapshot,
            operation="query",
            query=question,
            seeds=seeds,
            depth=depth,
            direction="both",
            scores=scores,
            traversal_roots=traversal_roots,
        )

    def explain(self, node: str, *, depth: int = 1) -> GraphQueryResult:
        snapshot = self._snapshot(node, depth=depth)
        seed = self._resolve_node(snapshot, node)
        return self._traverse(
            snapshot,
            operation="explain",
            query=node,
            seeds=(seed,),
            depth=depth,
            direction="both",
        )

    def neighbors(
        self,
        node: str,
        *,
        depth: int = 1,
        direction: Literal["both", "out", "in"] = "both",
    ) -> GraphQueryResult:
        snapshot = self._snapshot(node, depth=depth)
        seed = self._resolve_node(snapshot, node)
        return self._traverse(
            snapshot,
            operation="neighbors",
            query=f"{node}:{direction}",
            seeds=(seed,),
            depth=depth,
            direction=direction,
        )

    def path(self, source: str, target: str) -> GraphQueryResult:
        self._validate_text(source)
        self._validate_text(target)
        snapshot = self._current_snapshot()
        source_id = self._resolve_node(snapshot, source)
        target_id = self._resolve_node(snapshot, target)
        adjacency = self._adjacency(snapshot.edges, direction="both")
        queue: deque[str] = deque([source_id])
        parent: dict[str, str | None] = {source_id: None}
        while queue and target_id not in parent:
            current = queue.popleft()
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in parent:
                    parent[neighbor] = current
                    queue.append(neighbor)
        if target_id not in parent:
            raise CodeGraphQueryError("No path exists between the selected code nodes")
        path: list[str] = []
        cursor: str | None = target_id
        while cursor is not None:
            path.append(cursor)
            cursor = parent[cursor]
        path.reverse()
        selected = set(path)
        return self._result(
            snapshot,
            operation="path",
            query=f"{source}\n{target}",
            selected=selected,
            seeds=(source_id, target_id),
            preferred_edges=self._path_edges(snapshot.edges, path),
            ordered_node_ids=tuple(path),
            node_depths={node_id: depth for depth, node_id in enumerate(path)},
        )

    def impact(self, node_or_path: str, *, depth: int = 3) -> GraphQueryResult:
        snapshot = self._snapshot(node_or_path, depth=depth)
        seed = self._resolve_node(snapshot, node_or_path)
        normalized_path = node_or_path.removeprefix("./")
        seeds = tuple(
            sorted(
                {
                    seed,
                    *(node.node_id for node in snapshot.nodes if node.path == normalized_path),
                }
            )
        )
        reverse: dict[str, set[str]] = defaultdict(set)
        for edge in snapshot.edges:
            if edge.relation in _IMPACT_RELATIONS:
                reverse[edge.target].add(edge.source)
        selected = set(seeds)
        frontier = set(seeds)
        node_depths = {seed_id: 0 for seed_id in seeds}
        for current_depth in range(1, depth + 1):
            upcoming: set[str] = set()
            for current in sorted(frontier):
                upcoming.update(reverse.get(current, ()))
            upcoming.difference_update(selected)
            for node_id in upcoming:
                node_depths[node_id] = current_depth
            selected.update(upcoming)
            frontier = upcoming
            if not frontier or len(selected) >= self.config.query_max_nodes:
                break
        return self._result(
            snapshot,
            operation="impact",
            query=node_or_path,
            selected=selected,
            seeds=seeds,
            node_depths=node_depths,
        )

    def _snapshot(self, query: str, *, depth: int) -> CodeGraphSnapshot:
        self._validate_text(query)
        if not 1 <= depth <= 3:
            raise CodeGraphQueryError("Code graph traversal depth must be inside 1..3")
        return self._current_snapshot()

    def _current_snapshot(self) -> CodeGraphSnapshot:
        projection = CodeGraphProjection(self.root)
        status = projection.status(verify_hashes=True)
        if status.state is not CodeGraphState.CURRENT:
            raise CodeGraphUnavailable(
                f"Code graph is not current: {status.reason or status.state.value}"
            )
        return projection.current_snapshot()

    def _validate_text(self, value: str) -> None:
        if not 1 <= len(value) <= 512 or "\x00" in value:
            raise CodeGraphQueryError("Code graph query exceeds its text limits")
        decision = self.scanner.scan(value.encode("utf-8"), path=None)
        if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
            raise CodeGraphQueryError("Code graph query failed sensitivity policy")

    def _relevance_scores(self, snapshot: CodeGraphSnapshot, query: str) -> dict[str, int]:
        query_tokens = _tokens(query)
        normalized = query.casefold().strip()
        document_frequency: dict[str, int] = defaultdict(int)
        terms_by_node: dict[str, tuple[str, ...]] = {}
        for node in snapshot.nodes:
            terms = _node_terms(node)
            terms_by_node[node.node_id] = terms
            for term in set(terms):
                document_frequency[term] += 1
        scores: dict[str, int] = {}
        total = max(1, len(snapshot.nodes))
        for node in snapshot.nodes:
            searchable = " ".join(
                value for value in (node.label, node.qualified_name, node.path or "") if value
            ).casefold()
            score = 20_000 if normalized and normalized == node.node_id.casefold() else 0
            if normalized and normalized == node.label.casefold():
                score += 12_000
            elif normalized and normalized in searchable:
                score += 2_000
            node_terms = set(terms_by_node[node.node_id])
            for term in query_tokens:
                frequency = document_frequency.get(term, 0)
                idf = max(1, round(1_000 * math.log((total + 1) / (frequency + 1))))
                if term in node_terms:
                    score += 2_000 + idf
                elif any(candidate.startswith(term) for candidate in node_terms):
                    score += 600 + idf // 2
                elif len(term) >= 3 and term in searchable:
                    score += 240
            if node.is_god:
                score += 30
            if node.is_bridge:
                score += 40
            if score > 0:
                scores[node.node_id] = score
        return scores

    def _seed_nodes(
        self,
        snapshot: CodeGraphSnapshot,
        query: str,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        scores = self._relevance_scores(snapshot, query)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return tuple(node_id for node_id, _ in ranked[:limit])

    def _resolve_node(self, snapshot: CodeGraphSnapshot, value: str) -> str:
        by_id = {node.node_id: node for node in snapshot.nodes}
        if value in by_id:
            return value
        normalized_path = value.removeprefix("./")
        path_matches = [node for node in snapshot.nodes if node.path == normalized_path]
        if path_matches:
            path_matches.sort(
                key=lambda node: (
                    node.kind is not CodeNodeKind.FILE,
                    node.kind.value,
                    node.node_id,
                )
            )
            return path_matches[0].node_id
        seeds = self._seed_nodes(snapshot, value, limit=2)
        if not seeds:
            raise CodeGraphQueryError("Code graph node was not found")
        if len(seeds) > 1:
            first = by_id[seeds[0]]
            second = by_id[seeds[1]]
            if first.label.casefold() == second.label.casefold() and first.path != second.path:
                raise CodeGraphQueryError("Code graph node reference is ambiguous")
        return seeds[0]

    @staticmethod
    def _adjacency(
        edges: Iterable[CodeEdge],
        *,
        direction: Literal["both", "out", "in"],
    ) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            if direction in {"both", "out"}:
                adjacency[edge.source].add(edge.target)
            if direction in {"both", "in"}:
                adjacency[edge.target].add(edge.source)
        return adjacency

    def _traverse(
        self,
        snapshot: CodeGraphSnapshot,
        *,
        operation: Literal["query", "explain", "neighbors"],
        query: str,
        seeds: tuple[str, ...],
        depth: int,
        direction: Literal["both", "out", "in"],
        scores: dict[str, int] | None = None,
        traversal_roots: tuple[str, ...] | None = None,
    ) -> GraphQueryResult:
        adjacency = self._adjacency(snapshot.edges, direction=direction)
        ranking = scores or {}
        roots = traversal_roots if traversal_roots else seeds
        top_score = max((ranking.get(root, 0) for root in roots), default=0)
        # A neighbor must carry a meaningful share of the best match to consume
        # budget on a query; structural context stays one hop around the roots.
        relevance_floor = max(600, top_score // 25) if operation == "query" else 0
        selected = set(roots)
        frontier = set(roots)
        node_depths = {root_id: 0 for root_id in roots}
        for current_depth in range(1, depth + 1):
            upcoming: set[str] = set()
            for current in sorted(frontier):
                upcoming.update(adjacency.get(current, ()))
            upcoming.difference_update(selected)
            if operation == "query" and current_depth > 1:
                upcoming = {
                    node_id for node_id in upcoming if ranking.get(node_id, 0) >= relevance_floor
                }
            elif operation == "query":
                upcoming = {
                    node_id
                    for node_id in upcoming
                    if ranking.get(node_id, 0) * 2 >= relevance_floor
                }
            room = max(0, self.config.query_max_nodes - len(selected))
            accepted = sorted(
                upcoming,
                key=lambda node_id: (-ranking.get(node_id, 0), node_id),
            )[:room]
            selected.update(accepted)
            for node_id in accepted:
                node_depths[node_id] = current_depth
            frontier = upcoming.intersection(selected)
            if not frontier or len(selected) >= self.config.query_max_nodes:
                break
        return self._result(
            snapshot,
            operation=operation,
            query=query,
            selected=selected,
            seeds=seeds,
            node_depths=node_depths,
            scores=scores,
        )

    @staticmethod
    def _path_edges(edges: tuple[CodeEdge, ...], path: list[str]) -> tuple[CodeEdge, ...]:
        selected: list[CodeEdge] = []
        for source, target in pairwise(path):
            candidates = sorted(
                (edge for edge in edges if {edge.source, edge.target} == {source, target}),
                key=lambda edge: (
                    edge.confidence is EdgeConfidence.AMBIGUOUS,
                    edge.confidence is EdgeConfidence.INFERRED,
                    edge.edge_id,
                ),
            )
            if candidates:
                selected.append(candidates[0])
        return tuple(selected)

    def _result(
        self,
        snapshot: CodeGraphSnapshot,
        *,
        operation: Literal["query", "explain", "neighbors", "path", "impact"],
        query: str,
        selected: set[str],
        seeds: tuple[str, ...],
        preferred_edges: tuple[CodeEdge, ...] | None = None,
        ordered_node_ids: tuple[str, ...] = (),
        node_depths: dict[str, int] | None = None,
        scores: dict[str, int] | None = None,
    ) -> GraphQueryResult:
        by_id = {node.node_id: node for node in snapshot.nodes}
        if operation == "path":
            if len(ordered_node_ids) > self.config.query_max_nodes:
                raise CodeGraphQueryError("Shortest path exceeds the configured node budget")
            ordered_ids = list(ordered_node_ids)
        elif scores is None:
            ordered_ids = list(dict.fromkeys((*seeds, *sorted(selected))))
        else:
            ranking = scores
            ordered_ids = list(
                dict.fromkeys(
                    (
                        *seeds,
                        *sorted(
                            selected,
                            key=lambda node_id: (-ranking.get(node_id, 0), node_id),
                        ),
                    )
                )
            )
        truncated = len(ordered_ids) > self.config.query_max_nodes
        ordered_ids = ordered_ids[: self.config.query_max_nodes]
        allowed = set(ordered_ids)
        depths = node_depths or {}
        nodes = tuple(
            CodeGraphQueryNode.from_node(by_id[node_id], depth=max(0, depths.get(node_id, 0)))
            for node_id in ordered_ids
        )
        source_edges = snapshot.edges if preferred_edges is None else preferred_edges
        edge_ranking = scores or {}
        if operation == "query":
            # Relevance-first edge slice: god-node fan-out must not spend the
            # byte budget ahead of edges that touch the actual answer nodes.
            def edge_key(item: CodeEdge) -> tuple[int, bool, str, str]:
                return (
                    -(edge_ranking.get(item.source, 0) + edge_ranking.get(item.target, 0)),
                    item.confidence is EdgeConfidence.AMBIGUOUS,
                    item.relation.value,
                    item.edge_id,
                )

            edge_budget = min(self.config.query_max_edges, 3 * max(1, len(ordered_ids)))
        else:

            def edge_key(item: CodeEdge) -> tuple[int, bool, str, str]:
                return (
                    0,
                    item.confidence is EdgeConfidence.AMBIGUOUS,
                    item.relation.value,
                    item.edge_id,
                )

            edge_budget = self.config.query_max_edges
        edges = tuple(
            CodeGraphQueryEdge.from_edge(edge)
            for edge in sorted(source_edges, key=edge_key)
            if edge.source in allowed and edge.target in allowed
        )
        if len(edges) > edge_budget:
            edges = edges[:edge_budget]
            truncated = True
        result = GraphQueryResult(
            operation=operation,
            snapshot_id=snapshot.snapshot_id,
            snapshot_fingerprint=snapshot.logical_fingerprint,
            query_sha256=sha256_hex(query.encode("utf-8")),
            nodes=nodes,
            edges=edges,
            seed_node_ids=tuple(seed for seed in seeds if seed in allowed),
            ordered_node_ids=tuple(node_id for node_id in ordered_node_ids if node_id in allowed),
            truncated=truncated,
            estimated_context_bytes=0,
        )
        protected = set(
            result.ordered_node_ids if operation == "path" else result.seed_node_ids[:1]
        )
        while len(canonical_json_bytes(result)) > self.config.query_max_bytes:
            if operation != "path" and result.edges:
                # Tail edges are the least relevant under edge_key; shed them
                # before sacrificing answer nodes.
                result = result.model_copy(update={"edges": result.edges[:-1], "truncated": True})
                continue
            removable = next(
                (node for node in reversed(nodes) if node.node_id not in protected),
                None,
            )
            if removable is None:
                break
            nodes = tuple(node for node in nodes if node.node_id != removable.node_id)
            kept = {node.node_id for node in nodes}
            result = result.model_copy(
                update={
                    "nodes": nodes,
                    "edges": tuple(
                        edge for edge in result.edges if edge.source in kept and edge.target in kept
                    ),
                    "seed_node_ids": tuple(seed for seed in result.seed_node_ids if seed in kept),
                    "ordered_node_ids": tuple(
                        node_id for node_id in result.ordered_node_ids if node_id in kept
                    ),
                    "truncated": True,
                }
            )
        if len(canonical_json_bytes(result)) > self.config.query_max_bytes:
            raise CodeGraphQueryError("Code graph result cannot fit the configured response budget")
        estimated = len(canonical_json_bytes(result))
        return result.model_copy(update={"estimated_context_bytes": estimated})
