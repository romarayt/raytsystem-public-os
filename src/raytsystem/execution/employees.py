from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from raytsystem.catalog import CatalogService, CatalogSnapshot
from raytsystem.contracts import (
    AgentDefinition,
    RuntimeAdapterDefinition,
    RuntimeAdapterState,
)
from raytsystem.contracts.base import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.execution import (
    DigitalEmployee,
    EmployeeStatus,
    FilesystemPolicy,
    GraphPolicy,
    HeartbeatPolicy,
    WorkspaceMode,
)
from raytsystem.execution.config import FeatureFlags


class EmployeeCatalogError(ValueError):
    """Raised when catalog definitions cannot form a safe employee registry."""


@dataclass(frozen=True)
class EmployeeCatalogSnapshot:
    catalog_sha256: str
    employees: tuple[DigitalEmployee, ...]

    def get(self, employee_id: str) -> DigitalEmployee | None:
        return next((item for item in self.employees if item.employee_id == employee_id), None)


def _filesystem_policy(agent: AgentDefinition) -> FilesystemPolicy:
    if agent.requested_filesystem_mode == "staging_only":
        return FilesystemPolicy(
            mode=WorkspaceMode.TASK_WORKTREE,
            allow_workspace_read=True,
            allow_staged_write=True,
        )
    return FilesystemPolicy(
        mode=WorkspaceMode.WORKSPACE_ROOT_READONLY,
        allow_workspace_read=agent.requested_filesystem_mode == "read_only",
        allow_staged_write=False,
    )


def _configuration_revision(
    *,
    agent: AgentDefinition,
    adapter: RuntimeAdapterDefinition,
    filesystem_policy: FilesystemPolicy,
    graph_policy: GraphPolicy,
    heartbeat_policy: HeartbeatPolicy,
) -> str:
    material = {
        "agent": agent,
        "adapter": adapter,
        "filesystem_policy": filesystem_policy,
        "graph_policy": graph_policy,
        "heartbeat_policy": heartbeat_policy,
    }
    return sha256_hex(canonical_json_bytes(material))


def employee_from_definition(
    agent: AgentDefinition,
    adapter: RuntimeAdapterDefinition,
    *,
    flags: FeatureFlags,
) -> DigitalEmployee:
    if adapter.adapter_id != agent.runtime_adapter_id:
        raise EmployeeCatalogError("Agent and runtime adapter definitions do not match")
    filesystem_policy = _filesystem_policy(agent)
    graph_policy = GraphPolicy()
    heartbeat_policy = HeartbeatPolicy(
        manual_enabled=flags.heartbeats_enabled,
        scheduled_enabled=False,
        interval_seconds=0,
    )
    enabled = (
        flags.digital_employees_enabled
        and agent.enabled
        and adapter.state in {RuntimeAdapterState.AVAILABLE, RuntimeAdapterState.CONFIGURED}
        and flags.adapter_enabled(adapter.adapter_id)
    )
    return DigitalEmployee(
        employee_id=derive_id("employee", {"agent_definition_id": agent.agent_id}),
        agent_definition_id=agent.agent_id,
        agent_definition_sha256=sha256_hex(canonical_json_bytes(agent)),
        name=agent.name,
        role=agent.role,
        description=agent.description,
        runtime_adapter_id=adapter.adapter_id,
        enabled_skill_ids=tuple(sorted(agent.skill_ids)),
        instruction_bundle=tuple(sorted(agent.context_paths)),
        filesystem_policy=filesystem_policy,
        graph_policy=graph_policy,
        heartbeat_policy=heartbeat_policy,
        status=EmployeeStatus.IDLE if enabled else EmployeeStatus.DISABLED,
        configuration_revision=_configuration_revision(
            agent=agent,
            adapter=adapter,
            filesystem_policy=filesystem_policy,
            graph_policy=graph_policy,
            heartbeat_policy=heartbeat_policy,
        ),
    )


class DigitalEmployeeCatalog:
    """Projects inert catalog definitions into feature-gated digital employees."""

    def __init__(
        self,
        catalog: CatalogService,
        *,
        flags: FeatureFlags,
        catalog_read_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.flags = flags
        self._catalog_read_guard = catalog_read_guard or nullcontext

    def load(self) -> EmployeeCatalogSnapshot:
        with self._catalog_read_guard():
            snapshot = self.catalog.load()
            return project_employee_catalog(snapshot, flags=self.flags)


def project_employee_catalog(
    snapshot: CatalogSnapshot,
    *,
    flags: FeatureFlags,
) -> EmployeeCatalogSnapshot:
    adapters = {item.adapter_id: item for item in snapshot.adapters}
    employees: list[DigitalEmployee] = []
    for agent in sorted(snapshot.agents, key=lambda item: item.agent_id):
        try:
            adapter = adapters[agent.runtime_adapter_id]
        except KeyError as error:
            raise EmployeeCatalogError("Agent references an unavailable runtime adapter") from error
        employees.append(employee_from_definition(agent, adapter, flags=flags))
    employee_ids = [item.employee_id for item in employees]
    if len(employee_ids) != len(set(employee_ids)):
        raise EmployeeCatalogError("Employee identities are not unique")
    return EmployeeCatalogSnapshot(
        catalog_sha256=snapshot.catalog_sha256,
        employees=tuple(employees),
    )
