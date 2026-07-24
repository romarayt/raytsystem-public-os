"""Shared fixtures for platform-feature tests (evals, telemetry, emergency, packs...)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from raytsystem.contracts import ApprovalRecord
from raytsystem.platform_store import initialize_platform_store

DEFAULT_FLAGS: dict[str, bool] = {
    "evals_enabled": True,
    "promptfoo_adapter_enabled": False,
    "telemetry_enabled": True,
    "otel_export_enabled": False,
    "replay_enabled": True,
    "policy_simulator_enabled": True,
    "emergency_controls_enabled": True,
    "mcp_governance_enabled": True,
    "acp_adapter_enabled": False,
    "a2a_gateway_enabled": False,
    "pack_lifecycle_enabled": True,
    "workflow_engine_enabled": True,
    "notifications_enabled": True,
    "external_notifications_enabled": False,
    "restricted_encryption_enabled": False,
    "backup_enabled": True,
    "registry_projection_enabled": False,
    "promptfoo_remote_generation_enabled": False,
    "external_mcp_execution_enabled": False,
    "a2a_network_exposure_enabled": False,
    "external_kms_enabled": False,
}
DEFAULT_POLICY: dict[str, Any] = {
    "network_default": "none",
    "workspace_default": "staging_only",
    "external_actions_default": "approval_required",
    "mcp_tool_default": "catalog_only",
    "a2a_bind": "loopback",
    "max_span_attributes": 32,
    "max_span_attribute_bytes": 16384,
    "max_a2a_artifact_bytes": 1048576,
    "max_notification_bytes": 16384,
}
DEFAULT_BREAKERS: dict[str, int] = {
    "repeated_error": 5,
    "no_progress_heartbeats": 10,
    "max_run_duration_seconds": 7200,
    "max_heartbeat_count": 1000,
    "token_spike": 200000,
    "max_changed_files": 500,
    "protected_path": 1,
    "forbidden_egress": 1,
    "policy_violations": 3,
    "runaway_subtasks": 100,
    "failed_approvals": 5,
    "retry_loop": 20,
}


def platform_config_payload(
    *,
    flag_overrides: dict[str, bool] | None = None,
    policy_overrides: dict[str, Any] | None = None,
    breaker_overrides: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "store": "ops/platform.sqlite",
        "features": DEFAULT_FLAGS | (flag_overrides or {}),
        "policy": DEFAULT_POLICY | (policy_overrides or {}),
        "circuit_breakers": DEFAULT_BREAKERS | (breaker_overrides or {}),
    }


def make_platform_workspace(
    root: Path,
    *,
    flag_overrides: dict[str, bool] | None = None,
    policy_overrides: dict[str, Any] | None = None,
    breaker_overrides: dict[str, int] | None = None,
) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = platform_config_payload(
        flag_overrides=flag_overrides,
        policy_overrides=policy_overrides,
        breaker_overrides=breaker_overrides,
    )
    (config_dir / "platform.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True), encoding="utf-8"
    )
    return root


def store_approval(
    root: Path,
    *,
    action: str,
    target_id: str,
    artifact_sha256: str,
    scope: tuple[str, ...],
    destination: str | None = None,
    approver: str = "user_local_test",
    expires_in_seconds: int = 3600,
) -> ApprovalRecord:
    now = datetime.now(UTC)
    approval = ApprovalRecord.create(
        action=action,
        target_id=target_id,
        artifact_sha256=artifact_sha256,
        destination=destination,
        scope=scope,
        policy_version="1.0.0",
        approver=approver,
        approved_at=now,
        expires_at=now + timedelta(seconds=expires_in_seconds),
    )
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="authority_approval",
            record_id=approval.approval_id,
            payload=approval.model_dump(mode="json"),
            state="accepted",
            expected_revision=None,
        )
    return approval
