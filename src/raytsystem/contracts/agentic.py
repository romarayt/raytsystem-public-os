from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sensitivity,
    Sha256,
    TrustClass,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


def _validate_catalog_path(value: str) -> str:
    normalized = PurePosixPath(value).as_posix()
    if normalized in {"AGENTS.md", "WORK.md", "CLAUDE.md"}:
        return normalized
    path = PurePosixPath(normalized)
    if path.parts and path.parts[0] in {"packs", "skills"}:
        return normalized
    raise ValueError("Catalog context paths must stay in allowlisted instruction roots")


CatalogPath = Annotated[RelativePath, AfterValidator(_validate_catalog_path)]


class RuntimeAdapterState(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    CONFIGURED = "configured"
    DEGRADED = "degraded"


class TaskStatus(StrEnum):
    INBOX = "inbox"
    PLANNED = "planned"
    READY = "ready"
    RUNNING = "running"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskEventKind(StrEnum):
    CREATED = "created"
    TRANSITIONED = "transitioned"
    CANCELLED = "cancelled"


class GraphLens(StrEnum):
    UNIVERSE = "universe"
    KNOWLEDGE = "knowledge"
    WORK = "work"
    AGENT = "agent"
    EVIDENCE = "evidence"
    CODE = "code"


class PackManifest(VersionedModel):
    schema_name: Literal["PackManifestV1"] = "PackManifestV1"
    pack_id: Identifier
    name: NonEmptyStr
    version: NonEmptyStr
    description: NonEmptyStr
    license_expression: NonEmptyStr
    trust_class: TrustClass
    agent_ids: tuple[Identifier, ...] = ()
    skill_ids: tuple[Identifier, ...] = ()
    context_paths: tuple[CatalogPath, ...] = ()
    optional: bool = True


class AgentDefinition(VersionedModel):
    schema_name: Literal["AgentDefinitionV1"] = "AgentDefinitionV1"
    agent_id: Identifier
    name: NonEmptyStr
    role: Identifier
    description: NonEmptyStr
    version: NonEmptyStr
    pack_id: Identifier
    runtime_adapter_id: Identifier
    skill_ids: tuple[Identifier, ...] = ()
    context_paths: tuple[CatalogPath, ...] = ()
    capabilities: tuple[Identifier, ...] = ()
    requested_filesystem_mode: Literal["none", "read_only", "staging_only"] = "none"
    approved_data_classes: tuple[Identifier, ...] = ()
    egress_destination: NonEmptyStr | None = None
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    enabled: bool = False


class SkillDefinition(VersionedModel):
    schema_name: Literal["SkillDefinitionV1"] = "SkillDefinitionV1"
    skill_id: Identifier
    name: NonEmptyStr
    description: NonEmptyStr
    version: NonEmptyStr
    source_path: CatalogPath
    source_sha256: Sha256
    pack_id: Identifier
    trust_class: TrustClass
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    permissions: tuple[Identifier, ...] = ()
    test_status: Literal["pass", "pending", "unavailable"] = "pending"
    enabled: bool = True


class InstructionDocument(VersionedModel):
    schema_name: Literal["InstructionDocumentV1"] = "InstructionDocumentV1"
    document_id: Identifier
    kind: Identifier
    label: NonEmptyStr
    path: CatalogPath
    content_sha256: Sha256
    size_bytes: int = Field(ge=0, le=1024 * 1024)
    sensitivity: Sensitivity
    editable: bool = False


class RuntimeAdapterDefinition(VersionedModel):
    schema_name: Literal["RuntimeAdapterDefinitionV1"] = "RuntimeAdapterDefinitionV1"
    adapter_id: Identifier
    name: NonEmptyStr
    version: NonEmptyStr
    state: RuntimeAdapterState
    isolation_mode: Identifier
    capabilities: tuple[Identifier, ...] = ()
    egress_destination: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None


class ContextSnapshot(VersionedModel):
    schema_name: Literal["ContextSnapshotV1"] = "ContextSnapshotV1"
    context_snapshot_id: Identifier
    agent_id: Identifier
    instruction_hashes: dict[Identifier, Sha256]
    skill_hashes: dict[Identifier, Sha256]
    policy_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"context_snapshot_id"})

    def verify_id(self) -> bool:
        return self.context_snapshot_id == derive_id("ctx", self.identity_payload())


