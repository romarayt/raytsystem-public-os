from __future__ import annotations

from pathlib import Path
from typing import Any

from raytsystem.evals import EvalService
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.notifications import NotificationService
from raytsystem.packages import PackageLifecycleService
from raytsystem.platform_store import open_platform_store_read_only
from raytsystem.policy_simulator import PolicySimulator
from raytsystem.protocols import A2AGateway, AcpAdapter
from raytsystem.replay import ReplayService
from raytsystem.system_status import platform_status
from raytsystem.telemetry import TraceService
from raytsystem.tooling import McpGovernanceService
from raytsystem.workflows import WorkflowService

SYSTEM_SECTIONS = frozenset(
    {
        "evals",
        "traces",
        "replays",
        "policies",
        "tools",
        "protocols",
        "packages",
        "workflows",
        "notifications",
        "backups",
    }
)

_SECTION_FLAGS: dict[str, tuple[str, ...]] = {
    "evals": ("evals_enabled",),
    "traces": ("telemetry_enabled",),
    "replays": ("replay_enabled",),
    "policies": ("policy_simulator_enabled", "emergency_controls_enabled"),
    "tools": ("mcp_governance_enabled",),
    "protocols": ("acp_adapter_enabled", "a2a_gateway_enabled"),
    "packages": ("pack_lifecycle_enabled",),
    "workflows": ("workflow_engine_enabled",),
    "notifications": ("notifications_enabled",),
    "backups": ("backup_enabled",),
}
# "protocols" covers two independent adapters, so any enabled flag keeps it live;
# every other section requires all of its flags.
_ANY_FLAG_SECTIONS = frozenset({"protocols"})
# "policies" is derived from configuration files only, so it never reports the
# store outage that outranks a disabled flag everywhere else.
_CONFIG_ONLY_SECTIONS = frozenset({"policies"})


class FeatureReadModel:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def features(self) -> dict[str, Any]:
        return platform_status(self.root)

    def section(self, section: str, *, limit: int = 100) -> dict[str, Any]:
        if section not in SYSTEM_SECTIONS:
            raise KeyError(section)
        features = load_feature_config(self.root)
        payload = _honest_state(
            section, self._section_payload(section, features, limit=limit), features
        )
        if (
            str(payload.get("state")) == "disabled"
            and section not in _CONFIG_ONLY_SECTIONS
            and self._store_unavailable()
        ):
            return payload | {"snapshot_id": "pview_unavailable", "state": "unavailable"}
        return payload

    def _store_unavailable(self) -> bool:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return True
        store.close()
        return False

    def trace_detail(self, trace_id: str) -> dict[str, Any] | None:
        return TraceService(self.root).trace_detail(trace_id)

    def _section_payload(
        self, section: str, features: FeatureConfig, *, limit: int
    ) -> dict[str, Any]:
        if section == "evals":
            return EvalService(self.root, features=features).list_runs(limit=limit)
        if section == "traces":
            return TraceService(self.root, features=features).list_traces(limit=limit)
        if section == "replays":
            return ReplayService(self.root, features=features).list_plans(limit=limit)
        if section == "policies":
            policy_sha256 = PolicySimulator(self.root, features=features).policy_sha256
            return {
                "snapshot_id": "policy_" + policy_sha256,
                "state": "ready",
                "policy_sha256": policy_sha256,
                "network_default": features.policy.get("network_default", "none"),
                "workspace_default": features.policy.get("workspace_default", "staging_only"),
                "external_actions_default": features.policy.get(
                    "external_actions_default", "approval_required"
                ),
            }
        if section == "tools":
            return McpGovernanceService(self.root, features=features).snapshot(limit=limit)
        if section == "protocols":
            acp = AcpAdapter(self.root, features=features).snapshot()
            a2a = A2AGateway(self.root, features=features).snapshot()
            snapshot_id = str(acp["snapshot_id"])
            return {
                "snapshot_id": snapshot_id,
                "state": "unavailable" if snapshot_id == "pview_unavailable" else "ready",
                "acp": acp,
                "a2a": a2a,
            }
        if section == "packages":
            return PackageLifecycleService(self.root, features=features).snapshot()
        if section == "workflows":
            return WorkflowService(self.root, features=features).snapshot()
        if section == "notifications":
            return NotificationService(self.root, features=features).snapshot(limit=limit)
        return self._backups(limit=limit)

    def _backups(self, *, limit: int) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {
                "snapshot_id": "pview_unavailable",
                "state": "unavailable",
                "backups": [],
            }
        with store:
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready",
                "backups": [record.payload for record in store.list_heads("backup", limit=limit)],
            }


def _honest_state(section: str, payload: dict[str, Any], features: FeatureConfig) -> dict[str, Any]:
    # A missing store is a harder failure than a disabled flag, so it always wins.
    if str(payload.get("state")) == "unavailable":
        return payload
    flags = _SECTION_FLAGS[section]
    live = (
        any(features.enabled(flag) for flag in flags)
        if section in _ANY_FLAG_SECTIONS
        else all(features.enabled(flag) for flag in flags)
    )
    if not live:
        return payload | {"state": "disabled"}
    return payload
