from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from raytsystem.catalog import CatalogService, CatalogSnapshot
from raytsystem.contracts import AgentDefinition, RuntimeAdapterDefinition, RuntimeAdapterState
from raytsystem.contracts.base import VersionedModel, canonical_json_bytes, sha256_hex
from raytsystem.contracts.execution import (
    BudgetPolicy,
    BudgetUsage,
    CommentKind,
    DigitalEmployee,
    EmployeeStatus,
    ExecutionApproval,
    ExecutionComment,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionSession,
    ExecutionSessionStatus,
    TaskAssignment,
    TaskGraphScope,
    TaskWorkspace,
    TranscriptEvent,
    WorkspaceStatus,
)
from raytsystem.execution.config import ExecutionConfig, FeatureFlags, load_execution_config
from raytsystem.execution.employees import (
    EmployeeCatalogSnapshot,
    project_employee_catalog,
)
from raytsystem.execution.store import ExecutionStore

_MAX_PAGE_SIZE = 500
_MAX_TRANSCRIPT_PAGE_SIZE = 1_000
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_:.@/-]{1,255}$")

ModelT = TypeVar("ModelT", bound=VersionedModel)
FeatureKey = Literal[
    "code_graph_enabled",
    "digital_employees_enabled",
    "runtime_execution_enabled",
    "task_workspaces_enabled",
]


@dataclass(frozen=True)
class _ViewContext:
    config: ExecutionConfig
    catalog: CatalogSnapshot
    employees: EmployeeCatalogSnapshot


