from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    McpHealth,
    McpInvocation,
    McpPolicy,
    McpPromptDefinition,
    McpResourceDefinition,
    McpServerDefinition,
    McpServerRevision,
    McpToolDefinition,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.governance import McpServerState, McpToolPolicy
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    StoredRecord,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.sensitivity import SecretScanner

_EXECUTABLE = re.compile(r"^[a-zA-Z0-9_.@/-]{1,255}$")
_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "type",
        "title",
        "description",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
    }
)
_TOOL_RECORD_EXTRAS = frozenset({"input_schema", "untrusted_output"})


class McpGovernanceError(RuntimeError):
    """MCP catalog input or state transition violates governance policy."""


class McpGovernanceService:
    def __init__(
        self,
        root: Path,
        *,
        scanner: SecretScanner | None = None,
        features: FeatureConfig | None = None,
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()
        self.features = features or load_feature_config(self.root)

    def discover(
        self,
        definition: McpServerDefinition,
        tools: tuple[tuple[McpToolDefinition, dict[str, Any]], ...],
        *,
        observed_package_bytes: bytes,
        resources: tuple[McpResourceDefinition, ...] = (),
        prompts: tuple[McpPromptDefinition, ...] = (),
        actor_id: str = "raytsystem_mcp_catalog",
    ) -> McpServerRevision:
        self._require_enabled()
        state = McpServerState.DISCOVERED
        reasons: list[str] = []
        if sha256_hex(observed_package_bytes) != definition.package_sha256:
            state = McpServerState.QUARANTINED
            reasons.append("package_hash_mismatch")
        if definition.transport == "stdio" and (
            definition.executable is None
            or _EXECUTABLE.fullmatch(definition.executable) is None
            or definition.executable.startswith("/")
            or ".." in Path(definition.executable).parts
        ):
            state = McpServerState.QUARANTINED
            reasons.append("unsafe_executable")
        tool_ids = {tool.tool_id for tool, _schema in tools}
        if tool_ids != set(definition.tool_ids):
            state = McpServerState.QUARANTINED
            reasons.append("tool_manifest_mismatch")
        for tool, schema in tools:
            if tool.server_id != definition.server_id:
                state = McpServerState.QUARANTINED
                reasons.append("tool_server_mismatch")
            try:
                _validate_schema(schema)
            except McpGovernanceError:
                state = McpServerState.QUARANTINED
                reasons.append("malicious_schema")
            if sha256_hex(canonical_json_bytes(schema)) != tool.input_schema_sha256:
                state = McpServerState.QUARANTINED
                reasons.append("tool_schema_hash_mismatch")
            if tool.policy is not McpToolPolicy.CATALOG_ONLY:
                state = McpServerState.QUARANTINED
                reasons.append("unsafe_initial_tool_policy")
        if {resource.resource_id for resource in resources} != set(definition.resource_ids):
            state = McpServerState.QUARANTINED
            reasons.append("resource_manifest_mismatch")
        for resource in resources:
            if resource.server_id != definition.server_id:
                state = McpServerState.QUARANTINED
                reasons.append("resource_server_mismatch")
            if not resource.catalog_only:
                state = McpServerState.QUARANTINED
                reasons.append("unsafe_initial_resource_policy")
        if {prompt.prompt_id for prompt in prompts} != set(definition.prompt_ids):
            state = McpServerState.QUARANTINED
            reasons.append("prompt_manifest_mismatch")
        for prompt in prompts:
            if prompt.server_id != definition.server_id:
                state = McpServerState.QUARANTINED
                reasons.append("prompt_server_mismatch")
            if not prompt.catalog_only:
                state = McpServerState.QUARANTINED
                reasons.append("unsafe_initial_prompt_policy")
        definition_sha256 = sha256_hex(canonical_json_bytes(definition.model_dump(mode="json")))
        revision_id = derive_id(
            "mcprev",
            {
                "server_id": definition.server_id,
                "definition_sha256": definition_sha256,
                "state": state.value,
            },
        )
        revision = McpServerRevision(
            revision_id=revision_id,
            server_id=definition.server_id,
            definition_sha256=definition_sha256,
            state=state,
            created_at=datetime.now(UTC),
        )
        with initialize_platform_store(self.root) as store:
            if store.head("mcp_revision", revision_id) is not None:
                return revision
            prior_server = store.head("mcp_server", definition.server_id)
            store.append_record(
                kind="mcp_server",
                record_id=definition.server_id,
                payload=definition.model_dump(mode="json")
                | {"quarantine_reasons": sorted(set(reasons))},
                state=state.value,
                expected_revision=None if prior_server is None else prior_server.revision,
            )
            for tool, schema in tools:
                prior = store.head("mcp_tool", tool.tool_id)
                store.append_record(
                    kind="mcp_tool",
                    record_id=tool.tool_id,
                    payload=tool.model_dump(mode="json")
                    | {"input_schema": schema, "untrusted_output": True},
                    state=McpToolPolicy.CATALOG_ONLY.value,
                    expected_revision=None if prior is None else prior.revision,
                )
            for resource in resources:
                prior = store.head("mcp_resource", resource.resource_id)
                store.append_record(
                    kind="mcp_resource",
                    record_id=resource.resource_id,
                    payload=resource.model_dump(mode="json") | {"server_revision_id": revision_id},
                    state="catalog_only",
                    expected_revision=None if prior is None else prior.revision,
                )
            for prompt in prompts:
                prior = store.head("mcp_prompt", prompt.prompt_id)
                store.append_record(
                    kind="mcp_prompt",
                    record_id=prompt.prompt_id,
                    payload=prompt.model_dump(mode="json") | {"server_revision_id": revision_id},
                    state="catalog_only",
                    expected_revision=None if prior is None else prior.revision,
                )
            store.append_record(
                kind="mcp_revision",
                record_id=revision_id,
                payload=revision.model_dump(mode="json"),
                state=state.value,
                expected_revision=None,
            )
            store.append_event(
                stream_id=definition.server_id,
                aggregate_id=revision_id,
                event_type="mcp_discovered",
                actor_id=actor_id,
                payload_schema="mcp_server_revision_v1",
                payload={"revision_id": revision_id, "state": state.value, "reasons": reasons},
            )
        return revision

    def validate_revision(self, revision_id: str) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            if revision.state is McpServerState.QUARANTINED:
                raise McpGovernanceError("Quarantined MCP revisions cannot validate")
            if revision.state is McpServerState.BLOCKED:
                raise McpGovernanceError("Blocked MCP revisions cannot validate")
            if revision.state is not McpServerState.DISCOVERED:
                # Monotonic state machine: re-validation never regresses a later state.
                return revision
            validated = revision.model_copy(update={"state": McpServerState.VALIDATED})
            store.append_record(
                kind="mcp_revision",
                record_id=revision_id,
                payload=validated.model_dump(mode="json"),
                state=validated.state.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=revision.server_id,
                aggregate_id=revision_id,
                event_type="mcp_validated",
                actor_id="raytsystem_mcp_validator",
                payload_schema="mcp_server_revision_v1",
                payload={"revision_id": revision_id},
            )
            return validated

    def approve_catalog(
        self,
        revision_id: str,
        *,
        approved_by: str,
        approval_id: str,
    ) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            if revision.state is not McpServerState.VALIDATED:
                raise McpGovernanceError("Only validated MCP revisions may be approved")
            try:
                AuthorityResolver(self.root).require_approval(
                    approval_id,
                    action="approve_mcp_catalog",
                    target_id=revision_id,
                    artifact_sha256=revision.definition_sha256,
                    required_scope=frozenset({"mcp_catalog"}),
                )
            except AuthorityError as error:
                raise McpGovernanceError("MCP approval authority is invalid") from error
            approved = revision.model_copy(
                update={
                    "state": McpServerState.APPROVED,
                    "approved_by": approved_by,
                    "approval_id": approval_id,
                }
            )
            store.append_record(
                kind="mcp_revision",
                record_id=revision_id,
                payload=approved.model_dump(mode="json"),
                state=approved.state.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=revision.server_id,
                aggregate_id=revision_id,
                event_type="mcp_catalog_approved",
                actor_id=approved_by,
                payload_schema="mcp_server_revision_v1",
                payload={"revision_id": revision_id, "approval_id": approval_id},
            )
            return approved

    def enable_server(self, revision_id: str, *, actor_id: str, reason: str) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            if revision.state is not McpServerState.APPROVED:
                raise McpGovernanceError("Only approved MCP revisions may be enabled")
            if revision.approved_by is None or revision.approval_id is None:
                raise McpGovernanceError("MCP enablement requires a recorded approval")
            self._pinned_definition(store, revision)
            return self._transition(
                store, prior, revision, McpServerState.ENABLED, "mcp_enabled", actor_id, reason
            )

    def disable_server(self, revision_id: str, *, actor_id: str, reason: str) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            if revision.state not in {McpServerState.ENABLED, McpServerState.DEGRADED}:
                raise McpGovernanceError("Only enabled MCP revisions may be disabled")
            return self._transition(
                store, prior, revision, McpServerState.DISABLED, "mcp_disabled", actor_id, reason
            )

    def mark_degraded(self, revision_id: str, *, actor_id: str, reason: str) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            if revision.state is not McpServerState.ENABLED:
                raise McpGovernanceError("Only enabled MCP revisions may degrade")
            return self._transition(
                store, prior, revision, McpServerState.DEGRADED, "mcp_degraded", actor_id, reason
            )

    def block_server(self, revision_id: str, *, actor_id: str, reason: str) -> McpServerRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior, revision = self._revision(store, revision_id)
            return self._transition(
                store, prior, revision, McpServerState.BLOCKED, "mcp_blocked", actor_id, reason
            )

    def set_policy(self, policy: McpPolicy, *, actor_id: str) -> McpPolicy:
        self._require_enabled()
        if not policy.verify_hash():
            raise McpGovernanceError("MCP policy hash is invalid")
        if any(
            value is not McpToolPolicy.CATALOG_ONLY for value in policy.tool_policies.values()
        ) and not self.features.enabled("external_mcp_execution_enabled"):
            raise McpGovernanceError("External MCP execution is disabled; catalog_only is required")
        with initialize_platform_store(self.root) as store:
            _prior, revision = self._revision(store, policy.server_revision_id)
            if revision.state not in {McpServerState.APPROVED, McpServerState.ENABLED}:
                raise McpGovernanceError("MCP policy references an unapproved revision")
            definition = self._pinned_definition(store, revision)
            if set(policy.tool_policies) != set(definition.tool_ids):
                raise McpGovernanceError("MCP policy tool set does not match the revision")
            for tool_id in definition.tool_ids:
                tool = self._cataloged_tool(store, tool_id)
                if tool.server_id != definition.server_id:
                    raise McpGovernanceError("MCP policy tool binding is invalid")
            if not self.features.enabled("external_mcp_execution_enabled") and (
                policy.network_allowlist
                or policy.read_roots
                or policy.write_roots
                or policy.secret_ids
            ):
                raise McpGovernanceError("Catalog-only MCP policy cannot request capabilities")
            prior = store.head("mcp_policy", policy.policy_id)
            store.append_record(
                kind="mcp_policy",
                record_id=policy.policy_id,
                payload=policy.model_dump(mode="json"),
                state="active",
                expected_revision=None if prior is None else prior.revision,
            )
            store.append_event(
                stream_id=policy.server_revision_id,
                aggregate_id=policy.policy_id,
                event_type="mcp_policy_updated",
                actor_id=actor_id,
                payload_schema="mcp_policy_v1",
                payload={"policy_id": policy.policy_id, "policy_sha256": policy.policy_sha256},
            )
        return policy

    def redact_output(self, output: bytes) -> tuple[bytes, bool]:
        if len(output) > 4 * 1024 * 1024:
            raise McpGovernanceError("MCP output exceeds the global limit")
        decision = self.scanner.scan(output)
        return (b"[REDACTED]", True) if decision.blocks_processing else (output, False)

    def invoke(
        self,
        *,
        policy_id: str,
        tool_id: str,
        connection_id: str,
        policy_decision_id: str,
        input_bytes: bytes,
        runner: Callable[[bytes], bytes],
        actor_id: str = "raytsystem_mcp_invoker",
    ) -> McpInvocation:
        self._require_enabled()
        if not self.features.enabled("external_mcp_execution_enabled"):
            raise McpGovernanceError(
                "External MCP execution is disabled; servers remain catalog_only"
            )
        with initialize_platform_store(self.root) as store:
            policy_record = store.head("mcp_policy", policy_id)
            if policy_record is None:
                raise McpGovernanceError("MCP policy does not exist")
            policy = McpPolicy.model_validate(policy_record.payload)
            if not policy.verify_hash():
                raise McpGovernanceError("MCP policy hash is invalid")
            _prior, revision = self._revision(store, policy.server_revision_id)
            if revision.state is not McpServerState.ENABLED:
                raise McpGovernanceError("Only enabled MCP servers may invoke tools")
            self._pinned_definition(store, revision)
            if policy.tool_policies.get(tool_id) is not McpToolPolicy.ENABLED:
                raise McpGovernanceError("MCP tool policy does not permit invocation")
            tool = self._cataloged_tool(store, tool_id)
            if tool.server_id != revision.server_id:
                raise McpGovernanceError("MCP tool does not belong to the policy server")
            if len(input_bytes) > tool.max_input_bytes:
                raise McpGovernanceError("MCP input exceeds the per-tool limit")
            started_at = datetime.now(UTC)
            output = runner(input_bytes)
            completed_at = datetime.now(UTC)
            truncated = len(output) > tool.max_output_bytes
            bounded_output, redacted = self.redact_output(output[: tool.max_output_bytes])
            elapsed_ms = int((completed_at - started_at).total_seconds() * 1000)
            timeout_exceeded = elapsed_ms > tool.timeout_ms
            input_sha256 = sha256_hex(input_bytes)
            invocation = McpInvocation(
                invocation_id=derive_id(
                    "mcpinv",
                    {
                        "connection_id": connection_id,
                        "tool_id": tool_id,
                        "input_sha256": input_sha256,
                        "started_at": started_at.isoformat(),
                    },
                ),
                connection_id=connection_id,
                tool_id=tool_id,
                input_sha256=input_sha256,
                output_sha256=sha256_hex(bounded_output),
                policy_decision_id=policy_decision_id,
                redacted=redacted or truncated,
                state="failed" if timeout_exceeded else "succeeded",
                started_at=started_at,
                completed_at=completed_at,
                extensions={
                    "enforced_limits": {
                        "timeout_ms": tool.timeout_ms,
                        "max_input_bytes": tool.max_input_bytes,
                        "max_output_bytes": tool.max_output_bytes,
                    },
                    "elapsed_ms": elapsed_ms,
                    "output_truncated": truncated,
                    "timeout_exceeded": timeout_exceeded,
                },
            )
            store.append_record(
                kind="mcp_invocation",
                record_id=invocation.invocation_id,
                payload=invocation.model_dump(mode="json"),
                state=invocation.state,
                expected_revision=None,
            )
            store.append_event(
                stream_id=revision.server_id,
                aggregate_id=invocation.invocation_id,
                event_type="mcp_invoked",
                actor_id=actor_id,
                payload_schema="mcp_invocation_v1",
                payload={
                    "invocation_id": invocation.invocation_id,
                    "tool_id": tool_id,
                    "state": invocation.state,
                    "redacted": invocation.redacted,
                    "output_truncated": truncated,
                },
            )
            return invocation

    def health(self, revision_id: str) -> McpHealth:
        return McpHealth(
            health_id=derive_id("mcphealth", {"revision_id": revision_id, "state": "disabled"}),
            server_revision_id=revision_id,
            state="disabled",
            reason_codes=("external_mcp_execution_disabled",),
            checked_at=datetime.now(UTC),
        )

    def snapshot(self, *, limit: int = 100) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {
                "snapshot_id": "pview_unavailable",
                "state": "unavailable",
                "servers": [],
                "tools": [],
                "resources": [],
                "prompts": [],
            }
        with store:
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "catalog_only",
                "servers": [
                    record.payload for record in store.list_heads("mcp_server", limit=limit)
                ],
                "tools": [record.payload for record in store.list_heads("mcp_tool", limit=limit)],
                "resources": [
                    record.payload for record in store.list_heads("mcp_resource", limit=limit)
                ],
                "prompts": [
                    record.payload for record in store.list_heads("mcp_prompt", limit=limit)
                ],
                "external_execution": self.features.enabled("external_mcp_execution_enabled"),
            }

    def _require_enabled(self) -> None:
        if not self.features.enabled("mcp_governance_enabled"):
            raise McpGovernanceError("MCP governance is disabled")

    def _revision(
        self, store: PlatformStore, revision_id: str
    ) -> tuple[StoredRecord, McpServerRevision]:
        prior = store.head("mcp_revision", revision_id)
        if prior is None:
            raise McpGovernanceError("MCP revision does not exist")
        return prior, McpServerRevision.model_validate(prior.payload)

    def _transition(
        self,
        store: PlatformStore,
        prior: StoredRecord,
        revision: McpServerRevision,
        state: McpServerState,
        event_type: str,
        actor_id: str,
        reason: str,
    ) -> McpServerRevision:
        if not reason.strip():
            raise McpGovernanceError("MCP state transitions require a reason")
        updated = revision.model_copy(update={"state": state})
        store.append_record(
            kind="mcp_revision",
            record_id=revision.revision_id,
            payload=updated.model_dump(mode="json"),
            state=state.value,
            expected_revision=prior.revision,
        )
        store.append_event(
            stream_id=revision.server_id,
            aggregate_id=revision.revision_id,
            event_type=event_type,
            actor_id=actor_id,
            payload_schema="mcp_server_revision_v1",
            payload={
                "revision_id": revision.revision_id,
                "state": state.value,
                "reason": reason,
            },
        )
        return updated

    def _pinned_definition(
        self, store: PlatformStore, revision: McpServerRevision
    ) -> McpServerDefinition:
        server = store.head("mcp_server", revision.server_id)
        if server is None:
            raise McpGovernanceError("MCP server record is unavailable")
        payload = {
            key: value for key, value in server.payload.items() if key != "quarantine_reasons"
        }
        try:
            definition = McpServerDefinition.model_validate(payload)
        except ValidationError as error:
            raise McpGovernanceError("MCP server record is corrupted") from error
        rendered = sha256_hex(canonical_json_bytes(definition.model_dump(mode="json")))
        if rendered != revision.definition_sha256:
            raise McpGovernanceError("MCP server definition does not match the pinned revision")
        return definition

    def _cataloged_tool(self, store: PlatformStore, tool_id: str) -> McpToolDefinition:
        record = store.head("mcp_tool", tool_id)
        if record is None:
            raise McpGovernanceError("MCP tool is not cataloged")
        payload = {
            key: value for key, value in record.payload.items() if key not in _TOOL_RECORD_EXTRAS
        }
        try:
            tool = McpToolDefinition.model_validate(payload)
        except ValidationError as error:
            raise McpGovernanceError("MCP tool record is corrupted") from error
        schema = record.payload.get("input_schema")
        if sha256_hex(canonical_json_bytes(schema)) != tool.input_schema_sha256:
            raise McpGovernanceError("MCP tool schema hash pinning failed")
        return tool


