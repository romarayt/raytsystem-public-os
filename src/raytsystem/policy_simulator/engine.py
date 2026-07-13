from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from raytsystem.contracts import PolicySimulation, canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.governance import ExecutionPlan
from raytsystem.features import FeatureConfig

_SIDE_EFFECTS = frozenset(
    {"send", "publish", "upload", "delete", "pay", "git_push", "pull_request"}
)
_READ_ONLY_TOOLS = frozenset(
    {
        "read_catalog",
        "get_status",
        "list_tasks",
        "search_knowledge",
        "query_graph",
        "catalog_schemas",
    }
)
_PROTECTED_ROOTS = (
    "_raw",
    "ledger/objects",
    "ledger/generations",
    "knowledge/claims",
    "knowledge/entities",
    "knowledge/sources",
)
_STAGING_WRITE_ROOTS = ("ops/staging", ".raytsystem/worktrees")


def evaluate_execution(
    plan: ExecutionPlan,
    *,
    features: FeatureConfig,
    emergency_actions: frozenset[str] = frozenset(),
    granted_approval_kinds: frozenset[str] = frozenset(),
    runtime_execution_enabled: bool = True,
    runtime_adapter_enabled: bool = True,
    runtime_reason_codes: frozenset[str] = frozenset(),
    runtime_required_approvals: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> PolicySimulation:
    """Pure execution policy shared by simulation and runtime authorization."""

    reasons: set[str] = set(runtime_reason_codes)
    approvals: set[str] = set(runtime_required_approvals)
    allowed_tools: set[str] = set()
    blocked_tools: set[str] = set()

    reasons.update(
        runtime_feature_reasons(
            plan.runtime_id,
            runtime_execution_enabled=runtime_execution_enabled,
            runtime_adapter_enabled=runtime_adapter_enabled,
        )
    )

    roots = (*plan.read_roots, *plan.write_roots)
    if any(_under_root(path, _PROTECTED_ROOTS) for path in roots):
        reasons.add("protected_workspace_root")
    if plan.workspace_mode in {"none", "read_only"} and plan.write_roots:
        reasons.add("workspace_write_not_allowed")
    if plan.workspace_mode == "staging_only" and any(
        not _under_root(path, _STAGING_WRITE_ROOTS) for path in plan.write_roots
    ):
        reasons.add("write_outside_staging")

    if (
        "disable_runtime_execution" in emergency_actions
        or "pause_all_employees" in emergency_actions
    ):
        reasons.add("emergency_runtime_disabled")
    if plan.network_access != "none":
        approvals.add("network_egress")
        if "disable_network_adapters" in emergency_actions:
            reasons.add("emergency_network_disabled")
        if not plan.network_destinations:
            reasons.add("network_destination_missing")
    if plan.provider is not None:
        approvals.add("model_egress")
        if "disable_external_providers" in emergency_actions:
            reasons.add("emergency_provider_disabled")
    if plan.requested_secrets:
        approvals.add("secret_decrypt")
        if not features.enabled("restricted_encryption_enabled"):
            reasons.add("secret_provider_unavailable")
    for side_effect in plan.potential_side_effects:
        if side_effect in _SIDE_EFFECTS:
            approvals.add(side_effect)
        else:
            reasons.add("unknown_side_effect")
    for tool in plan.requested_tools:
        if tool in _READ_ONLY_TOOLS:
            allowed_tools.add(tool)
        elif tool.startswith("mcp_"):
            if not features.enabled("mcp_governance_enabled"):
                blocked_tools.add(tool)
                reasons.add("mcp_governance_disabled")
            elif not features.enabled("external_mcp_execution_enabled"):
                blocked_tools.add(tool)
                reasons.add("mcp_catalog_only")
            else:
                approvals.add("mcp_tool_execution")
                allowed_tools.add(tool)
        else:
            approvals.add("tool_execution")
            allowed_tools.add(tool)
    missing_approvals = approvals - granted_approval_kinds
    if reasons:
        outcome = "blocked"
    elif missing_approvals:
        outcome = "approval_required"
    else:
        outcome = "allowed"
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    identity: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "policy_sha256": plan.policy_sha256,
        "employee_id": plan.employee_id,
        "task_id": plan.task_id,
        "runtime_id": plan.runtime_id,
        "provider": plan.provider,
        "model": plan.model,
        "workspace_mode": plan.workspace_mode,
        "read_roots": plan.read_roots,
        "write_roots": plan.write_roots,
        "network_access": plan.network_access,
        "graph_scope": plan.graph_scope,
        "knowledge_scope": plan.knowledge_scope,
        "token_budget": plan.token_budget,
        "cost_budget": plan.cost_budget,
        "allowed_tools": sorted(allowed_tools),
        "blocked_tools": sorted(blocked_tools),
        "secrets_requested": sorted(plan.requested_secrets),
        "required_approvals": sorted(missing_approvals),
        "potential_side_effects": sorted(plan.potential_side_effects),
        "outcome": outcome,
        "reason_codes": sorted(reasons),
        "dry_run": True,
        "created_at": created_at,
    }
    return PolicySimulation(
        simulation_id=derive_id(
            "psim",
            {
                "plan_id": plan.plan_id,
                "policy_sha256": plan.policy_sha256,
                "decision_sha256": sha256_hex(canonical_json_bytes(identity)),
            },
        ),
        **identity,
    )


def _under_root(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def runtime_feature_reasons(
    runtime_id: str,
    *,
    runtime_execution_enabled: bool,
    runtime_adapter_enabled: bool,
) -> frozenset[str]:
    del runtime_id
    if not runtime_execution_enabled:
        return frozenset({"runtime_execution_disabled"})
    if not runtime_adapter_enabled:
        return frozenset({"runtime_adapter_disabled"})
    return frozenset()
