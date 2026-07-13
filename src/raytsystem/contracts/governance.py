from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeDecimal,
    RelativePath,
    Sensitivity,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


class FeatureState(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    ERROR = "error"


class FeatureStatus(VersionedModel):
    schema_name: Literal["FeatureStatusV1"] = "FeatureStatusV1"
    feature_id: Identifier
    enabled: bool
    state: FeatureState
    snapshot_id: Identifier
    reason_codes: tuple[Identifier, ...] = ()
    approval_required: bool = False
    observed_at: AwareDatetime

    @field_validator("observed_at")
    @classmethod
    def _feature_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _feature_invariants(self) -> FeatureStatus:
        if self.enabled and self.state is FeatureState.DISABLED:
            raise ValueError("Enabled features cannot report disabled")
        if self.approval_required and self.state is not FeatureState.APPROVAL_REQUIRED:
            raise ValueError("Approval state is inconsistent")
        return self


class DataClassification(VersionedModel):
    schema_name: Literal["DataClassificationV1"] = "DataClassificationV1"
    classification_id: Identifier
    sensitivity: Sensitivity
    egress_class: Identifier
    disposition: Literal["allow", "redact", "quarantine", "deny"]
    reason_codes: tuple[Identifier, ...]
    policy_sha256: Sha256


class ExecutionPlan(VersionedModel):
    schema_name: Literal["ExecutionPlanV1"] = "ExecutionPlanV1"
    plan_id: Identifier
    employee_id: Identifier
    task_id: Identifier
    task_revision: int = Field(ge=1)
    runtime_id: Identifier
    provider: Identifier | None = None
    model: NonEmptyStr | None = None
    workspace_mode: Literal["none", "read_only", "staging_only", "isolated"]
    read_roots: tuple[RelativePath, ...] = ()
    write_roots: tuple[RelativePath, ...] = ()
    network_access: Literal["none", "allowlist", "unrestricted"] = "none"
    network_destinations: tuple[NonEmptyStr, ...] = ()
    requested_tools: tuple[Identifier, ...] = ()
    requested_secrets: tuple[Identifier, ...] = ()
    graph_scope: tuple[Identifier, ...] = ()
    knowledge_scope: tuple[Identifier, ...] = ()
    token_budget: int | None = Field(default=None, ge=0)
    cost_budget: NonNegativeDecimal | None = None
    potential_side_effects: tuple[Identifier, ...] = ()
    policy_sha256: Sha256

    @model_validator(mode="after")
    def _plan_invariants(self) -> ExecutionPlan:
        if self.network_access == "none" and self.network_destinations:
            raise ValueError("Network destinations require network access")
        if set(self.write_roots) - set(self.read_roots):
            raise ValueError("Write roots must also be readable")
        if self.workspace_mode in {"none", "read_only"} and self.write_roots:
            raise ValueError("Readonly execution plans cannot request write roots")
        if self.workspace_mode == "none" and self.read_roots:
            raise ValueError("Workspace-free execution plans cannot request roots")
        collections = (
            self.read_roots,
            self.write_roots,
            self.network_destinations,
            self.requested_tools,
            self.requested_secrets,
            self.graph_scope,
            self.knowledge_scope,
            self.potential_side_effects,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Execution plan collections must be unique")
        return self


class PolicySimulation(VersionedModel):
    schema_name: Literal["PolicySimulationV1"] = "PolicySimulationV1"
    simulation_id: Identifier
    plan_id: Identifier
    policy_sha256: Sha256
    employee_id: Identifier | None = None
    task_id: Identifier | None = None
    runtime_id: Identifier | None = None
    provider: Identifier | None = None
    model: NonEmptyStr | None = None
    workspace_mode: Literal["none", "read_only", "staging_only", "isolated"] | None = None
    read_roots: tuple[RelativePath, ...] = ()
    write_roots: tuple[RelativePath, ...] = ()
    network_access: Literal["none", "allowlist", "unrestricted"] | None = None
    graph_scope: tuple[Identifier, ...] = ()
    knowledge_scope: tuple[Identifier, ...] = ()
    token_budget: int | None = Field(default=None, ge=0)
    cost_budget: NonNegativeDecimal | None = None
    allowed_tools: tuple[Identifier, ...]
    blocked_tools: tuple[Identifier, ...]
    secrets_requested: tuple[Identifier, ...]
    required_approvals: tuple[Identifier, ...]
    potential_side_effects: tuple[Identifier, ...]
    outcome: Literal["allowed", "approval_required", "blocked"]
    reason_codes: tuple[Identifier, ...]
    dry_run: Literal[True] = True
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _simulation_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _simulation_invariants(self) -> PolicySimulation:
        if set(self.allowed_tools) & set(self.blocked_tools):
            raise ValueError("A tool cannot be both allowed and blocked")
        if self.outcome == "approval_required" and not self.required_approvals:
            raise ValueError("Approval outcome requires approval kinds")
        return self


class EmergencyAction(StrEnum):
    PAUSE_ALL_EMPLOYEES = "pause_all_employees"
    CANCEL_ACTIVE_RUNS = "cancel_active_runs"
    DISABLE_RUNTIME_EXECUTION = "disable_runtime_execution"
    DISABLE_NETWORK_ADAPTERS = "disable_network_adapters"
    DISABLE_EXTERNAL_PROVIDERS = "disable_external_providers"
    FREEZE_TASK_CHECKOUT = "freeze_task_checkout"
    REVOKE_RUNTIME_SESSIONS = "revoke_runtime_sessions"
    REVOKE_PENDING_APPROVALS = "revoke_pending_approvals"
    EMERGENCY_BUDGET_STOP = "emergency_budget_stop"


class EmergencyState(VersionedModel):
    schema_name: Literal["EmergencyStateV1"] = "EmergencyStateV1"
    emergency_state_id: Identifier
    active_actions: tuple[EmergencyAction, ...]
    reason: NonEmptyStr
    activated_by: Identifier
    approval_id: Identifier | None = None
    security_lock: bool = True
    revision: int = Field(ge=1)
    activated_at: AwareDatetime
    recovered_at: AwareDatetime | None = None
    recovered_by: Identifier | None = None

    @field_validator("activated_at", "recovered_at")
    @classmethod
    def _emergency_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _emergency_invariants(self) -> EmergencyState:
        if not self.active_actions and self.recovered_at is None:
            raise ValueError("Active emergency state requires an action")
        if len(self.active_actions) != len(set(self.active_actions)):
            raise ValueError("Emergency actions must be unique")
        if (self.recovered_at is None) != (self.recovered_by is None):
            raise ValueError("Recovery time and actor must be present together")
        if self.recovered_at is not None and self.recovered_at < self.activated_at:
            raise ValueError("Recovery cannot predate activation")
        return self


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker(VersionedModel):
    schema_name: Literal["CircuitBreakerV1"] = "CircuitBreakerV1"
    breaker_id: Identifier
    scope: Identifier
    trigger: Identifier
    state: CircuitBreakerState
    threshold: int = Field(ge=1)
    observed: int = Field(ge=0)
    reason: NonEmptyStr | None = None
    automatic_recovery_limit: int = Field(default=0, ge=0, le=10)
    security_breaker: bool = False
    revision: int = Field(ge=1)
    opened_at: AwareDatetime | None = None
    updated_at: AwareDatetime

    @field_validator("opened_at", "updated_at")
    @classmethod
    def _breaker_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class McpServerState(StrEnum):
    DISCOVERED = "discovered"
    QUARANTINED = "quarantined"
    VALIDATED = "validated"
    APPROVED = "approved"
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class McpToolPolicy(StrEnum):
    BLOCKED = "blocked"
    CATALOG_ONLY = "catalog_only"
    READ_ONLY = "read_only"
    APPROVAL_REQUIRED = "approval_required"
    ENABLED = "enabled"


class McpToolDefinition(VersionedModel):
    schema_name: Literal["McpToolDefinitionV1"] = "McpToolDefinitionV1"
    tool_id: Identifier
    server_id: Identifier
    name: Identifier
    description: NonEmptyStr
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256 | None = None
    policy: McpToolPolicy = McpToolPolicy.CATALOG_ONLY
    timeout_ms: int = Field(default=10_000, ge=1, le=300_000)
    max_input_bytes: int = Field(default=65_536, ge=1, le=1_048_576)
    max_output_bytes: int = Field(default=262_144, ge=1, le=4_194_304)


class McpResourceDefinition(VersionedModel):
    schema_name: Literal["McpResourceDefinitionV1"] = "McpResourceDefinitionV1"
    resource_id: Identifier
    server_id: Identifier
    uri_template: NonEmptyStr
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    catalog_only: bool = True


class McpPromptDefinition(VersionedModel):
    schema_name: Literal["McpPromptDefinitionV1"] = "McpPromptDefinitionV1"
    prompt_id: Identifier
    server_id: Identifier
    name: Identifier
    content_sha256: Sha256
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    catalog_only: bool = True


class McpServerDefinition(VersionedModel):
    schema_name: Literal["McpServerDefinitionV1"] = "McpServerDefinitionV1"
    server_id: Identifier
    source: NonEmptyStr
    version: NonEmptyStr
    package_sha256: Sha256
    transport: Literal["stdio", "http", "sse"]
    executable: NonEmptyStr | None = None
    url: NonEmptyStr | None = None
    publisher: NonEmptyStr
    license_expression: NonEmptyStr
    tool_ids: tuple[Identifier, ...] = ()
    resource_ids: tuple[Identifier, ...] = ()
    prompt_ids: tuple[Identifier, ...] = ()
    environment_requirements: tuple[Identifier, ...] = ()
    filesystem_requirements: tuple[RelativePath, ...] = ()
    network_destinations: tuple[NonEmptyStr, ...] = ()
    secret_ids: tuple[Identifier, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    trust_class: Literal["untrusted", "community", "verified", "official"] = "untrusted"
    enabled: bool = False

    @model_validator(mode="after")
    def _server_endpoint(self) -> McpServerDefinition:
        if self.transport == "stdio" and self.executable is None:
            raise ValueError("stdio MCP servers require an executable declaration")
        if self.transport != "stdio" and self.url is None:
            raise ValueError("network MCP servers require a URL declaration")
        if self.executable is not None and self.url is not None:
            raise ValueError("MCP server endpoint must be unambiguous")
        return self


class McpServerRevision(VersionedModel):
    schema_name: Literal["McpServerRevisionV1"] = "McpServerRevisionV1"
    revision_id: Identifier
    server_id: Identifier
    definition_sha256: Sha256
    state: McpServerState
    previous_revision_id: Identifier | None = None
    approved_by: Identifier | None = None
    approval_id: Identifier | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _revision_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class McpConnection(VersionedModel):
    schema_name: Literal["McpConnectionV1"] = "McpConnectionV1"
    connection_id: Identifier
    server_revision_id: Identifier
    session_id: Identifier
    state: Literal["opening", "ready", "closed", "failed"]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _connection_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class McpHealth(VersionedModel):
    schema_name: Literal["McpHealthV1"] = "McpHealthV1"
    health_id: Identifier
    server_revision_id: Identifier
    state: Literal["unknown", "healthy", "degraded", "unhealthy", "disabled"]
    reason_codes: tuple[Identifier, ...] = ()
    checked_at: AwareDatetime

    @field_validator("checked_at")
    @classmethod
    def _health_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class McpPolicy(VersionedModel):
    schema_name: Literal["McpPolicyV1"] = "McpPolicyV1"
    policy_id: Identifier
    server_revision_id: Identifier
    tool_policies: dict[Identifier, McpToolPolicy]
    network_allowlist: tuple[NonEmptyStr, ...] = ()
    read_roots: tuple[RelativePath, ...] = ()
    write_roots: tuple[RelativePath, ...] = ()
    secret_ids: tuple[Identifier, ...] = ()
    policy_sha256: Sha256

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"policy_sha256"})

    def verify_hash(self) -> bool:
        return self.policy_sha256 == sha256_hex(canonical_json_bytes(self.identity_payload()))


class McpInvocation(VersionedModel):
    schema_name: Literal["McpInvocationV1"] = "McpInvocationV1"
    invocation_id: Identifier
    connection_id: Identifier
    tool_id: Identifier
    input_sha256: Sha256
    output_sha256: Sha256 | None = None
    policy_decision_id: Identifier
    approval_id: Identifier | None = None
    redacted: bool = True
    state: Literal["planned", "blocked", "running", "succeeded", "failed"]
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _invocation_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class AcpCapabilitySet(VersionedModel):
    schema_name: Literal["AcpCapabilitySetV1"] = "AcpCapabilitySetV1"
    capability_set_id: Identifier
    protocol_version: NonEmptyStr
    capabilities: tuple[Identifier, ...]
    negotiated_extensions: tuple[Identifier, ...] = ()


class AcpSession(VersionedModel):
    schema_name: Literal["AcpSessionV1"] = "AcpSessionV1"
    acp_session_id: Identifier
    runtime_session_id: Identifier
    adapter_id: Identifier
    capability_set_id: Identifier
    workspace_id: Identifier
    state: Literal["initializing", "ready", "streaming", "cancelled", "closed", "failed"]
    resume_token_sha256: Sha256 | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _acp_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AcpEvent(VersionedModel):
    schema_name: Literal["AcpEventV1"] = "AcpEventV1"
    event_id: Identifier
    acp_session_id: Identifier
    sequence: int = Field(ge=1)
    event_type: Literal[
        "message",
        "tool_call",
        "permission_request",
        "terminal_output",
        "file_change",
        "cancelled",
        "completed",
        "error",
    ]
    payload_sha256: Sha256
    policy_decision_id: Identifier | None = None
    approval_id: Identifier | None = None
    redacted: bool = True
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _acp_event_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"event_id"})

    def verify_id(self) -> bool:
        return self.event_id == derive_id("acpevt", self.identity_payload())