def _validate_schema(
    value: Any,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
    context: str = "schema",
) -> None:
    if depth > 24:
        raise McpGovernanceError("MCP schema is too deeply nested")
    counter = nodes if nodes is not None else [0]
    counter[0] += 1
    if counter[0] > 2_000:
        raise McpGovernanceError("MCP schema is too complex")
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) for key in value):
            raise McpGovernanceError("MCP schema object is invalid")
        if context == "schema" and set(value) - _SCHEMA_KEYS:
            raise McpGovernanceError("MCP schema contains unsupported keywords")
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef", "contentEncoding", "contentMediaType"}:
                raise McpGovernanceError("MCP schema contains an unsafe keyword")
            if context == "schema" and key == "properties":
                if not isinstance(item, dict):
                    raise McpGovernanceError("MCP properties must be an object")
                for property_schema in item.values():
                    _validate_schema(
                        property_schema,
                        depth=depth + 1,
                        nodes=counter,
                        context="schema",
                    )
            elif (
                context == "schema"
                and key in {"items", "additionalProperties"}
                and isinstance(item, dict)
            ):
                _validate_schema(item, depth=depth + 1, nodes=counter, context="schema")
            elif context == "schema" and key in {"oneOf", "anyOf", "allOf"}:
                if not isinstance(item, list):
                    raise McpGovernanceError("MCP schema composition must be a list")
                for child in item:
                    _validate_schema(child, depth=depth + 1, nodes=counter, context="schema")
            else:
                _validate_schema(item, depth=depth + 1, nodes=counter, context="value")
    elif isinstance(value, list):
        if len(value) > 1_000:
            raise McpGovernanceError("MCP schema list is too large")
        for item in value:
            _validate_schema(item, depth=depth + 1, nodes=counter, context=context)
    elif value is not None and not isinstance(value, str | int | float | bool):
        raise McpGovernanceError("MCP schema contains an unsupported value")
