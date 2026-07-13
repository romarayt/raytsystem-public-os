from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from raytsystem.contracts import TaskPriority, TaskStatus
from raytsystem.contracts.execution import CommentKind, ExecutionRunStatus

PublicIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_:.@-]{1,255}$"),
]
SkillIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,63}$"),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VersionedApiRequest(ApiRequest):
    request_version: Literal["1.0"] = "1.0"


class TaskCreateRequest(ApiRequest):
    title: str = Field(min_length=1, max_length=4096)
    description: str = Field(default="", max_length=32_768)
    priority: TaskPriority = TaskPriority.NORMAL
    project_id: PublicIdentifier = "project_default"
    mission_id: PublicIdentifier | None = None
    assignee_ids: tuple[PublicIdentifier, ...] = Field(default=(), max_length=64)
    skill_ids: tuple[PublicIdentifier, ...] = Field(default=(), max_length=64)
    dependency_ids: tuple[PublicIdentifier, ...] = Field(default=(), max_length=256)
    tags: tuple[PublicIdentifier, ...] = Field(default=(), max_length=64)
    expected_generation_id: PublicIdentifier | None = None

    @field_validator("assignee_ids", "skill_ids", "dependency_ids", "tags")
    @classmethod
    def _unique_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Reference lists must not contain duplicates")
        return value


class TaskTransitionRequest(ApiRequest):
    target: TaskStatus
    blocked_reason: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_generation_id: PublicIdentifier


class CodeGraphMutationRequest(ApiRequest):
    expected_snapshot_id: PublicIdentifier | None = None


_OnboardingTemplate = Literal["auto", "software", "content", "research"]


class OnboardingPlanRequest(ApiRequest):
    target: str = Field(min_length=1, max_length=4096)
    source_type: _OnboardingTemplate = "auto"
    template: _OnboardingTemplate = "auto"
    mode: Literal["managed", "vendored"] = "managed"
    context_language: Literal["en", "ru"] = "en"


class OnboardingApplyRequest(OnboardingPlanRequest):
    confirm: str = Field(min_length=1, max_length=256)


class OnboardingUninstallRequest(ApiRequest):
    target: str = Field(min_length=1, max_length=4096)


class OnboardingPromptRequest(ApiRequest):
    target: str = Field(min_length=1, max_length=4096)
    agent: Literal["codex", "claude"] = "claude"
    mode: Literal["managed", "vendored"] = "managed"
    context_language: Literal["en", "ru"] = "en"


class CodeGraphQueryRequest(ApiRequest):
    query: str = Field(min_length=1, max_length=512)
    expected_snapshot_id: PublicIdentifier
    depth: int = Field(default=2, ge=1, le=3)


class CodeGraphNodeRequest(ApiRequest):
    node_id: PublicIdentifier
    expected_snapshot_id: PublicIdentifier
    depth: int = Field(default=1, ge=1, le=3)
    direction: Literal["both", "in", "out"] = "both"


class CodeGraphPathRequest(ApiRequest):
    source_node_id: PublicIdentifier
    target_node_id: PublicIdentifier
    expected_snapshot_id: PublicIdentifier


class CodeGraphImpactRequest(ApiRequest):
    node_id: PublicIdentifier
    expected_snapshot_id: PublicIdentifier
    depth: int = Field(default=3, ge=1, le=3)


class ExecutionAssignmentRequest(VersionedApiRequest):
    employee_id: PublicIdentifier
    expected_task_generation_id: PublicIdentifier
    budget_policy_id: PublicIdentifier | None = None
    approval_policy_id: PublicIdentifier | None = None


class ExecutionWorkspaceRequest(VersionedApiRequest):
    expected_task_generation_id: PublicIdentifier


class ExecutionHeartbeatRequest(VersionedApiRequest):
    expected_task_generation_id: PublicIdentifier
    approval_id: PublicIdentifier | None = None


class ExecutionRunControlRequest(VersionedApiRequest):
    expected_status: ExecutionRunStatus


class ExecutionResumeRequest(VersionedApiRequest):
    expected_task_generation_id: PublicIdentifier
    expected_status: Literal["cancelled"] = "cancelled"
    approval_id: PublicIdentifier | None = None


class ExecutionCommentRequest(VersionedApiRequest):
    kind: CommentKind = CommentKind.COMMENT
    run_id: PublicIdentifier | None = None
    body: str = Field(min_length=1, max_length=32_768)


class ExecutionBudgetRequest(VersionedApiRequest):
    scope_kind: Literal["employee", "task", "project", "run", "workspace"]
    scope_id: PublicIdentifier
    input_token_limit: int = Field(default=0, ge=0, le=10_000_000_000)
    output_token_limit: int = Field(default=0, ge=0, le=10_000_000_000)
    cached_token_limit: int = Field(default=0, ge=0, le=10_000_000_000)
    cost_limit_micros: int | None = Field(default=None, ge=0, le=10**15)
    run_limit: int = Field(default=0, ge=0, le=1_000_000)
    heartbeat_limit: int = Field(default=0, ge=0, le=1_000_000)
    active_run_action: Literal["finish_current", "cancel"] = "cancel"


class ExecutionApprovalRequest(VersionedApiRequest):
    policy_decision_id: PublicIdentifier
    expires_in_seconds: int = Field(default=3_600, ge=60, le=86_400)


class SkillSaveRequest(BaseModel):
    """Exact Markdown plus both catalog and source CAS fences."""

    # Markdown is exact user data: unlike the ordinary compact DTOs, leading/trailing whitespace
    # must survive request validation so the preview and committed hash bind identical bytes.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    request_version: Literal["1.0"] = "1.0"
    content: str = Field(min_length=1, max_length=64 * 1024)
    expected_catalog_sha256: Sha256Digest
    expected_source_sha256: Sha256Digest


class SkillForkPreviewRequest(VersionedApiRequest):
    new_skill_id: SkillIdentifier | None = None
    expected_catalog_sha256: Sha256Digest
    expected_source_sha256: Sha256Digest


class SkillForkRequest(VersionedApiRequest):
    new_skill_id: SkillIdentifier
    expected_catalog_sha256: Sha256Digest
    expected_source_sha256: Sha256Digest
