from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sha256,
    VersionedModel,
    derive_id,
)


class EmployeeStatus(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    ASSIGNED = "assigned"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    ERROR = "error"
    TERMINATED = "terminated"


class WorkspaceMode(StrEnum):
    WORKSPACE_ROOT_READONLY = "workspace_root_readonly"
    TASK_WORKTREE = "task_worktree"
    APPROVED_EXTERNAL_ROOT = "approved_external_root"


class WorkspaceStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    ACTIVE = "active"
    STALE = "stale"
    BLOCKED = "blocked"


class TaskLeaseStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    RELEASED = "released"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ExecutionRunStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    REVIEW = "review"
    BLOCKED = "blocked"
    FAILED = "failed"


class ExecutionSessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    INCOMPATIBLE = "incompatible"
    ERROR = "error"


class CommentKind(StrEnum):
    COMMENT = "comment"
    PROGRESS = "progress"
    DECISION = "decision"
    QUESTION = "question"
    BLOCKED = "blocked"
    HANDOFF = "handoff"
    REVIEW = "review"
    SYSTEM = "system"


class RuntimeHealthStatus(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class FilesystemPolicy(VersionedModel):
    schema_name: Literal["FilesystemPolicyV1"] = "FilesystemPolicyV1"
    mode: WorkspaceMode = WorkspaceMode.TASK_WORKTREE
    allow_workspace_read: bool = True
    allow_staged_write: bool = True
    allow_git_read: bool = True
    allow_git_write: bool = False
    approved_external_root_id: Identifier | None = None

    @model_validator(mode="after")
    def _external_root_binding(self) -> FilesystemPolicy:
        if self.mode is WorkspaceMode.APPROVED_EXTERNAL_ROOT:
            if self.approved_external_root_id is None:
                raise ValueError("Approved external mode requires a typed root ID")
        elif self.approved_external_root_id is not None:
            raise ValueError("Only approved external mode may carry an external root ID")
        if self.mode is WorkspaceMode.WORKSPACE_ROOT_READONLY and self.allow_staged_write:
            raise ValueError("Readonly workspace mode cannot allow staged writes")
        return self


class GraphPolicy(VersionedModel):
    schema_name: Literal["GraphPolicyV1"] = "GraphPolicyV1"
    max_depth: int = Field(default=2, ge=0, le=8)
    max_nodes: int = Field(default=40, ge=1, le=500)
    max_edges: int = Field(default=100, ge=0, le=2_000)
    max_bytes: int = Field(default=48_000, ge=1_024, le=2_000_000)
    include_relations: tuple[Identifier, ...] = (
        "calls",
        "imports",
        "tests",
        "depends_on",
        "implemented_by",
    )

    @field_validator("include_relations")
    @classmethod
    def _unique_relations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Graph relation allowlist must be unique")
        return value


class HeartbeatPolicy(VersionedModel):
    schema_name: Literal["HeartbeatPolicyV1"] = "HeartbeatPolicyV1"
    manual_enabled: bool = True
    wake_on_assignment: bool = False
    wake_on_comment: bool = False
    scheduled_enabled: bool = False
    interval_seconds: int = Field(default=0, ge=0, le=30 * 24 * 60 * 60)
    max_duration_seconds: int = Field(default=3_600, ge=1, le=24 * 60 * 60)
    max_idle_seconds: int = Field(default=900, ge=1, le=24 * 60 * 60)
    max_consecutive_failures: int = Field(default=3, ge=1, le=100)
    max_without_progress: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def _schedule_shape(self) -> HeartbeatPolicy:
        if self.scheduled_enabled != (self.interval_seconds > 0):
            raise ValueError("Scheduled heartbeat state and interval must agree")
        return self


class TokenBudget(VersionedModel):
    schema_name: Literal["TokenBudgetV1"] = "TokenBudgetV1"
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens


class BudgetPolicy(VersionedModel):
    schema_name: Literal["BudgetPolicyV1"] = "BudgetPolicyV1"
    budget_policy_id: Identifier
    scope_kind: Literal["employee", "task", "project", "run", "workspace"]
    scope_id: Identifier
    token_limit: TokenBudget = Field(default_factory=TokenBudget)
    cost_limit_micros: int | None = Field(default=None, ge=0)
    run_limit: int = Field(default=0, ge=0)
    heartbeat_limit: int = Field(default=0, ge=0)
    active_run_action: Literal["finish_current", "cancel"] = "cancel"
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @classmethod
    def create(
        cls,
        *,
        scope_kind: Literal["employee", "task", "project", "run", "workspace"],
        scope_id: str,
        token_limit: TokenBudget,
        created_at: datetime,
        cost_limit_micros: int | None = None,
        run_limit: int = 0,
        heartbeat_limit: int = 0,
        active_run_action: Literal["finish_current", "cancel"] = "cancel",
    ) -> BudgetPolicy:
        material = {
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "token_limit": token_limit,
            "cost_limit_micros": cost_limit_micros,
            "run_limit": run_limit,
            "heartbeat_limit": heartbeat_limit,
            "active_run_action": active_run_action,
            "created_at": created_at,
        }
        return cls(
            budget_policy_id=derive_id("budget", material),
            scope_kind=scope_kind,
            scope_id=scope_id,
            token_limit=token_limit,
            created_at=created_at,
            cost_limit_micros=cost_limit_micros,
            run_limit=run_limit,
            heartbeat_limit=heartbeat_limit,
            active_run_action=active_run_action,
        )


class BudgetUsage(VersionedModel):
    schema_name: Literal["BudgetUsageV1"] = "BudgetUsageV1"
    budget_policy_id: Identifier
    tokens: TokenBudget = Field(default_factory=TokenBudget)
    estimated_cost_micros: int | None = Field(default=None, ge=0)
    actual_cost_micros: int | None = Field(default=None, ge=0)
    run_count: int = Field(default=0, ge=0)
    heartbeat_count: int = Field(default=0, ge=0)
    updated_at: AwareDatetime

    @field_validator("updated_at")
    @classmethod
    def _updated_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class DigitalEmployee(VersionedModel):
    schema_name: Literal["DigitalEmployeeV1"] = "DigitalEmployeeV1"
    employee_id: Identifier
    agent_definition_id: Identifier
    agent_definition_sha256: Sha256
    name: NonEmptyStr
    role: Identifier
    description: str = Field(default="", max_length=8_192)
    reporting_manager_id: Identifier | None = None
    runtime_adapter_id: Identifier
    enabled_skill_ids: tuple[Identifier, ...] = ()
    instruction_bundle: tuple[RelativePath, ...] = ()
    filesystem_policy: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    graph_policy: GraphPolicy = Field(default_factory=GraphPolicy)
    heartbeat_policy: HeartbeatPolicy = Field(default_factory=HeartbeatPolicy)
    concurrency_limit: int = Field(default=1, ge=1, le=32)
    budget_policy_id: Identifier | None = None
    status: EmployeeStatus = EmployeeStatus.DISABLED
    current_session_id: Identifier | None = None
    current_task_id: Identifier | None = None
    configuration_revision: Sha256

    @field_validator("enabled_skill_ids", "instruction_bundle")
    @classmethod
    def _unique_employee_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Employee skill and instruction lists must be unique")
        return value

    @model_validator(mode="after")
    def _employee_state(self) -> DigitalEmployee:
        if self.reporting_manager_id == self.employee_id:
            raise ValueError("An employee cannot report to itself")
        if self.status is EmployeeStatus.RUNNING and (
            self.current_session_id is None or self.current_task_id is None
        ):
            raise ValueError("Running employees require task and session bindings")
        if self.status in {EmployeeStatus.DISABLED, EmployeeStatus.TERMINATED} and (
            self.current_session_id is not None or self.current_task_id is not None
        ):
            raise ValueError("Inactive employees cannot retain active bindings")
        return self


class TaskAssignment(VersionedModel):
    schema_name: Literal["TaskAssignmentV1"] = "TaskAssignmentV1"
    assignment_id: Identifier
    task_id: Identifier
    task_generation_id: Identifier
    task_revision: int = Field(ge=1)
    employee_id: Identifier
    runtime_adapter_id: Identifier
    budget_policy_id: Identifier | None = None
    approval_policy_id: Identifier | None = None
    graph_scope_id: Identifier | None = None
    status: Literal["active", "released"] = "active"
    revision: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _assignment_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _assignment_time_order(self) -> TaskAssignment:
        if self.updated_at < self.created_at:
            raise ValueError("Assignment update cannot predate creation")
        return self


class TaskGraphScope(VersionedModel):
    schema_name: Literal["TaskGraphScopeV1"] = "TaskGraphScopeV1"
    graph_scope_id: Identifier
    task_id: Identifier
    graph_snapshot_id: Identifier
    graph_fingerprint: Sha256
    generation_fingerprint: Sha256
    roots: tuple[RelativePath, ...] = ()
    seed_node_ids: tuple[Identifier, ...] = ()
    max_depth: int = Field(default=2, ge=0, le=8)
    max_nodes: int = Field(default=40, ge=1, le=500)
    max_edges: int = Field(default=100, ge=0, le=2_000)
    max_bytes: int = Field(default=48_000, ge=1_024, le=2_000_000)
    include_relations: tuple[Identifier, ...] = ()
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _scope_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("roots", "seed_node_ids", "include_relations")
    @classmethod
    def _unique_scope_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Graph scope lists must be unique")
        return value

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"graph_scope_id"})

    def verify_id(self) -> bool:
        return self.graph_scope_id == derive_id("gscope", self.identity_payload())