class AgentInvocation(VersionedModel):
    schema_name: Literal["AgentInvocationV1"] = "AgentInvocationV1"
    invocation_id: Identifier
    task_id: Identifier
    agent_id: Identifier
    agent_definition_sha256: Sha256
    runtime_adapter_sha256: Sha256
    context_snapshot_sha256: Sha256
    skill_definition_hashes: dict[Identifier, Sha256]
    policy_decision_id: Identifier
    policy_decision_sha256: Sha256
    idempotency_key: Identifier
    requested_capabilities: tuple[Identifier, ...]
    granted_capabilities: tuple[Identifier, ...]
    data_class: Identifier
    egress_destination: NonEmptyStr | None = None
    approval_id: Identifier | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _invocation_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _invocation_invariants(self) -> AgentInvocation:
        if not set(self.granted_capabilities).issubset(self.requested_capabilities):
            raise ValueError("Granted capabilities must be a subset of requested capabilities")
        if len(set(self.requested_capabilities)) != len(self.requested_capabilities):
            raise ValueError("Requested capabilities must be unique")
        if len(set(self.granted_capabilities)) != len(self.granted_capabilities):
            raise ValueError("Granted capabilities must be unique")
        return self

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"invocation_id"})

    def verify_id(self) -> bool:
        return self.invocation_id == derive_id("ainv", self.identity_payload())


class AgentTask(VersionedModel):
    schema_name: Literal["AgentTaskV1"] = "AgentTaskV1"
    task_id: Identifier
    title: NonEmptyStr
    description: str = Field(default="", max_length=32_768)
    status: TaskStatus = TaskStatus.INBOX
    priority: TaskPriority = TaskPriority.NORMAL
    project_id: Identifier = "project_default"
    mission_id: Identifier | None = None
    assignee_ids: tuple[Identifier, ...] = ()
    skill_ids: tuple[Identifier, ...] = ()
    dependency_ids: tuple[Identifier, ...] = ()
    artifact_ids: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()
    blocked_reason: NonEmptyStr | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    created_by: Identifier
    revision: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _task_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _task_invariants(self) -> AgentTask:
        collection_fields = {
            "assignee_ids": self.assignee_ids,
            "skill_ids": self.skill_ids,
            "dependency_ids": self.dependency_ids,
            "artifact_ids": self.artifact_ids,
            "tags": self.tags,
        }
        if any(len(values) != len(set(values)) for values in collection_fields.values()):
            raise ValueError("Task reference collections must contain unique IDs")
        if self.task_id in self.dependency_ids:
            raise ValueError("A task cannot depend on itself")
        if self.updated_at < self.created_at:
            raise ValueError("Task update cannot predate creation")
        if self.status is TaskStatus.BLOCKED and self.blocked_reason is None:
            raise ValueError("Blocked tasks require a reason")
        if self.status is not TaskStatus.BLOCKED and self.blocked_reason is not None:
            raise ValueError("Only blocked tasks may carry a blocked reason")
        return self


class TaskEvent(VersionedModel):
    schema_name: Literal["TaskEventV1"] = "TaskEventV1"
    event_id: Identifier
    event_kind: TaskEventKind
    task_id: Identifier
    operation_key: Identifier
    idempotency_key: Identifier
    previous_event_id: Identifier | None = None
    previous_task_sha256: Sha256 | None = None
    task_sha256: Sha256
    from_status: TaskStatus | None = None
    to_status: TaskStatus
    actor: Identifier
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _event_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"event_id"})

    def verify_id(self) -> bool:
        return self.event_id == derive_id("tevt", self.identity_payload())

    @model_validator(mode="after")
    def _event_invariants(self) -> TaskEvent:
        if self.event_kind is TaskEventKind.CREATED:
            if self.from_status is not None or self.previous_task_sha256 is not None:
                raise ValueError("Created task events cannot reference a prior task state")
            if self.to_status is not TaskStatus.INBOX:
                raise ValueError("Created task events must enter the inbox")
        elif self.from_status is None or self.previous_task_sha256 is None:
            raise ValueError("Task transitions require the prior task state")
        if self.event_kind is TaskEventKind.CANCELLED:
            if self.to_status is not TaskStatus.CANCELLED:
                raise ValueError("Cancelled events must enter the cancelled state")
        elif self.to_status is TaskStatus.CANCELLED:
            raise ValueError("Cancelled task states require a cancelled event")
        return self


