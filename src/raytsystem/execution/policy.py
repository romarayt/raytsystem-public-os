from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from raytsystem.contracts import (
    ApprovalRecord,
    PolicyDecision,
    PolicyOutcome,
    Sensitivity,
)
from raytsystem.contracts.base import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    ExecutionApproval,
    FilesystemPolicy,
)
from raytsystem.execution.config import FeatureFlags
from raytsystem.policy_simulator.engine import runtime_feature_reasons

_POLICY_VERSION = "1.0.0"
_POLICY_DOCUMENT = {
    "runtime_requires_dual_feature_gate": True,
    "managed_workspace_only": True,
    "graph_first_fails_closed": True,
    "provider_egress_requires_scoped_approval": True,
    "external_side_effects_forbidden": True,
    "budget_hard_stop": True,
}
_POLICY_SHA256 = sha256_hex(canonical_json_bytes(_POLICY_DOCUMENT))
_PROVIDER_DESTINATIONS = {
    "adapter_codex_local": "provider:openai",
    "adapter_claude_code": "provider:anthropic",
}


@dataclass(frozen=True)
class ExecutionPolicyRequest:
    target_id: str
    payload_sha256: str
    adapter_id: str
    filesystem_policy: FilesystemPolicy
    sensitivity: Sensitivity
    graph_current: bool
    graph_required: bool
    employee_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    workspace_id: str | None = None
    budget_policy: BudgetPolicy | None = None
    budget_usage: BudgetUsage | None = None
    approval: ApprovalRecord | ExecutionApproval | None = None
    scheduled_wake: bool = False


def _budget_exhausted(policy: BudgetPolicy, usage: BudgetUsage | None) -> bool:
    if usage is None:
        return False
    if usage.budget_policy_id != policy.budget_policy_id:
        return True
    token_cap = policy.token_limit
    token_usage = usage.tokens
    token_limits = (
        (token_cap.input_tokens, token_usage.input_tokens),
        (token_cap.output_tokens, token_usage.output_tokens),
        (token_cap.cached_tokens, token_usage.cached_tokens),
    )
    if any(cap > 0 and consumed >= cap for cap, consumed in token_limits):
        return True
    if policy.cost_limit_micros is not None:
        observed_cost = usage.actual_cost_micros
        if observed_cost is None:
            observed_cost = usage.estimated_cost_micros
        if observed_cost is not None and observed_cost >= policy.cost_limit_micros:
            return True
    if policy.run_limit > 0 and usage.run_count >= policy.run_limit:
        return True
    return policy.heartbeat_limit > 0 and usage.heartbeat_count >= policy.heartbeat_limit


def evaluate_execution_policy(
    request: ExecutionPolicyRequest,
    *,
    flags: FeatureFlags,
    at: datetime | None = None,
) -> PolicyDecision:
    evaluated_at = (at or datetime.now(UTC)).astimezone(UTC)
    destination = _PROVIDER_DESTINATIONS.get(request.adapter_id)
    reasons: list[str] = []
    approval_scope: tuple[str, ...] = ()
    outcome = PolicyOutcome.ALLOW

    runtime_reasons = runtime_feature_reasons(
        request.adapter_id,
        runtime_execution_enabled=flags.runtime_execution_enabled,
        runtime_adapter_enabled=flags.adapter_enabled(request.adapter_id),
    )

    if runtime_reasons:
        reasons.extend(sorted(runtime_reasons))
        outcome = PolicyOutcome.DENY
    elif not flags.digital_employees_enabled:
        reasons.append("digital_employees_disabled")
        outcome = PolicyOutcome.DENY
    elif not flags.adapter_enabled(request.adapter_id):
        reasons.append("runtime_adapter_disabled")
        outcome = PolicyOutcome.DENY
    elif request.scheduled_wake and not flags.scheduled_heartbeats_enabled:
        reasons.append("scheduled_heartbeats_disabled")
        outcome = PolicyOutcome.DENY
    elif request.graph_required and not request.graph_current:
        reasons.append("code_graph_not_current")
        outcome = PolicyOutcome.DENY
    elif not request.filesystem_policy.allow_workspace_read:
        reasons.append("workspace_read_not_granted")
        outcome = PolicyOutcome.DENY
    elif request.budget_policy is not None and _budget_exhausted(
        request.budget_policy,
        request.budget_usage,
    ):
        reasons.append("budget_exhausted")
        outcome = PolicyOutcome.DENY
    elif destination is not None:
        approval_scope = (
            ("private_corpus_egress", "provider_egress", "runtime_execution")
            if request.sensitivity is not Sensitivity.PUBLIC
            else ("provider_egress", "runtime_execution")
        )
        approval = request.approval
        if approval is None:
            reasons.append("provider_egress_approval_required")
            outcome = PolicyOutcome.REQUIRE_APPROVAL
        elif not _approval_valid(
            approval,
            request=request,
            destination=destination,
            required_scope=approval_scope,
            at=evaluated_at,
        ):
            reasons.append("provider_egress_approval_invalid")
            outcome = PolicyOutcome.DENY
        else:
            reasons.append("scoped_provider_egress_approved")
    else:
        reasons.append("local_deterministic_adapter")

    material = {
        "action": "execute_runtime",
        "target_id": request.target_id,
        "payload_sha256": request.payload_sha256,
        "destination": destination,
        "policy_version": _POLICY_VERSION,
        "policy_sha256": _POLICY_SHA256,
        "outcome": outcome,
        "reason_codes": tuple(reasons),
        "required_approval_scope": approval_scope,
        "evaluated_at": evaluated_at,
    }
    return PolicyDecision(
        policy_decision_id=derive_id("pdec", material),
        action="execute_runtime",
        target_id=request.target_id,
        payload_sha256=request.payload_sha256,
        destination=destination,
        policy_version=_POLICY_VERSION,
        policy_sha256=_POLICY_SHA256,
        outcome=outcome,
        reason_codes=tuple(reasons),
        required_approval_scope=approval_scope,
        evaluated_at=evaluated_at,
    )


def _approval_valid(
    approval: ApprovalRecord | ExecutionApproval,
    *,
    request: ExecutionPolicyRequest,
    destination: str,
    required_scope: tuple[str, ...],
    at: datetime,
) -> bool:
    if not set(required_scope).issubset(approval.scope):
        return False
    if isinstance(approval, ApprovalRecord):
        return approval.is_valid_for(
            action="execute_runtime",
            target_id=request.target_id,
            artifact_sha256=request.payload_sha256,
            destination=destination,
            at=at,
        )
    return bool(
        approval.action == "execute_runtime"
        and approval.payload_sha256 == request.payload_sha256
        and approval.destination == destination
        and approval.employee_id == request.employee_id
        and approval.task_id == request.task_id
        and approval.run_id == request.run_id
        and approval.workspace_id == request.workspace_id
        and approval.approved_at <= at < approval.expires_at
    )
