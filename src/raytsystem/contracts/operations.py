from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    ComponentRef,
    ErrorRecord,
    Identifier,
    NonEmptyStr,
    RecordRef,
    RelativePath,
    Sensitivity,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
)


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    STAGING = "staging"
    VALIDATING = "validating"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_APPROVAL = "awaiting_approval"
    PROMOTING = "promoting"
    PROMOTED = "promoted"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


class LeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    REVOKED = "revoked"


class PromotionState(StrEnum):
    PREPARED = "prepared"
    COMMITTING = "committing"
    COMMITTED = "committed"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    ABORTED = "aborted"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    QUARANTINE = "quarantine"
    REDACT = "redact"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class OperationFingerprint(VersionedModel):
    schema_name: Literal["OperationFingerprintV1"] = "OperationFingerprintV1"
    operation_type: Identifier
    input_hashes: tuple[Sha256, ...] = ()
    components: tuple[ComponentRef, ...] = ()
    schema_registry_sha256: Sha256
    prompt_or_skill_sha256: Sha256 | None = None
    model_id: NonEmptyStr | None = None
    pipeline_version: NonEmptyStr
    relevant_config_sha256: Sha256

    def operation_key(self) -> str:
        return derive_id("op", self)


class Run(VersionedModel):
    schema_name: Literal["RunV1"] = "RunV1"
    run_id: Identifier
    operation_type: Identifier
    operation_key: Identifier
    partition_key: Identifier
    input_refs: tuple[RecordRef, ...] = ()
    fingerprint: OperationFingerprint
    surface: Identifier
    permission_mode: Identifier
    egress_destination: NonEmptyStr | None = None
    state: RunState = RunState.QUEUED
    last_completed_gate: Identifier | None = None
    retry_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    staging_path: RelativePath
    result_refs: tuple[RecordRef, ...] = ()
    error: ErrorRecord | None = None


