from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from raytsystem.contracts import (
    PolicyOutcome,
    PolicySimulation,
    Sensitivity,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.contracts.execution import (
    BudgetPolicy,
    FilesystemPolicy,
    TokenBudget,
    WorkspaceMode,
)
from raytsystem.contracts.governance import ExecutionPlan
from raytsystem.execution.config import ExecutionConfig, ExecutionConfigError, load_execution_config
from raytsystem.features import FeatureConfig, load_feature_config

if TYPE_CHECKING:
    from raytsystem.execution.policy import ExecutionPolicyRequest
from raytsystem.platform_store import (
    PLATFORM_DB_RELATIVE,
    PlatformStoreError,
    open_platform_store_read_only,
)
from raytsystem.policy_simulator.engine import evaluate_execution
from raytsystem.security.paths import PathPolicyError, read_regular_file

_EMERGENCY_UNAVAILABLE = frozenset({"emergency_state_unavailable"})


class PolicySimulatorError(RuntimeError):
    """The requested simulation does not match the active execution policy."""


class PolicySimulator:
    def __init__(self, root: Path, *, features: FeatureConfig | None = None) -> None:
        self.root = root.resolve()
        self.features = features or load_feature_config(self.root)

    @property
    def policy_sha256(self) -> str:
        payload: dict[str, str] = {}
        for relative in ("config/policies.yaml", "config/platform.yaml"):
            try:
                data = read_regular_file(self.root, relative, max_bytes=512 * 1024).data
            except (OSError, PathPolicyError) as error:
                raise PolicySimulatorError(
                    "Execution policy configuration is unavailable"
                ) from error
            payload[relative] = sha256_hex(data)
        return sha256_hex(canonical_json_bytes(payload))

    def simulate(
        self,
        plan: ExecutionPlan,
        *,
        granted_approval_kinds: frozenset[str] = frozenset(),
    ) -> PolicySimulation:
        if not self.features.enabled("policy_simulator_enabled"):
            raise PolicySimulatorError("Policy simulator is disabled")
        if plan.policy_sha256 != self.policy_sha256:
            raise PolicySimulatorError("Execution plan is bound to a stale policy")
        # Imported lazily: raytsystem.execution.policy imports policy_simulator.engine
        # at module load, so a top-level import here would be circular.
        from raytsystem.execution.policy import evaluate_execution_policy

        execution = self._execution_config()
        emergency_actions, runtime_reasons = self._emergency_state()
        decision = evaluate_execution_policy(
            self.runtime_policy_request(plan, execution=execution),
            flags=execution.features,
        )
        runtime_approvals: frozenset[str] = frozenset()
        if decision.outcome is PolicyOutcome.DENY:
            runtime_reasons = runtime_reasons | frozenset(decision.reason_codes)
        elif decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            runtime_approvals = frozenset(decision.required_approval_scope)
        return evaluate_execution(
            plan,
            features=self.features,
            emergency_actions=emergency_actions,
            granted_approval_kinds=granted_approval_kinds,
            runtime_execution_enabled=execution.features.runtime_execution_enabled,
            runtime_adapter_enabled=execution.features.adapter_enabled(plan.runtime_id),
            runtime_reason_codes=runtime_reasons,
            runtime_required_approvals=runtime_approvals,
        )

    def authorize_execution(
        self,
        plan: ExecutionPlan,
        *,
        granted_approval_kinds: frozenset[str] = frozenset(),
    ) -> PolicySimulation:
        """Runtime gate: intentionally calls the exact same pure policy as simulation."""

        if granted_approval_kinds:
            raise PolicySimulatorError(
                "Runtime authorization requires resolved approval records, not caller claims"
            )
        decision = self.simulate(plan, granted_approval_kinds=frozenset())
        if decision.outcome != "allowed":
            raise PolicySimulatorError(
                "Execution is not authorized: " + ",".join(decision.reason_codes)
            )
        return decision

    def runtime_policy_request(
        self,
        plan: ExecutionPlan,
        *,
        execution: ExecutionConfig | None = None,
    ) -> ExecutionPolicyRequest:
        """Map the dry-run plan onto the exact request the runtime preflight evaluates."""

        # Imported lazily: raytsystem.execution.policy imports policy_simulator.engine
        # at module load, so a top-level import here would be circular.
        from raytsystem.execution.policy import ExecutionPolicyRequest

        config = execution or self._execution_config()
        return ExecutionPolicyRequest(
            target_id=plan.plan_id,
            payload_sha256=sha256_hex(canonical_json_bytes(plan.model_dump(mode="json"))),
            adapter_id=plan.runtime_id,
            filesystem_policy=self._filesystem_policy(plan.workspace_mode),
            # A dry run cannot resolve the task record, so sensitivity fails closed
            # to the widest provider-egress approval scope.
            sensitivity=Sensitivity.RESTRICTED,
            # Workspace preparation pins a fresh graph snapshot before the runtime
            # preflight runs, so a compliant run always evaluates with a current graph.
            graph_current=True,
            graph_required=config.features.code_graph_enabled,
            employee_id=plan.employee_id,
            task_id=plan.task_id,
            budget_policy=self._budget_policy(plan),
            budget_usage=None,
            approval=None,
        )

    def _execution_config(self) -> ExecutionConfig:
        try:
            return load_execution_config(self.root)
        except ExecutionConfigError as error:
            raise PolicySimulatorError("Execution configuration is unavailable") from error

    @staticmethod
    def _filesystem_policy(workspace_mode: str) -> FilesystemPolicy:
        if workspace_mode in {"staging_only", "isolated"}:
            return FilesystemPolicy(mode=WorkspaceMode.TASK_WORKTREE)
        return FilesystemPolicy(
            mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
            allow_staged_write=False,
        )

    @staticmethod
    def _budget_policy(plan: ExecutionPlan) -> BudgetPolicy | None:
        if plan.token_budget is None and plan.cost_budget is None:
            return None
        # BudgetPolicy has no aggregate token cap; the plan total binds output tokens,
        # and cost truncation keeps the simulated cap at least as strict as the plan.
        cost_limit = None if plan.cost_budget is None else int(plan.cost_budget * 1_000_000)
        return BudgetPolicy.create(
            scope_kind="task",
            scope_id=plan.task_id,
            token_limit=TokenBudget(output_tokens=plan.token_budget or 0),
            created_at=datetime.now(UTC),
            cost_limit_micros=cost_limit,
        )

    def _emergency_state(self) -> tuple[frozenset[str], frozenset[str]]:
        if not (self.root / PLATFORM_DB_RELATIVE).is_file():
            return frozenset(), frozenset()
        store = open_platform_store_read_only(self.root)
        if store is None:
            return frozenset(), _EMERGENCY_UNAVAILABLE
        try:
            with store:
                records = store.list_heads("emergency", state="active", limit=100)
                actions: set[str] = set()
                for record in records:
                    values = record.payload.get("active_actions", [])
                    if isinstance(values, list):
                        actions.update(str(value) for value in values)
                return frozenset(actions), frozenset()
        except (OSError, sqlite3.Error, PlatformStoreError):
            return frozenset(), _EMERGENCY_UNAVAILABLE
