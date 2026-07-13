from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.security.paths import PathPolicyError, read_regular_file

PLATFORM_CONFIG = "config/platform.yaml"
FEATURE_NAMES = (
    "evals_enabled",
    "promptfoo_adapter_enabled",
    "telemetry_enabled",
    "otel_export_enabled",
    "replay_enabled",
    "policy_simulator_enabled",
    "emergency_controls_enabled",
    "mcp_governance_enabled",
    "acp_adapter_enabled",
    "a2a_gateway_enabled",
    "pack_lifecycle_enabled",
    "workflow_engine_enabled",
    "notifications_enabled",
    "external_notifications_enabled",
    "restricted_encryption_enabled",
    "backup_enabled",
    "promptfoo_remote_generation_enabled",
    "external_mcp_execution_enabled",
    "a2a_network_exposure_enabled",
    "external_kms_enabled",
)
_DEPENDENCIES = {
    "promptfoo_adapter_enabled": "evals_enabled",
    "promptfoo_remote_generation_enabled": "promptfoo_adapter_enabled",
    "otel_export_enabled": "telemetry_enabled",
    "external_mcp_execution_enabled": "mcp_governance_enabled",
    "a2a_network_exposure_enabled": "a2a_gateway_enabled",
    "external_notifications_enabled": "notifications_enabled",
    "external_kms_enabled": "restricted_encryption_enabled",
}


class FeatureConfigError(RuntimeError):
    """Feature configuration is malformed or enables an impossible dependency."""


@dataclass(frozen=True)
class FeatureConfig:
    version: str
    store: str
    flags: dict[str, bool]
    policy: dict[str, Any]
    circuit_breakers: dict[str, int]
    config_sha256: str

    def enabled(self, feature: str) -> bool:
        if feature not in self.flags:
            raise KeyError(feature)
        return self.flags[feature]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "flags": dict(sorted(self.flags.items())),
            "config_sha256": self.config_sha256,
        }


def load_feature_config(root: Path) -> FeatureConfig:
    try:
        data = read_regular_file(root.resolve(), PLATFORM_CONFIG, max_bytes=128 * 1024).data
    except (OSError, PathPolicyError) as error:
        raise FeatureConfigError("Platform feature configuration is unavailable") from error
    try:
        payload = yaml.safe_load(data)
    except yaml.YAMLError as error:
        raise FeatureConfigError("Platform feature configuration is invalid") from error
    if not isinstance(payload, dict):
        raise FeatureConfigError("Platform feature configuration must be a mapping")
    version = payload.get("version")
    store = payload.get("store")
    raw_flags = payload.get("features")
    raw_policy = payload.get("policy", {})
    raw_breakers = payload.get("circuit_breakers", {})
    if not isinstance(version, str) or not version:
        raise FeatureConfigError("Platform configuration version is missing")
    if store != "ops/platform.sqlite":
        raise FeatureConfigError("Platform store path must remain fixed")
    if not isinstance(raw_flags, dict) or set(raw_flags) != set(FEATURE_NAMES):
        raise FeatureConfigError("Platform feature flags are incomplete or unknown")
    if any(not isinstance(value, bool) for value in raw_flags.values()):
        raise FeatureConfigError("Platform feature flags must be booleans")
    flags = {name: bool(raw_flags[name]) for name in FEATURE_NAMES}
    for child, parent in _DEPENDENCIES.items():
        if flags[child] and not flags[parent]:
            raise FeatureConfigError(f"{child} requires {parent}")
    if not isinstance(raw_policy, dict):
        raise FeatureConfigError("Platform policy must be a mapping")
    if not isinstance(raw_breakers, dict) or any(
        not isinstance(key, str) or not isinstance(value, int) or value <= 0
        for key, value in raw_breakers.items()
    ):
        raise FeatureConfigError("Circuit breaker thresholds must be positive integers")
    normalized = {
        "version": version,
        "store": store,
        "features": flags,
        "policy": raw_policy,
        "circuit_breakers": raw_breakers,
    }
    return FeatureConfig(
        version=version,
        store=store,
        flags=flags,
        policy=dict(raw_policy),
        circuit_breakers={str(key): int(value) for key, value in raw_breakers.items()},
        config_sha256=sha256_hex(canonical_json_bytes(normalized)),
    )
