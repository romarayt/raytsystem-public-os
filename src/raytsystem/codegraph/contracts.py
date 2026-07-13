from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    RelativePath,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


class CodeGraphContract(VersionedModel):
    """Closed derived-plane contract; extension payloads are not accepted in v1."""

    @field_validator("extensions")
    @classmethod
    def _closed_extensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("Code graph v1 extensions are closed")
        return value


def _bounded_metadata(value: dict[str, str]) -> dict[str, str]:
    if len(value) > 32:
        raise ValueError("Code graph metadata exceeds the field-count limit")
    for key, item in value.items():
        if not 1 <= len(key) <= 64 or not 0 <= len(item) <= 1024:
            raise ValueError("Code graph metadata exceeds its text limits")
    return value


class CodeNodeKind(StrEnum):
    REPOSITORY = "repository"
    DIRECTORY = "directory"
    FILE = "file"
    MODULE = "module"
    PACKAGE = "package"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    API_ENDPOINT = "api_endpoint"
    DATABASE_TABLE = "database_table"
    DATABASE_SCHEMA = "database_schema"
    CONFIGURATION = "configuration"
    TEST = "test"
    ADR = "adr"
    RATIONALE = "rationale"
    DEPENDENCY = "dependency"


class CodeRelation(StrEnum):
    CONTAINS = "contains"
    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    CONFIGURED_BY = "configured_by"
    TESTS = "tests"
    EXPLAINED_BY = "explained_by"
    IMPLEMENTED_BY = "implemented_by"
    EXPLAINS = "explains"
    VERIFIES = "verifies"
    CHANGED = "changed"
    TOUCHES = "touches"
    OPERATES_ON = "operates_on"


class EdgeConfidence(StrEnum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


class CodeGraphState(StrEnum):
    MISSING = "missing"
    CURRENT = "current"
    UNCHECKED = "unchecked"
    STALE = "stale"
    BUILDING = "building"
    ERROR = "error"


class CodeSourceLocation(CodeGraphContract):
    schema_name: Literal["CodeSourceLocationV1"] = "CodeSourceLocationV1"
    start_line: int = Field(ge=1, le=10_000_000)
    start_column: int = Field(default=0, ge=0, le=10_000_000)
    end_line: int = Field(ge=1, le=10_000_000)
    end_column: int = Field(default=0, ge=0, le=10_000_000)

    @model_validator(mode="after")
    def _ordered(self) -> CodeSourceLocation:
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("Source location end cannot precede its start")
        return self


# Internal compatibility alias for the extraction layer; the public schema name is namespaced.
SourceLocation = CodeSourceLocation


class CodeFileEntry(CodeGraphContract):
    schema_name: Literal["CodeFileEntryV1"] = "CodeFileEntryV1"
    path: RelativePath
    content_sha256: Sha256
    size_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    mtime_ns: int = Field(ge=0)
    language: Identifier
    extractor: Identifier
    extractor_version: str = Field(min_length=1, max_length=128)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"mtime_ns"})


class CodeNode(CodeGraphContract):
    schema_name: Literal["CodeNodeV1"] = "CodeNodeV1"
    node_id: Identifier
    kind: CodeNodeKind
    label: str = Field(min_length=1, max_length=256)
    qualified_name: str = Field(default="", max_length=1024)
    path: RelativePath | None = None
    location: SourceLocation | None = None
    content_fingerprint: Sha256
    extractor: Identifier
    extractor_version: str = Field(min_length=1, max_length=128)
    community_id: int | None = Field(default=None, ge=0, le=1_000_000)
    is_god: bool = False
    is_bridge: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    _validate_metadata = field_validator("metadata")(_bounded_metadata)

    @model_validator(mode="after")
    def _source_shape(self) -> CodeNode:
        if self.location is not None and self.path is None:
            raise ValueError("Located code nodes require a workspace-relative path")
        return self