class TaskWorkspace(VersionedModel):
    schema_name: Literal["TaskWorkspaceV1"] = "TaskWorkspaceV1"
    workspace_id: Identifier
    task_id: Identifier
    mode: WorkspaceMode
    relative_root: RelativePath
    repo_path: RelativePath
    context_path: RelativePath
    artifacts_path: RelativePath
    logs_path: RelativePath
    git_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    graph_snapshot_id: Identifier
    graph_fingerprint: Sha256
    manifest_sha256: Sha256
    status: WorkspaceStatus = WorkspaceStatus.READY
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _workspace_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _workspace_shape(self) -> TaskWorkspace:
        if self.updated_at < self.created_at:
            raise ValueError("Workspace update cannot predate creation")
        prefix = f"{self.relative_root}/"
        for child in (self.repo_path, self.context_path, self.artifacts_path, self.logs_path):
            if not child.startswith(prefix):
                raise ValueError("Workspace child paths must stay inside the workspace root")
        return self


class TaskLease(VersionedModel):
    schema_name: Literal["TaskLeaseV1"] = "TaskLeaseV1"
    lease_id: Identifier
    task_id: Identifier
    employee_id: Identifier
    run_id: Identifier
    task_generation_id: Identifier
    task_revision: int = Field(ge=1)
    control_epoch: Identifier
    fencing_token: int = Field(gt=0)
    acquired_at: AwareDatetime
    expires_at: AwareDatetime
    status: TaskLeaseStatus = TaskLeaseStatus.ACTIVE

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def _lease_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _lease_expiry(self) -> TaskLease:
        if self.expires_at <= self.acquired_at:
            raise ValueError("Task lease expiry must follow acquisition")
        return self


