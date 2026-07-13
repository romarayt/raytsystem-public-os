from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from raytsystem.codegraph.contracts import (
    CodeEdge,
    CodeNode,
    CodeNodeKind,
    CodeRelation,
    EdgeConfidence,
)
from raytsystem.codegraph.extract import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    FileExtraction,
    PendingEdge,
    stable_edge_id,
    stable_node_id,
)
from raytsystem.codegraph.security import (
    CodeGraphSecurityError,
    safe_source_name,
    sanitize_label,
    sanitize_metadata,
)
from raytsystem.contracts import canonical_json_bytes, sha256_hex


@dataclass(frozen=True)
class ResolvedGraph:
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]
    unresolved_references: int
    ambiguous_edges: int


def _add_node(nodes: dict[str, CodeNode], node: CodeNode) -> None:
    existing = nodes.get(node.node_id)
    if existing is not None and existing != node:
        raise CodeGraphSecurityError("Code graph node identity collision")
    nodes[node.node_id] = node


def _add_edge(edges: dict[str, CodeEdge], edge: CodeEdge) -> None:
    existing = edges.get(edge.edge_id)
    if existing is not None and existing != edge:
        raise CodeGraphSecurityError("Code graph edge identity collision")
    edges[edge.edge_id] = edge


def _repository_and_directories(
    extractions: tuple[FileExtraction, ...],
    nodes: dict[str, CodeNode],
    edges: dict[str, CodeEdge],
) -> str:
    aggregate = sha256_hex(
        canonical_json_bytes(
            [
                {"path": extraction.path, "sha256": extraction.content_sha256}
                for extraction in extractions
            ]
        )
    )
    repository_id = stable_node_id(
        CodeNodeKind.REPOSITORY,
        path=None,
        qualified_name="workspace",
    )
    repository = CodeNode(
        node_id=repository_id,
        kind=CodeNodeKind.REPOSITORY,
        label="raytsystem repository",
        qualified_name="workspace",
        path=None,
        content_fingerprint=aggregate,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        metadata=sanitize_metadata({"resolution_key": "repository:workspace"}),
    )
    _add_node(nodes, repository)

    files_by_directory: dict[str, list[FileExtraction]] = defaultdict(list)
    for extraction in extractions:
        parent = PurePosixPath(extraction.path).parent
        while str(parent) != ".":
            files_by_directory[parent.as_posix()].append(extraction)
            parent = parent.parent
    directory_ids: dict[str, str] = {}
    for directory, files in sorted(files_by_directory.items()):
        directory_id = stable_node_id(
            CodeNodeKind.DIRECTORY,
            path=directory,
            qualified_name=directory,
        )
        directory_ids[directory] = directory_id
        fingerprint = sha256_hex(
            canonical_json_bytes(
                [
                    {"path": item.path, "sha256": item.content_sha256}
                    for item in sorted(files, key=lambda value: value.path)
                ]
            )
        )
        _add_node(
            nodes,
            CodeNode(
                node_id=directory_id,
                kind=CodeNodeKind.DIRECTORY,
                label=sanitize_label(PurePosixPath(directory).name),
                qualified_name=directory,
                path=directory,
                content_fingerprint=fingerprint,
                extractor=EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSION,
                metadata=sanitize_metadata({"resolution_key": f"directory:{directory}"}),
            ),
        )
    file_nodes = {
        node.path: node
        for node in nodes.values()
        if node.kind is CodeNodeKind.FILE and node.path is not None
    }
    for directory, directory_id in sorted(directory_ids.items()):
        parent = PurePosixPath(directory).parent
        source = directory_ids.get(parent.as_posix(), repository_id)
        evidence_file = sorted(files_by_directory[directory], key=lambda value: value.path)[0].path
        edge = CodeEdge(
            edge_id=stable_edge_id(
                source,
                directory_id,
                CodeRelation.CONTAINS,
                source_file=evidence_file,
                source_location=None,
            ),
            source=source,
            target=directory_id,
            relation=CodeRelation.CONTAINS,
            confidence=EdgeConfidence.EXTRACTED,
            source_file=evidence_file,
            extractor=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            content_fingerprint=nodes[directory_id].content_fingerprint,
            metadata=sanitize_metadata({"structural": "true"}),
        )
        _add_edge(edges, edge)
    for extraction in extractions:
        target = file_nodes.get(extraction.path)
        if target is None:
            raise CodeGraphSecurityError("Code graph extraction omitted its file node")
        parent = PurePosixPath(extraction.path).parent
        source = directory_ids.get(parent.as_posix(), repository_id)
        _add_edge(
            edges,
            CodeEdge(
                edge_id=stable_edge_id(
                    source,
                    target.node_id,
                    CodeRelation.CONTAINS,
                    source_file=extraction.path,
                    source_location=None,
                ),
                source=source,
                target=target.node_id,
                relation=CodeRelation.CONTAINS,
                confidence=EdgeConfidence.EXTRACTED,
                source_file=extraction.path,
                extractor=EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSION,
                content_fingerprint=extraction.content_sha256,
                metadata=sanitize_metadata({"structural": "true"}),
            ),
        )
    return repository_id