class Lease(VersionedModel):
    schema_name: Literal["LeaseV1"] = "LeaseV1"
    lease_id: Identifier
    partition_key: Identifier
    owner_id: Identifier
    control_epoch: Identifier = "epoch_initial"
    fencing_token: int = Field(gt=0)
    acquired_at: AwareDatetime
    renewed_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    state: LeaseState = LeaseState.ACTIVE

    @field_validator("acquired_at", "renewed_at", "expires_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _expiry_after_acquisition(self) -> Lease:
        if self.expires_at <= self.acquired_at:
            raise ValueError("Lease expiry must follow acquisition")
        return self

    def allows(self, *, owner_id: str, fencing_token: int, at: datetime) -> bool:
        return (
            self.state is LeaseState.ACTIVE
            and self.owner_id == owner_id
            and self.fencing_token == fencing_token
            and at.astimezone(UTC) < self.expires_at
        )


class PolicyDecision(VersionedModel):
    schema_name: Literal["PolicyDecisionV1"] = "PolicyDecisionV1"
    policy_decision_id: Identifier
    action: Identifier
    target_id: Identifier
    payload_sha256: Sha256
    destination: NonEmptyStr | None = None
    policy_version: NonEmptyStr
    policy_sha256: Sha256
    outcome: PolicyOutcome
    reason_codes: tuple[Identifier, ...] = ()
    required_approval_scope: tuple[Identifier, ...] = ()
    evaluated_at: AwareDatetime


class ApprovalRecord(VersionedModel):
    schema_name: Literal["ApprovalRecordV1"] = "ApprovalRecordV1"
    approval_id: Identifier
    decision: ApprovalDecision = ApprovalDecision.APPROVE
    action: Identifier
    target_id: Identifier
    artifact_sha256: Sha256
    destination: NonEmptyStr | None = None
    scope: tuple[Identifier, ...] = ()
    policy_version: NonEmptyStr
    policy_sha256: Sha256 | None = None
    approver: NonEmptyStr
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    conditions: tuple[NonEmptyStr, ...] = ()

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _valid_window(self) -> ApprovalRecord:
        if self.expires_at <= self.approved_at:
            raise ValueError("Approval expiry must follow issue time")
        return self

    @classmethod
    def create(
        cls,
        *,
        action: str,
        target_id: str,
        artifact_sha256: str,
        policy_version: str,
        approver: str,
        approved_at: datetime,
        expires_at: datetime,
        destination: str | None = None,
        scope: tuple[str, ...] = (),
        policy_sha256: str | None = None,
        conditions: tuple[str, ...] = (),
    ) -> ApprovalRecord:
        identity = {
            "decision": ApprovalDecision.APPROVE,
            "action": action,
            "target_id": target_id,
            "artifact_sha256": artifact_sha256,
            "destination": destination,
            "policy_version": policy_version,
            "approver": approver,
            "approved_at": approved_at,
            "expires_at": expires_at,
            "scope": scope,
            "policy_sha256": policy_sha256,
            "conditions": conditions,
        }
        return cls(
            approval_id=derive_id("apr", identity),
            action=action,
            target_id=target_id,
            artifact_sha256=artifact_sha256,
            destination=destination,
            scope=scope,
            policy_version=policy_version,
            policy_sha256=policy_sha256,
            approver=approver,
            approved_at=approved_at,
            expires_at=expires_at,
            conditions=conditions,
        )

    def is_valid_for(
        self,
        *,
        action: str,
        target_id: str,
        artifact_sha256: str,
        at: datetime,
        destination: str | None = None,
    ) -> bool:
        return (
            self.decision is ApprovalDecision.APPROVE
            and self.action == action
            and self.target_id == target_id
            and self.artifact_sha256 == artifact_sha256
            and self.destination == destination
            and self.approved_at <= at.astimezone(UTC) < self.expires_at
        )


_PROMOTION_TRANSITIONS: dict[PromotionState, frozenset[PromotionState]] = {
    PromotionState.PREPARED: frozenset({PromotionState.COMMITTING, PromotionState.ABORTED}),
    PromotionState.COMMITTING: frozenset({PromotionState.COMMITTED, PromotionState.ABORTED}),
    PromotionState.COMMITTED: frozenset({PromotionState.RECONCILING}),
    PromotionState.RECONCILING: frozenset({PromotionState.COMPLETED}),
    PromotionState.COMPLETED: frozenset(),
    PromotionState.ABORTED: frozenset(),
}


class PromotionTxn(VersionedModel):
    schema_name: Literal["PromotionTxnV1"] = "PromotionTxnV1"
    txn_id: Identifier
    run_id: Identifier
    operation_key: Identifier
    partition_key: Identifier = "ledger:current"
    control_epoch: Identifier = "epoch_initial"
    parent_generation_id: Identifier
    next_generation_id: Identifier
    candidate_manifest_sha256: Sha256 | None = None
    event_id: Identifier
    partition_fencing_token: int = Field(gt=0)
    global_fencing_token: int = Field(gt=0)
    output_hashes: dict[str, Sha256]
    policy_decision_id: Identifier | None = None
    approval_id: Identifier | None = None
    state: PromotionState
    created_at: AwareDatetime
    updated_at: AwareDatetime

    def transition(self, target: PromotionState, *, at: datetime | None = None) -> PromotionTxn:
        if target not in _PROMOTION_TRANSITIONS[self.state]:
            raise ValueError(f"Illegal promotion transition: {self.state.value} -> {target.value}")
        return self.model_copy(
            update={"state": target, "updated_at": at or datetime.now(UTC)},
        )


class PromotionEvent(VersionedModel):
    schema_name: Literal["PromotionEventV1"] = "PromotionEventV1"
    event_id: Identifier
    event_type: Literal["promotion", "compensating_promotion"] = "promotion"
    txn_id: Identifier
    run_id: Identifier
    operation_key: Identifier
    parent_generation_id: Identifier
    new_generation_id: Identifier
    committed_at: AwareDatetime
    previous_event_sha256: Sha256 | None = None


class GitCheckpointEvent(VersionedModel):
    schema_name: Literal["GitCheckpointEventV1"] = "GitCheckpointEventV1"
    checkpoint_id: Identifier
    event_id: Identifier
    generation_id: Identifier
    commit_sha: Sha256
    recorded_at: AwareDatetime


class GenerationEntry(VersionedModel):
    schema_name: Literal["GenerationEntryV1"] = "GenerationEntryV1"
    kind: Identifier
    logical_id: Identifier
    object_sha256: Sha256
    tombstone: bool = False


class LedgerGeneration(VersionedModel):
    schema_name: Literal["LedgerGenerationV1"] = "LedgerGenerationV1"
    generation_id: Identifier
    parent_generation_id: Identifier | None = None
    records: dict[str, GenerationEntry] = Field(default_factory=dict)
    schema_registry_sha256: Sha256 | None = None
    created_at: AwareDatetime
    promotion_txn_id: Identifier | None = None
    promotion_event_id: Identifier | None = None

    def identity_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="python", exclude={"generation_id"})
        return payload

    def verify_id(self) -> bool:
        if self.generation_id == "genesis":
            return self.parent_generation_id is None and not self.records
        return self.generation_id == derive_id("gen", self.identity_payload())

    def manifest_sha256(self) -> str:
        from raytsystem.contracts.base import sha256_hex

        return sha256_hex(canonical_json_bytes(self))


class SensitivityDecision(VersionedModel):
    schema_name: Literal["SensitivityDecisionV1"] = "SensitivityDecisionV1"
    decision_id: Identifier
    input_id: Identifier
    scanner: ComponentRef
    sensitivity: Sensitivity
    reason_codes: tuple[Identifier, ...]
    disposition: Literal["allow", "quarantine", "restrict", "redact", "block"]
    decided_at: AwareDatetime
