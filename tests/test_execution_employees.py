from __future__ import annotations

import pytest

from raytsystem.contracts import (
    AgentDefinition,
    RuntimeAdapterDefinition,
    RuntimeAdapterState,
)
from raytsystem.contracts.execution import EmployeeStatus, WorkspaceMode
from raytsystem.execution.config import FeatureFlags
from raytsystem.execution.employees import EmployeeCatalogError, employee_from_definition


def _agent(*, enabled: bool = True, filesystem: str = "staging_only") -> AgentDefinition:
    return AgentDefinition(
        agent_id="agent_builder",
        name="Builder",
        role="software_engineer",
        description="Implements an approved task in an isolated worktree.",
        version="1.0.0",
        pack_id="pack_core",
        runtime_adapter_id="adapter_fake",
        skill_ids=("skill_build",),
        context_paths=("AGENTS.md",),
        capabilities=("code_edit",),
        requested_filesystem_mode=filesystem,  # type: ignore[arg-type]
        accent="#123456",
        enabled=enabled,
    )


def _adapter(adapter_id: str = "adapter_fake") -> RuntimeAdapterDefinition:
    return RuntimeAdapterDefinition(
        adapter_id=adapter_id,
        name="Deterministic fake",
        version="1.0.0",
        state=RuntimeAdapterState.AVAILABLE,
        isolation_mode="managed_worktree",
        capabilities=("code_edit",),
    )


def test_employee_identity_and_revision_are_deterministic() -> None:
    flags = FeatureFlags(runtime_execution_enabled=True)

    first = employee_from_definition(_agent(), _adapter(), flags=flags)
    second = employee_from_definition(_agent(), _adapter(), flags=flags)

    assert first == second
    assert first.employee_id.startswith("employee_")
    assert first.status is EmployeeStatus.IDLE
    assert first.filesystem_policy.mode is WorkspaceMode.TASK_WORKTREE


def test_employee_stays_disabled_behind_global_runtime_flag() -> None:
    employee = employee_from_definition(_agent(), _adapter(), flags=FeatureFlags())

    assert employee.status is EmployeeStatus.DISABLED


def test_read_only_agent_never_gets_staged_write() -> None:
    employee = employee_from_definition(
        _agent(filesystem="read_only"),
        _adapter(),
        flags=FeatureFlags(runtime_execution_enabled=True),
    )

    assert employee.filesystem_policy.mode is WorkspaceMode.WORKSPACE_ROOT_READONLY
    assert not employee.filesystem_policy.allow_staged_write


def test_agent_and_adapter_mismatch_fails_closed() -> None:
    with pytest.raises(EmployeeCatalogError, match="do not match"):
        employee_from_definition(
            _agent(),
            _adapter("adapter_other"),
            flags=FeatureFlags(runtime_execution_enabled=True),
        )
