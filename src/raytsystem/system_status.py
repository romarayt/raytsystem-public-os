from __future__ import annotations

from pathlib import Path
from typing import Any

from raytsystem.features import FeatureConfigError, load_feature_config
from raytsystem.migrations import MigrationError, MigrationService
from raytsystem.platform_store import (
    PLATFORM_DB_RELATIVE,
    PLATFORM_SCHEMA_VERSION,
    PlatformStoreError,
    open_platform_store_read_only,
)
from raytsystem.secrets import SecretEncryptionService


def core_doctor(root: Path) -> bool:
    """Core workspace health used by restore verification: config, ledger, platform."""

    root = root.resolve()
    if not (root / "config" / "raytsystem.toml").is_file():
        return False
    current = root / "ledger" / "CURRENT"
    if current.is_file():
        generation = current.read_text(encoding="utf-8").strip()
        if not (root / "ledger" / "generations" / f"{generation}.json").is_file():
            return False
    return platform_status(root)["state"] not in {"error", "degraded"}


def platform_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not (root / "config" / "platform.yaml").is_file():
        return {
            "state": "unconfigured",
            "reason": "config/platform.yaml is absent; platform features are unavailable",
            "active_feature_flags": {},
            "platform_store": "unavailable",
        }
    try:
        features = load_feature_config(root)
    except FeatureConfigError as error:
        return {
            "state": "error",
            "reason": str(error),
            "active_feature_flags": {},
            "platform_store": "unavailable",
        }
    try:
        migration = MigrationService(root).status()
    except MigrationError as error:
        migration = {"state": "unavailable", "reason": str(error)}
    counts = {
        "event_backlog": 0,
        "notification_backlog": 0,
        "outbox_backlog": 0,
        "eval_regression_count": 0,
    }
    record_state: dict[str, Any] = {
        "circuit_breakers": [],
        "emergency_state": {
            "snapshot_id": "pview_unavailable",
            "state": "unavailable",
            "active_actions": [],
            "revision": None,
        },
        "mcp_health": ("unavailable" if features.enabled("mcp_governance_enabled") else "disabled"),
        "acp_health": "idle" if features.enabled("acp_adapter_enabled") else "disabled",
        "last_successful_backup": None,
    }
    snapshot_id = "pview_unavailable"
    store = open_platform_store_read_only(root)
    if store is None:
        store_state = "uninitialized"
    else:
        try:
            with store:
                emergency = store.head("emergency", "emergency_global")
                active_actions = (
                    list(emergency.payload.get("active_actions", []))
                    if emergency is not None and emergency.state == "active"
                    else []
                )
                counts = {
                    "event_backlog": store.event_count(),
                    "notification_backlog": len(
                        store.list_heads("notification", state="unread", limit=500)
                    ),
                    "outbox_backlog": len(
                        store.list_heads("notification_outbox", state="draft", limit=500)
                    ),
                    "eval_regression_count": len(
                        store.list_heads("eval_comparison", state="regression", limit=500)
                    ),
                }
                breakers = [
                    record.payload
                    for record in store.list_heads("breaker", state="open", limit=200)
                ]
                mcp = store.list_heads("mcp_revision", limit=1)
                acp = store.list_heads("acp_session", limit=1)
                backups = store.list_heads("backup", state="created", limit=1)
                record_state = {
                    "circuit_breakers": breakers,
                    "emergency_state": {
                        "snapshot_id": store.snapshot_id(),
                        "state": "blocked" if active_actions else "ready",
                        "active_actions": active_actions,
                        "revision": None if emergency is None else emergency.revision,
                    },
                    "mcp_health": (
                        ("catalog_only" if mcp else "unavailable")
                        if features.enabled("mcp_governance_enabled")
                        else "disabled"
                    ),
                    "acp_health": (
                        ("ready" if acp else "idle")
                        if features.enabled("acp_adapter_enabled")
                        else "disabled"
                    ),
                    "last_successful_backup": backups[0].payload if backups else None,
                }
                snapshot_id = store.snapshot_id()
                store_state = "ready"
        except (PlatformStoreError, ValueError):
            # A corrupted operational record must degrade status, never crash it.
            store_state = "degraded"
    key_status = SecretEncryptionService(root, features=features).status()
    trace_path = root / PLATFORM_DB_RELATIVE
    if store_state == "ready":
        state = "ready"
    elif store_state == "degraded":
        state = "degraded"
    else:
        state = "unavailable"
    return {
        "state": state,
        "snapshot_id": snapshot_id,
        "active_feature_flags": dict(sorted(features.flags.items())),
        "feature_config_sha256": features.config_sha256,
        "schema_versions": {
            "platform_store": str(PLATFORM_SCHEMA_VERSION),
            "feature_config": features.version,
        },
        "migration": migration,
        **counts,
        "trace_storage_size": trace_path.stat().st_size if trace_path.is_file() else 0,
        **record_state,
        "a2a_state": ("loopback_only" if features.enabled("a2a_gateway_enabled") else "disabled"),
        "a2a_network_exposure": False,
        "encryption_provider": key_status.model_dump(mode="json"),
        "platform_store": store_state,
    }