class ExecutionInvocation(VersionedModel):
    schema_name: Literal["ExecutionInvocationV1"] = "ExecutionInvocationV1"
    invocation_id: Identifier
    source: Literal["manual", "assignment", "comment", "approval", "retry", "resume"]
    task_id: Identifier
    task_generation_id: Identifier
    task_revision: int = Field(ge=1)
    employee_id: Identifier
    employee_configuration_revision: Sha256
    runtime_adapter_id: Identifier
    runtime_adapter_sha256: Sha256
    workspace_id: Identifier
    workspace_manifest_sha256: Sha256
    graph_scope_id: Identifier
    graph_snapshot_id: Identifier
    context_snapshot_sha256: Sha256
    policy_decision_id: Identifier
    policy_decision_sha256: Sha256
    budget_policy_ids: tuple[Identifier, ...] = ()
    approval_id: Identifier | None = None
    idempotency_key: Identifier
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _invocation_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"invocation_id"})

    def verify_id(self) -> bool:
        return self.invocation_id == derive_id("xinv", self.identity_payload())


class ExecutionUsage(VersionedModel):
    schema_name: Literal["ExecutionUsageV1"] = "ExecutionUsageV1"
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimated_cost_micros: int | None = Field(default=None, ge=0)
    actual_cost_micros: int | None = Field(default=None, ge=0)


class TestResult(VersionedModel):
    schema_name: Literal["ExecutionTestResultV1"] = "ExecutionTestResultV1"
    name: NonEmptyStr
    status: Literal["passed", "failed", "skipped", "not_reported"]
    summary: str = Field(default="", max_length=8_192)