class CodeEdge(CodeGraphContract):
    schema_name: Literal["CodeEdgeV1"] = "CodeEdgeV1"
    edge_id: Identifier
    source: Identifier
    target: Identifier
    relation: CodeRelation
    confidence: EdgeConfidence
    source_file: RelativePath
    source_location: SourceLocation | None = None
    extractor: Identifier
    extractor_version: str = Field(min_length=1, max_length=128)
    content_fingerprint: Sha256
    metadata: dict[str, str] = Field(default_factory=dict)

    _validate_metadata = field_validator("metadata")(_bounded_metadata)


class CodeCommunity(CodeGraphContract):
    schema_name: Literal["CodeCommunityV1"] = "CodeCommunityV1"
    community_id: int = Field(ge=0, le=1_000_000)
    label: str = Field(min_length=1, max_length=256)
    node_ids: tuple[Identifier, ...]
    cohesion_ppm: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("node_ids")
    @classmethod
    def _unique_nodes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("Community nodes must be non-empty and unique")
        return value


class CodeGraphMetrics(CodeGraphContract):
    schema_name: Literal["CodeGraphMetricsV1"] = "CodeGraphMetricsV1"
    processed_files: int = Field(default=0, ge=0)
    skipped_files: int = Field(default=0, ge=0)
    deleted_files: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    node_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    unresolved_references: int = Field(default=0, ge=0)
    ambiguous_edges: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class CodeGraphSnapshot(CodeGraphContract):
    schema_name: Literal["CodeGraphSnapshotV1"] = "CodeGraphSnapshotV1"
    snapshot_id: Identifier
    logical_fingerprint: Sha256
    manifest_fingerprint: Sha256
    config_fingerprint: Sha256
    extractor_fingerprint: Sha256
    files: tuple[CodeFileEntry, ...]
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]
    communities: tuple[CodeCommunity, ...] = ()
    metrics: CodeGraphMetrics = Field(default_factory=CodeGraphMetrics)
    git_head: str | None = Field(default=None, max_length=128)
    git_branch: str | None = Field(default=None, max_length=256)
    dirty: bool = False
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        manifest_fingerprint: str,
        config_fingerprint: str,
        extractor_fingerprint: str,
        files: tuple[CodeFileEntry, ...],
        nodes: tuple[CodeNode, ...],
        edges: tuple[CodeEdge, ...],
        communities: tuple[CodeCommunity, ...],
        metrics: CodeGraphMetrics,
        git_head: str | None,
        git_branch: str | None,
        dirty: bool,
        created_at: datetime,
    ) -> CodeGraphSnapshot:
        identity = cls._identity_material(
            manifest_fingerprint=manifest_fingerprint,
            config_fingerprint=config_fingerprint,
            extractor_fingerprint=extractor_fingerprint,
            files=files,
            nodes=nodes,
            edges=edges,
            communities=communities,
        )
        logical_fingerprint = sha256_hex(canonical_json_bytes(identity))
        return cls(
            snapshot_id=derive_id("cgraph", {"sha256": logical_fingerprint}),
            logical_fingerprint=logical_fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            config_fingerprint=config_fingerprint,
            extractor_fingerprint=extractor_fingerprint,
            files=files,
            nodes=nodes,
            edges=edges,
            communities=communities,
            metrics=metrics,
            git_head=git_head,
            git_branch=git_branch,
            dirty=dirty,
            created_at=created_at,
        )

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _closure(self) -> CodeGraphSnapshot:
        paths = [entry.path for entry in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("Code graph file entries must be sorted and unique")
        node_ids = [node.node_id for node in self.nodes]
        if node_ids != sorted(node_ids) or len(node_ids) != len(set(node_ids)):
            raise ValueError("Code graph nodes must be sorted and unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if edge_ids != sorted(edge_ids) or len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Code graph edges must be sorted and unique")
        known = set(node_ids)
        if any(edge.source not in known or edge.target not in known for edge in self.edges):
            raise ValueError("Code graph edges must form a closed graph")
        if self.metrics.node_count != len(self.nodes) or self.metrics.edge_count != len(self.edges):
            raise ValueError("Code graph metrics do not match the snapshot")
        community_members = {
            node_id for community in self.communities for node_id in community.node_ids
        }
        if not community_members.issubset(known):
            raise ValueError("Code graph communities reference unknown nodes")
        community_ids = {community.community_id for community in self.communities}
        if len(community_ids) != len(self.communities):
            raise ValueError("Code graph community IDs must be unique")
        if any(
            node.community_id is not None and node.community_id not in community_ids
            for node in self.nodes
        ):
            raise ValueError("Code graph nodes reference unknown communities")
        if self.logical_fingerprint != sha256_hex(canonical_json_bytes(self.identity_payload())):
            raise ValueError("Code graph logical fingerprint is invalid")
        if not self.verify_id():
            raise ValueError("Code graph snapshot ID is invalid")
        return self

    def identity_payload(self) -> dict[str, Any]:
        return self._identity_material(
            manifest_fingerprint=self.manifest_fingerprint,
            config_fingerprint=self.config_fingerprint,
            extractor_fingerprint=self.extractor_fingerprint,
            files=self.files,
            nodes=self.nodes,
            edges=self.edges,
            communities=self.communities,
        )

    @staticmethod
    def _identity_material(
        *,
        manifest_fingerprint: str,
        config_fingerprint: str,
        extractor_fingerprint: str,
        files: tuple[CodeFileEntry, ...],
        nodes: tuple[CodeNode, ...],
        edges: tuple[CodeEdge, ...],
        communities: tuple[CodeCommunity, ...],
    ) -> dict[str, Any]:
        return {
            "manifest_fingerprint": manifest_fingerprint,
            "config_fingerprint": config_fingerprint,
            "extractor_fingerprint": extractor_fingerprint,
            "files": [entry.identity_payload() for entry in files],
            "nodes": nodes,
            "edges": edges,
            "communities": communities,
        }

    def verify_id(self) -> bool:
        return self.snapshot_id == derive_id("cgraph", {"sha256": self.logical_fingerprint})


class CodeGraphManifest(CodeGraphContract):
    schema_name: Literal["CodeGraphManifestV1"] = "CodeGraphManifestV1"
    manifest_id: Identifier
    snapshot_id: Identifier
    snapshot_sha256: Sha256
    config_fingerprint: Sha256
    extractor_fingerprint: Sha256
    files: tuple[CodeFileEntry, ...]
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        snapshot_sha256: str,
        config_fingerprint: str,
        extractor_fingerprint: str,
        files: tuple[CodeFileEntry, ...],
        created_at: datetime,
    ) -> CodeGraphManifest:
        identity = cls._identity_material(
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
            config_fingerprint=config_fingerprint,
            extractor_fingerprint=extractor_fingerprint,
            files=files,
        )
        return cls(
            manifest_id=derive_id("cgmanifest", identity),
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
            config_fingerprint=config_fingerprint,
            extractor_fingerprint=extractor_fingerprint,
            files=files,
            created_at=created_at,
        )

    @field_validator("created_at")
    @classmethod
    def _manifest_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self._identity_material(
            snapshot_id=self.snapshot_id,
            snapshot_sha256=self.snapshot_sha256,
            config_fingerprint=self.config_fingerprint,
            extractor_fingerprint=self.extractor_fingerprint,
            files=self.files,
        )

    @staticmethod
    def _identity_material(
        *,
        snapshot_id: str,
        snapshot_sha256: str,
        config_fingerprint: str,
        extractor_fingerprint: str,
        files: tuple[CodeFileEntry, ...],
    ) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_sha256,
            "config_fingerprint": config_fingerprint,
            "extractor_fingerprint": extractor_fingerprint,
            "files": files,
        }

    def verify_id(self) -> bool:
        return self.manifest_id == derive_id("cgmanifest", self.identity_payload())


