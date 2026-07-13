"""Emergency stop machine-enforcement: gates, breakers, revocation, recovery (spec §9, §22)."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import ApprovalRecord, canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.governance import CircuitBreakerState, EmergencyAction
from raytsystem.emergency import EmergencyError, EmergencyService
from raytsystem.platform_store import (
    PLATFORM_DB_RELATIVE,
    PlatformStore,
    initialize_platform_store,
    open_platform_store_read_only,
)

pytestmark = pytest.mark.filterwarnings("error")

_RUNTIME_BLOCKING = (
    EmergencyAction.PAUSE_ALL_EMPLOYEES,
    EmergencyAction.CANCEL_ACTIVE_RUNS,
    EmergencyAction.DISABLE_RUNTIME_EXECUTION,
    EmergencyAction.EMERGENCY_BUDGET_STOP,
)
_GATE_NAMES = {
    EmergencyAction.DISABLE_NETWORK_ADAPTERS: "assert_network_allowed",
    EmergencyAction.DISABLE_EXTERNAL_PROVIDERS: "assert_provider_egress_allowed",
    EmergencyAction.FREEZE_TASK_CHECKOUT: "assert_task_checkout_allowed",
    EmergencyAction.REVOKE_RUNTIME_SESSIONS: "assert_session_grant_allowed",
}


def _activate(
    service: EmergencyService,
    actions: tuple[EmergencyAction, ...],
    key: str,
    *,
    reason: str = "containment",
) -> dict[str, Any]:
    return service.activate(actions, reason=reason, actor_id="user_local_test", idempotency_key=key)


def _recovery_approval(
    root: Path, actions: tuple[EmergencyAction, ...], reason: str
) -> ApprovalRecord:
    return store_approval(
        root,
        action="recover_emergency",
        target_id="emergency_global",
        artifact_sha256=sha256_hex(
            canonical_json_bytes(
                {"actions": sorted(action.value for action in actions), "reason": reason}
            )
        ),
        scope=("emergency_recovery",),
    )


def _close_approval(root: Path, breaker_id: str, reason: str) -> ApprovalRecord:
    return store_approval(
        root,
        action="close_circuit_breaker",
        target_id=breaker_id,
        artifact_sha256=sha256_hex(
            canonical_json_bytes({"breaker_id": breaker_id, "reason": reason})
        ),
        scope=("emergency_recovery",),
    )


def test_activate_race_replays_receipt_and_never_loses_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)
    first = _activate(service, (EmergencyAction.FREEZE_TASK_CHECKOUT,), "idem_first")
    replay = _activate(service, (EmergencyAction.FREEZE_TASK_CHECKOUT,), "idem_first")
    assert first == replay
    _activate(service, (EmergencyAction.PAUSE_ALL_EMPLOYEES,), "idem_second")
    real_head = PlatformStore.head

    def stale_head(self: PlatformStore, kind: str, record_id: str) -> Any:
        record = real_head(self, kind, record_id)
        if kind == "emergency" and record is not None:
            return replace(record, revision=record.revision - 1)
        return record

    monkeypatch.setattr(PlatformStore, "head", stale_head)
    with pytest.raises(EmergencyError, match="retry safely"):
        _activate(service, (EmergencyAction.CANCEL_ACTIVE_RUNS,), "idem_third")
    monkeypatch.undo()
    assert set(service.snapshot()["active_actions"]) == {
        EmergencyAction.FREEZE_TASK_CHECKOUT.value,
        EmergencyAction.PAUSE_ALL_EMPLOYEES.value,
    }


def test_activate_accumulates_and_each_runtime_action_blocks(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path / "accumulate")
    service = EmergencyService(root)
    first = _activate(service, (EmergencyAction.PAUSE_ALL_EMPLOYEES,), "idem_one")
    assert first["expected_effect"] == [EmergencyAction.PAUSE_ALL_EMPLOYEES.value]
    second = _activate(service, (EmergencyAction.EMERGENCY_BUDGET_STOP,), "idem_two")
    assert second["expected_effect"] == sorted(
        (EmergencyAction.PAUSE_ALL_EMPLOYEES.value, EmergencyAction.EMERGENCY_BUDGET_STOP.value)
    )
    for action in _RUNTIME_BLOCKING:
        action_root = make_platform_workspace(tmp_path / action.value)
        action_service = EmergencyService(action_root)
        _activate(action_service, (action,), "idem_block")
        with pytest.raises(EmergencyError, match="stopped by emergency"):
            action_service.assert_runtime_allowed()


def test_new_gates_block_only_their_actions(tmp_path: Path) -> None:
    for action, gate_name in _GATE_NAMES.items():
        root = make_platform_workspace(tmp_path / action.value)
        service = EmergencyService(root)
        _activate(service, (action,), "idem_gate")
        with pytest.raises(EmergencyError, match=action.value):
            getattr(service, gate_name)()
        for other_action, other_gate in _GATE_NAMES.items():
            if other_action is not action:
                getattr(service, other_gate)()
        service.assert_runtime_allowed()


def test_every_gate_fails_closed_when_store_is_present_but_unreadable(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path / "corrupt")
    initialize_platform_store(root).close()
    (root / PLATFORM_DB_RELATIVE).write_bytes(b"this is not a sqlite database")
    service = EmergencyService(root)
    gates = tuple(getattr(service, name) for name in _GATE_NAMES.values())
    for gate in (*gates, service.assert_runtime_allowed):
        with pytest.raises(EmergencyError, match="fails closed"):
            gate()
    absent = EmergencyService(make_platform_workspace(tmp_path / "absent"))
    for name in _GATE_NAMES.values():
        getattr(absent, name)()


def test_recover_requires_exact_fresh_approval(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)
    _activate(service, (EmergencyAction.DISABLE_RUNTIME_EXECUTION,), "idem_stop")
    with pytest.raises(EmergencyError, match="fresh approval"):
        service.recover(
            (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
            actor_id="user_local_test",
            approval_id="",
            reason="restore",
            idempotency_key="idem_r0",
        )
    wrong_scope = store_approval(
        root,
        action="recover_emergency",
        target_id="emergency_global",
        artifact_sha256=sha256_hex(
            canonical_json_bytes(
                {"actions": [EmergencyAction.DISABLE_RUNTIME_EXECUTION.value], "reason": "restore"}
            )
        ),
        scope=("unrelated_scope",),
    )
    wrong_hash = store_approval(
        root,
        action="recover_emergency",
        target_id="emergency_global",
        artifact_sha256="b" * 64,
        scope=("emergency_recovery",),
    )
    for approval_id in (wrong_scope.approval_id, wrong_hash.approval_id):
        with pytest.raises(EmergencyError, match="approval is invalid"):
            service.recover(
                (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
                actor_id="user_local_test",
                approval_id=approval_id,
                reason="restore",
                idempotency_key="idem_r1",
            )
    good = _recovery_approval(root, (EmergencyAction.DISABLE_RUNTIME_EXECUTION,), "restore")
    receipt = service.recover(
        (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
        actor_id="user_local_test",
        approval_id=good.approval_id,
        reason="restore",
        idempotency_key="idem_r2",
    )
    assert receipt["state"] == "recovered"
    service.assert_runtime_allowed()


def test_runtime_recovery_refused_while_security_breaker_is_open(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)
    breaker = service.observe_breaker("protected_path", observed=1, scope="scope_security")
    assert breaker.state is CircuitBreakerState.OPEN and breaker.security_breaker
    with pytest.raises(EmergencyError, match="stopped by emergency"):
        service.assert_runtime_allowed()
    recovery = _recovery_approval(
        root, (EmergencyAction.DISABLE_RUNTIME_EXECUTION,), "restore runtime"
    )
    with pytest.raises(EmergencyError, match="closed manually"):
        service.recover(
            (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
            actor_id="user_local_test",
            approval_id=recovery.approval_id,
            reason="restore runtime",
            idempotency_key="idem_sec_r1",
        )
    close = _close_approval(root, breaker.breaker_id, "path audit complete")
    closed = service.close_breaker(
        breaker.breaker_id,
        approval_id=close.approval_id,
        reason="path audit complete",
        actor_id="user_local_test",
    )
    assert closed.state is CircuitBreakerState.CLOSED
    receipt = service.recover(
        (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
        actor_id="user_local_test",
        approval_id=recovery.approval_id,
        reason="restore runtime",
        idempotency_key="idem_sec_r2",
    )
    assert receipt["state"] == "recovered"
    service.assert_runtime_allowed()


def test_security_breaker_latches_until_manual_close_with_approval(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)
    opened = service.observe_breaker("forbidden_egress", observed=1, scope="scope_latch")
    assert opened.state is CircuitBreakerState.OPEN
    assert opened.automatic_recovery_limit == 0
    latched = service.observe_breaker("forbidden_egress", observed=0, scope="scope_latch")
    assert latched.state is CircuitBreakerState.OPEN and latched.observed == 1
    with pytest.raises(EmergencyError, match="approval is invalid"):
        service.close_breaker(
            opened.breaker_id,
            approval_id="apr_missing",
            reason="unaudited",
            actor_id="user_local_test",
        )
    wrong_scope = store_approval(
        root,
        action="close_circuit_breaker",
        target_id=opened.breaker_id,
        artifact_sha256=sha256_hex(
            canonical_json_bytes({"breaker_id": opened.breaker_id, "reason": "audited"})
        ),
        scope=("unrelated_scope",),
    )
    with pytest.raises(EmergencyError, match="approval is invalid"):
        service.close_breaker(
            opened.breaker_id,
            approval_id=wrong_scope.approval_id,
            reason="audited",
            actor_id="user_local_test",
        )
    good = _close_approval(root, opened.breaker_id, "egress audit complete")
    closed = service.close_breaker(
        opened.breaker_id,
        approval_id=good.approval_id,
        reason="egress audit complete",
        actor_id="user_local_test",
    )
    assert closed.state is CircuitBreakerState.CLOSED
    after = service.observe_breaker("forbidden_egress", observed=0, scope="scope_latch")
    assert after.state is CircuitBreakerState.CLOSED


def test_non_security_breaker_auto_recovery_is_bounded(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)

    def trip() -> CircuitBreakerState:
        return service.observe_breaker("repeated_error", observed=5, scope="scope_auto").state

    def calm() -> CircuitBreakerState:
        return service.observe_breaker("repeated_error", observed=0, scope="scope_auto").state

    assert trip() is CircuitBreakerState.OPEN
    assert calm() is CircuitBreakerState.HALF_OPEN
    assert calm() is CircuitBreakerState.CLOSED
    assert trip() is CircuitBreakerState.OPEN
    assert calm() is CircuitBreakerState.OPEN
    assert calm() is CircuitBreakerState.OPEN
    breaker_id = derive_id("breaker", {"trigger": "repeated_error", "scope": "scope_auto"})
    approval = _close_approval(root, breaker_id, "operator reset")
    reset = service.close_breaker(
        breaker_id,
        approval_id=approval.approval_id,
        reason="operator reset",
        actor_id="user_local_test",
    )
    assert reset.state is CircuitBreakerState.CLOSED
    assert reset.extensions["automatic_recoveries_used"] == 0
    assert trip() is CircuitBreakerState.OPEN
    assert calm() is CircuitBreakerState.HALF_OPEN


def test_revoke_pending_approvals_rejects_prior_approvals_only(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    resolver = AuthorityResolver(root)
    check = {
        "action": "publish_document",
        "target_id": "doc_1",
        "artifact_sha256": "a" * 64,
        "required_scope": frozenset({"publishing"}),
    }
    before = store_approval(
        root,
        action="publish_document",
        target_id="doc_1",
        artifact_sha256="a" * 64,
        scope=("publishing",),
    )
    resolver.require_approval(before.approval_id, **check)
    time.sleep(0.002)
    service = EmergencyService(root)
    _activate(service, (EmergencyAction.REVOKE_PENDING_APPROVALS,), "idem_revoke")
    with pytest.raises(AuthorityError, match="revoked"):
        resolver.require_approval(before.approval_id, **check)
    time.sleep(0.002)
    after = store_approval(
        root,
        action="publish_document",
        target_id="doc_1",
        artifact_sha256="a" * 64,
        scope=("publishing",),
    )
    resolver.require_approval(after.approval_id, **check)
    recovery = _recovery_approval(
        root, (EmergencyAction.REVOKE_PENDING_APPROVALS,), "incident resolved"
    )
    receipt = service.recover(
        (EmergencyAction.REVOKE_PENDING_APPROVALS,),
        actor_id="user_local_test",
        approval_id=recovery.approval_id,
        reason="incident resolved",
        idempotency_key="idem_revoke_recover",
    )
    assert receipt["state"] == "recovered"


def test_audit_streams_verify_and_record_reasons(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = EmergencyService(root)
    _activate(service, (EmergencyAction.FREEZE_TASK_CHECKOUT,), "idem_audit")
    breaker = service.observe_breaker("repeated_error", observed=9, scope="scope_audit")
    close = _close_approval(root, breaker.breaker_id, "verified fix")
    service.close_breaker(
        breaker.breaker_id,
        approval_id=close.approval_id,
        reason="verified fix",
        actor_id="user_local_test",
    )
    recovery = _recovery_approval(
        root,
        (EmergencyAction.DISABLE_RUNTIME_EXECUTION, EmergencyAction.FREEZE_TASK_CHECKOUT),
        "incident resolved",
    )
    service.recover(
        (EmergencyAction.DISABLE_RUNTIME_EXECUTION, EmergencyAction.FREEZE_TASK_CHECKOUT),
        actor_id="user_local_test",
        approval_id=recovery.approval_id,
        reason="incident resolved",
        idempotency_key="idem_audit_recover",
    )
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.verify_event_stream("emergency_global")
        assert store.verify_event_stream(breaker.breaker_id)
        emergency_events = store.list_events("emergency_global")
        breaker_events = store.list_events(breaker.breaker_id)
    event_types = {event["event_type"] for event in emergency_events}
    assert {"emergency_activated", "emergency_recovered"} <= event_types
    assert all("reason_sha256" in event["payload"] for event in emergency_events)
    closed_events = [
        event for event in breaker_events if event["event_type"] == "circuit_breaker_closed"
    ]
    assert closed_events
    assert closed_events[0]["payload"]["reason_sha256"] == derive_id(
        "reason", {"reason": "verified fix"}
    )
    assert closed_events[0]["payload"]["approval_id"] == close.approval_id


def test_disabled_emergency_controls_fail_every_mutating_api(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"emergency_controls_enabled": False})
    service = EmergencyService(root)
    with pytest.raises(EmergencyError, match="disabled"):
        _activate(service, (EmergencyAction.PAUSE_ALL_EMPLOYEES,), "idem_disabled")
    with pytest.raises(EmergencyError, match="disabled"):
        service.recover(
            (EmergencyAction.PAUSE_ALL_EMPLOYEES,),
            actor_id="user_local_test",
            approval_id="apr_disabled",
            reason="restore",
            idempotency_key="idem_disabled_r",
        )
    with pytest.raises(EmergencyError, match="disabled"):
        service.observe_breaker("repeated_error", observed=0, scope="scope_disabled")
    with pytest.raises(EmergencyError, match="disabled"):
        service.close_breaker(
            "breaker_disabled",
            approval_id="apr_disabled",
            reason="restore",
            actor_id="user_local_test",
        )
