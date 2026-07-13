"""ACP session boundary and loopback-only A2A gateway: fail-closed protocol tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from platform_helpers import make_platform_workspace
from raytsystem.contracts import A2AAgentCard, AcpEvent, canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.execution import ExecutionSession
from raytsystem.execution.store import ExecutionStore
from raytsystem.platform_store import open_platform_store_read_only
from raytsystem.protocols import A2AGateway, A2AGatewayError, AcpAdapter, AcpAdapterError

pytestmark = pytest.mark.filterwarnings("error")

RUNTIME_SESSION_ID = "xsession_protocol"
ADAPTER_ID = "adapter_fake"
WORKSPACE_ID = "workspace_protocol"
DEFAULT_CAPABILITIES = (
    "messages",
    "streaming_events",
    "cancellation",
    "session_resume",
    "permission_requests",
)


def _acp_workspace(root: Path, **flag_overrides: bool) -> Path:
    workspace = make_platform_workspace(
        root, flag_overrides={"acp_adapter_enabled": True, **flag_overrides}
    )
    session = ExecutionSession(
        session_id=RUNTIME_SESSION_ID,
        runtime_adapter_id=ADAPTER_ID,
        provider="fake",
        task_id="task_protocol",
        employee_id="demp_protocol",
        workspace_id=WORKSPACE_ID,
        graph_snapshot_id="cgraph_protocol",
        context_snapshot_sha256="e" * 64,
        compatibility_sha256="f" * 64,
        started_at=datetime.now(UTC),
    )
    with ExecutionStore.open_for_write(workspace / "ops" / "control.sqlite") as store:
        store.put(session, expected_revision=None)
    return workspace


def _initialize(
    adapter: AcpAdapter,
    *,
    protocol_version: str = "1.0.0",
    requested: tuple[str, ...] = DEFAULT_CAPABILITIES,
):
    return adapter.initialize(
        runtime_session_id=RUNTIME_SESSION_ID,
        adapter_id=ADAPTER_ID,
        workspace_id=WORKSPACE_ID,
        protocol_version=protocol_version,
        requested_capabilities=requested,
    )


def _acp_event_raw(
    session_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, Any],
    *,
    policy_decision_id: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    seed = AcpEvent.model_validate(
        {
            "event_id": "acpevt_" + "0" * 64,
            "acp_session_id": session_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload_sha256": sha256_hex(canonical_json_bytes(payload)),
            "policy_decision_id": policy_decision_id,
            "approval_id": approval_id,
            "created_at": datetime.now(UTC),
        }
    )
    event = seed.model_copy(update={"event_id": derive_id("acpevt", seed.identity_payload())})
    raw = event.model_dump(mode="json")
    raw["payload"] = payload
    return raw


def test_acp_initialize_negotiates_subset_and_issues_resume_token(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    capability_set, session, resume_token = _initialize(
        adapter, requested=(*DEFAULT_CAPABILITIES, "unsupported_capability", "root_shell")
    )
    assert set(capability_set.capabilities) == set(DEFAULT_CAPABILITIES)
    assert "unsupported_capability" not in capability_set.capabilities
    assert session.state == "ready"
    assert session.resume_token_sha256 == sha256_hex(resume_token.encode("utf-8"))
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        head = store.head("acp_session", session.acp_session_id)
        assert head is not None and head.payload["resume_token_sha256"] != resume_token
        assert store.verify_event_stream(session.acp_session_id)


def test_acp_initialize_refuses_unbound_runtime_session(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    with pytest.raises(AcpAdapterError, match="not bound"):
        adapter.initialize(
            runtime_session_id="xsession_missing",
            adapter_id=ADAPTER_ID,
            workspace_id=WORKSPACE_ID,
            protocol_version="1.0.0",
            requested_capabilities=DEFAULT_CAPABILITIES,
        )
    with pytest.raises(AcpAdapterError, match="not bound"):
        adapter.initialize(
            runtime_session_id=RUNTIME_SESSION_ID,
            adapter_id="adapter_other",
            workspace_id=WORKSPACE_ID,
            protocol_version="1.0.0",
            requested_capabilities=DEFAULT_CAPABILITIES,
        )


def test_acp_disabled_flag_fails_closed_on_every_entry_point(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    adapter = AcpAdapter(root)
    with pytest.raises(AcpAdapterError, match="disabled"):
        _initialize(adapter)
    with pytest.raises(AcpAdapterError, match="disabled"):
        adapter.accept_event({"payload": {}})
    with pytest.raises(AcpAdapterError, match="disabled"):
        adapter.resume("acpsess_" + "0" * 64, "token")
    with pytest.raises(AcpAdapterError, match="disabled"):
        adapter.cancel("acpsess_" + "0" * 64)
    with pytest.raises(AcpAdapterError, match="disabled"):
        adapter.close("acpsess_" + "0" * 64)
    with pytest.raises(AcpAdapterError, match="disabled"):
        adapter.fail("acpsess_" + "0" * 64, reason="failure")
    assert adapter.snapshot()["state"] == "disabled"
    assert adapter.snapshot()["sessions"] == []


def test_acp_malformed_events_are_rejected_without_state_corruption(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, session, _ = _initialize(adapter)
    accepted = adapter.accept_event(
        _acp_event_raw(session.acp_session_id, 1, "message", {"text": "hello"})
    )
    assert accepted.sequence == 1
    with pytest.raises(AcpAdapterError, match="sequence"):
        adapter.accept_event(_acp_event_raw(session.acp_session_id, 5, "message", {"text": "skip"}))
    with pytest.raises(AcpAdapterError, match="unknown session"):
        adapter.accept_event(_acp_event_raw("acpsess_" + "0" * 64, 1, "message", {"text": "ghost"}))
    with pytest.raises(AcpAdapterError, match="bounded limit"):
        adapter.accept_event({"payload": {"blob": "x" * (300 * 1024)}})
    with pytest.raises(AcpAdapterError, match="Malformed"):
        adapter.accept_event({"payload": {}, "event_id": "acpevt_bad"})
    with pytest.raises(AcpAdapterError, match="payload must be an object"):
        adapter.accept_event({"payload": ["not", "an", "object"]})
    forged = _acp_event_raw(session.acp_session_id, 2, "message", {"text": "forged"})
    forged["event_id"] = "acpevt_" + "1" * 64
    with pytest.raises(AcpAdapterError, match="hash-bound"):
        adapter.accept_event(forged)
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        sequence = store.head("acp_sequence", session.acp_session_id)
        assert sequence is not None and sequence.payload["last_sequence"] == 1
        head = store.head("acp_session", session.acp_session_id)
        assert head is not None and head.payload["state"] == "streaming"
        assert store.verify_event_stream(session.acp_session_id)
    next_event = adapter.accept_event(
        _acp_event_raw(session.acp_session_id, 2, "message", {"text": "still alive"})
    )
    assert next_event.sequence == 2


def test_acp_permission_bypass_is_rejected_and_never_auto_approves(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, session, _ = _initialize(adapter, requested=("messages", "session_resume"))
    with pytest.raises(AcpAdapterError, match="bypasses policy"):
        adapter.accept_event(
            _acp_event_raw(session.acp_session_id, 1, "permission_request", {"tool": "bash"})
        )
    with pytest.raises(AcpAdapterError, match="never carry"):
        adapter.accept_event(
            _acp_event_raw(
                session.acp_session_id,
                1,
                "permission_request",
                {"tool": "bash"},
                policy_decision_id="pdec_" + "0" * 64,
                approval_id="apr_" + "0" * 64,
            )
        )
    with pytest.raises(AcpAdapterError, match="authority is invalid"):
        adapter.accept_event(
            _acp_event_raw(
                session.acp_session_id,
                1,
                "permission_request",
                {"tool": "bash"},
                policy_decision_id="pdec_" + "0" * 64,
            )
        )
    with pytest.raises(AcpAdapterError, match="requires approval"):
        adapter.accept_event(
            _acp_event_raw(
                session.acp_session_id,
                1,
                "tool_call",
                {"tool": "bash"},
                policy_decision_id="pdec_" + "0" * 64,
            )
        )
    with pytest.raises(AcpAdapterError, match="never negotiated"):
        adapter.accept_event(
            _acp_event_raw(session.acp_session_id, 1, "cancelled", {"reason": "not negotiated"})
        )
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.list_heads("authority_approval", limit=10) == ()
        sequence = store.head("acp_sequence", session.acp_session_id)
        assert sequence is not None and sequence.payload["last_sequence"] == 0
        head = store.head("acp_session", session.acp_session_id)
        assert head is not None and head.payload["state"] == "ready"


def test_acp_resume_requires_exact_token_and_rotates_it(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, session, first_token = _initialize(adapter)
    with pytest.raises(AcpAdapterError, match="resume token is invalid"):
        adapter.resume(session.acp_session_id, "deadbeef")
    with pytest.raises(AcpAdapterError, match="resume token is invalid"):
        adapter.resume(session.acp_session_id, "")
    resumed, second_token = adapter.resume(session.acp_session_id, first_token)
    assert second_token != first_token
    assert resumed.resume_token_sha256 == sha256_hex(second_token.encode("utf-8"))
    with pytest.raises(AcpAdapterError, match="resume token is invalid"):
        adapter.resume(session.acp_session_id, first_token)
    _, third_token = adapter.resume(session.acp_session_id, second_token)
    assert third_token not in {first_token, second_token}


def test_acp_resume_requires_negotiated_capability(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, session, token = _initialize(adapter, requested=("messages",))
    with pytest.raises(AcpAdapterError, match="never negotiated"):
        adapter.resume(session.acp_session_id, token)


def test_acp_cancelled_session_accepts_nothing(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, session, token = _initialize(adapter)
    adapter.accept_event(_acp_event_raw(session.acp_session_id, 1, "message", {"text": "start"}))
    cancelled = adapter.cancel(session.acp_session_id, reason="operator_stop")
    assert cancelled.state == "cancelled"
    assert adapter.cancel(session.acp_session_id, reason="operator_stop").state == "cancelled"
    with pytest.raises(AcpAdapterError, match="inactive session"):
        adapter.accept_event(_acp_event_raw(session.acp_session_id, 2, "message", {"text": "late"}))
    with pytest.raises(AcpAdapterError, match="not resumable"):
        adapter.resume(session.acp_session_id, token)
    with pytest.raises(AcpAdapterError, match="already terminal"):
        adapter.close(session.acp_session_id)
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        events = store.list_events(session.acp_session_id)
        assert [item["event_type"] for item in events].count("acp_session_cancelled") == 1
        assert store.verify_event_stream(session.acp_session_id)


def test_acp_lifecycle_transitions_cover_streaming_closed_and_failed(tmp_path: Path) -> None:
    root = _acp_workspace(tmp_path)
    adapter = AcpAdapter(root)
    _, streaming_session, _ = _initialize(adapter, protocol_version="1.0.1")
    assert streaming_session.state == "ready"
    adapter.accept_event(
        _acp_event_raw(streaming_session.acp_session_id, 1, "message", {"text": "first"})
    )
    completed = adapter.accept_event(
        _acp_event_raw(streaming_session.acp_session_id, 2, "completed", {"summary": "done"})
    )
    assert completed.event_type == "completed"
    _, failing_session, _ = _initialize(adapter, protocol_version="1.0.2")
    failed = adapter.fail(failing_session.acp_session_id, reason="runtime_crash")
    assert failed.state == "failed"
    _, closing_session, _ = _initialize(adapter, protocol_version="1.0.3")
    closed = adapter.close(closing_session.acp_session_id)
    assert closed.state == "closed"
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        for session_id, state in (
            (streaming_session.acp_session_id, "closed"),
            (failing_session.acp_session_id, "failed"),
            (closing_session.acp_session_id, "closed"),
        ):
            head = store.head("acp_session", session_id)
            assert head is not None and head.payload["state"] == state
            with pytest.raises(AcpAdapterError, match="inactive session"):
                adapter.accept_event(_acp_event_raw(session_id, 3, "message", {"text": "x"}))


def _a2a_workspace(root: Path, **overrides: object) -> Path:
    flag_overrides: dict[str, bool] = {"a2a_gateway_enabled": True}
    policy_overrides: dict[str, object] = {}
    for key, value in overrides.items():
        if isinstance(value, bool):
            flag_overrides[key] = value
        else:
            policy_overrides[key] = value
    return make_platform_workspace(
        root, flag_overrides=flag_overrides, policy_overrides=policy_overrides
    )


def _project_card(gateway: A2AGateway) -> A2AAgentCard:
    return gateway.project_agent_card(
        local_agent_id="agent_local_analyst",
        protocol_version="1.0.0",
        capability_ids=("code_review",),
        authentication_schemes=("shared_secret",),
        extension_ids=("ext.progress", "ext.metrics"),
        authentication=b"loopback-shared-secret",
    )


def _submit(gateway: A2AGateway, card: A2AAgentCard, **overrides: Any):
    kwargs: dict[str, Any] = {
        "remote_address": "127.0.0.1",
        "remote_identity": "peer_local_agent",
        "authentication": b"loopback-shared-secret",
        "card": card,
        "task_payload": b'{"goal":"review"}',
        "artifacts": {"artifact_diff": b"diff-bytes"},
        "extension_ids": ("ext.progress",),
    }
    kwargs.update(overrides)
    return gateway.submit(**kwargs)


def test_a2a_disabled_flag_fails_closed_on_every_entry_point(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    gateway = A2AGateway(root)
    with pytest.raises(A2AGatewayError, match="disabled"):
        _project_card(gateway)
    with pytest.raises(A2AGatewayError, match="disabled"):
        gateway.submit(
            remote_address="127.0.0.1",
            remote_identity="peer_local_agent",
            authentication=b"secret",
            card=A2AAgentCard(
                agent_card_id="a2acard_" + "0" * 64,
                local_agent_id="agent_local_analyst",
                protocol_version="1.0.0",
                capability_ids=("code_review",),
                authentication_schemes=("shared_secret",),
            ),
            task_payload=b"{}",
            artifacts={},
        )
    with pytest.raises(A2AGatewayError, match="disabled"):
        gateway.cancel_task("a2areq_" + "0" * 64, actor_id="operator_local", reason="stop")
    with pytest.raises(A2AGatewayError, match="disabled"):
        gateway.task_status("a2areq_" + "0" * 64)
    with pytest.raises(A2AGatewayError, match="disabled"):
        gateway.list_cards()
    snapshot = gateway.snapshot()
    assert snapshot["state"] == "disabled"
    assert snapshot["requests"] == [] and snapshot["cards"] == []


def test_a2a_network_exposure_flag_is_refused_everywhere(tmp_path: Path) -> None:
    root = _a2a_workspace(tmp_path, a2a_network_exposure_enabled=True)
    gateway = A2AGateway(root)
    with pytest.raises(A2AGatewayError, match="exposure"):
        _project_card(gateway)
    with pytest.raises(A2AGatewayError, match="exposure"):
        gateway.task_status("a2areq_" + "0" * 64)
    with pytest.raises(A2AGatewayError, match="exposure"):
        gateway.cancel_task("a2areq_" + "0" * 64, actor_id="operator_local", reason="stop")
    snapshot = gateway.snapshot()
    assert snapshot["state"] == "disabled" and snapshot["network_exposure"] is False


def test_a2a_forged_identity_is_rejected(tmp_path: Path) -> None:
    root = _a2a_workspace(tmp_path)
    gateway = A2AGateway(root)
    card = _project_card(gateway)
    unregistered = card.model_copy(update={"agent_card_id": "a2acard_" + "0" * 64})
    with pytest.raises(A2AGatewayError, match="not locally registered"):
        _submit(gateway, unregistered)
    tampered = card.model_copy(update={"capability_ids": ("code_review", "exfiltrate")})
    with pytest.raises(A2AGatewayError, match="authentication failed"):
        _submit(gateway, tampered)
    with pytest.raises(A2AGatewayError, match="authentication failed"):
        _submit(gateway, card, authentication=b"wrong-secret")


def test_a2a_oversized_artifact_is_rejected(tmp_path: Path) -> None:
    root = _a2a_workspace(tmp_path, max_a2a_artifact_bytes=1024)
    gateway = A2AGateway(root)
    card = _project_card(gateway)
    with pytest.raises(A2AGatewayError, match="quarantine limit"):
        _submit(gateway, card, artifacts={"artifact_big": b"x" * 2048})
    with pytest.raises(A2AGatewayError, match="quarantine limit"):
        _submit(gateway, card, task_payload=b"x" * 2000)
    with pytest.raises(A2AGatewayError, match="quarantine limit"):
        _submit(
            gateway,
            card,
            task_payload=b"x" * 600,
            artifacts={"artifact_a": b"y" * 300, "artifact_b": b"z" * 300},
        )


def test_a2a_unknown_extension_and_non_loopback_are_rejected(tmp_path: Path) -> None:
    root = _a2a_workspace(tmp_path)
    gateway = A2AGateway(root)
    card = _project_card(gateway)
    with pytest.raises(A2AGatewayError, match="never declared"):
        _submit(gateway, card, extension_ids=("ext.unknown",))
    with pytest.raises(A2AGatewayError, match="loopback"):
        _submit(gateway, card, remote_address="10.0.0.5")


def test_a2a_submit_status_cancel_lifecycle(tmp_path: Path) -> None:
    root = _a2a_workspace(tmp_path)
    gateway = A2AGateway(root)
    card = _project_card(gateway)
    assert card.loopback_only is True and card.published is False
    assert [item.agent_card_id for item in gateway.list_cards()] == [card.agent_card_id]
    request, status = _submit(gateway, card)
    assert request.trusted is False and request.quarantined is True
    assert request.extension_ids == ("ext.progress",)
    assert status.state == "quarantined"
    assert request.local_proposal_id is not None
    repeat_request, repeat_status = _submit(gateway, card)
    assert repeat_request.request_id == request.request_id
    assert repeat_status.state == "quarantined"
    assert gateway.task_status(request.request_id).state == "quarantined"
    cancelled = gateway.cancel_task(
        request.request_id, actor_id="operator_local", reason="operator requested stop"
    )
    assert cancelled.state == "cancelled"
    again = gateway.cancel_task(
        request.request_id, actor_id="operator_local", reason="operator requested stop"
    )
    assert again.state == "cancelled"
    assert gateway.task_status(request.request_id).state == "cancelled"
    with pytest.raises(A2AGatewayError, match="unknown"):
        gateway.task_status("a2areq_" + "1" * 64)
    with pytest.raises(A2AGatewayError, match="unknown"):
        gateway.cancel_task("a2areq_" + "1" * 64, actor_id="operator_local", reason="stop")
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        proposal = store.head("a2a_proposal", str(request.local_proposal_id))
        assert proposal is not None and proposal.state == "quarantined"
        assert proposal.payload["trusted"] is False
        assert proposal.payload["quarantined"] is True
        assert proposal.payload["canonical_state_changed"] is False
        events = store.list_events(request.request_id)
        event_types = [item["event_type"] for item in events]
        assert event_types.count("a2a_task_quarantined") == 1
        assert event_types.count("a2a_task_cancelled") == 1
        assert all(item["payload"]["canonical_state_changed"] is False for item in events)
        assert store.verify_event_stream(request.request_id)
    snapshot = gateway.snapshot()
    assert snapshot["state"] == "loopback_only"
    assert snapshot["network_exposure"] is False
    assert len(snapshot["requests"]) == 1
    assert len(snapshot["cards"]) == 1
    assert snapshot["statuses"][0]["state"] == "cancelled"
