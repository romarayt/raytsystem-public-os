from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from raytsystem.contracts.base import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.execution import (
    ExecutionSession,
    ExecutionSessionStatus,
    ExecutionUsage,
)


@dataclass(frozen=True)
class SessionCompatibilityInput:
    runtime_adapter_id: str
    runtime_adapter_sha256: str
    provider: str
    model: str | None
    task_id: str
    employee_id: str
    employee_configuration_revision: str
    workspace_id: str
    workspace_manifest_sha256: str
    repository_commit: str
    graph_snapshot_id: str
    graph_fingerprint: str
    context_snapshot_sha256: str
    policy_sha256: str
    instruction_bundle_sha256: str

    def fingerprint(self) -> str:
        return sha256_hex(canonical_json_bytes(self.__dict__))


@dataclass(frozen=True)
class SessionResolution:
    compatible: bool
    reason_code: str | None
    compatibility_sha256: str


def resolve_session(
    existing: ExecutionSession | None,
    compatibility: SessionCompatibilityInput,
) -> SessionResolution:
    fingerprint = compatibility.fingerprint()
    if existing is None:
        return SessionResolution(False, "session_missing", fingerprint)
    if existing.status not in {
        ExecutionSessionStatus.ACTIVE,
        ExecutionSessionStatus.PAUSED,
    }:
        return SessionResolution(False, "session_not_resumable", fingerprint)
    identity_pairs = (
        (existing.runtime_adapter_id, compatibility.runtime_adapter_id, "runtime_changed"),
        (existing.provider, compatibility.provider, "provider_changed"),
        (existing.model, compatibility.model, "model_changed"),
        (existing.task_id, compatibility.task_id, "task_changed"),
        (existing.employee_id, compatibility.employee_id, "employee_changed"),
        (existing.workspace_id, compatibility.workspace_id, "workspace_changed"),
        (existing.graph_snapshot_id, compatibility.graph_snapshot_id, "graph_changed"),
        (
            existing.context_snapshot_sha256,
            compatibility.context_snapshot_sha256,
            "context_changed",
        ),
    )
    for observed, expected, reason in identity_pairs:
        if observed != expected:
            return SessionResolution(False, reason, fingerprint)
    if existing.compatibility_sha256 != fingerprint:
        return SessionResolution(False, "compatibility_fingerprint_changed", fingerprint)
    return SessionResolution(True, None, fingerprint)


def create_session(
    compatibility: SessionCompatibilityInput,
    *,
    started_at: datetime | None = None,
    provider_session_id: str | None = None,
    previous_run_id: str | None = None,
) -> ExecutionSession:
    timestamp = (started_at or datetime.now(UTC)).astimezone(UTC)
    fingerprint = compatibility.fingerprint()
    identity = {
        "compatibility_sha256": fingerprint,
        "started_at": timestamp,
        "provider_session_id": provider_session_id,
        "previous_run_id": previous_run_id,
    }
    return ExecutionSession(
        session_id=derive_id("xsession", identity),
        provider_session_id=provider_session_id,
        runtime_adapter_id=compatibility.runtime_adapter_id,
        provider=compatibility.provider,
        model=compatibility.model,
        task_id=compatibility.task_id,
        employee_id=compatibility.employee_id,
        workspace_id=compatibility.workspace_id,
        graph_snapshot_id=compatibility.graph_snapshot_id,
        context_snapshot_sha256=compatibility.context_snapshot_sha256,
        compatibility_sha256=fingerprint,
        started_at=timestamp,
        previous_run_id=previous_run_id,
    )


def add_usage(left: ExecutionUsage, right: ExecutionUsage) -> ExecutionUsage:
    estimated = (
        None
        if left.estimated_cost_micros is None and right.estimated_cost_micros is None
        else (left.estimated_cost_micros or 0) + (right.estimated_cost_micros or 0)
    )
    actual = (
        None
        if left.actual_cost_micros is None and right.actual_cost_micros is None
        else (left.actual_cost_micros or 0) + (right.actual_cost_micros or 0)
    )
    return ExecutionUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        estimated_cost_micros=estimated,
        actual_cost_micros=actual,
    )