class ExecutionRun(VersionedModel):
    schema_name: Literal["ExecutionRunV1"] = "ExecutionRunV1"
    run_id: Identifier
    invocation_id: Identifier
    invocation_source: Identifier
    employee_id: Identifier
    task_id: Identifier
    runtime_adapter_id: Identifier
    provider: Identifier
    model: str | None = Field(default=None, max_length=256)
    safe_command: tuple[str, ...]
    cwd_token: RelativePath
    workspace_id: Identifier
    graph_snapshot_id: Identifier
    graph_scope_id: Identifier
    session_id_before: Identifier | None = None
    session_id_after: Identifier | None = None
    policy_decision_id: Identifier
    approval_id: Identifier | None = None
    task_lease_id: Identifier
    fencing_token: int = Field(gt=0)
    status: ExecutionRunStatus = ExecutionRunStatus.QUEUED
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    changed_files: tuple[RelativePath, ...] = ()
    tests: tuple[TestResult, ...] = ()
    artifact_ids: tuple[Identifier, ...] = ()
    summary: str = Field(default="", max_length=32_768)
    error_code: Identifier | None = None
    retry_of_run_id: Identifier | None = None
    resumed_from_run_id: Identifier | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _run_time_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @field_validator("changed_files", "artifact_ids")
    @classmethod
    def _unique_run_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Execution run collections must be unique")
        return value

    @model_validator(mode="after")
    def _run_shape(self) -> ExecutionRun:
        if not self.safe_command or any(
            not item or len(item) > 4_096 for item in self.safe_command
        ):
            raise ValueError("Execution command representation is invalid")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("Execution run cannot end before it starts")
        terminal = {
            ExecutionRunStatus.CANCELLED,
            ExecutionRunStatus.SUCCEEDED,
            ExecutionRunStatus.REVIEW,
            ExecutionRunStatus.BLOCKED,
            ExecutionRunStatus.FAILED,
        }
        if (self.status in terminal) != (self.ended_at is not None):
            raise ValueError("Terminal execution runs require an end time")
        return self


class ExecutionSession(VersionedModel):
    schema_name: Literal["ExecutionSessionV1"] = "ExecutionSessionV1"
    session_id: Identifier
    provider_session_id: str | None = Field(default=None, max_length=512)
    runtime_adapter_id: Identifier
    provider: Identifier
    model: str | None = Field(default=None, max_length=256)
    task_id: Identifier
    employee_id: Identifier
    workspace_id: Identifier
    graph_snapshot_id: Identifier
    context_snapshot_sha256: Sha256
    compatibility_sha256: Sha256
    started_at: AwareDatetime
    last_resumed_at: AwareDatetime | None = None
    status: ExecutionSessionStatus = ExecutionSessionStatus.ACTIVE
    previous_run_id: Identifier | None = None
    usage_totals: ExecutionUsage = Field(default_factory=ExecutionUsage)
    incompatibility_reason: Identifier | None = None

    @field_validator("started_at", "last_resumed_at")
    @classmethod
    def _session_time_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _session_shape(self) -> ExecutionSession:
        if self.last_resumed_at is not None and self.last_resumed_at < self.started_at:
            raise ValueError("Session resume cannot predate session start")
        if (
            self.status is ExecutionSessionStatus.INCOMPATIBLE
            and self.incompatibility_reason is None
        ):
            raise ValueError("Incompatible sessions require a typed reason")
        return self


class ExecutionApproval(VersionedModel):
    schema_name: Literal["ExecutionApprovalV1"] = "ExecutionApprovalV1"
    approval_id: Identifier
    action: Identifier
    payload_sha256: Sha256
    employee_id: Identifier | None = None
    task_id: Identifier | None = None
    run_id: Identifier | None = None
    workspace_id: Identifier | None = None
    destination: NonEmptyStr | None = None
    scope: tuple[Identifier, ...] = ()
    approved_by: NonEmptyStr
    approved_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _approval_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _approval_window(self) -> ExecutionApproval:
        if self.expires_at <= self.approved_at:
            raise ValueError("Approval expiry must follow approval time")
        return self

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"approval_id"})

    def verify_id(self) -> bool:
        return self.approval_id == derive_id("xapr", self.identity_payload())


class ExecutionComment(VersionedModel):
    schema_name: Literal["ExecutionCommentV1"] = "ExecutionCommentV1"
    comment_id: Identifier
    task_id: Identifier
    kind: CommentKind
    actor: Identifier
    run_id: Identifier | None = None
    body: str = Field(min_length=1, max_length=32_768)
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _comment_created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"comment_id"})

    def verify_id(self) -> bool:
        return self.comment_id == derive_id("comment", self.identity_payload())


class TranscriptEvent(VersionedModel):
    schema_name: Literal["TranscriptEventV1"] = "TranscriptEventV1"
    transcript_event_id: Identifier
    run_id: Identifier
    sequence: int = Field(ge=0, le=10_000_000)
    stream: Literal["system", "stdout", "stderr", "tool", "result"]
    event_type: Identifier
    text: str = Field(default="", max_length=32_768)
    redacted: bool = False
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _transcript_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class RuntimeHealth(VersionedModel):
    schema_name: Literal["RuntimeHealthV1"] = "RuntimeHealthV1"
    runtime_adapter_id: Identifier
    status: RuntimeHealthStatus
    executable: str = Field(max_length=512)
    version: str | None = Field(default=None, max_length=256)
    capabilities: tuple[Identifier, ...] = ()
    reason_code: Identifier | None = None
    checked_at: AwareDatetime

    @field_validator("checked_at")
    @classmethod
    def _health_time_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
