from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from raytsystem.catalog import CatalogService, CatalogSnapshot
from raytsystem.codegraph.contracts import (
    CodeEdge,
    CodeGraphSnapshot,
    CodeNode,
    CodeNodeKind,
    CodeRelation,
)
from raytsystem.codegraph.detect import load_code_graph_config
from raytsystem.contracts import (
    EntityObject,
    GraphEdgeView,
    GraphLens,
    GraphNodeView,
    GraphSnapshot,
    RunSummary,
    TaskStatus,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.corpus import ActiveCorpus
from raytsystem.readmodel import load_run_summaries
from raytsystem.storage import IntegrityError
from raytsystem.tasking import TaskBoardSnapshot, TaskService


class UniverseError(IntegrityError):
    """The verified state planes cannot form a closed public graph."""


@dataclass(frozen=True)
class _NodeSpec:
    node_id: str
    kind: str
    label: str
    subtitle: str
    status: str
    ring: str
    importance: int
    recorded_at: datetime | None = None
    source_ref: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _EdgeSpec:
    source: str
    target: str
    kind: str
    status: str = "active"
    directed: bool = True
    qualifier: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class UniverseService:
    """Build one disposable graph from verified immutable state planes."""

    max_nodes = 10_000
    max_edges = 50_000
    _public_code_metadata = frozenset(
        {
            "candidate_count",
            "configuration",
            "external",
            "handler",
            "language",
            "method",
            "module",
            "reference_kind",
            "resolution_key",
            "route",
            "symbol",
        }
    )
    _ring_radius: ClassVar[dict[str, int]] = {
        "core": 0,
        "instruction": 180,
        "capability": 340,
        "work": 520,
        "code": 680,
        "knowledge": 830,
        "evidence": 990,
        "application": 1_140,
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def snapshot(
        self,
        *,
        corpus: ActiveCorpus | None = None,
        tasks: TaskBoardSnapshot | None = None,
        catalog: CatalogSnapshot | None = None,
        runs: tuple[RunSummary, ...] | None = None,
        code_snapshot: CodeGraphSnapshot | None = None,
        code_snapshot_sha256: str | None = None,
        code_graph_state: str = "missing",
    ) -> GraphSnapshot:
        active_corpus = corpus or ActiveCorpus.load(self.root, verify_evidence=True)
        task_board = tasks or TaskService(self.root).snapshot()
        catalog_snapshot = catalog or CatalogService(self.root).load()
        run_summaries = runs or load_run_summaries(
            self.root,
            active_corpus.run_manifests,
        )
        nodes: dict[str, _NodeSpec] = {}
        edges: list[_EdgeSpec] = []
        edge_keys: set[tuple[str, str, str, str]] = set()
        code_view_node_count = 0
        code_view_edge_count = 0

        def add_node(node: _NodeSpec) -> None:
            if node.node_id in nodes and nodes[node.node_id] != node:
                raise UniverseError("Graph node identity collision")
            nodes[node.node_id] = node

        def add_edge(edge: _EdgeSpec) -> None:
            key = (edge.source, edge.target, edge.kind, edge.qualifier)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append(edge)

        workspace_id = "workspace_current"
        add_node(
            _NodeSpec(
                workspace_id,
                "workspace",
                "raytsystem workspace",
                "Local control plane",
                "local_only",
                "core",
                100,
                active_corpus.generation.created_at,
            )
        )
        knowledge_generation_id = active_corpus.generation.generation_id
        add_node(
            _NodeSpec(
                knowledge_generation_id,
                "generation",
                "Knowledge generation",
                self._short_id(knowledge_generation_id),
                "active",
                "core",
                92,
                active_corpus.generation.created_at,
                metadata={"sha256": active_corpus.generation_sha256},
            )
        )
        add_edge(_EdgeSpec(workspace_id, knowledge_generation_id, "activates"))

        if task_board.generation_id is not None:
            add_node(
                _NodeSpec(
                    task_board.generation_id,
                    "task_generation",
                    "Task generation",
                    self._short_id(task_board.generation_id),
                    "active",
                    "work",
                    78,
                    max((task.updated_at for task in task_board.tasks), default=None),
                    metadata={"sha256": task_board.generation_sha256 or ""},
                )
            )
            add_edge(_EdgeSpec(workspace_id, task_board.generation_id, "activates"))

        instruction_by_path: dict[str, str] = {}
        for instruction in catalog_snapshot.instructions:
            instruction_by_path[instruction.path] = instruction.document_id
            add_node(
                _NodeSpec(
                    instruction.document_id,
                    "instruction",
                    instruction.label,
                    instruction.kind,
                    instruction.sensitivity.value,
                    "instruction",
                    72,
                    metadata={
                        "sha256": instruction.content_sha256,
                        "size_bytes": str(instruction.size_bytes),
                    },
                )
            )
            add_edge(_EdgeSpec(workspace_id, instruction.document_id, "governed_by"))

        for adapter in catalog_snapshot.adapters:
            add_node(
                _NodeSpec(
                    adapter.adapter_id,
                    "adapter",
                    adapter.name,
                    adapter.isolation_mode,
                    adapter.state.value,
                    "application",
                    54,
                    metadata={
                        "version": adapter.version,
                        "reason": adapter.reason or "",
                        "egress": adapter.egress_destination or "none",
                    },
                )
            )
            add_edge(_EdgeSpec(workspace_id, adapter.adapter_id, "offers"))

        for pack in catalog_snapshot.packs:
            add_node(
                _NodeSpec(
                    pack.pack_id,
                    "pack",
                    pack.name,
                    pack.description,
                    "optional" if pack.optional else "core",
                    "instruction",
                    64,
                    metadata={
                        "version": pack.version,
                        "license": pack.license_expression,
                        "trust": pack.trust_class.value,
                    },
                )
            )
            add_edge(_EdgeSpec(workspace_id, pack.pack_id, "contains"))

        for skill in catalog_snapshot.skills:
            add_node(
                _NodeSpec(
                    skill.skill_id,
                    "skill",
                    skill.name,
                    skill.description,
                    "enabled" if skill.enabled else "restricted",
                    "capability",
                    68,
                    metadata={
                        "sha256": skill.source_sha256,
                        "trust": skill.trust_class.value,
                        "test_status": skill.test_status,
                        "sensitivity": skill.sensitivity.value,
                    },
                )
            )
            add_edge(_EdgeSpec(skill.pack_id, skill.skill_id, "contains"))

        for pack in catalog_snapshot.packs:
            for skill_id in pack.skill_ids:
                if skill_id in nodes:
                    add_edge(
                        _EdgeSpec(
                            pack.pack_id,
                            skill_id,
                            "includes",
                            qualifier=pack.pack_id,
                        )
                    )
            for path in pack.context_paths:
                target = instruction_by_path.get(path)
                if target is not None:
                    add_edge(_EdgeSpec(pack.pack_id, target, "governed_by"))

        for agent in catalog_snapshot.agents:
            add_node(
                _NodeSpec(
                    agent.agent_id,
                    "agent",
                    agent.name,
                    agent.description,
                    "declared" if not agent.enabled else "configured",
                    "capability",
                    76,
                    metadata={
                        "role": agent.role,
                        "version": agent.version,
                        "filesystem_request": agent.requested_filesystem_mode,
                        "egress": agent.egress_destination or "none",
                    },
                )
            )
            add_edge(_EdgeSpec(agent.pack_id, agent.agent_id, "contains"))
            add_edge(
                _EdgeSpec(
                    agent.agent_id,
                    agent.runtime_adapter_id,
                    "configured_for",
                )
            )
            for skill_id in agent.skill_ids:
                add_edge(_EdgeSpec(agent.agent_id, skill_id, "uses"))
            for context_path in agent.context_paths:
                target = instruction_by_path.get(context_path)
                if target is not None:
                    add_edge(_EdgeSpec(agent.agent_id, target, "governed_by"))

        agent_ids = {agent.agent_id for agent in catalog_snapshot.agents}
        skill_ids = {skill.skill_id for skill in catalog_snapshot.skills}
        task_ids = {task.task_id for task in task_board.tasks}
        for task in task_board.tasks:
            add_node(
                _NodeSpec(
                    task.task_id,
                    "task",
                    task.title,
                    task.priority.value,
                    task.status.value,
                    "work",
                    80 if task.status is TaskStatus.BLOCKED else 66,
                    task.updated_at,
                    metadata={
                        "priority": task.priority.value,
                        "project_id": task.project_id,
                        "revision": str(task.revision),
                        "sensitivity": task.sensitivity.value,
                    },
                )
            )
            if task_board.generation_id is not None:
                add_edge(_EdgeSpec(task_board.generation_id, task.task_id, "contains"))
            else:
                add_edge(_EdgeSpec(workspace_id, task.task_id, "contains"))
            for assignee_id in task.assignee_ids:
                if assignee_id in agent_ids:
                    add_edge(_EdgeSpec(task.task_id, assignee_id, "assigned_to"))
            for skill_id in task.skill_ids:
                if skill_id in skill_ids:
                    add_edge(_EdgeSpec(task.task_id, skill_id, "uses"))
            for dependency_id in task.dependency_ids:
                if dependency_id in task_ids:
                    add_edge(_EdgeSpec(task.task_id, dependency_id, "depends_on"))

        for run in run_summaries:
            add_node(
                _NodeSpec(
                    run.run_id,
                    "run",
                    run.operation_type.replace("_", " ").title(),
                    self._short_id(run.run_id),
                    run.state,
                    "work",
                    58,
                    run.updated_at,
                    metadata={
                        "manifest_sha256": run.manifest_sha256,
                        "semantic_noop": str(run.semantic_noop).lower(),
                    },
                )
            )
            add_edge(_EdgeSpec(workspace_id, run.run_id, "recorded"))
            if run.generation_id == knowledge_generation_id:
                add_edge(_EdgeSpec(run.run_id, knowledge_generation_id, "produced"))

        for source in active_corpus.sources.values():
            add_node(
                _NodeSpec(
                    source.source_id,
                    "source",
                    source.display_name or source.source_type,
                    source.source_type,
                    source.sensitivity.value,
                    "evidence",
                    64,
                    source.created_at,
                    metadata={
                        "trust": source.trust_class.value,
                        "rights": source.rights,
                    },
                )
            )
            add_edge(_EdgeSpec(knowledge_generation_id, source.source_id, "contains"))

        for evidence_id, resolved in active_corpus.evidence.items():
            add_node(
                _NodeSpec(
                    evidence_id,
                    "evidence",
                    f"Evidence {self._short_id(evidence_id)}",
                    resolved.segment.locator.kind,
                    "verified",
                    "evidence",
                    70,
                    resolved.normalization.created_at,
                    source_ref=resolved.source.source_id,
                    metadata={
                        "source_id": resolved.source.source_id,
                        "revision_id": resolved.revision.source_revision_id,
                        "normalization_id": resolved.normalization.normalization_id,
                        "locator_kind": resolved.segment.locator.kind,
                        "excerpt_sha256": resolved.segment.excerpt_sha256,
                    },
                )
            )
            add_edge(_EdgeSpec(resolved.source.source_id, evidence_id, "contains"))

        for entity in active_corpus.entities.values():
            add_node(
                _NodeSpec(
                    entity.entity_id,
                    "entity",
                    entity.canonical_label,
                    entity.entity_type,
                    entity.lifecycle_status.value,
                    "knowledge",
                    62,
                    metadata={"entity_type": entity.entity_type},
                )
            )
            add_edge(_EdgeSpec(knowledge_generation_id, entity.entity_id, "contains"))
            for superseded_by in entity.superseded_by:
                if superseded_by in active_corpus.entities:
                    add_edge(_EdgeSpec(entity.entity_id, superseded_by, "superseded_by"))

        for claim in active_corpus.claims.values():
            add_node(
                _NodeSpec(
                    claim.claim_id,
                    "claim",
                    self._truncate(claim.statement, 180),
                    claim.language,
                    claim.status.value,
                    "knowledge",
                    82 if claim.status.value in {"supported", "confirmed"} else 70,
                    claim.recorded_at,
                    metadata={
                        "evidence_count": str(len(claim.evidence_ids)),
                        "relation_count": str(len(claim.relation_ids)),
                    },
                )
            )
            add_edge(_EdgeSpec(knowledge_generation_id, claim.claim_id, "contains"))
            for evidence_id in claim.evidence_ids:
                add_edge(_EdgeSpec(evidence_id, claim.claim_id, "supports"))
            for superseded in claim.supersedes:
                if superseded in active_corpus.claims:
                    add_edge(_EdgeSpec(claim.claim_id, superseded, "supersedes"))
            for contradicted in claim.contradicts:
                if contradicted in active_corpus.claims:
                    add_edge(_EdgeSpec(claim.claim_id, contradicted, "contradicts"))

        for relation in active_corpus.relations.values():
            for claim_id in relation.claim_ids:
                if claim_id in active_corpus.claims:
                    add_edge(
                        _EdgeSpec(
                            claim_id,
                            relation.subject_entity_id,
                            "asserts_about",
                            qualifier=relation.relation_id,
                        )
                    )
            if isinstance(relation.object, EntityObject):
                add_edge(
                    _EdgeSpec(
                        relation.subject_entity_id,
                        relation.object.entity_id,
                        relation.predicate,
                        status=relation.lifecycle_status.value,
                        qualifier=relation.relation_id,
                        metadata={"relation_id": relation.relation_id},
                    )
                )

        if (code_snapshot is None) != (code_snapshot_sha256 is None):
            raise UniverseError("Code snapshot and object hash must be supplied together")
        if code_snapshot is not None:
            code_nodes, code_edges = self._code_slice(code_snapshot)
            code_node_by_id = {node.node_id: node for node in code_nodes}
            code_view_node_count = len(code_nodes)
            code_view_edge_count = len(code_edges)
            for node in code_nodes:
                metadata = {
                    key: value
                    for key, value in node.metadata.items()
                    if key in self._public_code_metadata
                }
                metadata.update(
                    {
                        "qualified_name": node.qualified_name,
                        "path": node.path or "",
                        "content_fingerprint": node.content_fingerprint,
                        "extractor": node.extractor,
                        "extractor_version": node.extractor_version,
                        "community_id": "" if node.community_id is None else str(node.community_id),
                        "is_god": str(node.is_god).lower(),
                        "is_bridge": str(node.is_bridge).lower(),
                        "code_snapshot_id": code_snapshot.snapshot_id,
                    }
                )
                if node.location is not None:
                    metadata.update(
                        {
                            "start_line": str(node.location.start_line),
                            "start_column": str(node.location.start_column),
                            "end_line": str(node.location.end_line),
                            "end_column": str(node.location.end_column),
                        }
                    )
                add_node(
                    _NodeSpec(
                        node.node_id,
                        node.kind.value,
                        node.label,
                        node.path or node.qualified_name or "Code graph node",
                        code_graph_state,
                        "code",
                        self._code_importance(node),
                        code_snapshot.created_at,
                        None,
                        metadata,
                    )
                )
            for edge in code_edges:
                edge_metadata = {
                    "code_edge_id": edge.edge_id,
                    "confidence": edge.confidence.value,
                    "source_file": edge.source_file,
                    "source_line": (
                        "" if edge.source_location is None else str(edge.source_location.start_line)
                    ),
                    "extractor": edge.extractor,
                    "content_fingerprint": edge.content_fingerprint,
                    "code_snapshot_id": code_snapshot.snapshot_id,
                }
                add_edge(
                    _EdgeSpec(
                        edge.source,
                        edge.target,
                        edge.relation.value,
                        edge.confidence.value.casefold(),
                        True,
                        edge.edge_id,
                        edge_metadata,
                    )
                )
                source_node = code_node_by_id.get(edge.source)
                if (
                    edge.relation is CodeRelation.TESTS
                    and source_node is not None
                    and source_node.kind is CodeNodeKind.TEST
                ):
                    add_edge(
                        _EdgeSpec(
                            edge.source,
                            edge.target,
                            "verifies",
                            edge.confidence.value.casefold(),
                            True,
                            f"{edge.edge_id}:verifies",
                            edge_metadata | {"cross_plane": "test_to_code"},
                        )
                    )
                if (
                    edge.relation is CodeRelation.REFERENCES
                    and source_node is not None
                    and source_node.kind is CodeNodeKind.ADR
                ):
                    add_edge(
                        _EdgeSpec(
                            edge.source,
                            edge.target,
                            "explains",
                            edge.confidence.value.casefold(),
                            True,
                            f"{edge.edge_id}:explains",
                            edge_metadata | {"cross_plane": "adr_to_code"},
                        )
                    )
            for node in code_nodes:
                if node.kind is CodeNodeKind.REPOSITORY:
                    add_edge(
                        _EdgeSpec(
                            workspace_id,
                            node.node_id,
                            "contains",
                            qualifier=code_snapshot.snapshot_id,
                        )
                    )

        if len(nodes) > self.max_nodes or len(edges) > self.max_edges:
            raise UniverseError("Graph exceeds the safe first-release projection limit")
        unresolved = [
            edge for edge in edges if edge.source not in nodes or edge.target not in nodes
        ]
        if unresolved:
            raise UniverseError("Graph edge references an unavailable node")

        rendered_nodes = self._layout(nodes)
        rendered_edges = tuple(
            GraphEdgeView(
                edge_id=derive_id(
                    "gedge",
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "kind": edge.kind,
                        "qualifier": edge.qualifier,
                    },
                ),
                source=edge.source,
                target=edge.target,
                kind=edge.kind,
                status=edge.status,
                directed=edge.directed,
                metadata=edge.metadata,
            )
            for edge in sorted(
                edges,
                key=lambda item: (item.source, item.target, item.kind, item.qualifier),
            )
        )
        timestamps = [active_corpus.generation.created_at]
        timestamps.extend(task.updated_at for task in task_board.tasks)
        timestamps.extend(run.updated_at for run in run_summaries)
        if code_snapshot is not None:
            timestamps.append(code_snapshot.created_at)
        created_at = max(timestamps)
        seed = GraphSnapshot(
            graph_snapshot_id="graph_pending",
            knowledge_generation_id=knowledge_generation_id,
            knowledge_generation_sha256=active_corpus.generation_sha256,
            task_generation_id=task_board.generation_id,
            task_generation_sha256=task_board.generation_sha256,
            catalog_sha256=catalog_snapshot.catalog_sha256,
            code_snapshot_id=None if code_snapshot is None else code_snapshot.snapshot_id,
            code_snapshot_sha256=code_snapshot_sha256,
            code_snapshot_fingerprint=(
                None if code_snapshot is None else code_snapshot.logical_fingerprint
            ),
            code_graph_state=code_graph_state,
            code_file_count=0 if code_snapshot is None else len(code_snapshot.files),
            code_node_count=0 if code_snapshot is None else len(code_snapshot.nodes),
            code_edge_count=0 if code_snapshot is None else len(code_snapshot.edges),
            code_ambiguous_edges=(
                0 if code_snapshot is None else code_snapshot.metrics.ambiguous_edges
            ),
            code_view_node_count=code_view_node_count,
            code_view_edge_count=code_view_edge_count,
            code_view_truncated=(
                False
                if code_snapshot is None
                else code_view_node_count < len(code_snapshot.nodes)
                or code_view_edge_count < len(code_snapshot.edges)
            ),
            nodes=rendered_nodes,
            edges=rendered_edges,
            supported_lenses=tuple(GraphLens),
            created_at=created_at,
        )
        return seed.model_copy(
            update={"graph_snapshot_id": derive_id("graph", seed.identity_payload())}
        )

    def _code_slice(
        self,
        snapshot: CodeGraphSnapshot,
    ) -> tuple[tuple[CodeNode, ...], tuple[CodeEdge, ...]]:
        config = load_code_graph_config(self.root)
        priorities = {
            CodeNodeKind.REPOSITORY: 0,
            CodeNodeKind.PACKAGE: 1,
            CodeNodeKind.MODULE: 2,
            CodeNodeKind.API_ENDPOINT: 3,
            CodeNodeKind.CLASS: 4,
            CodeNodeKind.TEST: 5,
            CodeNodeKind.FILE: 6,
            CodeNodeKind.FUNCTION: 7,
            CodeNodeKind.METHOD: 8,
        }
        ordered = sorted(
            snapshot.nodes,
            key=lambda node: (
                not node.is_god,
                not node.is_bridge,
                priorities.get(node.kind, 9),
                node.node_id,
            ),
        )
        selected_nodes = tuple(ordered[: config.universe_max_nodes])
        selected_ids = {node.node_id for node in selected_nodes}
        selected_edges = tuple(
            edge
            for edge in snapshot.edges
            if edge.source in selected_ids and edge.target in selected_ids
        )[: config.universe_max_edges]
        return selected_nodes, selected_edges

    @staticmethod
    def _code_importance(node: CodeNode) -> int:
        base = {
            CodeNodeKind.REPOSITORY: 96,
            CodeNodeKind.PACKAGE: 84,
            CodeNodeKind.MODULE: 82,
            CodeNodeKind.API_ENDPOINT: 78,
            CodeNodeKind.CLASS: 72,
            CodeNodeKind.FILE: 58,
            CodeNodeKind.TEST: 56,
            CodeNodeKind.FUNCTION: 52,
            CodeNodeKind.METHOD: 50,
            CodeNodeKind.CONFIGURATION: 48,
            CodeNodeKind.ADR: 48,
            CodeNodeKind.RATIONALE: 36,
        }.get(node.kind, 44)
        return min(100, base + (8 if node.is_god else 0) + (6 if node.is_bridge else 0))

    def _layout(self, nodes: dict[str, _NodeSpec]) -> tuple[GraphNodeView, ...]:
        by_ring: dict[str, list[_NodeSpec]] = {}
        for node in nodes.values():
            by_ring.setdefault(node.ring, []).append(node)
        rendered: list[GraphNodeView] = []
        for ring, ring_nodes in sorted(by_ring.items()):
            ordered = sorted(
                ring_nodes,
                key=lambda item: (
                    0 if item.kind == "workspace" else 1,
                    item.kind,
                    item.node_id,
                ),
            )
            radius = self._ring_radius.get(ring, 650)
            for index, node in enumerate(ordered):
                if radius == 0:
                    # Keep the workspace as the visual anchor while preventing
                    # other core-plane generations from occupying the same point.
                    core_radius = 0 if index == 0 else 72
                    angle = 2 * math.pi * (index - 1) / max(len(ordered) - 1, 1)
                    x = round(core_radius * math.cos(angle))
                    y = round(core_radius * math.sin(angle))
                else:
                    angle = (2 * math.pi * index / max(len(ordered), 1)) - math.pi / 2
                    x = round(radius * math.cos(angle))
                    y = round(radius * math.sin(angle))
                rendered.append(
                    GraphNodeView(
                        node_id=node.node_id,
                        kind=node.kind,
                        label=node.label,
                        subtitle=node.subtitle,
                        status=node.status,
                        ring=node.ring,
                        importance=node.importance,
                        x=x,
                        y=y,
                        recorded_at=node.recorded_at,
                        source_ref=node.source_ref,
                        metadata=node.metadata,
                    )
                )
        return tuple(sorted(rendered, key=lambda item: item.node_id))

    @staticmethod
    def _short_id(value: str) -> str:
        return value if len(value) <= 18 else f"{value[:9]}…{value[-6:]}"

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def graph_logical_sha256(snapshot: GraphSnapshot) -> str:
    """Return a transport-independent fingerprint for caching and stale-detail checks."""

    return sha256_hex(canonical_json_bytes(snapshot))