class CodeGraphStatus(CodeGraphContract):
    schema_name: Literal["CodeGraphStatusV1"] = "CodeGraphStatusV1"
    state: CodeGraphState
    snapshot_id: Identifier | None = None
    snapshot_fingerprint: Sha256 | None = None
    file_count: int = Field(default=0, ge=0)
    node_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    ambiguous_edges: int = Field(default=0, ge=0)
    changed_files: int = Field(default=0, ge=0)
    deleted_files: int = Field(default=0, ge=0)
    changed_paths: tuple[RelativePath, ...] = Field(default=(), max_length=500)
    deleted_paths: tuple[RelativePath, ...] = Field(default=(), max_length=500)
    paths_truncated: bool = False
    reason: str | None = Field(default=None, max_length=512)

    @field_validator("changed_paths", "deleted_paths")
    @classmethod
    def _ordered_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("Code graph status paths must be sorted and unique")
        return value


class CodeGraphQueryNode(CodeGraphContract):
    """Compact context slice of one CodeNode; full provenance stays in the snapshot."""

    schema_name: Literal["GraphQueryNodeV1"] = "GraphQueryNodeV1"
    node_id: Identifier
    kind: CodeNodeKind
    label: str = Field(min_length=1, max_length=256)
    qualified_name: str = Field(default="", max_length=1024)
    path: RelativePath | None = None
    location: SourceLocation | None = None
    community_id: int | None = Field(default=None, ge=0, le=1_000_000)
    is_god: bool = False
    is_bridge: bool = False
    depth: int = Field(default=0, ge=0, le=16)

    @classmethod
    def from_node(cls, node: CodeNode, *, depth: int) -> CodeGraphQueryNode:
        return cls(
            node_id=node.node_id,
            kind=node.kind,
            label=node.label,
            qualified_name=node.qualified_name,
            path=node.path,
            location=node.location,
            community_id=node.community_id,
            is_god=node.is_god,
            is_bridge=node.is_bridge,
            depth=depth,
        )


