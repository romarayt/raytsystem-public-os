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
    PositiveDecimal,
    RelativePath,
    Sensitivity,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


class EvalAssertionType(StrEnum):
    EXACT_MATCH = "exact_match"
    CONTAINS = "contains"
    REGEX = "regex"
    JSON_SCHEMA = "json_schema"
    FILE_EXISTS = "file_exists"
    FILE_HASH = "file_hash"
    ARTIFACT_TYPE = "artifact_type"
    TEST_RESULT = "test_result"
    COMMAND_EXIT_STATUS = "command_exit_status"
    CITATION_EXISTS = "citation_exists"
    SOURCE_LOCATION_EXISTS = "source_location_exists"
    TASK_TRANSITION = "task_transition"
    APPROVAL_COMPLIANCE = "approval_compliance"
    FORBIDDEN_ACTION_ABSENT = "forbidden_action_absent"
    BUDGET_NOT_EXCEEDED = "budget_not_exceeded"
    NO_SECRET_LEAK = "no_secret_leak"
    NO_PROTECTED_PATH_MODIFICATION = "no_protected_path_modification"


class EvalRunState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class EvalTarget(VersionedModel):
    schema_name: Literal["EvalTargetV1"] = "EvalTargetV1"
    target_id: Identifier
    kind: Literal["agent", "skill", "prompt", "retrieval", "workflow", "runtime"]
    component_id: Identifier
    component_version: NonEmptyStr
    component_sha256: Sha256
    configuration_sha256: Sha256


class EvalDataset(VersionedModel):
    schema_name: Literal["EvalDatasetV1"] = "EvalDatasetV1"
    dataset_id: Identifier
    name: NonEmptyStr
    version: NonEmptyStr
    manifest_sha256: Sha256
    case_ids: tuple[Identifier, ...]
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    provenance: tuple[Identifier, ...] = ()

    @field_validator("case_ids")
    @classmethod
    def _unique_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("Eval datasets require unique cases")
        return value


class EvalAssertion(VersionedModel):
    schema_name: Literal["EvalAssertionV1"] = "EvalAssertionV1"
    assertion_id: Identifier
    assertion_type: EvalAssertionType
    target: NonEmptyStr
    expected: Any = None
    deterministic: bool = True
    required: bool = True
    weight: NonNegativeDecimal = Decimal("1")
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _deterministic_only(self) -> EvalAssertion:
        if not self.deterministic:
            raise ValueError("LLM judges must use EvalJudge, not EvalAssertion")
        if len(canonical_json_bytes(self.configuration)) > 16_384:
            raise ValueError("Eval assertion configuration is too large")
        return self


class EvalJudge(VersionedModel):
    schema_name: Literal["EvalJudgeV1"] = "EvalJudgeV1"
    judge_id: Identifier
    kind: Literal["llm"] = "llm"
    provider: Identifier
    model: NonEmptyStr
    rubric_sha256: Sha256
    optional: bool = True
    enabled: bool = False
    destination: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _optional_boundary(self) -> EvalJudge:
        if not self.optional:
            raise ValueError("LLM judges must remain optional")
        return self