class ExecutionViewProvider:
    """Read-only, JSON-ready execution-plane projections for the loopback UI."""

    def __init__(
        self,
        root: Path,
        *,
        store_provider: Callable[[], ExecutionStore | None] | None = None,
        catalog_read_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self._store_provider = store_provider
        self._catalog_read_guard = catalog_read_guard or nullcontext

    def features(self) -> dict[str, Any]:
        context = self._context()
        store = self._open_store(context)
        storage_state = "uninitialized" if store is None else "ready"
        self._close_store(store)
        return _snapshot(
            section="execution_features",
            state="ready",
            storage_state=storage_state,
            flags=context.config.features,
            payload={
                "catalog_sha256": context.catalog.catalog_sha256,
                "limits": {
                    "max_run_seconds": context.config.max_run_seconds,
                    "max_output_bytes": context.config.max_output_bytes,
                    "max_transcript_events": context.config.max_transcript_events,
                    "max_context_bytes": context.config.max_context_bytes,
                    "max_concurrent_runs": context.config.max_concurrent_runs,
                },
            },
        )

    def employees(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        _validate_page(limit, offset)
        context = self._context()
        selected = context.employees.employees[offset : offset + limit]
        store = self._open_store(context)
        try:
            items = [
                _employee_view(
                    employee,
                    None if store is None else store.get(DigitalEmployee, employee.employee_id),
                    flags=context.config.features,
                )
                for employee in selected
            ]
        finally:
            self._close_store(store)
        return _snapshot(
            section="employees",
            state="catalog_only" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={
                "catalog_sha256": context.employees.catalog_sha256,
                "employees": items,
                "pagination": _pagination(limit, offset, len(items)),
                "total_catalog_employees": len(context.employees.employees),
            },
        )

    def agents(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Return one user-facing Agent row per stable agent-definition identity.

        Catalog and execution remain separate backend planes. This projection is the only place
        where they are combined, and the join uses ``agent_definition_id`` / ``employee_id`` only.
        """

        _validate_page(limit, offset)
        context = self._context()
        store = self._open_store(context)
        try:
            items = self._agent_items(context, store)
        finally:
            self._close_store(store)
        selected = items[offset : offset + limit]
        return _snapshot(
            section="agents",
            state="catalog_only" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={
                "catalog_sha256": context.catalog.catalog_sha256,
                "agents": selected,
                "pagination": _pagination(limit, offset, len(selected)),
                "total_agents": len(items),
            },
        )

    def agent_detail(self, agent_id: str, *, limit: int = 100) -> dict[str, Any]:
        """Return a bounded, sanitized Agent detail assembled from both backend planes."""

        _validate_identifier(agent_id, label="agent ID")
        _validate_page(limit, 0)
        context = self._context()
        store = self._open_store(context)
        try:
            item = next(
                (
                    candidate
                    for candidate in self._agent_items(context, store)
                    if candidate["agent_id"] == agent_id
                ),
                None,
            )
            if item is None:
                raise ValueError("Agent was not found")
            employee_id = str(item["employee_id"])
            runs: tuple[ExecutionRun, ...] = ()
            sessions: tuple[ExecutionSession, ...] = ()
            assignments: tuple[TaskAssignment, ...] = ()
            approvals: tuple[ExecutionApproval, ...] = ()
            policies: tuple[BudgetPolicy, ...] = ()
            if store is not None:
                runs = store.list(ExecutionRun, employee_id=employee_id, limit=limit)
                sessions = store.list(ExecutionSession, employee_id=employee_id, limit=limit)
                assignments = store.list(TaskAssignment, employee_id=employee_id, limit=limit)
                approvals = store.list(ExecutionApproval, employee_id=employee_id, limit=limit)
                policies = store.list(
                    BudgetPolicy,
                    state="employee",
                    scope_id=employee_id,
                    limit=limit,
                )
            definition = context.catalog.agent(agent_id)
            skill_by_id = {skill.skill_id: skill for skill in context.catalog.skills}
            skills = [
                {
                    "skill_id": skill_id,
                    "name": skill_by_id[skill_id].name if skill_id in skill_by_id else skill_id,
                    "status": (
                        "enabled"
                        if skill_id in skill_by_id and skill_by_id[skill_id].enabled
                        else "restricted"
                    ),
                    "permissions": (
                        list(skill_by_id[skill_id].permissions) if skill_id in skill_by_id else []
                    ),
                }
                for skill_id in item["skill_ids"]
            ]
            payload = {
                "agent": item,
                "instruction": {
                    "context_paths": [] if definition is None else list(definition.context_paths),
                    "capabilities": [] if definition is None else list(definition.capabilities),
                    "system_boundaries": {
                        "canonical_knowledge_write": False,
                        "external_side_effects": "approval_required",
                        "runtime_output_is_untrusted": True,
                    },
                    "limitations": [
                        "catalog_definition_is_inert",
                        "sensitive_runtime_fields_are_omitted",
                    ],
                },
                "skills": skills,
                "runtime": {
                    "execution": item["execution"],
                    "sessions": [_session_view(record) for record in sessions],
                    "runs": [_run_view(record) for record in runs],
                    "budgets": [
                        _budget_view(record, store.get(BudgetUsage, record.budget_policy_id))
                        for record in policies
                    ]
                    if store is not None
                    else [],
                    "leases": [],
                },
                "access": {
                    "filesystem": item["filesystem_policy"],
                    "data_classes": item["approved_data_classes"],
                    "tools": [],
                    "network": {
                        "egress_declared": item["egress_declared"],
                        "approval_required": True,
                    },
                    "approvals": [_approval_view(record) for record in approvals],
                    "effective_permissions": item["effective_permissions"],
                },
                "history": {
                    "assignments": [_assignment_view(record) for record in assignments],
                    "runs": [_run_view(record) for record in runs],
                    "configuration_revision": item["configuration_revision"],
                    "audit_events": [],
                },
            }
        finally:
            self._close_store(store)
        return _snapshot(
            section="agent_detail",
            state="catalog_only" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={"catalog_sha256": context.catalog.catalog_sha256, **payload},
        )

    def assignments(
        self,
        *,
        task_id: str | None = None,
        employee_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="assignments",
            key="assignments",
            model=TaskAssignment,
            view=_assignment_view,
            feature_key="digital_employees_enabled",
            task_id=task_id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )

    def workspaces(
        self,
        *,
        task_id: str | None = None,
        status: WorkspaceStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="workspaces",
            key="workspaces",
            model=TaskWorkspace,
            view=_workspace_view,
            feature_key="task_workspaces_enabled",
            state=None if status is None else status.value,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    def graph_scopes(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="graph_scopes",
            key="graph_scopes",
            model=TaskGraphScope,
            view=_graph_scope_view,
            feature_key="code_graph_enabled",
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    def runs(
        self,
        *,
        task_id: str | None = None,
        employee_id: str | None = None,
        status: ExecutionRunStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="execution_runs",
            key="runs",
            model=ExecutionRun,
            view=_run_view,
            feature_key="runtime_execution_enabled",
            state=None if status is None else status.value,
            task_id=task_id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )

    def sessions(
        self,
        *,
        task_id: str | None = None,
        employee_id: str | None = None,
        status: ExecutionSessionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="execution_sessions",
            key="sessions",
            model=ExecutionSession,
            view=_session_view,
            feature_key="runtime_execution_enabled",
            state=None if status is None else status.value,
            task_id=task_id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )

    def budgets(
        self,
        *,
        scope_id: str | None = None,
        scope_kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        _validate_page(limit, offset)
        _validate_optional_identifier(scope_id, label="scope ID")
        _validate_optional_identifier(scope_kind, label="scope kind")
        context = self._context()
        store = self._open_store(context)
        policies: tuple[BudgetPolicy, ...] = ()
        items: list[dict[str, Any]] = []
        try:
            if store is not None:
                policies = store.list(
                    BudgetPolicy,
                    state=scope_kind,
                    scope_id=scope_id,
                    limit=limit,
                    offset=offset,
                )
                for policy in policies:
                    usage = store.get(BudgetUsage, policy.budget_policy_id)
                    items.append(_budget_view(policy, usage))
        finally:
            self._close_store(store)
        return _snapshot(
            section="budgets",
            state="uninitialized" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={
                "feature_state": _feature_state(context.config.features.digital_employees_enabled),
                "budgets": items,
                "pagination": _pagination(limit, offset, len(policies)),
            },
        )

    def approvals(
        self,
        *,
        task_id: str | None = None,
        employee_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="execution_approvals",
            key="approvals",
            model=ExecutionApproval,
            view=_approval_view,
            feature_key="digital_employees_enabled",
            task_id=task_id,
            employee_id=employee_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )

    def comments(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        kind: CommentKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._record_snapshot(
            section="execution_comments",
            key="comments",
            model=ExecutionComment,
            view=_comment_view,
            feature_key="digital_employees_enabled",
            state=None if kind is None else kind.value,
            task_id=task_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )

    def task_detail(self, task_id: str, *, limit: int = 100) -> dict[str, Any]:
        _validate_identifier(task_id, label="task ID")
        _validate_page(limit, 0)
        context = self._context()
        store = self._open_store(context)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "section_limit": limit,
            "assignments": [],
            "workspaces": [],
            "graph_scopes": [],
            "runs": [],
            "sessions": [],
            "budgets": [],
            "approvals": [],
            "comments": [],
        }
        try:
            if store is not None:
                assignments = store.list(TaskAssignment, task_id=task_id, limit=limit)
                workspaces = store.list(TaskWorkspace, task_id=task_id, limit=limit)
                graph_scopes = store.list(TaskGraphScope, task_id=task_id, limit=limit)
                runs = store.list(ExecutionRun, task_id=task_id, limit=limit)
                sessions = store.list(ExecutionSession, task_id=task_id, limit=limit)
                policies = store.list(
                    BudgetPolicy,
                    state="task",
                    scope_id=task_id,
                    limit=limit,
                )
                approvals = store.list(ExecutionApproval, task_id=task_id, limit=limit)
                comments = store.list(ExecutionComment, task_id=task_id, limit=limit)
                payload.update(
                    {
                        "assignments": [_assignment_view(item) for item in assignments],
                        "workspaces": [_workspace_view(item) for item in workspaces],
                        "graph_scopes": [_graph_scope_view(item) for item in graph_scopes],
                        "runs": [_run_view(item) for item in runs],
                        "sessions": [_session_view(item) for item in sessions],
                        "budgets": [
                            _budget_view(
                                item,
                                store.get(BudgetUsage, item.budget_policy_id),
                            )
                            for item in policies
                        ],
                        "approvals": [_approval_view(item) for item in approvals],
                        "comments": [_comment_view(item) for item in comments],
                    }
                )
        finally:
            self._close_store(store)
        return _snapshot(
            section="task_execution_detail",
            state="uninitialized" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload=payload,
        )

    def runtime_health(self) -> dict[str, Any]:
        context = self._context()
        items = [
            _runtime_health_view(adapter, context.config.features)
            for adapter in context.catalog.adapters
        ]
        return _snapshot(
            section="runtime_health",
            state="ready",
            storage_state="not_required",
            flags=context.config.features,
            payload={"probe_performed": False, "adapters": items},
        )

    def transcript(
        self,
        run_id: str,
        *,
        after_sequence: int = -1,
        limit: int = 250,
    ) -> dict[str, Any]:
        _validate_identifier(run_id, label="run ID")
        if not -1 <= after_sequence <= 10_000_000:
            raise ValueError("Transcript cursor is out of bounds")
        if not 1 <= limit <= _MAX_TRANSCRIPT_PAGE_SIZE:
            raise ValueError("Transcript page size is out of bounds")
        context = self._context()
        store = self._open_store(context)
        events: tuple[TranscriptEvent, ...] = ()
        try:
            if store is not None:
                events = store.list_transcript(
                    run_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
        finally:
            self._close_store(store)
        return _snapshot(
            section="execution_transcript",
            state="uninitialized" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={
                "run_id": run_id,
                "after_sequence": after_sequence,
                "events": [_transcript_view(item) for item in events],
                "returned": len(events),
            },
        )

    def _agent_items(
        self,
        context: _ViewContext,
        store: ExecutionStore | None,
    ) -> list[dict[str, Any]]:
        definitions = {definition.agent_id: definition for definition in context.catalog.agents}
        projections = {
            employee.agent_definition_id: employee for employee in context.employees.employees
        }
        stored_by_definition: dict[str, list[DigitalEmployee]] = {}
        if store is not None:
            offset = 0
            while True:
                page = store.list(
                    DigitalEmployee,
                    limit=_MAX_PAGE_SIZE,
                    offset=offset,
                )
                for employee in page:
                    stored_by_definition.setdefault(employee.agent_definition_id, []).append(
                        employee
                    )
                if len(page) < _MAX_PAGE_SIZE:
                    break
                offset += len(page)
                if offset > 1_000_000:
                    raise ValueError("Digital employee store exceeds the safe pagination bound")
        adapter_by_id = {adapter.adapter_id: adapter for adapter in context.catalog.adapters}
        agent_ids = sorted(set(definitions) | set(stored_by_definition))
        return [
            _agent_view(
                agent_id=agent_id,
                definition=definitions.get(agent_id),
                projection=projections.get(agent_id),
                stored=tuple(
                    sorted(
                        stored_by_definition.get(agent_id, []),
                        key=lambda employee: employee.employee_id,
                    )
                ),
                adapter_by_id=adapter_by_id,
                flags=context.config.features,
                storage_initialized=store is not None,
            )
            for agent_id in agent_ids
        ]

    def _record_snapshot(
        self,
        *,
        section: str,
        key: str,
        model: type[ModelT],
        view: Callable[[ModelT], dict[str, Any]],
        feature_key: FeatureKey,
        state: str | None = None,
        task_id: str | None = None,
        employee_id: str | None = None,
        run_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        _validate_page(limit, offset)
        for value, label in (
            (state, "state"),
            (task_id, "task ID"),
            (employee_id, "employee ID"),
            (run_id, "run ID"),
            (workspace_id, "workspace ID"),
            (scope_id, "scope ID"),
        ):
            _validate_optional_identifier(value, label=label)
        context = self._context()
        store = self._open_store(context)
        records: tuple[ModelT, ...] = ()
        try:
            if store is not None:
                records = store.list(
                    model,
                    state=state,
                    task_id=task_id,
                    employee_id=employee_id,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    scope_id=scope_id,
                    limit=limit,
                    offset=offset,
                )
        finally:
            self._close_store(store)
        return _snapshot(
            section=section,
            state="uninitialized" if store is None else "ready",
            storage_state="uninitialized" if store is None else "ready",
            flags=context.config.features,
            payload={
                "feature_state": _feature_state(
                    _feature_enabled(context.config.features, feature_key)
                ),
                key: [view(item) for item in records],
                "pagination": _pagination(limit, offset, len(records)),
            },
        )

    def _context(self) -> _ViewContext:
        with self._catalog_read_guard():
            config = load_execution_config(self.root)
            catalog = CatalogService(self.root).load()
            employees = project_employee_catalog(catalog, flags=config.features)
            return _ViewContext(config=config, catalog=catalog, employees=employees)

    def _open_store(self, context: _ViewContext) -> ExecutionStore | None:
        if self._store_provider is not None:
            borrowed = self._store_provider()
            if borrowed is not None:
                return borrowed
        return ExecutionStore.open_for_read(self.root / context.config.control_db_path)

    def _close_store(self, store: ExecutionStore | None) -> None:
        if store is None:
            return
        borrowed = None if self._store_provider is None else self._store_provider()
        if store is not borrowed:
            store.close()


def _snapshot(
    *,
    section: str,
    state: str,
    storage_state: str,
    flags: FeatureFlags,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "section": section,
        "state": state,
        "storage_state": storage_state,
        "features": asdict(flags),
        **payload,
    }
    return {
        "snapshot_id": f"xview_{sha256_hex(canonical_json_bytes(body))}",
        **body,
    }


def _employee_view(
    catalog_employee: DigitalEmployee,
    stored_employee: DigitalEmployee | None,
    *,
    flags: FeatureFlags,
) -> dict[str, Any]:
    configuration_current = bool(
        stored_employee is not None
        and stored_employee.configuration_revision == catalog_employee.configuration_revision
    )
    gate_enabled = catalog_employee.status is not EmployeeStatus.DISABLED
    if not gate_enabled:
        status = EmployeeStatus.DISABLED.value
        state_source = "feature_gate"
        if not flags.digital_employees_enabled:
            reason = "digital_employees_disabled"
        elif not flags.runtime_execution_enabled:
            reason = "runtime_execution_disabled"
        elif not flags.adapter_enabled(catalog_employee.runtime_adapter_id):
            reason = "runtime_adapter_disabled"
        else:
            reason = "catalog_definition_disabled"
    elif stored_employee is None:
        status = catalog_employee.status.value
        state_source = "catalog"
        reason = "operational_state_uninitialized"
    elif not configuration_current:
        status = EmployeeStatus.DISABLED.value
        state_source = "stale_store_record"
        reason = "configuration_revision_changed"
    else:
        status = stored_employee.status.value
        state_source = "execution_store"
        reason = "persisted_operational_state"
    operational = stored_employee if configuration_current else None
    return {
        "employee_id": catalog_employee.employee_id,
        "agent_definition_id": catalog_employee.agent_definition_id,
        "name": catalog_employee.name,
        "role": catalog_employee.role,
        "description": catalog_employee.description,
        "runtime_adapter_id": catalog_employee.runtime_adapter_id,
        "enabled_skill_ids": list(catalog_employee.enabled_skill_ids),
        "configuration_revision": catalog_employee.configuration_revision,
        "configuration_current": configuration_current if stored_employee is not None else None,
        "status": status,
        "stored_status": None if stored_employee is None else stored_employee.status.value,
        "state_source": state_source,
        "reason_code": reason,
        "current_task_id": None if operational is None else operational.current_task_id,
        "current_session_id": None if operational is None else operational.current_session_id,
        "reporting_manager_id": (None if operational is None else operational.reporting_manager_id),
        "budget_policy_id": None if operational is None else operational.budget_policy_id,
        "concurrency_limit": catalog_employee.concurrency_limit,
        "filesystem_policy": {
            "mode": catalog_employee.filesystem_policy.mode.value,
            "allow_workspace_read": catalog_employee.filesystem_policy.allow_workspace_read,
            "allow_staged_write": catalog_employee.filesystem_policy.allow_staged_write,
            "allow_git_read": catalog_employee.filesystem_policy.allow_git_read,
            "allow_git_write": catalog_employee.filesystem_policy.allow_git_write,
        },
        "graph_policy": {
            "max_depth": catalog_employee.graph_policy.max_depth,
            "max_nodes": catalog_employee.graph_policy.max_nodes,
            "max_edges": catalog_employee.graph_policy.max_edges,
            "max_bytes": catalog_employee.graph_policy.max_bytes,
            "include_relations": list(catalog_employee.graph_policy.include_relations),
        },
        "heartbeat_policy": {
            "manual_enabled": catalog_employee.heartbeat_policy.manual_enabled,
            "scheduled_enabled": catalog_employee.heartbeat_policy.scheduled_enabled,
            "interval_seconds": catalog_employee.heartbeat_policy.interval_seconds,
        },
        "instruction_paths_omitted": True,
    }


def _agent_view(
    *,
    agent_id: str,
    definition: AgentDefinition | None,
    projection: DigitalEmployee | None,
    stored: tuple[DigitalEmployee, ...],
    adapter_by_id: dict[str, RuntimeAdapterDefinition],
    flags: FeatureFlags,
    storage_initialized: bool,
) -> dict[str, Any]:
    expected_employee_id = None if projection is None else projection.employee_id
    matching = [employee for employee in stored if employee.employee_id == expected_employee_id]
    identity_mismatch = bool(definition is not None and stored and not matching)
    duplicate_records = len(stored) > 1
    stored_employee = matching[0] if len(matching) == 1 else None
    effective = projection or (stored[0] if stored else None)
    if effective is None:
        raise ValueError("Agent projection has neither a definition nor an execution record")

    if projection is not None:
        effective_execution = _employee_view(projection, stored_employee, flags=flags)
    else:
        effective_execution = _orphan_employee_view(effective)

    if definition is None:
        readiness = "degraded"
        reason = "definition_missing"
    elif duplicate_records:
        readiness = "degraded"
        reason = "duplicate_execution_records"
    elif identity_mismatch:
        readiness = "degraded"
        reason = "employee_identity_mismatch"
    elif not storage_initialized:
        readiness = "requires_configuration"
        reason = "execution_store_uninitialized"
    elif not flags.digital_employees_enabled or not flags.runtime_execution_enabled:
        readiness = "disabled"
        reason = (
            "digital_employees_disabled"
            if not flags.digital_employees_enabled
            else "runtime_execution_disabled"
        )
    elif stored_employee is None:
        readiness = "catalog_only"
        reason = "operational_record_missing"
    elif not bool(effective_execution["configuration_current"]):
        readiness = "requires_configuration"
        reason = "configuration_revision_changed"
    elif effective_execution["status"] == EmployeeStatus.RUNNING.value:
        readiness = "running"
        reason = "persisted_operational_state"
    elif effective_execution["status"] in {
        EmployeeStatus.IDLE.value,
        EmployeeStatus.ASSIGNED.value,
    }:
        readiness = "ready"
        reason = "persisted_operational_state"
    elif effective_execution["status"] in {
        EmployeeStatus.BLOCKED.value,
        EmployeeStatus.ERROR.value,
    }:
        readiness = "requires_configuration"
        reason = str(effective_execution["reason_code"])
    else:
        readiness = "disabled"
        reason = str(effective_execution["reason_code"])

    adapter = adapter_by_id.get(effective.runtime_adapter_id)
    filesystem_policy = dict(effective_execution["filesystem_policy"])
    definition_payload = None
    if definition is not None:
        definition_payload = {
            "agent_id": definition.agent_id,
            "name": definition.name,
            "role": definition.role,
            "description": definition.description,
            "version": definition.version,
            "pack_id": definition.pack_id,
            "runtime_adapter_id": definition.runtime_adapter_id,
            "skill_ids": list(definition.skill_ids),
            "context_paths": list(definition.context_paths),
            "capabilities": list(definition.capabilities),
            "requested_filesystem_mode": definition.requested_filesystem_mode,
            "approved_data_classes": list(definition.approved_data_classes),
            "egress_declared": definition.egress_destination is not None,
            "accent": definition.accent,
            "enabled": definition.enabled,
        }
    return {
        "agent_id": agent_id,
        "employee_id": effective.employee_id,
        "name": effective.name,
        "role": effective.role,
        "description": effective.description,
        "pack_id": None if definition is None else definition.pack_id,
        "version": None if definition is None else definition.version,
        "definition": definition_payload,
        "definition_state": (
            "missing" if definition is None else "configured" if definition.enabled else "declared"
        ),
        "execution": None if not stored else effective_execution,
        "execution_status": effective_execution["status"],
        "execution_state_source": effective_execution["state_source"],
        "readiness": readiness,
        "unavailable_reason": reason,
        "runtime_adapter": {
            "adapter_id": effective.runtime_adapter_id,
            "name": effective.runtime_adapter_id if adapter is None else adapter.name,
            "state": "unavailable" if adapter is None else adapter.state.value,
            "isolation_mode": None if adapter is None else adapter.isolation_mode,
            "reason": None if adapter is None else adapter.reason,
        },
        "skill_ids": list(effective.enabled_skill_ids),
        "skills_count": len(effective.enabled_skill_ids),
        "filesystem_policy": filesystem_policy,
        "concurrency_limit": effective.concurrency_limit,
        "current_task_id": effective_execution["current_task_id"],
        "current_session_id": effective_execution["current_session_id"],
        "configuration_revision": effective.configuration_revision,
        "configuration_current": effective_execution["configuration_current"],
        "approved_data_classes": (
            [] if definition is None else list(definition.approved_data_classes)
        ),
        "egress_declared": definition is not None and definition.egress_destination is not None,
        "effective_permissions": {
            "workspace_read": bool(filesystem_policy["allow_workspace_read"]),
            "staged_write": bool(filesystem_policy["allow_staged_write"]),
            "git_write": bool(filesystem_policy["allow_git_write"]),
            "network": definition is not None and definition.egress_destination is not None,
        },
        "duplicate_execution_record_count": len(stored),
    }


def _orphan_employee_view(employee: DigitalEmployee) -> dict[str, Any]:
    return {
        "employee_id": employee.employee_id,
        "agent_definition_id": employee.agent_definition_id,
        "name": employee.name,
        "role": employee.role,
        "description": employee.description,
        "runtime_adapter_id": employee.runtime_adapter_id,
        "enabled_skill_ids": list(employee.enabled_skill_ids),
        "configuration_revision": employee.configuration_revision,
        "configuration_current": False,
        "status": "degraded",
        "stored_status": employee.status.value,
        "state_source": "orphan_execution_record",
        "reason_code": "definition_missing",
        "current_task_id": employee.current_task_id,
        "current_session_id": employee.current_session_id,
        "reporting_manager_id": employee.reporting_manager_id,
        "budget_policy_id": employee.budget_policy_id,
        "concurrency_limit": employee.concurrency_limit,
        "filesystem_policy": {
            "mode": employee.filesystem_policy.mode.value,
            "allow_workspace_read": employee.filesystem_policy.allow_workspace_read,
            "allow_staged_write": employee.filesystem_policy.allow_staged_write,
            "allow_git_read": employee.filesystem_policy.allow_git_read,
            "allow_git_write": employee.filesystem_policy.allow_git_write,
        },
        "graph_policy": {
            "max_depth": employee.graph_policy.max_depth,
            "max_nodes": employee.graph_policy.max_nodes,
            "max_edges": employee.graph_policy.max_edges,
            "max_bytes": employee.graph_policy.max_bytes,
            "include_relations": list(employee.graph_policy.include_relations),
        },
        "heartbeat_policy": {
            "manual_enabled": employee.heartbeat_policy.manual_enabled,
            "scheduled_enabled": employee.heartbeat_policy.scheduled_enabled,
            "interval_seconds": employee.heartbeat_policy.interval_seconds,
        },
        "instruction_paths_omitted": True,
    }


def _assignment_view(item: TaskAssignment) -> dict[str, Any]:
    return {
        "assignment_id": item.assignment_id,
        "task_id": item.task_id,
        "task_generation_id": item.task_generation_id,
        "task_revision": item.task_revision,
        "employee_id": item.employee_id,
        "runtime_adapter_id": item.runtime_adapter_id,
        "budget_policy_id": item.budget_policy_id,
        "approval_policy_id": item.approval_policy_id,
        "graph_scope_id": item.graph_scope_id,
        "revision": item.revision,
        "created_at": _time(item.created_at),
        "updated_at": _time(item.updated_at),
    }


def _workspace_view(item: TaskWorkspace) -> dict[str, Any]:
    return {
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "mode": item.mode.value,
        "git_commit": item.git_commit,
        "graph_snapshot_id": item.graph_snapshot_id,
        "graph_fingerprint": item.graph_fingerprint,
        "manifest_sha256": item.manifest_sha256,
        "status": item.status.value,
        "created_at": _time(item.created_at),
        "updated_at": _time(item.updated_at),
        "paths_omitted": True,
    }


def _graph_scope_view(item: TaskGraphScope) -> dict[str, Any]:
    return {
        "graph_scope_id": item.graph_scope_id,
        "task_id": item.task_id,
        "graph_snapshot_id": item.graph_snapshot_id,
        "graph_fingerprint": item.graph_fingerprint,
        "generation_fingerprint": item.generation_fingerprint,
        "seed_node_ids": list(item.seed_node_ids),
        "max_depth": item.max_depth,
        "max_nodes": item.max_nodes,
        "max_edges": item.max_edges,
        "max_bytes": item.max_bytes,
        "include_relations": list(item.include_relations),
        "root_count": len(item.roots),
        "roots_omitted": True,
        "created_at": _time(item.created_at),
    }


def _run_view(item: ExecutionRun) -> dict[str, Any]:
    return {
        "run_id": item.run_id,
        "invocation_id": item.invocation_id,
        "invocation_source": item.invocation_source,
        "employee_id": item.employee_id,
        "task_id": item.task_id,
        "runtime_adapter_id": item.runtime_adapter_id,
        "provider": item.provider,
        "model": item.model,
        "workspace_id": item.workspace_id,
        "graph_snapshot_id": item.graph_snapshot_id,
        "graph_scope_id": item.graph_scope_id,
        "session_id_before": item.session_id_before,
        "session_id_after": item.session_id_after,
        "policy_decision_id": item.policy_decision_id,
        "approval_id": item.approval_id,
        "task_lease_id": item.task_lease_id,
        "fencing_token": item.fencing_token,
        "status": item.status.value,
        "started_at": _time(item.started_at),
        "ended_at": None if item.ended_at is None else _time(item.ended_at),
        "exit_code": item.exit_code,
        "usage": item.usage.model_dump(mode="json", exclude={"extensions"}),
        "changed_file_count": len(item.changed_files),
        "tests": [{"name": test.name, "status": test.status} for test in item.tests],
        "artifact_ids": list(item.artifact_ids),
        "summary": item.summary,
        "error_code": item.error_code,
        "retry_of_run_id": item.retry_of_run_id,
        "resumed_from_run_id": item.resumed_from_run_id,
        "command_omitted": True,
        "working_directory_omitted": True,
    }


def _session_view(item: ExecutionSession) -> dict[str, Any]:
    return {
        "session_id": item.session_id,
        "runtime_adapter_id": item.runtime_adapter_id,
        "provider": item.provider,
        "model": item.model,
        "task_id": item.task_id,
        "employee_id": item.employee_id,
        "workspace_id": item.workspace_id,
        "graph_snapshot_id": item.graph_snapshot_id,
        "context_snapshot_sha256": item.context_snapshot_sha256,
        "compatibility_sha256": item.compatibility_sha256,
        "started_at": _time(item.started_at),
        "last_resumed_at": (None if item.last_resumed_at is None else _time(item.last_resumed_at)),
        "status": item.status.value,
        "previous_run_id": item.previous_run_id,
        "usage_totals": item.usage_totals.model_dump(mode="json", exclude={"extensions"}),
        "incompatibility_reason": item.incompatibility_reason,
        "provider_session_present": item.provider_session_id is not None,
        "provider_session_omitted": True,
    }


def _budget_view(item: BudgetPolicy, usage: BudgetUsage | None) -> dict[str, Any]:
    return {
        "budget_policy_id": item.budget_policy_id,
        "scope_kind": item.scope_kind,
        "scope_id": item.scope_id,
        "token_limit": item.token_limit.model_dump(mode="json", exclude={"extensions"}),
        "cost_limit_micros": item.cost_limit_micros,
        "run_limit": item.run_limit,
        "heartbeat_limit": item.heartbeat_limit,
        "active_run_action": item.active_run_action,
        "created_at": _time(item.created_at),
        "usage": None
        if usage is None
        else {
            "tokens": usage.tokens.model_dump(mode="json", exclude={"extensions"}),
            "estimated_cost_micros": usage.estimated_cost_micros,
            "actual_cost_micros": usage.actual_cost_micros,
            "run_count": usage.run_count,
            "heartbeat_count": usage.heartbeat_count,
            "updated_at": _time(usage.updated_at),
        },
    }


def _approval_view(item: ExecutionApproval) -> dict[str, Any]:
    return {
        "approval_id": item.approval_id,
        "action": item.action,
        "payload_sha256": item.payload_sha256,
        "employee_id": item.employee_id,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "workspace_id": item.workspace_id,
        "scope": list(item.scope),
        "approved_by": item.approved_by,
        "approved_at": _time(item.approved_at),
        "expires_at": _time(item.expires_at),
        "destination_present": item.destination is not None,
        "destination_omitted": True,
    }


def _comment_view(item: ExecutionComment) -> dict[str, Any]:
    return {
        "comment_id": item.comment_id,
        "task_id": item.task_id,
        "kind": item.kind.value,
        "actor": item.actor,
        "run_id": item.run_id,
        "body": item.body,
        "created_at": _time(item.created_at),
    }


def _transcript_view(item: TranscriptEvent) -> dict[str, Any]:
    return {
        "transcript_event_id": item.transcript_event_id,
        "run_id": item.run_id,
        "sequence": item.sequence,
        "stream": item.stream,
        "event_type": item.event_type,
        "text": item.text,
        "redacted": item.redacted,
        "created_at": _time(item.created_at),
    }


def _runtime_health_view(
    adapter: RuntimeAdapterDefinition,
    flags: FeatureFlags,
) -> dict[str, Any]:
    feature_enabled = flags.adapter_enabled(adapter.adapter_id)
    catalog_eligible = adapter.state in {
        RuntimeAdapterState.AVAILABLE,
        RuntimeAdapterState.CONFIGURED,
    }
    if not flags.runtime_execution_enabled:
        status = "disabled"
        reason_code = "runtime_execution_disabled"
    elif not feature_enabled:
        status = "disabled"
        reason_code = "adapter_feature_disabled"
    elif adapter.state is RuntimeAdapterState.DISABLED:
        status = "disabled"
        reason_code = "catalog_adapter_disabled"
    elif adapter.state is RuntimeAdapterState.DEGRADED:
        status = "degraded"
        reason_code = "catalog_adapter_degraded"
    else:
        status = "not_probed"
        reason_code = "host_probe_not_requested"
    return {
        "runtime_adapter_id": adapter.adapter_id,
        "name": adapter.name,
        "version": adapter.version,
        "status": status,
        "reason_code": reason_code,
        "catalog_state": adapter.state.value,
        "catalog_reason": adapter.reason,
        "isolation_mode": adapter.isolation_mode,
        "capabilities": list(adapter.capabilities),
        "feature_enabled": feature_enabled,
        "execution_eligible": feature_enabled and catalog_eligible,
        "probe_performed": False,
        "executable_omitted": True,
    }


def _feature_state(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _feature_enabled(flags: FeatureFlags, key: FeatureKey) -> bool:
    if key == "code_graph_enabled":
        return flags.code_graph_enabled
    if key == "digital_employees_enabled":
        return flags.digital_employees_enabled
    if key == "runtime_execution_enabled":
        return flags.runtime_execution_enabled
    return flags.task_workspaces_enabled


def _pagination(limit: int, offset: int, returned: int) -> dict[str, int]:
    return {"limit": limit, "offset": offset, "returned": returned}


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= _MAX_PAGE_SIZE or not 0 <= offset <= 1_000_000:
        raise ValueError("Execution view pagination is out of bounds")


def _validate_identifier(value: str, *, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is malformed")


def _validate_optional_identifier(value: str | None, *, label: str) -> None:
    if value is not None:
        _validate_identifier(value, label=label)


def _time(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")
