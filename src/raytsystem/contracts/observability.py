from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeDecimal,
    RelativePath,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


class SpanKind(StrEnum):
    TASK = "task"
    RUN = "run"
    MODEL = "model"
    GRAPH_QUERY = "graph_query"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    FILESYSTEM = "filesystem"
    APPROVAL = "approval"
    TEST = "test"
    ARTIFACT = "artifact"


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class RedactionStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REDACTED = "redacted"
    REJECTED = "rejected"


class AuditEvent(VersionedModel):
    schema_name: Literal["AuditEventV1"] = "AuditEventV1"
    event_id: Identifier
    stream_id: Identifier
    aggregate_id: Identifier
    sequence: int = Field(ge=1)
    event_type: Identifier
    causation_id: Identifier | None = None
    correlation_id: Identifier | None = None
    actor_id: Identifier
    payload_schema: Identifier
    payload_sha256: Sha256
    sensitivity: Literal["public", "internal", "confidential", "restricted", "secret"]
    previous_event_sha256: Sha256 | None = None
    recorded_at: AwareDatetime

    @field_validator("recorded_at")
    @classmethod
    def _event_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "payload_schema": self.payload_schema,
            "payload_sha256": self.payload_sha256,
            "sensitivity": self.sensitivity,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "previous_event_sha256": self.previous_event_sha256,
            "recorded_at": self.recorded_at,
        }

    def verify_id(self) -> bool:
        return self.event_id == derive_id("aevt", self.identity_payload())


class TraceRecord(VersionedModel):
    schema_name: Literal["TraceRecordV1"] = "TraceRecordV1"
    trace_id: Identifier
    task_id: Identifier | None = None
    root_run_id: Identifier
    root_span_id: Identifier
    created_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    status: SpanStatus = SpanStatus.UNSET
    span_count: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimated_cost: NonNegativeDecimal = Decimal("0")
    actual_cost: NonNegativeDecimal | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def _trace_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _trace_time_order(self) -> TraceRecord:
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("Trace completion cannot predate creation")
        return self


class TraceSpan(VersionedModel):
    schema_name: Literal["TraceSpanV1"] = "TraceSpanV1"
    trace_id: Identifier
    span_id: Identifier
    parent_span_id: Identifier | None = None
    span_kind: SpanKind
    task_id: Identifier | None = None
    run_id: Identifier
    employee_id: Identifier | None = None
    agent_id: Identifier | None = None
    session_id: Identifier | None = None
    workspace_id: Identifier
    graph_snapshot_id: Identifier | None = None
    knowledge_generation_id: Identifier | None = None
    operation_name: Identifier
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: SpanStatus = SpanStatus.UNSET
    provider: Identifier | None = None
    model: NonEmptyStr | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimated_cost: NonNegativeDecimal = Decimal("0")
    actual_cost: NonNegativeDecimal | None = None
    retry_count: int = Field(default=0, ge=0, le=1_000)
    tool_name: Identifier | None = None
    policy_decision_id: Identifier | None = None
    approval_id: Identifier | None = None
    error_code: Identifier | None = None
    redaction_status: RedactionStatus = RedactionStatus.NOT_REQUIRED
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _span_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _span_invariants(self) -> TraceSpan:
        if self.parent_span_id == self.span_id:
            raise ValueError("A span cannot parent itself")
        if self.ended_at is not None:
            if self.ended_at < self.started_at:
                raise ValueError("Span end cannot predate start")
            expected = int((self.ended_at - self.started_at).total_seconds() * 1_000)
            if self.duration_ms is None or abs(self.duration_ms - expected) > 1:
                raise ValueError("Span duration must match timestamps")
        elif self.duration_ms is not None:
            raise ValueError("Open spans cannot have a duration")
        if len(self.attributes) > 32:
            raise ValueError("Span attributes exceed the bounded limit")
        if any(len(key) > 128 or len(value) > 1_024 for key, value in self.attributes.items()):
            raise ValueError("Span attribute key or value is too large")
        if len(canonical_json_bytes(self.attributes)) > 16_384:
            raise ValueError("Span attributes are too large")
        return self