class CodeGraphQueryEdge(CodeGraphContract):
    """Compact context slice of one CodeEdge.

    Source file, location and extractor provenance stay on the snapshot edge and
    remain addressable through the stable ``edge_id``.
    """

    schema_name: Literal["GraphQueryEdgeV1"] = "GraphQueryEdgeV1"
    edge_id: Identifier
    source: Identifier
    target: Identifier
    relation: CodeRelation
    confidence: EdgeConfidence

    @classmethod
    def from_edge(cls, edge: CodeEdge) -> CodeGraphQueryEdge:
        return cls(
            edge_id=edge.edge_id,
            source=edge.source,
            target=edge.target,
            relation=edge.relation,
            confidence=edge.confidence,
        )


class CodeGraphQueryResult(CodeGraphContract):
    schema_name: Literal["GraphQueryResultV1"] = "GraphQueryResultV1"
    operation: Literal["query", "explain", "neighbors", "path", "impact"]
    snapshot_id: Identifier
    snapshot_fingerprint: Sha256
    query_sha256: Sha256
    nodes: tuple[CodeGraphQueryNode, ...]
    edges: tuple[CodeGraphQueryEdge, ...]
    seed_node_ids: tuple[Identifier, ...] = ()
    ordered_node_ids: tuple[Identifier, ...] = ()
    truncated: bool = False
    estimated_context_bytes: int = Field(default=0, ge=0)
    fallback_reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _result_closure(self) -> CodeGraphQueryResult:
        node_ids = {node.node_id for node in self.nodes}
        if any(edge.source not in node_ids or edge.target not in node_ids for edge in self.edges):
            raise ValueError("Graph query edges must resolve inside the result")
        if any(seed not in node_ids for seed in self.seed_node_ids):
            raise ValueError("Graph query seeds must resolve inside the result")
        if any(node_id not in node_ids for node_id in self.ordered_node_ids):
            raise ValueError("Ordered graph nodes must resolve inside the result")
        return self


# Internal compatibility alias; v1.4 exports the namespaced public contract.
GraphQueryResult = CodeGraphQueryResult