def _alias_index(nodes: dict[str, CodeNode]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        resolution_key = node.metadata.get("resolution_key")
        if resolution_key:
            aliases[resolution_key].append(node.node_id)
        if node.path is not None and node.kind is CodeNodeKind.FILE:
            aliases[f"file:{node.path}"].append(node.node_id)
        if node.kind in {
            CodeNodeKind.CLASS,
            CodeNodeKind.FUNCTION,
            CodeNodeKind.METHOD,
            CodeNodeKind.TEST,
        }:
            symbol = node.metadata.get("symbol") or node.label
            aliases[f"symbol:{symbol}"].append(node.node_id)
        if node.kind is CodeNodeKind.MODULE:
            aliases[f"module:{node.qualified_name}"].append(node.node_id)
        if node.kind is CodeNodeKind.DATABASE_TABLE:
            aliases[f"table:{node.qualified_name}"].append(node.node_id)
    return {key: sorted(set(values)) for key, values in aliases.items()}


def _dependency_node(
    target_ref: str,
    *,
    ambiguous: bool,
    candidates: int = 0,
) -> CodeNode:
    prefix, _, raw = target_ref.partition(":")
    label = raw.split("/", maxsplit=1)[0].split(".", maxsplit=1)[0] or raw or "unknown"
    qualified = f"{prefix}:{raw}"
    if ambiguous:
        label = f"Ambiguous: {label}"
        qualified = f"ambiguous:{qualified}"
    fingerprint = sha256_hex(
        canonical_json_bytes({"reference": qualified, "candidates": candidates})
    )
    return CodeNode(
        node_id=stable_node_id(
            CodeNodeKind.DEPENDENCY,
            path=None,
            qualified_name=qualified,
        ),
        kind=CodeNodeKind.DEPENDENCY,
        label=sanitize_label(label),
        qualified_name=safe_source_name(qualified),
        path=None,
        content_fingerprint=fingerprint,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        metadata=sanitize_metadata(
            {
                "external": "false" if ambiguous else "true",
                "reference_kind": prefix,
                "candidate_count": str(candidates),
            }
        ),
    )


def _relative_module_candidates(pending: PendingEdge, aliases: dict[str, list[str]]) -> list[str]:
    raw = pending.target_ref.removeprefix("module:")
    if not raw.startswith("."):
        return aliases.get(pending.target_ref, [])
    source_dir = PurePosixPath(pending.source_file).parent.as_posix()
    normalized = posixpath.normpath(posixpath.join(source_dir, raw))
    keys = [f"file:{normalized}"]
    for suffix in (".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.tsx", "/index.js"):
        keys.append(f"file:{normalized}{suffix}")
    found: list[str] = []
    for key in keys:
        found.extend(aliases.get(key, ()))
    return sorted(set(found))


def _resolved_edge(pending: PendingEdge, target: str, confidence: EdgeConfidence) -> CodeEdge:
    return CodeEdge(
        edge_id=stable_edge_id(
            pending.source,
            target,
            pending.relation,
            source_file=pending.source_file,
            source_location=pending.source_location,
        ),
        source=pending.source,
        target=target,
        relation=pending.relation,
        confidence=confidence,
        source_file=pending.source_file,
        source_location=pending.source_location,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        content_fingerprint=pending.content_fingerprint,
        metadata=sanitize_metadata(pending.metadata),
    )


def resolve_graph(extractions: tuple[FileExtraction, ...]) -> ResolvedGraph:
    nodes: dict[str, CodeNode] = {}
    edges: dict[str, CodeEdge] = {}
    for extraction in sorted(extractions, key=lambda value: value.path):
        for node in extraction.nodes:
            _add_node(nodes, node)
        for edge in extraction.edges:
            _add_edge(edges, edge)
    _repository_and_directories(extractions, nodes, edges)
    aliases = _alias_index(nodes)
    unresolved = 0
    ambiguous = 0
    pending_edges = sorted(
        (edge for extraction in extractions for edge in extraction.pending_edges),
        key=lambda item: (
            item.source_file,
            0 if item.source_location is None else item.source_location.start_line,
            item.source,
            item.target_ref,
            item.relation.value,
        ),
    )
    for pending in pending_edges:
        candidates = (
            _relative_module_candidates(pending, aliases)
            if pending.target_ref.startswith("module:")
            else aliases.get(pending.target_ref, [])
        )
        if len(candidates) == 1:
            _add_edge(edges, _resolved_edge(pending, candidates[0], pending.confidence))
            continue
        if len(candidates) > 1:
            placeholder = _dependency_node(
                pending.target_ref,
                ambiguous=True,
                candidates=len(candidates),
            )
            _add_node(nodes, placeholder)
            _add_edge(edges, _resolved_edge(pending, placeholder.node_id, EdgeConfidence.AMBIGUOUS))
            ambiguous += 1
            continue
        if pending.target_ref.startswith(("dependency:", "module:")):
            dependency = _dependency_node(pending.target_ref, ambiguous=False)
            _add_node(nodes, dependency)
            confidence = (
                EdgeConfidence.EXTRACTED
                if pending.relation is CodeRelation.DEPENDS_ON
                else pending.confidence
            )
            _add_edge(edges, _resolved_edge(pending, dependency.node_id, confidence))
            continue
        unresolved += 1
    known = set(nodes)
    if any(edge.source not in known or edge.target not in known for edge in edges.values()):
        raise CodeGraphSecurityError("Resolved code graph is not closed")
    return ResolvedGraph(
        nodes=tuple(sorted(nodes.values(), key=lambda item: item.node_id)),
        edges=tuple(sorted(edges.values(), key=lambda item: item.edge_id)),
        unresolved_references=unresolved,
        ambiguous_edges=ambiguous,
    )
