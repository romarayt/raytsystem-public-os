"""Local notification inbox: emit/dedup, gated transitions, allowlisted no-send outbox."""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import raytsystem.notifications.service as notification_service_module
from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import (
    Notification,
    NotificationOutbox,
    PolicyDecision,
    PolicyOutcome,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.workflows import NotificationType
from raytsystem.notifications import NotificationError, NotificationService
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only

pytestmark = pytest.mark.filterwarnings("error")

SECRET = "AKIA" + "A" * 16
DESTINATION = "dest_slack_ops"


def _external_root(
    tmp_path: Path,
    *,
    destinations: tuple[str, ...] = (DESTINATION,),
    retry_limit: int = 3,
) -> Path:
    return make_platform_workspace(
        tmp_path,
        flag_overrides={"external_notifications_enabled": True},
        policy_overrides={
            "notification_destinations": list(destinations),
            "notification_retry_limit": retry_limit,
        },
    )


def _emit(service: NotificationService, **overrides: Any) -> Notification:
    params: dict[str, Any] = {
        "severity": "medium",
        "related_object_id": "run_demo",
        "actor_id": "raytsystem_tests",
        "payload": {"title": "Run failed", "message": "Run run_demo failed"},
    }
    params.update(overrides)
    return service.emit(NotificationType.RUN_FAILED, **params)


def _head_payload(root: Path, kind: str, record_id: str) -> dict[str, Any]:
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        record = store.head(kind, record_id)
        assert record is not None
        return dict(record.payload)


def _preview_sha(root: Path, notification_id: str, destination: str) -> str:
    payload = _head_payload(root, "notification", notification_id)
    preview = {
        "title": payload["title"],
        "message": payload["message"],
        "destination_id": destination,
    }
    return sha256_hex(canonical_json_bytes(preview))


def _authorize(
    root: Path,
    *,
    notification_id: str,
    payload_sha256: str,
    destination: str = DESTINATION,
) -> tuple[str, str]:
    material: dict[str, Any] = {
        "action": "send_notification",
        "target_id": notification_id,
        "payload_sha256": payload_sha256,
        "destination": destination,
        "policy_version": "1.0.0",
        "policy_sha256": "6" * 64,
        "outcome": PolicyOutcome.REQUIRE_APPROVAL,
        "reason_codes": (),
        "required_approval_scope": ("external_notification",),
        "evaluated_at": datetime.now(UTC),
    }
    decision = PolicyDecision.model_validate(
        material | {"policy_decision_id": derive_id("pdec", material)}
    )
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="authority_policy",
            record_id=decision.policy_decision_id,
            payload=decision.model_dump(mode="json"),
            state="require_approval",
            expected_revision=None,
        )
    approval = store_approval(
        root,
        action="send_notification",
        target_id=notification_id,
        artifact_sha256=payload_sha256,
        scope=("external_notification",),
        destination=destination,
    )
    return decision.policy_decision_id, approval.approval_id


def _prepared_outbox(
    root: Path,
    service: NotificationService,
    *,
    idempotency_key: str,
) -> tuple[NotificationOutbox, str]:
    notice = _emit(service)
    sha = _preview_sha(root, notice.notification_id, DESTINATION)
    decision_id, approval_id = _authorize(
        root, notification_id=notice.notification_id, payload_sha256=sha
    )
    outbox = service.prepare_outbox(
        notice.notification_id,
        adapter="slack",
        destination_id=DESTINATION,
        policy_decision_id=decision_id,
        approval_id=approval_id,
        idempotency_key=idempotency_key,
    )
    return outbox, approval_id


