"""Governed MCP catalog: lifecycle state machine, quarantine, policy, and limits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import (
    McpPolicy,
    McpPromptDefinition,
    McpResourceDefinition,
    McpServerDefinition,
    McpServerRevision,
    McpToolDefinition,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.contracts.governance import McpServerState, McpToolPolicy
from raytsystem.platform_store import open_platform_store_read_only
from raytsystem.tooling import McpGovernanceError, McpGovernanceService

pytestmark = pytest.mark.filterwarnings("error")

_PACKAGE = b"mcp-package-bytes"
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


def _tool(schema: dict[str, Any] = _SCHEMA, **overrides: object) -> McpToolDefinition:
    payload: dict[str, object] = {
        "tool_id": "tool_search",
        "server_id": "server_docs",
        "name": "search",
        "description": "Search the documentation catalog",
        "input_schema_sha256": sha256_hex(canonical_json_bytes(schema)),
    }
    payload.update(overrides)
    return McpToolDefinition.model_validate(payload)


def _definition(**overrides: object) -> McpServerDefinition:
    payload: dict[str, object] = {
        "server_id": "server_docs",
        "source": "packs/starter/mcp/docs",
        "version": "1.0.0",
        "package_sha256": sha256_hex(_PACKAGE),
        "transport": "stdio",
        "executable": "docs-mcp",
        "publisher": "raytsystem",
        "license_expression": "MIT",
        "tool_ids": ("tool_search",),
    }
    payload.update(overrides)
    return McpServerDefinition.model_validate(payload)


def _service(root: Path, **flag_overrides: bool) -> McpGovernanceService:
    return McpGovernanceService(make_platform_workspace(root, flag_overrides=flag_overrides))


def _discover(
    service: McpGovernanceService,
    definition: McpServerDefinition | None = None,
    tools: tuple[tuple[McpToolDefinition, dict[str, Any]], ...] | None = None,
    **kwargs: Any,
) -> McpServerRevision:
    return service.discover(
        definition or _definition(),
        ((_tool(), _SCHEMA),) if tools is None else tools,
        observed_package_bytes=_PACKAGE,
        **kwargs,
    )


def _approved(root: Path, service: McpGovernanceService) -> McpServerRevision:
    revision = _discover(service)
    service.validate_revision(revision.revision_id)
    approval = store_approval(
        root,
        action="approve_mcp_catalog",
        target_id=revision.revision_id,
        artifact_sha256=revision.definition_sha256,
        scope=("mcp_catalog",),
    )
    return service.approve_catalog(
        revision.revision_id, approved_by="user_local_test", approval_id=approval.approval_id
    )


def _policy(
    revision_id: str,
    tool_policies: dict[str, McpToolPolicy],
    **overrides: object,
) -> McpPolicy:
    payload: dict[str, object] = {
        "policy_id": "mcppol_docs",
        "server_revision_id": revision_id,
        "tool_policies": tool_policies,
        "policy_sha256": "0" * 64,
    }
    payload.update(overrides)
    unsigned = McpPolicy.model_validate(payload)
    return unsigned.model_copy(
        update={"policy_sha256": sha256_hex(canonical_json_bytes(unsigned.identity_payload()))}
    )


def _store_bytes(root: Path) -> bytes:
    data = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = root / "ops" / f"platform.sqlite{suffix}"
        if candidate.is_file():
            data += candidate.read_bytes()
    return data


def test_command_injection_is_quarantined_or_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    revision = _discover(service, _definition(executable="docs-mcp; rm -rf /tmp/x"))
    assert revision.state is McpServerState.QUARANTINED
    store = open_platform_store_read_only(tmp_path)
    assert store is not None
    with store:
        server = store.head("mcp_server", "server_docs")
        assert server is not None
        assert "unsafe_executable" in server.payload["quarantine_reasons"]
    with pytest.raises(ValidationError, match="unambiguous"):
        _definition(transport="http", url="http://127.0.0.1/mcp", executable="docs-mcp")
    with pytest.raises(McpGovernanceError, match="cannot validate"):
        service.validate_revision(revision.revision_id)


def test_filesystem_path_escape_is_rejected() -> None:
    with pytest.raises(ValidationError, match="workspace"):
        _definition(filesystem_requirements=("/etc/passwd",))
    with pytest.raises(ValidationError, match="workspace"):
        _definition(filesystem_requirements=("data/../../secrets",))


def test_malicious_schema_quarantines_revision(tmp_path: Path) -> None:
    unsafe: dict[str, Any] = {"type": "object", "$ref": "file:///etc/passwd"}
    service = _service(tmp_path / "unsafe")
    revision = _discover(service, tools=((_tool(unsafe), unsafe),))
    assert revision.state is McpServerState.QUARANTINED
    oversized: dict[str, Any] = {
        "type": "object",
        "properties": {f"key{index}": {"type": "string"} for index in range(2_500)},
    }
    other = _service(tmp_path / "oversized")
    quarantined = _discover(other, tools=((_tool(oversized), oversized),))
    assert quarantined.state is McpServerState.QUARANTINED
    store = open_platform_store_read_only(tmp_path / "oversized")
    assert store is not None
    with store:
        server = store.head("mcp_server", "server_docs")
        assert server is not None
        assert "malicious_schema" in server.payload["quarantine_reasons"]


def test_resources_and_prompts_are_cataloged_and_bound(tmp_path: Path) -> None:
    service = _service(tmp_path)
    resource = McpResourceDefinition(
        resource_id="res_docs_page",
        server_id="server_docs",
        uri_template="docs://{page}",
    )
    prompt = McpPromptDefinition(
        prompt_id="prm_docs_summary",
        server_id="server_docs",
        name="summarize",
        content_sha256=sha256_hex(b"prompt-body"),
    )
    revision = _discover(
        service,
        _definition(resource_ids=("res_docs_page",), prompt_ids=("prm_docs_summary",)),
        resources=(resource,),
        prompts=(prompt,),
    )
    assert revision.state is McpServerState.DISCOVERED
    snapshot = service.snapshot()
    assert [item["resource_id"] for item in snapshot["resources"]] == ["res_docs_page"]
    assert [item["prompt_id"] for item in snapshot["prompts"]] == ["prm_docs_summary"]
    assert snapshot["resources"][0]["server_revision_id"] == revision.revision_id
    assert snapshot["prompts"][0]["server_revision_id"] == revision.revision_id
    mismatched = _service(tmp_path / "mismatch")
    quarantined = _discover(mismatched, _definition(resource_ids=("res_docs_page",)))
    assert quarantined.state is McpServerState.QUARANTINED


def test_lifecycle_happy_path_with_hash_pinning(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approved = _approved(tmp_path, service)
    assert approved.state is McpServerState.APPROVED
    enabled = service.enable_server(
        approved.revision_id, actor_id="user_local_test", reason="pilot rollout"
    )
    assert enabled.state is McpServerState.ENABLED
    store = open_platform_store_read_only(tmp_path)
    assert store is not None
    with store:
        events = store.list_events(approved.server_id)
        assert [event["event_type"] for event in events][-1] == "mcp_enabled"
        assert events[-1]["payload"]["reason"] == "pilot rollout"
        assert store.verify_event_stream(approved.server_id)


def test_enable_requires_approval_and_pinned_definition(tmp_path: Path) -> None:
    service = _service(tmp_path)
    revision = _discover(service)
    validated = service.validate_revision(revision.revision_id)
    assert validated.state is McpServerState.VALIDATED
    with pytest.raises(McpGovernanceError, match="approved"):
        service.enable_server(revision.revision_id, actor_id="user_local_test", reason="early")
    approved = _approved(tmp_path, service)
    _discover(service, _definition(version="2.0.0", package_sha256=sha256_hex(_PACKAGE)))
    with pytest.raises(McpGovernanceError, match="pinned"):
        service.enable_server(approved.revision_id, actor_id="user_local_test", reason="stale")


def test_revalidation_never_downgrades_an_approved_revision(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approved = _approved(tmp_path, service)
    revalidated = service.validate_revision(approved.revision_id)
    assert revalidated.state is McpServerState.APPROVED
    store = open_platform_store_read_only(tmp_path)
    assert store is not None
    with store:
        head = store.head("mcp_revision", approved.revision_id)
        assert head is not None and head.state == McpServerState.APPROVED.value


def test_block_is_allowed_from_any_state_and_is_terminal(tmp_path: Path) -> None:
    service = _service(tmp_path)
    revision = _discover(service)
    blocked = service.block_server(
        revision.revision_id, actor_id="user_local_test", reason="supply chain alert"
    )
    assert blocked.state is McpServerState.BLOCKED
    with pytest.raises(McpGovernanceError, match="Blocked"):
        service.validate_revision(revision.revision_id)
    with pytest.raises(McpGovernanceError, match="approved"):
        service.enable_server(revision.revision_id, actor_id="user_local_test", reason="retry")
    with pytest.raises(McpGovernanceError, match="reason"):
        service.block_server(revision.revision_id, actor_id="user_local_test", reason="  ")


def test_degraded_and_disabled_transitions_are_guarded(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approved = _approved(tmp_path, service)
    with pytest.raises(McpGovernanceError, match="enabled"):
        service.mark_degraded(approved.revision_id, actor_id="user_local_test", reason="flaky")
    with pytest.raises(McpGovernanceError, match="enabled"):
        service.disable_server(approved.revision_id, actor_id="user_local_test", reason="off")
    service.enable_server(approved.revision_id, actor_id="user_local_test", reason="rollout")
    degraded = service.mark_degraded(
        approved.revision_id, actor_id="user_local_test", reason="latency"
    )
    assert degraded.state is McpServerState.DEGRADED
    disabled = service.disable_server(
        approved.revision_id, actor_id="user_local_test", reason="maintenance"
    )
    assert disabled.state is McpServerState.DISABLED


def test_default_tool_policy_is_catalog_only_and_execution_flag_gates(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approved = _approved(tmp_path, service)
    store = open_platform_store_read_only(tmp_path)
    assert store is not None
    with store:
        tool = store.head("mcp_tool", "tool_search")
        assert tool is not None and tool.state == McpToolPolicy.CATALOG_ONLY.value
    with pytest.raises(McpGovernanceError, match="catalog_only is required"):
        service.set_policy(
            _policy(approved.revision_id, {"tool_search": McpToolPolicy.ENABLED}),
            actor_id="user_local_test",
        )
    stored = service.set_policy(
        _policy(approved.revision_id, {"tool_search": McpToolPolicy.CATALOG_ONLY}),
        actor_id="user_local_test",
    )
    assert stored.verify_hash()
    with pytest.raises(McpGovernanceError, match="disabled"):
        service.invoke(
            policy_id=stored.policy_id,
            tool_id="tool_search",
            connection_id="conn_local_test",
            policy_decision_id="pdec_local_test",
            input_bytes=b"{}",
            runner=lambda _data: b"{}",
        )
    hot = _service(tmp_path / "hot")
    revision = _discover(hot, tools=((_tool(policy=McpToolPolicy.ENABLED), _SCHEMA),))
    assert revision.state is McpServerState.QUARANTINED


def test_set_policy_validates_against_the_pinned_revision(tmp_path: Path) -> None:
    service = _service(tmp_path, external_mcp_execution_enabled=True)
    approved = _approved(tmp_path, service)
    with pytest.raises(McpGovernanceError, match="tool set"):
        service.set_policy(
            _policy(approved.revision_id, {"tool_other": McpToolPolicy.ENABLED}),
            actor_id="user_local_test",
        )
    _discover(service, _definition(version="2.0.0"))
    with pytest.raises(McpGovernanceError, match="pinned"):
        service.set_policy(
            _policy(approved.revision_id, {"tool_search": McpToolPolicy.ENABLED}),
            actor_id="user_local_test",
        )


def test_invoke_enforces_per_tool_limits_and_records_them(tmp_path: Path) -> None:
    service = _service(tmp_path, external_mcp_execution_enabled=True)
    approved = _approved(tmp_path, service)
    policy = service.set_policy(
        _policy(approved.revision_id, {"tool_search": McpToolPolicy.ENABLED}),
        actor_id="user_local_test",
    )
    service.enable_server(approved.revision_id, actor_id="user_local_test", reason="pilot")
    calls: list[bytes] = []

    def runner(data: bytes) -> bytes:
        calls.append(data)
        return b"x" * 300_000

    with pytest.raises(McpGovernanceError, match="per-tool limit"):
        service.invoke(
            policy_id=policy.policy_id,
            tool_id="tool_search",
            connection_id="conn_local_test",
            policy_decision_id="pdec_local_test",
            input_bytes=b"x" * 70_000,
            runner=runner,
        )
    assert calls == []
    invocation = service.invoke(
        policy_id=policy.policy_id,
        tool_id="tool_search",
        connection_id="conn_local_test",
        policy_decision_id="pdec_local_test",
        input_bytes=b'{"query": "docs"}',
        runner=runner,
    )
    assert calls == [b'{"query": "docs"}']
    assert invocation.state == "succeeded"
    assert invocation.redacted is True
    assert invocation.output_sha256 == sha256_hex(b"x" * 262_144)
    limits = invocation.extensions["enforced_limits"]
    assert limits == {"timeout_ms": 10_000, "max_input_bytes": 65_536, "max_output_bytes": 262_144}
    assert invocation.extensions["output_truncated"] is True
    store = open_platform_store_read_only(tmp_path)
    assert store is not None
    with store:
        record = store.head("mcp_invocation", invocation.invocation_id)
        assert record is not None
        assert record.payload["extensions"]["enforced_limits"] == limits


def test_invocation_output_secret_is_redacted_in_store(tmp_path: Path) -> None:
    secret = b"AKIA" + b"B" * 16
    service = _service(tmp_path, external_mcp_execution_enabled=True)
    approved = _approved(tmp_path, service)
    policy = service.set_policy(
        _policy(approved.revision_id, {"tool_search": McpToolPolicy.ENABLED}),
        actor_id="user_local_test",
    )
    service.enable_server(approved.revision_id, actor_id="user_local_test", reason="pilot")
    invocation = service.invoke(
        policy_id=policy.policy_id,
        tool_id="tool_search",
        connection_id="conn_local_test",
        policy_decision_id="pdec_local_test",
        input_bytes=b'{"query": "docs"}',
        runner=lambda _data: b'{"result": "' + secret + b'"}',
    )
    assert invocation.redacted is True
    assert invocation.output_sha256 == sha256_hex(b"[REDACTED]")
    assert secret not in _store_bytes(tmp_path)


def test_disabled_governance_fails_closed_for_all_mutations(tmp_path: Path) -> None:
    service = _service(tmp_path, mcp_governance_enabled=False)
    revision_id = "mcprev_missing"
    with pytest.raises(McpGovernanceError, match="governance is disabled"):
        _discover(service)
    for mutation in (
        lambda: service.validate_revision(revision_id),
        lambda: service.approve_catalog(
            revision_id, approved_by="user_local_test", approval_id="apr_missing"
        ),
        lambda: service.enable_server(revision_id, actor_id="user_local_test", reason="r"),
        lambda: service.disable_server(revision_id, actor_id="user_local_test", reason="r"),
        lambda: service.mark_degraded(revision_id, actor_id="user_local_test", reason="r"),
        lambda: service.block_server(revision_id, actor_id="user_local_test", reason="r"),
        lambda: service.set_policy(
            _policy(revision_id, {"tool_search": McpToolPolicy.CATALOG_ONLY}),
            actor_id="user_local_test",
        ),
        lambda: service.invoke(
            policy_id="mcppol_docs",
            tool_id="tool_search",
            connection_id="conn_local_test",
            policy_decision_id="pdec_local_test",
            input_bytes=b"{}",
            runner=lambda _data: b"{}",
        ),
    ):
        with pytest.raises(McpGovernanceError, match="governance is disabled"):
            mutation()
