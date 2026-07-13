from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator

from raytsystem.contracts.base import (
    ComponentRef,
    Identifier,
    NonEmptyStr,
    RecordRef,
    RelativePath,
    Sha256,
    VersionedModel,
)


class ArtifactState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    AWAITING_REVIEW = "awaiting_review"
    READY = "ready"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class OutboxState(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"


class Artifact(VersionedModel):
    schema_name: Literal["ArtifactV1"] = "ArtifactV1"
    artifact_id: Identifier
    kind: Identifier
    project_id: Identifier
    stage_id: Identifier
    run_id: Identifier
    state: ArtifactState = ArtifactState.DRAFT
    input_refs: tuple[RecordRef, ...] = ()
    claim_ids: tuple[Identifier, ...] = ()
    skill_ref: ComponentRef | None = None
    prompt_ref: ComponentRef | None = None
    model_ref: ComponentRef | None = None
    output_sha256: Sha256
    path: RelativePath
    approval_ids: tuple[Identifier, ...] = ()
    feedback_ids: tuple[Identifier, ...] = ()
    created_at: AwareDatetime


class OutboxAction(VersionedModel):
    schema_name: Literal["OutboxActionV1"] = "OutboxActionV1"
    outbox_action_id: Identifier
    action_type: Identifier
    adapter: Identifier
    destination: NonEmptyStr
    payload_sha256: Sha256
    artifact_id: Identifier
    idempotency_key: Identifier
    policy_decision_id: Identifier
    approval_ids: tuple[Identifier, ...] = ()
    state: OutboxState = OutboxState.DRAFT
    attempt_count: int = Field(default=0, ge=0)
    last_attempt_at: AwareDatetime | None = None
    external_receipt: dict[str, Any] | None = None
    created_at: AwareDatetime

    @field_validator("last_attempt_at", "created_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class Feedback(VersionedModel):
    schema_name: Literal["FeedbackV1"] = "FeedbackV1"
    feedback_id: Identifier
    target: RecordRef
    feedback_type: Identifier
    value: NonEmptyStr
    unit: Identifier | None = None
    cohort: dict[str, str] = Field(default_factory=dict)
    scope: dict[str, str] = Field(default_factory=dict)
    sample_size: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime
    recorded_at: AwareDatetime
    source_refs: tuple[RecordRef, ...] = ()
    importing_run_id: Identifier

    @field_validator("observed_at", "recorded_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