def test_emit_redacts_secret_payload_before_persistence(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = NotificationService(root)
    notice = service.emit(
        NotificationType.SECURITY_FINDING,
        severity="high",
        related_object_id="run_demo",
        actor_id="raytsystem_emergency",
        dedup_key="security_finding:run_demo",
        payload={"title": f"key {SECRET}", "message": f"leak {SECRET}", "detail": SECRET},
    )
    assert notice.title == "[REDACTED]" and notice.message == "[REDACTED]"
    store_bytes = b"".join(
        path.read_bytes() for path in sorted((root / "ops").glob("platform.sqlite*"))
    )
    assert store_bytes and SECRET.encode("utf-8") not in store_bytes
    payload = _head_payload(root, "notification", notice.notification_id)
    assert payload["redacted"] is True
    assert payload["payload"] == {"detail": "[REDACTED]"}
    with pytest.raises(NotificationError, match="canonical"):
        service.emit(
            NotificationType.RUN_FAILED,
            severity="low",
            related_object_id="run_float",
            actor_id="raytsystem_tests",
            payload={"score": 0.5},
        )


def test_emit_deduplicates_until_resolved(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = NotificationService(root)
    first = _emit(service, severity="low", dedup_key="run_failed:run_demo")
    second = _emit(service, severity="critical", dedup_key="run_failed:run_demo")
    assert second.notification_id == first.notification_id
    assert second.severity == "critical"
    payload = _head_payload(root, "notification", first.notification_id)
    assert payload["occurrence_count"] == 2 and payload["severity"] == "critical"
    assert service.snapshot()["unread_count"] == 1
    service.transition(first.notification_id, "resolved", actor_id="user_local_test")
    third = _emit(service, severity="low", dedup_key="run_failed:run_demo")
    assert third.notification_id != first.notification_id
    assert third.state == "unread"
    assert service.snapshot()["unread_count"] == 1


def test_transition_lifecycle_guards_and_badge_counts(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = NotificationService(root)
    notice = _emit(service)
    read = service.transition(notice.notification_id, "read", actor_id="user_local_test")
    assert read.state == "read"
    acknowledged = service.transition(
        notice.notification_id, "acknowledged", actor_id="user_local_test"
    )
    assert acknowledged.state == "acknowledged" and acknowledged.acknowledged_at is not None
    resolved = service.transition(notice.notification_id, "resolved", actor_id="user_local_test")
    assert resolved.state == "resolved" and resolved.resolved_at is not None
    with pytest.raises(NotificationError, match="invalid"):
        service.transition(notice.notification_id, "read", actor_id="user_local_test")
    with pytest.raises(NotificationError, match="exist"):
        service.transition("notice_missing", "read", actor_id="user_local_test")
    other = _emit(service, related_object_id="run_other")
    snapshot = service.snapshot(state="unread")
    assert snapshot["unread_count"] == 1
    assert [item["notification_id"] for item in snapshot["notifications"]] == [
        other.notification_id
    ]
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.verify_event_stream(notice.notification_id)
        events = store.list_events(notice.notification_id)
    assert [event["event_type"] for event in events] == [
        "notification_created",
        "notification_transitioned",
        "notification_transitioned",
        "notification_transitioned",
    ]


def test_notifications_disabled_blocks_everything(tmp_path: Path) -> None:
    root = make_platform_workspace(
        tmp_path,
        flag_overrides={"notifications_enabled": False, "external_notifications_enabled": False},
    )
    service = NotificationService(root)
    with pytest.raises(NotificationError, match="disabled"):
        _emit(service)
    with pytest.raises(NotificationError, match="disabled"):
        service.transition("notice_any", "read", actor_id="user_local_test")
    with pytest.raises(NotificationError, match="disabled"):
        service.prepare_outbox(
            "notice_any",
            adapter="slack",
            destination_id=DESTINATION,
            policy_decision_id="pdec_any",
            approval_id="apr_any",
            idempotency_key="outbox_key_disabled",
        )
    with pytest.raises(NotificationError, match="disabled"):
        service.approve_outbox("nout_any", approval_id="apr_any", actor_id="user_local_test")
    with pytest.raises(NotificationError, match="disabled"):
        service.mark_sending("nout_any", actor_id="user_local_test")
    assert service.snapshot()["state"] == "disabled"


def test_external_notifications_disabled_blocks_outbox(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = NotificationService(root)
    notice = _emit(service)
    with pytest.raises(NotificationError, match="External notifications are disabled"):
        service.prepare_outbox(
            notice.notification_id,
            adapter="slack",
            destination_id=DESTINATION,
            policy_decision_id="pdec_any",
            approval_id="apr_any",
            idempotency_key="outbox_key_external",
        )
    with pytest.raises(NotificationError, match="External notifications are disabled"):
        service.approve_outbox("nout_any", approval_id="apr_any", actor_id="user_local_test")
    with pytest.raises(NotificationError, match="External notifications are disabled"):
        service.mark_sent("nout_any", actor_id="user_local_test")


def test_outbox_destination_allowlist_cannot_be_bypassed(tmp_path: Path) -> None:
    root = _external_root(tmp_path / "allow", destinations=("dest_allowed",))
    service = NotificationService(root)
    notice = _emit(service)
    evil_sha = _preview_sha(root, notice.notification_id, "dest_evil")
    decision_id, approval_id = _authorize(
        root,
        notification_id=notice.notification_id,
        payload_sha256=evil_sha,
        destination="dest_evil",
    )
    with pytest.raises(NotificationError, match="allowlisted"):
        service.prepare_outbox(
            notice.notification_id,
            adapter="webhook",
            destination_id="dest_evil",
            policy_decision_id=decision_id,
            approval_id=approval_id,
            idempotency_key="outbox_key_evil",
        )
    empty_root = make_platform_workspace(
        tmp_path / "empty", flag_overrides={"external_notifications_enabled": True}
    )
    empty_service = NotificationService(empty_root)
    empty_notice = _emit(empty_service)
    with pytest.raises(NotificationError, match="allowlisted"):
        empty_service.prepare_outbox(
            empty_notice.notification_id,
            adapter="webhook",
            destination_id="dest_allowed",
            policy_decision_id="pdec_any",
            approval_id="apr_any",
            idempotency_key="outbox_key_empty",
        )
    with pytest.raises(NotificationError, match="authority"):
        service.prepare_outbox(
            notice.notification_id,
            adapter="webhook",
            destination_id="dest_allowed",
            policy_decision_id=decision_id,
            approval_id=approval_id,
            idempotency_key="outbox_key_wrong_authority",
        )


def test_outbox_lifecycle_reaches_sent_and_dead_letter(tmp_path: Path) -> None:
    root = _external_root(tmp_path, retry_limit=2)
    service = NotificationService(root)
    outbox, approval_id = _prepared_outbox(root, service, idempotency_key="outbox_key_ok")
    assert outbox.state == "draft" and outbox.attempt_count == 0
    approved = service.approve_outbox(
        outbox.outbox_id, approval_id=approval_id, actor_id="user_local_test"
    )
    assert approved.state == "approved"
    with pytest.raises(NotificationError, match="invalid"):
        service.approve_outbox(
            outbox.outbox_id, approval_id=approval_id, actor_id="user_local_test"
        )
    with pytest.raises(NotificationError, match="invalid"):
        service.mark_failed(outbox.outbox_id, actor_id="raytsystem_outbox")
    sending = service.mark_sending(outbox.outbox_id, actor_id="raytsystem_outbox")
    assert sending.state == "sending"
    sent = service.mark_sent(outbox.outbox_id, actor_id="raytsystem_outbox")
    assert sent.state == "sent" and sent.attempt_count == 0
    failing, second_approval = _prepared_outbox(root, service, idempotency_key="outbox_key_fail")
    with pytest.raises(NotificationError, match="invalid"):
        service.mark_sending(failing.outbox_id, actor_id="raytsystem_outbox")
    service.approve_outbox(
        failing.outbox_id, approval_id=second_approval, actor_id="user_local_test"
    )
    service.mark_sending(failing.outbox_id, actor_id="raytsystem_outbox")
    failed = service.mark_failed(failing.outbox_id, actor_id="raytsystem_outbox")
    assert failed.state == "failed" and failed.attempt_count == 1
    service.mark_sending(failing.outbox_id, actor_id="raytsystem_outbox")
    dead = service.mark_failed(failing.outbox_id, actor_id="raytsystem_outbox")
    assert dead.state == "dead_letter" and dead.attempt_count == 2
    with pytest.raises(NotificationError, match="invalid"):
        service.mark_sending(failing.outbox_id, actor_id="raytsystem_outbox")
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.verify_event_stream(failing.outbox_id)


def test_prepare_outbox_replay_is_idempotent_and_revision_safe(tmp_path: Path) -> None:
    root = _external_root(tmp_path, destinations=(DESTINATION, "dest_other"))
    service = NotificationService(root)
    notice = _emit(service)
    sha = _preview_sha(root, notice.notification_id, DESTINATION)
    decision_id, approval_id = _authorize(
        root, notification_id=notice.notification_id, payload_sha256=sha
    )
    first = service.prepare_outbox(
        notice.notification_id,
        adapter="slack",
        destination_id=DESTINATION,
        policy_decision_id=decision_id,
        approval_id=approval_id,
        idempotency_key="outbox_key_replay",
    )
    replay = service.prepare_outbox(
        notice.notification_id,
        adapter="slack",
        destination_id=DESTINATION,
        policy_decision_id=decision_id,
        approval_id=approval_id,
        idempotency_key="outbox_key_replay",
    )
    assert replay.outbox_id == first.outbox_id
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        record = store.head("notification_outbox", first.outbox_id)
        assert record is not None and record.revision == 1
    other_sha = _preview_sha(root, notice.notification_id, "dest_other")
    other_decision, other_approval = _authorize(
        root,
        notification_id=notice.notification_id,
        payload_sha256=other_sha,
        destination="dest_other",
    )
    with pytest.raises(NotificationError, match="reused"):
        service.prepare_outbox(
            notice.notification_id,
            adapter="slack",
            destination_id="dest_other",
            policy_decision_id=other_decision,
            approval_id=other_approval,
            idempotency_key="outbox_key_replay",
        )
    service.approve_outbox(first.outbox_id, approval_id=approval_id, actor_id="user_local_test")
    again = service.prepare_outbox(
        notice.notification_id,
        adapter="slack",
        destination_id=DESTINATION,
        policy_decision_id=decision_id,
        approval_id=approval_id,
        idempotency_key="outbox_key_replay",
    )
    assert again.outbox_id == first.outbox_id and again.state == "approved"


def test_outbox_has_no_send_path_or_network_imports(tmp_path: Path) -> None:
    service = NotificationService(make_platform_workspace(tmp_path))
    with pytest.raises(NotificationError, match="not implemented"):
        service.send()
    source = inspect.getsource(notification_service_module)
    forbidden = r"^\s*(?:from|import)\s+(?:socket|http|urllib|requests|httpx|aiohttp|smtplib|ssl)"
    assert re.search(forbidden, source, re.MULTILINE) is None
