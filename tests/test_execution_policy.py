from __future__ import annotations

from datetime import UTC, datetime, timedelta

from raytsystem.contracts import ApprovalRecord, PolicyOutcome, Sensitivity
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    FilesystemPolicy,
    TokenBudget,
)
from raytsystem.execution.config import FeatureFlags
from raytsystem.execution.policy import ExecutionPolicyRequest, evaluate_execution_policy

HASH = "a" * 64
NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _request(adapter_id: str = "adapter_fake", **updates: object) -> ExecutionPolicyRequest:
    values: dict[str, object] = {
        "target_id": "xinv_example",
        "payload_sha256": HASH,
        "adapter_id": adapter_id,
        "filesystem_policy": FilesystemPolicy(),
        "sensitivity": Sensitivity.INTERNAL,
        "graph_current": True,
        "graph_required": True,
    }
    values.update(updates)
    return ExecutionPolicyRequest(**values)  # type: ignore[arg-type]


def test_fake_runtime_is_allowed_only_after_runtime_gate() -> None:
    allowed = evaluate_execution_policy(
        _request(),
        flags=FeatureFlags(runtime_execution_enabled=True),
        at=NOW,
    )
    denied = evaluate_execution_policy(_request(), flags=FeatureFlags(), at=NOW)

    assert allowed.outcome is PolicyOutcome.ALLOW
    assert denied.outcome is PolicyOutcome.DENY


def test_stale_required_graph_fails_closed() -> None:
    decision = evaluate_execution_policy(
        _request(graph_current=False),
        flags=FeatureFlags(runtime_execution_enabled=True),
        at=NOW,
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert "code_graph_not_current" in decision.reason_codes


def test_provider_runtime_requires_exact_scoped_egress_approval() -> None:
    flags = FeatureFlags(runtime_execution_enabled=True, codex_local_enabled=True)

    required = evaluate_execution_policy(
        _request("adapter_codex_local"),
        flags=flags,
        at=NOW,
    )
    approval = ApprovalRecord.create(
        action="execute_runtime",
        target_id="xinv_example",
        artifact_sha256=HASH,
        destination="provider:openai",
        scope=("private_corpus_egress", "provider_egress", "runtime_execution"),
        policy_version="1.0.0",
        approver="user:local",
        approved_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    allowed = evaluate_execution_policy(
        _request("adapter_codex_local", approval=approval),
        flags=flags,
        at=NOW,
    )

    assert required.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert allowed.outcome is PolicyOutcome.ALLOW


def test_exhausted_budget_is_denied_before_execution() -> None:
    policy = BudgetPolicy.create(
        scope_kind="task",
        scope_id="task_example",
        token_limit=TokenBudget(input_tokens=100),
        created_at=NOW,
    )
    usage = BudgetUsage(
        budget_policy_id=policy.budget_policy_id,
        tokens=TokenBudget(input_tokens=100),
        updated_at=NOW,
    )

    decision = evaluate_execution_policy(
        _request(budget_policy=policy, budget_usage=usage),
        flags=FeatureFlags(runtime_execution_enabled=True),
        at=NOW,
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert "budget_exhausted" in decision.reason_codes