class ExecutionRecord(VersionedModel):
    schema_name: Literal["ExecutionRecordV1"] = "ExecutionRecordV1"
    execution_record_id: Identifier
    run_id: Identifier
    task_id: Identifier | None = None
    task_revision: int | None = Field(default=None, ge=1)
    repository_snapshot_sha256: Sha256
    knowledge_generation_id: Identifier | None = None
    graph_snapshot_id: Identifier | None = None
    instruction_hashes: dict[Identifier, Sha256]
    skill_hashes: dict[Identifier, Sha256]
    policy_sha256: Sha256
    toolset_sha256: Sha256
    runtime_configuration_sha256: Sha256
    runtime_id: Identifier
    model: NonEmptyStr | None = None
    token_budget: int | None = Field(default=None, ge=0)
    cost_budget: NonNegativeDecimal | None = None
    side_effect_ids: tuple[Identifier, ...] = ()
    approval_ids: tuple[Identifier, ...] = ()
    trace_id: Identifier | None = None
    result_sha256: Sha256 | None = None
    manifest_path: RelativePath | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _record_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"execution_record_id"})

    def verify_id(self) -> bool:
        return self.execution_record_id == derive_id("xrec", self.identity_payload())


class RecordedSideEffectResult(VersionedModel):
    schema_name: Literal["RecordedSideEffectResultV1"] = "RecordedSideEffectResultV1"
    recorded_result_id: Identifier
    original_run_id: Identifier
    side_effect_id: Identifier
    invocation_sha256: Sha256
    result_sha256: Sha256
    recorded_at: AwareDatetime

    @field_validator("recorded_at")
    @classmethod
    def _result_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"recorded_result_id"})

    def verify_id(self) -> bool:
        return self.recorded_result_id == derive_id("rside", self.identity_payload())


class ReplayMode(StrEnum):
    REPLAY = "replay"
    FORK = "fork"


class ReplayPlan(VersionedModel):
    schema_name: Literal["ReplayPlanV1"] = "ReplayPlanV1"
    replay_plan_id: Identifier
    mode: ReplayMode
    original_run_id: Identifier
    original_execution_record_sha256: Sha256
    new_run_id: Identifier
    recorded_result_tool_ids: tuple[Identifier, ...] = ()
    blocked_side_effect_tool_ids: tuple[Identifier, ...] = ()
    required_approval_ids: tuple[Identifier, ...] = ()
    differences: dict[Identifier, Any] = Field(default_factory=dict)
    plan_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _replay_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _replay_invariants(self) -> ReplayPlan:
        if self.original_run_id == self.new_run_id:
            raise ValueError("Replay requires a new run ID")
        if self.mode is ReplayMode.REPLAY and self.differences:
            raise ValueError("Replay cannot change execution inputs")
        calculated = sha256_hex(
            canonical_json_bytes(
                self.model_dump(mode="python", exclude={"plan_sha256", "replay_plan_id"})
            )
        )
        if calculated != self.plan_sha256:
            raise ValueError("Replay plan hash is invalid")
        return self


class RunComparison(VersionedModel):
    schema_name: Literal["RunComparisonV1"] = "RunComparisonV1"
    comparison_id: Identifier
    left_run_id: Identifier
    right_run_id: Identifier
    eval_score_deltas: dict[Identifier, Decimal] = Field(default_factory=dict)
    assertion_changes: dict[Identifier, Literal["added", "removed", "changed"]] = Field(
        default_factory=dict
    )
    token_delta: int = 0
    cost_delta: Decimal = Decimal("0")
    latency_delta_ms: int = 0
    tool_call_changes: tuple[Identifier, ...] = ()
    file_changes: tuple[RelativePath, ...] = ()
    test_changes: tuple[Identifier, ...] = ()
    artifact_changes: tuple[Identifier, ...] = ()
    approval_changes: tuple[Identifier, ...] = ()
    failure_changes: tuple[Identifier, ...] = ()
    result_changed: bool
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _comparison_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
