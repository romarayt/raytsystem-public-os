from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raytsystem.contracts.base import validate_relative_path
from raytsystem.security.paths import PathPolicyError, read_regular_file


class ExecutionConfigError(ValueError):
    """Raised when execution-plane configuration is missing or unsafe."""


@dataclass(frozen=True)
class FeatureFlags:
    code_graph_enabled: bool = True
    graph_first_query_enabled: bool = True
    digital_employees_enabled: bool = True
    task_workspaces_enabled: bool = True
    runtime_execution_enabled: bool = False
    codex_local_enabled: bool = False
    claude_local_enabled: bool = False
    heartbeats_enabled: bool = True
    scheduled_heartbeats_enabled: bool = False

    def __post_init__(self) -> None:
        if self.graph_first_query_enabled and not self.code_graph_enabled:
            raise ExecutionConfigError("Graph-first query requires the code graph")
        if self.runtime_execution_enabled and not self.task_workspaces_enabled:
            raise ExecutionConfigError("Runtime execution requires managed task workspaces")
        if (self.codex_local_enabled or self.claude_local_enabled) and not (
            self.runtime_execution_enabled
        ):
            raise ExecutionConfigError("Runtime adapters require runtime execution")
        if self.scheduled_heartbeats_enabled and not self.heartbeats_enabled:
            raise ExecutionConfigError("Scheduled heartbeats require heartbeats")

    def adapter_enabled(self, adapter_id: str) -> bool:
        if not self.runtime_execution_enabled:
            return False
        if adapter_id == "adapter_fake":
            return True
        if adapter_id == "adapter_codex_local":
            return self.codex_local_enabled
        if adapter_id == "adapter_claude_code":
            return self.claude_local_enabled
        return False


@dataclass(frozen=True)
class ExecutionConfig:
    features: FeatureFlags
    control_db: str = "ops/control.sqlite"
    workspaces_root: str = ".raytsystem/workspaces"
    lease_ttl_seconds: int = 60
    lease_renewal_seconds: int = 20
    max_run_seconds: int = 3_600
    cancel_grace_seconds: int = 5
    max_output_bytes: int = 4 * 1024 * 1024
    max_transcript_events: int = 10_000
    max_context_bytes: int = 48_000
    max_concurrent_runs: int = 2

    def __post_init__(self) -> None:
        numeric_values = (
            self.lease_ttl_seconds,
            self.lease_renewal_seconds,
            self.max_run_seconds,
            self.cancel_grace_seconds,
            self.max_output_bytes,
            self.max_transcript_events,
            self.max_context_bytes,
            self.max_concurrent_runs,
        )
        if any(type(value) is not int for value in numeric_values):
            raise ExecutionConfigError("Execution limits must be integers")
        try:
            validate_relative_path(self.control_db)
            validate_relative_path(self.workspaces_root)
        except ValueError as error:
            raise ExecutionConfigError("Execution paths must stay inside the workspace") from error
        if not 10 <= self.lease_ttl_seconds <= 3_600:
            raise ExecutionConfigError("Execution lease TTL must be inside 10..3600 seconds")
        if not 1 <= self.lease_renewal_seconds < self.lease_ttl_seconds:
            raise ExecutionConfigError("Lease renewal must be positive and shorter than its TTL")
        if not 1 <= self.max_run_seconds <= 24 * 60 * 60:
            raise ExecutionConfigError("Run timeout must be inside 1 second..24 hours")
        if not 1 <= self.cancel_grace_seconds <= 60:
            raise ExecutionConfigError("Cancellation grace must be inside 1..60 seconds")
        if not 1_024 <= self.max_output_bytes <= 64 * 1024 * 1024:
            raise ExecutionConfigError("Run output cap is outside the supported range")
        if not 1 <= self.max_transcript_events <= 100_000:
            raise ExecutionConfigError("Transcript event cap is outside the supported range")
        if not 1_024 <= self.max_context_bytes <= 2_000_000:
            raise ExecutionConfigError("Context cap is outside the supported range")
        if not 1 <= self.max_concurrent_runs <= 32:
            raise ExecutionConfigError("Run concurrency must be inside 1..32")

    @property
    def control_db_path(self) -> Path:
        return Path(self.control_db)

    @property
    def workspaces_path(self) -> Path:
        return Path(self.workspaces_root)


_FEATURE_KEYS = tuple(FeatureFlags.__dataclass_fields__)
_EXECUTION_KEYS = {
    "workspaces_root",
    "lease_ttl_seconds",
    "lease_renewal_seconds",
    "max_run_seconds",
    "cancel_grace_seconds",
    "max_output_bytes",
    "max_transcript_events",
    "max_context_bytes",
    "max_concurrent_runs",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ExecutionConfigError(f"{label} must be a TOML table")
    return value


def _known(payload: dict[str, Any], keys: set[str] | tuple[str, ...], *, label: str) -> None:
    unknown = sorted(set(payload).difference(keys))
    if unknown:
        raise ExecutionConfigError(f"{label} contains unknown keys: {', '.join(unknown)}")


def _bools(payload: dict[str, Any], *, label: str) -> None:
    if any(type(value) is not bool for value in payload.values()):
        raise ExecutionConfigError(f"{label} values must be booleans")


def load_execution_config(root: Path) -> ExecutionConfig:
    """Load bounded execution configuration without following config symlinks."""

    resolved_root = root.resolve()
    try:
        data = read_regular_file(
            resolved_root,
            "config/raytsystem.toml",
            max_bytes=1024 * 1024,
        ).data
        document = tomllib.loads(data.decode("utf-8"))
    except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ExecutionConfigError("config/raytsystem.toml is unavailable or invalid") from error
    if not isinstance(document, dict):
        raise ExecutionConfigError("raytsystem configuration must be a TOML document")

    feature_payload = _mapping(document.get("features"), label="features")
    _known(feature_payload, _FEATURE_KEYS, label="features")
    _bools(feature_payload, label="features")
    features = FeatureFlags(**feature_payload)

    execution_payload = _mapping(document.get("execution"), label="execution")
    _known(execution_payload, _EXECUTION_KEYS, label="execution")
    control_db = document.get("control_db", "ops/control.sqlite")
    if not isinstance(control_db, str):
        raise ExecutionConfigError("control_db must be a relative path")
    try:
        return ExecutionConfig(
            features=features,
            control_db=control_db,
            **execution_payload,
        )
    except TypeError as error:
        raise ExecutionConfigError("execution values have invalid types") from error
