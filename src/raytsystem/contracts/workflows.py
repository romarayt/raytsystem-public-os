from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import Identifier, NonEmptyStr, Sha256, VersionedModel


class WorkflowNodeType(StrEnum):
    TASK = "task"
    AGENT = "agent"
    DETERMINISTIC_COMMAND = "deterministic_command"
    REVIEW = "review"
    APPROVAL = "approval"
    CONDITION = "condition"
    WAIT = "wait"
    ARTIFACT = "artifact"
    NOTIFICATION = "notification"
    SUBWORKFLOW = "subworkflow"


class WorkflowTrigger(VersionedModel):
    schema_name: Literal["WorkflowTriggerV1"] = "WorkflowTriggerV1"
    trigger_id: Identifier
    kind: Literal["manual", "task_event", "schedule", "webhook_proposal"]
    configuration_sha256: Sha256
    enabled: bool = True


class WorkflowCondition(VersionedModel):
    schema_name: Literal["WorkflowConditionV1"] = "WorkflowConditionV1"
    condition_id: Identifier
    operation: Literal["equals", "not_equals", "contains", "exists", "all_passed", "any_failed"]
    input_name: Identifier
    expected: Any = None


class WorkflowRetryPolicy(VersionedModel):
    schema_name: Literal["WorkflowRetryPolicyV1"] = "WorkflowRetryPolicyV1"
    retry_policy_id: Identifier
    max_attempts: int = Field(default=1, ge=1, le=20)
    initial_delay_ms: int = Field(default=0, ge=0, le=300_000)
    maximum_delay_ms: int = Field(default=0, ge=0, le=3_600_000)
    backoff: Literal["none", "linear", "exponential"] = "none"
    retryable_error_codes: tuple[Identifier, ...] = ()


class WorkflowApprovalGate(VersionedModel):
    schema_name: Literal["WorkflowApprovalGateV1"] = "WorkflowApprovalGateV1"
    approval_gate_id: Identifier
    action: Identifier
    scope_sha256: Sha256
    required_role: Identifier
    expires_after_seconds: int = Field(ge=1, le=604_800)


class WorkflowNode(VersionedModel):
    schema_name: Literal["WorkflowNodeV1"] = "WorkflowNodeV1"
    node_id: Identifier
    node_type: WorkflowNodeType
    name: NonEmptyStr
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256
    operation_id: Identifier | None = None
    agent_id: Identifier | None = None
    subworkflow_id: Identifier | None = None
    condition_id: Identifier | None = None
    approval_gate_id: Identifier | None = None
    retry_policy_id: Identifier | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    idempotency_scope: Literal["workflow_run", "task", "global"] = "workflow_run"

    @model_validator(mode="after")
    def _node_invariants(self) -> WorkflowNode:
        required = {
            WorkflowNodeType.DETERMINISTIC_COMMAND: self.operation_id,
            WorkflowNodeType.AGENT: self.agent_id,
            WorkflowNodeType.SUBWORKFLOW: self.subworkflow_id,
            WorkflowNodeType.CONDITION: self.condition_id,
            WorkflowNodeType.APPROVAL: self.approval_gate_id,
        }
        if self.node_type in required and required[self.node_type] is None:
            raise ValueError(f"{self.node_type.value} nodes require a typed reference")
        return self


class WorkflowEdge(VersionedModel):
    schema_name: Literal["WorkflowEdgeV1"] = "WorkflowEdgeV1"
    edge_id: Identifier
    source_node_id: Identifier
    target_node_id: Identifier
    source_output: Identifier = "result"
    target_input: Identifier = "input"
    condition_id: Identifier | None = None

    @model_validator(mode="after")
    def _edge_invariants(self) -> WorkflowEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("Workflow self-cycles are forbidden")
        return self


class WorkflowArtifactBinding(VersionedModel):
    schema_name: Literal["WorkflowArtifactBindingV1"] = "WorkflowArtifactBindingV1"
    binding_id: Identifier
    node_id: Identifier
    output_name: Identifier
    artifact_type: Identifier
    required: bool = True


class WorkflowDefinition(VersionedModel):
    schema_name: Literal["WorkflowDefinitionV1"] = "WorkflowDefinitionV1"
    workflow_id: Identifier
    name: NonEmptyStr
    description: NonEmptyStr
    current_revision_id: Identifier | None = None
    enabled: bool = False


