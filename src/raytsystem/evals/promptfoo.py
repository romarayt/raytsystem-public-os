from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.features import FeatureConfig


class PromptfooConfigError(RuntimeError):
    """Promptfoo configuration crosses the trusted, local-only adapter boundary."""


class PromptfooAdapter:
    """Validation-only optional boundary; this class never executes Promptfoo."""

    _CODE_KEYS = frozenset(
        {"javascript", "python", "script", "transform", "exec", "command", "module", "function"}
    )
    _REMOTE_FEATURES: ClassVar[dict[str, str]] = {
        "sharing": "cloud_sharing",
        "cloud": "cloud_sharing",
        "remotegeneration": "remote_generation",
        "telemetry": "telemetry",
    }
    _CODE_VALUES = frozenset({"javascript", "python", "script", "exec", "shell", "command", "file"})
    _CODE_FILE_SUFFIXES = (".js", ".cjs", ".mjs", ".ts", ".py")

    def __init__(self, workspace: Path, *, features: FeatureConfig) -> None:
        self.workspace = workspace.resolve()
        self.features = features

    def validate_config(
        self,
        config: dict[str, Any],
        *,
        trusted: bool,
        approved_provider_destinations: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        self._require_enabled()
        if not trusted:
            raise PromptfooConfigError("Untrusted Promptfoo configuration is forbidden")
        encoded = canonical_json_bytes(config)
        if len(encoded) > 256 * 1024:
            raise PromptfooConfigError("Promptfoo configuration is too large")
        remote_requested: set[str] = set()
        self._walk(config, remote_requested=remote_requested)
        if remote_requested and not self.features.enabled("promptfoo_remote_generation_enabled"):
            raise PromptfooConfigError("Remote Promptfoo features must remain disabled")
        providers = config.get("providers", [])
        if not isinstance(providers, list):
            raise PromptfooConfigError("Promptfoo providers must be a bounded list")
        destinations: set[str] = set()
        for provider in providers:
            if isinstance(provider, str):
                destinations.add(provider)
            elif isinstance(provider, dict) and isinstance(provider.get("id"), str):
                destinations.add(str(provider["id"]))
            else:
                raise PromptfooConfigError("Promptfoo provider declaration is invalid")
        for destination in destinations:
            scheme = destination.split(":", maxsplit=1)[0].replace("_", "").casefold()
            if scheme in self._CODE_VALUES:
                raise PromptfooConfigError("Executable Promptfoo providers are forbidden")
        if destinations - approved_provider_destinations:
            raise PromptfooConfigError("Promptfoo provider destination is not approved")
        return {
            "config_sha256": sha256_hex(encoded),
            "trusted": True,
            "workspace": self.workspace.name,
            "remote_generation": "remote_generation" in remote_requested,
            "cloud_sharing": "cloud_sharing" in remote_requested,
            "telemetry": "telemetry" in remote_requested,
            "custom_code": False,
            "provider_destinations": sorted(destinations),
        }

    def _require_enabled(self) -> None:
        if not self.features.enabled("promptfoo_adapter_enabled"):
            raise PromptfooConfigError("Promptfoo adapter is disabled")

    def _walk(self, value: Any, *, remote_requested: set[str], depth: int = 0) -> None:
        if depth > 24:
            raise PromptfooConfigError("Promptfoo configuration is too deeply nested")
        if isinstance(value, dict):
            if len(value) > 256:
                raise PromptfooConfigError("Promptfoo object is too large")
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PromptfooConfigError("Promptfoo keys must be strings")
                normalized = key.replace("_", "").casefold()
                if normalized in {item.replace("_", "").casefold() for item in self._CODE_KEYS}:
                    raise PromptfooConfigError("Custom JS/Python assertions are forbidden")
                remote_feature = self._REMOTE_FEATURES.get(normalized)
                if remote_feature is not None and not _remote_disabled(item):
                    remote_requested.add(remote_feature)
                if normalized in {"type", "assertion", "provider", "method"} and isinstance(
                    item, str
                ):
                    code_value = item.split(":", maxsplit=1)[0].casefold()
                    if code_value in self._CODE_VALUES:
                        raise PromptfooConfigError("Executable Promptfoo assertions are forbidden")
                self._walk(item, remote_requested=remote_requested, depth=depth + 1)
            return
        if isinstance(value, list):
            if len(value) > 2_000:
                raise PromptfooConfigError("Promptfoo list is too large")
            for item in value:
                self._walk(item, remote_requested=remote_requested, depth=depth + 1)
            return
        if isinstance(value, str):
            if value.casefold().startswith("file://"):
                reference = value[len("file://") :].split(":", maxsplit=1)[0].casefold()
                if reference.endswith(self._CODE_FILE_SUFFIXES):
                    raise PromptfooConfigError("Custom JS/Python assertions are forbidden")
            return
        if value is not None and not isinstance(value, int | bool | float):
            raise PromptfooConfigError("Promptfoo configuration contains an unsupported value")


def _remote_disabled(value: Any) -> bool:
    return value is None or value is False or value in ("off", "disabled")