class A2AAgentCard(VersionedModel):
    schema_name: Literal["A2AAgentCardV1"] = "A2AAgentCardV1"
    agent_card_id: Identifier
    local_agent_id: Identifier
    protocol_version: NonEmptyStr
    capability_ids: tuple[Identifier, ...]
    authentication_schemes: tuple[Identifier, ...]
    extension_ids: tuple[Identifier, ...] = ()
    loopback_only: Literal[True] = True
    published: Literal[False] = False


class A2ATaskRequest(VersionedModel):
    schema_name: Literal["A2ATaskRequestV1"] = "A2ATaskRequestV1"
    request_id: Identifier
    remote_identity: NonEmptyStr
    authentication_sha256: Sha256
    agent_card_id: Identifier
    protocol_version: NonEmptyStr
    task_payload_sha256: Sha256
    artifact_hashes: dict[Identifier, Sha256] = Field(default_factory=dict)
    extension_ids: tuple[Identifier, ...] = ()
    local_proposal_id: Identifier | None = None
    trusted: Literal[False] = False
    quarantined: bool = True
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _a2a_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class A2ATaskStatus(VersionedModel):
    schema_name: Literal["A2ATaskStatusV1"] = "A2ATaskStatusV1"
    status_id: Identifier
    request_id: Identifier
    local_task_id: Identifier | None = None
    state: Literal[
        "received",
        "quarantined",
        "proposed",
        "accepted",
        "running",
        "done",
        "cancelled",
        "rejected",
    ]
    artifact_ids: tuple[Identifier, ...] = ()
    policy_decision_id: Identifier
    updated_at: AwareDatetime

    @field_validator("updated_at")
    @classmethod
    def _a2a_status_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