class TaskBoardGeneration(VersionedModel):
    schema_name: Literal["TaskBoardGenerationV1"] = "TaskBoardGenerationV1"
    generation_id: Identifier
    parent_generation_id: Identifier | None = None
    parent_generation_sha256: Sha256 | None = None
    task_hashes: dict[Identifier, Sha256] = Field(default_factory=dict)
    latest_event_ids: dict[Identifier, Identifier] = Field(default_factory=dict)
    head_event_id: Identifier | None = None
    event_count: int = Field(default=0, ge=0, le=10_000)
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _generation_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"generation_id"})

    def verify_id(self) -> bool:
        return self.generation_id == derive_id("tgen", self.identity_payload())

    def manifest_sha256(self) -> str:
        return sha256_hex(canonical_json_bytes(self))

    @model_validator(mode="after")
    def _board_invariants(self) -> TaskBoardGeneration:
        if (self.parent_generation_id is None) != (self.parent_generation_sha256 is None):
            raise ValueError("Parent generation ID and hash must be present together")
        if self.parent_generation_id is None and self.event_count not in {0, 1}:
            raise ValueError("A root task generation can contain at most its creation event")
        if self.task_hashes and self.event_count < len(self.task_hashes):
            raise ValueError("Task event count cannot be smaller than the task count")
        if set(self.task_hashes) != set(self.latest_event_ids):
            raise ValueError("Task and latest-event indexes must have identical keys")
        if not self.task_hashes and self.head_event_id is not None:
            raise ValueError("An empty task board cannot have an event head")
        if self.task_hashes and self.head_event_id is None:
            raise ValueError("A non-empty task board requires an event head")
        if (
            self.head_event_id is not None
            and self.head_event_id not in self.latest_event_ids.values()
        ):
            raise ValueError("The event head must be the latest event for one task")
        return self


class RunSummary(VersionedModel):
    schema_name: Literal["RunSummaryV1"] = "RunSummaryV1"
    run_id: Identifier
    operation_type: Identifier
    state: Identifier
    generation_id: Identifier | None = None
    semantic_noop: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime
    manifest_sha256: Sha256

    @field_validator("created_at", "updated_at")
    @classmethod
    def _run_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _run_time_order(self) -> RunSummary:
        if self.updated_at < self.created_at:
            raise ValueError("Run update cannot predate creation")
        return self


class GraphNodeView(VersionedModel):
    schema_name: Literal["GraphNodeViewV1"] = "GraphNodeViewV1"
    node_id: Identifier
    kind: Identifier
    label: NonEmptyStr
    subtitle: str = Field(default="", max_length=4096)
    status: Identifier
    ring: Identifier
    importance: int = Field(default=1, ge=1, le=100)
    x: int = Field(ge=-100_000, le=100_000)
    y: int = Field(ge=-100_000, le=100_000)
    recorded_at: AwareDatetime | None = None
    source_ref: Identifier | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("recorded_at")
    @classmethod
    def _node_recorded_at_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class GraphEdgeView(VersionedModel):
    schema_name: Literal["GraphEdgeViewV1"] = "GraphEdgeViewV1"
    edge_id: Identifier
    source: Identifier
    target: Identifier
    kind: Identifier
    status: Identifier = "active"
    directed: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class GraphSnapshot(VersionedModel):
    schema_name: Literal["GraphSnapshotV1"] = "GraphSnapshotV1"
    graph_snapshot_id: Identifier
    knowledge_generation_id: Identifier
    knowledge_generation_sha256: Sha256
    task_generation_id: Identifier | None = None
    task_generation_sha256: Sha256 | None = None
    catalog_sha256: Sha256
    code_snapshot_id: Identifier | None = None
    code_snapshot_sha256: Sha256 | None = None
    code_snapshot_fingerprint: Sha256 | None = None
    code_graph_state: Identifier = "missing"
    code_file_count: int = Field(default=0, ge=0)
    code_node_count: int = Field(default=0, ge=0)
    code_edge_count: int = Field(default=0, ge=0)
    code_ambiguous_edges: int = Field(default=0, ge=0)
    code_view_node_count: int = Field(default=0, ge=0)
    code_view_edge_count: int = Field(default=0, ge=0)
    code_view_truncated: bool = False
    nodes: tuple[GraphNodeView, ...]
    edges: tuple[GraphEdgeView, ...]
    supported_lenses: tuple[GraphLens, ...]
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _graph_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _edge_closure(self) -> GraphSnapshot:
        if (self.task_generation_id is None) != (self.task_generation_sha256 is None):
            raise ValueError("Task generation ID and hash must be present together")
        code_identity = (
            self.code_snapshot_id,
            self.code_snapshot_sha256,
            self.code_snapshot_fingerprint,
        )
        if any(value is None for value in code_identity) and any(
            value is not None for value in code_identity
        ):
            raise ValueError(
                "Code snapshot ID, object hash and fingerprint must be present together"
            )
        if self.code_snapshot_id is None and any(
            (
                self.code_file_count,
                self.code_node_count,
                self.code_edge_count,
                self.code_ambiguous_edges,
                self.code_view_node_count,
                self.code_view_edge_count,
            )
        ):
            raise ValueError("Code graph counts require a bound code snapshot")
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("Graph node IDs must be unique")
        edge_ids = {edge.edge_id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("Graph edge IDs must be unique")
        if any(edge.source not in node_ids or edge.target not in node_ids for edge in self.edges):
            raise ValueError("Graph edges must resolve to nodes")
        return self

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"graph_snapshot_id"})

    def verify_id(self) -> bool:
        return self.graph_snapshot_id == derive_id("graph", self.identity_payload())