class WorkflowRevision(VersionedModel):
    schema_name: Literal["WorkflowRevisionV1"] = "WorkflowRevisionV1"
    revision_id: Identifier
    workflow_id: Identifier
    version: NonEmptyStr
    trigger_ids: tuple[Identifier, ...]
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    condition_ids: tuple[Identifier, ...] = ()
    approval_gate_ids: tuple[Identifier, ...] = ()
    retry_policy_ids: tuple[Identifier, ...] = ()
    artifact_binding_ids: tuple[Identifier, ...] = ()
    manifest_sha256: Sha256
    previous_revision_id: Identifier | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _revision_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _revision_invariants(self) -> WorkflowRevision:
        node_ids = [node.node_id for node in self.nodes]
        if not node_ids or len(node_ids) != len(set(node_ids)):
            raise ValueError("Workflow node IDs must be non-empty and unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Workflow edge IDs must be unique")
        known = set(node_ids)
        if any(
            edge.source_node_id not in known or edge.target_node_id not in known
            for edge in self.edges
        ):
            raise ValueError("Workflow edges must reference known nodes")
        return self


class WorkflowRun(VersionedModel):
    schema_name: Literal["WorkflowRunV1"] = "WorkflowRunV1"
    workflow_run_id: Identifier
    revision_id: Identifier
    state: Literal["planned", "running", "paused", "cancelled", "succeeded", "failed"]
    input_sha256: Sha256
    step_run_ids: tuple[Identifier, ...] = ()
    replay_of_run_id: Identifier | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _run_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class WorkflowStepRun(VersionedModel):
    schema_name: Literal["WorkflowStepRunV1"] = "WorkflowStepRunV1"
    step_run_id: Identifier
    workflow_run_id: Identifier
    node_id: Identifier
    state: Literal[
        "pending",
        "running",
        "waiting",
        "paused",
        "skipped",
        "succeeded",
        "failed",
        "cancelled",
    ]
    attempt: int = Field(default=1, ge=1, le=20)
    idempotency_key: Identifier
    input_sha256: Sha256
    output_sha256: Sha256 | None = None
    approval_id: Identifier | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _step_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class NotificationType(StrEnum):
    APPROVAL_NEEDED = "approval_needed"
    EMPLOYEE_BLOCKED = "employee_blocked"
    BUDGET_EXCEEDED = "budget_exceeded"
    RUN_FAILED = "run_failed"
    REVIEW_READY = "review_ready"
    EVAL_REGRESSION = "eval_regression"
    STALE_GRAPH = "stale_graph"
    INGESTION_FAILED = "ingestion_failed"
    SECURITY_FINDING = "security_finding"
    PACKAGE_UPDATE = "package_update"
    MIGRATION_REQUIRED = "migration_required"


class Notification(VersionedModel):
    schema_name: Literal["NotificationV1"] = "NotificationV1"
    notification_id: Identifier
    notification_type: NotificationType
    severity: Literal["info", "low", "medium", "high", "critical"]
    title: NonEmptyStr
    message: NonEmptyStr
    related_kind: Identifier
    related_id: Identifier
    origin_event_id: Identifier
    deduplication_key: Identifier
    state: Literal["unread", "read", "acknowledged", "resolved"] = "unread"
    created_at: AwareDatetime
    acknowledged_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None

    @field_validator("created_at", "acknowledged_at", "resolved_at")
    @classmethod
    def _notification_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class NotificationOutbox(VersionedModel):
    schema_name: Literal["NotificationOutboxV1"] = "NotificationOutboxV1"
    outbox_id: Identifier
    notification_id: Identifier
    adapter: Literal["webhook", "telegram", "slack", "email"]
    destination_id: Identifier
    payload_sha256: Sha256
    preview_sha256: Sha256
    policy_decision_id: Identifier
    approval_id: Identifier
    idempotency_key: Identifier
    state: Literal["draft", "approved", "sending", "sent", "failed", "dead_letter"]
    attempt_count: int = Field(default=0, ge=0, le=20)
    redacted: Literal[True] = True
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _outbox_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