class EvalCase(VersionedModel):
    schema_name: Literal["EvalCaseV1"] = "EvalCaseV1"
    case_id: Identifier
    name: NonEmptyStr
    task_fixture: RelativePath
    repository_snapshot_sha256: Sha256
    knowledge_generation_id: Identifier | None = None
    graph_snapshot_id: Identifier | None = None
    agent_configuration_sha256: Sha256
    runtime_id: Identifier
    model: NonEmptyStr | None = None
    instruction_hashes: dict[Identifier, Sha256]
    skill_hashes: dict[Identifier, Sha256]
    allowed_tools: tuple[Identifier, ...] = ()
    token_budget: int | None = Field(default=None, ge=0)
    cost_budget: NonNegativeDecimal | None = None
    expected_artifacts: tuple[Identifier, ...] = ()
    assertions: tuple[EvalAssertion, ...]
    judge_ids: tuple[Identifier, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    provenance: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _case_invariants(self) -> EvalCase:
        if not self.assertions:
            raise ValueError("Eval cases require deterministic assertions")
        collections = (
            self.allowed_tools,
            self.expected_artifacts,
            self.judge_ids,
            tuple(item.assertion_id for item in self.assertions),
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Eval case collections must be unique")
        return self


class EvalSuite(VersionedModel):
    schema_name: Literal["EvalSuiteV1"] = "EvalSuiteV1"
    suite_id: Identifier
    name: NonEmptyStr
    version: NonEmptyStr
    dataset_id: Identifier
    target_ids: tuple[Identifier, ...]
    case_ids: tuple[Identifier, ...]
    judge_ids: tuple[Identifier, ...] = ()
    manifest_sha256: Sha256
    enabled: bool = True

    @model_validator(mode="after")
    def _suite_invariants(self) -> EvalSuite:
        if not self.target_ids or not self.case_ids:
            raise ValueError("Eval suites require targets and cases")
        if any(
            len(values) != len(set(values))
            for values in (self.target_ids, self.case_ids, self.judge_ids)
        ):
            raise ValueError("Eval suite references must be unique")
        return self


class EvalScore(VersionedModel):
    schema_name: Literal["EvalScoreV1"] = "EvalScoreV1"
    score_id: Identifier
    name: Identifier
    value: Decimal
    maximum: PositiveDecimal
    deterministic: bool
    unit: Identifier = "ratio"


class EvalResult(VersionedModel):
    schema_name: Literal["EvalResultV1"] = "EvalResultV1"
    result_id: Identifier
    eval_run_id: Identifier
    case_id: Identifier
    passed: bool
    assertion_results: dict[Identifier, bool]
    scores: tuple[EvalScore, ...]
    failed_assertion_ids: tuple[Identifier, ...] = ()
    judge_score_ids: tuple[Identifier, ...] = ()
    artifact_hashes: dict[Identifier, Sha256] = Field(default_factory=dict)
    duration_ms: int = Field(ge=0)
    cost: NonNegativeDecimal = Decimal("0")
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _result_invariants(self) -> EvalResult:
        failed = tuple(sorted(key for key, passed in self.assertion_results.items() if not passed))
        if tuple(sorted(self.failed_assertion_ids)) != failed:
            raise ValueError("Failed assertion IDs do not match assertion results")
        if self.passed == bool(failed):
            raise ValueError("Eval result pass state is inconsistent")
        return self


class EvalRun(VersionedModel):
    schema_name: Literal["EvalRunV1"] = "EvalRunV1"
    eval_run_id: Identifier
    suite_id: Identifier
    suite_sha256: Sha256
    target_id: Identifier
    state: EvalRunState
    workspace_id: Identifier
    result_ids: tuple[Identifier, ...] = ()
    deterministic_score_ids: tuple[Identifier, ...] = ()
    judge_score_ids: tuple[Identifier, ...] = ()
    baseline_id: Identifier | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _run_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _run_time_order(self) -> EvalRun:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("Eval completion cannot predate start")
        return self


class EvalBaseline(VersionedModel):
    schema_name: Literal["EvalBaselineV1"] = "EvalBaselineV1"
    baseline_id: Identifier
    suite_id: Identifier
    suite_sha256: Sha256
    eval_run_id: Identifier
    result_hashes: dict[Identifier, Sha256]
    aggregate_sha256: Sha256
    accepted_by: Identifier
    approval_id: Identifier
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _baseline_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"baseline_id"})

    def verify_id(self) -> bool:
        return self.baseline_id == derive_id("ebase", self.identity_payload())

    def verify_aggregate(self) -> bool:
        return self.aggregate_sha256 == sha256_hex(canonical_json_bytes(self.result_hashes))


class EvalComparison(VersionedModel):
    schema_name: Literal["EvalComparisonV1"] = "EvalComparisonV1"
    comparison_id: Identifier
    baseline_id: Identifier
    candidate_eval_run_id: Identifier
    score_deltas: dict[Identifier, Decimal] = Field(default_factory=dict)
    added_failures: tuple[Identifier, ...] = ()
    resolved_failures: tuple[Identifier, ...] = ()
    regression: bool
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _comparison_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class EvalFinding(VersionedModel):
    schema_name: Literal["EvalFindingV1"] = "EvalFindingV1"
    finding_id: Identifier
    eval_run_id: Identifier
    case_id: Identifier | None = None
    severity: Literal["info", "low", "medium", "high", "critical"]
    code: Identifier
    message: NonEmptyStr
    assertion_id: Identifier | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _finding_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
