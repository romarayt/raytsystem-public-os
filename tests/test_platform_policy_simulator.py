"""Policy simulator: same policy functions as a real run, full plan echo, no writes."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from platform_helpers import make_platform_workspace
from raytsystem.contracts import PolicyOutcome
from raytsystem.contracts.governance import EmergencyAction, ExecutionPlan
from raytsystem.emergency import EmergencyService
from raytsystem.execution.config import load_execution_config
from raytsystem.execution.policy import evaluate_execution_policy
from raytsystem.policy_simulator import PolicySimulator, PolicySimulatorError

pytestmark = pytest.mark.filterwarnings("error")


def _workspace(
    root: Path,
    *,
    flag_overrides: dict[str, bool] | None = None,
    toml_features: dict[str, bool] | None = None,
) -> Path:
    make_platform_workspace(root, flag_overrides=flag_overrides)
    (root / "config" / "policies.yaml").write_text(
        'version: "1.0.0"\npromotion:\n  fixture: autonomous\n  real: manual_hash_bound\n',
        encoding="utf-8",
    )
    features = (
        {"runtime_execution_enabled": True, "codex_local_enabled": True}
        if toml_features is None
        else toml_features
    )
    lines = ['control_db = "ops/control.sqlite"', "", "[features]"]
    lines.extend(
        f"{key} = {'true' if value else 'false'}" for key, value in sorted(features.items())
    )
    (root / "config" / "raytsystem.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _plan(root: Path, **overrides: object) -> ExecutionPlan:
    payload: dict[str, object] = {
        "plan_id": "plan_simulated",
        "employee_id": "employee_dev",
        "task_id": "task_demo",
        "task_revision": 1,
        "runtime_id": "adapter_fake",
        "workspace_mode": "staging_only",
        "read_roots": ("ops/staging",),
        "write_roots": ("ops/staging",),
        "policy_sha256": PolicySimulator(root).policy_sha256,
    }
    payload.update(overrides)
    return ExecutionPlan.model_validate(payload)


def test_policy_simulation_matches_runtime_preflight(tmp_path: Path) -> None:
    enabled_root = _workspace(tmp_path / "enabled")
    disabled_root = _workspace(
        tmp_path / "disabled", toml_features={"runtime_execution_enabled": False}
    )
    matrix: tuple[tuple[Path, ExecutionPlan, str], ...] = (
        (enabled_root, _plan(enabled_root), "allowed"),
        (
            enabled_root,
            _plan(
                enabled_root,
                plan_id="plan_provider",
                runtime_id="adapter_codex_local",
                provider="openai",
                model="gpt-5-codex",
            ),
            "approval_required",
        ),
        (disabled_root, _plan(disabled_root), "blocked"),
        (
            enabled_root,
            _plan(
                enabled_root,
                plan_id="plan_protected",
                read_roots=("ledger/objects",),
                write_roots=(),
            ),
            "blocked",
        ),
        (
            enabled_root,
            _plan(
                enabled_root,
                plan_id="plan_outside_staging",
                read_roots=("notes",),
                write_roots=("notes",),
            ),
            "blocked",
        ),
        (
            enabled_root,
            _plan(
                enabled_root,
                plan_id="plan_budget",
                token_budget=1000,
                cost_budget=Decimal("2.5"),
            ),
            "allowed",
        ),
    )
    for root, plan, expected_outcome in matrix:
        simulator = PolicySimulator(root)
        flags = load_execution_config(root).features
        decision = evaluate_execution_policy(simulator.runtime_policy_request(plan), flags=flags)
        simulation = simulator.simulate(plan)
        assert simulation.outcome == expected_outcome, plan.plan_id
        if decision.outcome is PolicyOutcome.DENY:
            assert simulation.outcome == "blocked", plan.plan_id
            assert set(decision.reason_codes) <= set(simulation.reason_codes), plan.plan_id
        elif decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            assert simulation.outcome != "allowed", plan.plan_id
            assert set(decision.required_approval_scope) <= set(simulation.required_approvals)


def test_provider_egress_requires_full_runtime_approval_scope(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = _plan(
        root,
        plan_id="plan_provider",
        runtime_id="adapter_codex_local",
        provider="openai",
        model="gpt-5-codex",
    )
    simulation = PolicySimulator(root).simulate(plan)
    assert simulation.outcome == "approval_required"
    assert {
        "model_egress",
        "private_corpus_egress",
        "provider_egress",
        "runtime_execution",
    } <= set(simulation.required_approvals)


def test_disabled_runtime_and_boundary_violations_are_blocked(tmp_path: Path) -> None:
    disabled_root = _workspace(
        tmp_path / "disabled", toml_features={"runtime_execution_enabled": False}
    )
    disabled = PolicySimulator(disabled_root).simulate(_plan(disabled_root))
    assert disabled.outcome == "blocked"
    assert "runtime_execution_disabled" in disabled.reason_codes

    root = _workspace(tmp_path / "enabled")
    simulator = PolicySimulator(root)
    protected = simulator.simulate(
        _plan(root, plan_id="plan_protected", read_roots=("ledger/objects",), write_roots=())
    )
    assert protected.outcome == "blocked"
    assert "protected_workspace_root" in protected.reason_codes
    outside = simulator.simulate(
        _plan(root, plan_id="plan_outside", read_roots=("notes",), write_roots=("notes",))
    )
    assert outside.outcome == "blocked"
    assert "write_outside_staging" in outside.reason_codes


def test_simulation_performs_no_writes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    simulation = PolicySimulator(root).simulate(_plan(root, token_budget=500))
    assert simulation.dry_run is True
    after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    assert before == after
    assert not (root / "ops" / "platform.sqlite").exists()
    assert not (root / ".raytsystem").exists()


def test_stale_policy_hash_is_rejected(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = _plan(root).model_copy(update={"policy_sha256": "f" * 64})
    with pytest.raises(PolicySimulatorError, match="stale"):
        PolicySimulator(root).simulate(ExecutionPlan.model_validate(plan.model_dump(mode="json")))


def test_disabled_feature_flag_fails_closed(tmp_path: Path) -> None:
    root = _workspace(tmp_path, flag_overrides={"policy_simulator_enabled": False})
    plan = _plan(root)
    with pytest.raises(PolicySimulatorError, match="disabled"):
        PolicySimulator(root).simulate(plan)


def test_active_emergency_blocks_simulation(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    EmergencyService(root).activate(
        (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
        reason="containment drill",
        actor_id="user_local_test",
        idempotency_key="emergency-drill",
    )
    simulation = PolicySimulator(root).simulate(_plan(root))
    assert simulation.outcome == "blocked"
    assert "emergency_runtime_disabled" in simulation.reason_codes


def test_unreadable_emergency_store_fails_closed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "ops").mkdir()
    (root / "ops" / "platform.sqlite").write_bytes(b"this is not a sqlite database")
    simulation = PolicySimulator(root).simulate(_plan(root))
    assert simulation.outcome == "blocked"
    assert "emergency_state_unavailable" in simulation.reason_codes


def test_simulation_echoes_all_plan_facts(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = _plan(
        root,
        plan_id="plan_echo",
        runtime_id="adapter_codex_local",
        provider="openai",
        model="gpt-5-codex",
        network_access="allowlist",
        network_destinations=("provider:openai",),
        graph_scope=("graph_scope_main",),
        knowledge_scope=("knowledge_scope_main",),
        token_budget=2048,
        cost_budget=Decimal("1.25"),
    )
    simulation = PolicySimulator(root).simulate(plan)
    assert simulation.employee_id == plan.employee_id
    assert simulation.task_id == plan.task_id
    assert simulation.runtime_id == plan.runtime_id
    assert simulation.provider == plan.provider
    assert simulation.model == plan.model
    assert simulation.workspace_mode == plan.workspace_mode
    assert simulation.read_roots == plan.read_roots
    assert simulation.write_roots == plan.write_roots
    assert simulation.network_access == plan.network_access
    assert simulation.graph_scope == plan.graph_scope
    assert simulation.knowledge_scope == plan.knowledge_scope
    assert simulation.token_budget == plan.token_budget
    assert simulation.cost_budget == plan.cost_budget


def test_requested_secrets_require_decrypt_and_fail_without_provider(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    simulation = PolicySimulator(root).simulate(
        _plan(root, plan_id="plan_secrets", requested_secrets=("secret_api_token",))
    )
    assert simulation.outcome == "blocked"
    assert "secret_provider_unavailable" in simulation.reason_codes
    assert "secret_decrypt" in simulation.required_approvals
    assert simulation.secrets_requested == ("secret_api_token",)


def test_simulate_and_authorize_execution_agree(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    simulator = PolicySimulator(root)
    allowed_plan = _plan(root)
    simulated = simulator.simulate(allowed_plan)
    authorized = simulator.authorize_execution(allowed_plan)
    assert simulated.outcome == authorized.outcome == "allowed"
    assert simulated.reason_codes == authorized.reason_codes
    assert simulated.required_approvals == authorized.required_approvals
    assert simulated.plan_id == authorized.plan_id

    provider_plan = _plan(
        root,
        plan_id="plan_provider",
        runtime_id="adapter_codex_local",
        provider="openai",
    )
    assert simulator.simulate(provider_plan).outcome == "approval_required"
    with pytest.raises(PolicySimulatorError, match="not authorized"):
        simulator.authorize_execution(provider_plan)
    with pytest.raises(PolicySimulatorError, match="resolved approval records"):
        simulator.authorize_execution(
            allowed_plan, granted_approval_kinds=frozenset({"provider_egress"})
        )
