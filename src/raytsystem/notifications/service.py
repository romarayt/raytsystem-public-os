from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    Notification,
    NotificationOutbox,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.workflows import NotificationType
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    PlatformStoreError,
    StoredRecord,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.sensitivity import SecretScanner

Severity = Literal["info", "low", "medium", "high", "critical"]

_SEVERITY_ORDER: tuple[Severity, ...] = ("info", "low", "medium", "high", "critical")
_DESTINATION_POLICY_KEY = "notification_destinations"
_RETRY_LIMIT_POLICY_KEY = "notification_retry_limit"
_OUTBOX_KIND = "notification_outbox"
_DEDUP_KIND = "notification_dedup"


class NotificationError(RuntimeError):
    """Notification or outbox action violates redaction, egress, or state policy."""


def _max_severity(current: Severity, incoming: Severity) -> Severity:
    if _SEVERITY_ORDER.index(incoming) > _SEVERITY_ORDER.index(current):
        return incoming
    return current


def _notification_from_payload(payload: Mapping[str, Any]) -> Notification:
    clean = {key: value for key, value in payload.items() if key in Notification.model_fields}
    return Notification.model_validate(clean)


def _dedup_record_id(dedup_key: str) -> str:
    return derive_id("ndedup", {"deduplication_key": dedup_key})


class NotificationService:
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

    def emit(
        self,
        notification_type: NotificationType,
        *,
        severity: Severity,
        related_object_id: str,
        actor_id: str,
        dedup_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
        related_kind: str | None = None,
        origin_event_id: str | None = None,
    ) -> Notification:
        self._require_enabled()
        raw = dict(payload or {})
        title = str(raw.pop("title", notification_type.value.replace("_", " ")))
        message = str(raw.pop("message", f"{notification_type.value}: {related_object_id}"))
        title, title_redacted = self._redact(title)
        message, message_redacted = self._redact(message)
        clean_payload, payload_redacted = self._redact_payload(raw)
        body = {"title": title, "message": message, "payload": clean_payload}
        try:
            rendered = canonical_json_bytes(body)
        except (TypeError, ValueError) as error:
            raise NotificationError("Notification payload is not canonical JSON") from error
        limit = int(self.features.policy.get("max_notification_bytes", 16_384))
        if len(rendered) > limit:
            raise NotificationError("Notification exceeds the bounded size")
        if self.scanner.scan(rendered).blocks_processing:
            raise NotificationError("Notification payload contains restricted data")
        with initialize_platform_store(self.root) as store:
            if dedup_key is not None:
                bumped = self._bump_unresolved(
                    store, dedup_key, severity=severity, actor_id=actor_id
                )
                if bumped is not None:
                    return bumped
            origin = origin_event_id or derive_id(
                "norigin",
                {
                    "notification_type": notification_type.value,
                    "related_id": related_object_id,
                    "actor_id": actor_id,
                    "sequence": store.event_count(),
                },
            )
            deduplication_key = dedup_key or derive_id("ndk", {"origin_event_id": origin})
            kind = related_kind or related_object_id.split("_", 1)[0]
            notification_id = derive_id(
                "notice",
                {
                    "notification_type": notification_type.value,
                    "related_kind": kind,
                    "related_id": related_object_id,
                    "origin_event_id": origin,
                    "deduplication_key": deduplication_key,
                },
            )
            existing = store.head("notification", notification_id)
            if existing is not None:
                return _notification_from_payload(existing.payload)
            notice = Notification(
                notification_id=notification_id,
                notification_type=notification_type,
                severity=severity,
                title=title,
                message=message,
                related_kind=kind,
                related_id=related_object_id,
                origin_event_id=origin,
                deduplication_key=deduplication_key,
                state="unread",
                created_at=datetime.now(UTC),
            )
            was_redacted = title_redacted or message_redacted or payload_redacted
            store.append_record(
                kind="notification",
                record_id=notification_id,
                payload=notice.model_dump(mode="json")
                | {"redacted": was_redacted, "occurrence_count": 1, "payload": clean_payload},
                state="unread",
                expected_revision=None,
            )
            store.append_event(
                stream_id=notification_id,
                aggregate_id=notification_id,
                event_type="notification_created",
                actor_id=actor_id,
                payload_schema="notification_v1",
                payload={
                    "notification_id": notification_id,
                    "origin_event_id": origin,
                    "redacted": was_redacted,
                },
            )
            if dedup_key is not None:
                self._point_dedup(store, dedup_key, notification_id)
            return notice

    def create(
        self,
        *,
        notification_type: NotificationType,
        severity: Severity,
        title: str,
        message: str,
        related_kind: str,
        related_id: str,
        origin_event_id: str,
        deduplication_key: str,
    ) -> Notification:
        return self.emit(
            notification_type,
            severity=severity,
            related_object_id=related_id,
            actor_id="raytsystem_notifications",
            dedup_key=deduplication_key,
            payload={"title": title, "message": message},
            related_kind=related_kind,
            origin_event_id=origin_event_id,
        )

    def transition(
        self,
        notification_id: str,
        state: Literal["read", "acknowledged", "resolved"],
        *,
        actor_id: str,
    ) -> Notification:
        self._require_enabled()
        allowed = {
            "unread": {"read", "acknowledged", "resolved"},
            "read": {"acknowledged", "resolved"},
            "acknowledged": {"resolved"},
            "resolved": set(),
        }
        with initialize_platform_store(self.root) as store:
            prior = store.head("notification", notification_id)
            if prior is None:
                raise NotificationError("Notification does not exist")
            notice = _notification_from_payload(prior.payload)
            if state not in allowed[notice.state]:
                raise NotificationError("Notification transition is invalid")
            now = datetime.now(UTC)
            updates: dict[str, Any] = {"state": state}
            if state == "acknowledged":
                updates["acknowledged_at"] = now
            if state == "resolved":
                updates["resolved_at"] = now
                updates.setdefault("acknowledged_at", notice.acknowledged_at or now)
            updated = notice.model_copy(update=updates)
            store.append_record(
                kind="notification",
                record_id=notification_id,
                payload=updated.model_dump(mode="json")
                | {
                    "redacted": prior.payload.get("redacted", False),
                    "occurrence_count": int(prior.payload.get("occurrence_count", 1)),
                    "payload": prior.payload.get("payload", {}),
                },
                state=state,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=notification_id,
                aggregate_id=notification_id,
                event_type="notification_transitioned",
                actor_id=actor_id,
                payload_schema="notification_v1",
                payload={"state": state},
            )
            if state == "resolved":
                self._release_dedup(store, notice.deduplication_key, notification_id)
            return updated

    def prepare_outbox(
        self,
        notification_id: str,
        *,
        adapter: Literal["webhook", "telegram", "slack", "email"],
        destination_id: str,
        policy_decision_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> NotificationOutbox:
        self._require_external_enabled()
        self._require_allowlisted(destination_id)
        with initialize_platform_store(self.root) as store:
            notice = store.head("notification", notification_id)
            if notice is None:
                raise NotificationError("Notification does not exist")
            title, _ = self._redact(str(notice.payload.get("title", "")))
            message, _ = self._redact(str(notice.payload.get("message", "")))
            preview = {"title": title, "message": message, "destination_id": destination_id}
            rendered = canonical_json_bytes(preview)
            if self.scanner.scan(rendered).blocks_processing:
                raise NotificationError("Outbox preview contains restricted data")
            payload_sha256 = sha256_hex(rendered)
            request = {
                "notification_id": notification_id,
                "adapter": adapter,
                "destination_id": destination_id,
                "policy_decision_id": policy_decision_id,
                "approval_id": approval_id,
                "payload_sha256": payload_sha256,
            }
            try:
                receipt = store.idempotent_receipt(
                    scope=_OUTBOX_KIND, idempotency_key=idempotency_key, request=request
                )
            except PlatformStoreError as error:
                raise NotificationError("Outbox idempotency key was reused") from error
            if receipt is not None:
                replay = store.head(_OUTBOX_KIND, str(receipt["outbox_id"]))
                if replay is None:
                    raise NotificationError("Outbox idempotency receipt is orphaned")
                return NotificationOutbox.model_validate(replay.payload)
            try:
                resolver = AuthorityResolver(self.root)
                resolver.require_policy_decision(
                    policy_decision_id,
                    action="send_notification",
                    target_id=notification_id,
                    payload_sha256=payload_sha256,
                    destination=destination_id,
                )
                resolver.require_approval(
                    approval_id,
                    action="send_notification",
                    target_id=notification_id,
                    artifact_sha256=payload_sha256,
                    destination=destination_id,
                    required_scope=frozenset({"external_notification"}),
                )
            except AuthorityError as error:
                raise NotificationError("Notification outbox authority is invalid") from error
            outbox_id = derive_id(
                "nout",
                {
                    "notification_id": notification_id,
                    "adapter": adapter,
                    "destination_id": destination_id,
                    "idempotency_key": idempotency_key,
                },
            )
            existing = store.head(_OUTBOX_KIND, outbox_id)
            if existing is not None:
                outbox = NotificationOutbox.model_validate(existing.payload)
            else:
                outbox = NotificationOutbox(
                    outbox_id=outbox_id,
                    notification_id=notification_id,
                    adapter=adapter,
                    destination_id=destination_id,
                    payload_sha256=payload_sha256,
                    preview_sha256=payload_sha256,
                    policy_decision_id=policy_decision_id,
                    approval_id=approval_id,
                    idempotency_key=idempotency_key,
                    state="draft",
                    redacted=True,
                    created_at=datetime.now(UTC),
                )
                store.append_record(
                    kind=_OUTBOX_KIND,
                    record_id=outbox_id,
                    payload=outbox.model_dump(mode="json"),
                    state="draft",
                    expected_revision=None,
                )
            store.idempotent_receipt(
                scope=_OUTBOX_KIND,
                idempotency_key=idempotency_key,
                request=request,
                receipt={"outbox_id": outbox_id},
            )
            return outbox

    def approve_outbox(
        self, outbox_id: str, *, approval_id: str, actor_id: str
    ) -> NotificationOutbox:
        self._require_external_enabled()
        with initialize_platform_store(self.root) as store:
            prior, outbox = self._outbox_head(store, outbox_id)
            if outbox.state != "draft":
                raise NotificationError("Outbox transition is invalid")
            self._require_allowlisted(outbox.destination_id)
            try:
                AuthorityResolver(self.root).require_approval(
                    approval_id,
                    action="send_notification",
                    target_id=outbox.notification_id,
                    artifact_sha256=outbox.payload_sha256,
                    destination=outbox.destination_id,
                    required_scope=frozenset({"external_notification"}),
                )
            except AuthorityError as error:
                raise NotificationError("Notification outbox authority is invalid") from error
            return self._advance_outbox(store, prior, outbox, state="approved", actor_id=actor_id)

    def mark_sending(self, outbox_id: str, *, actor_id: str) -> NotificationOutbox:
        self._require_external_enabled()
        with initialize_platform_store(self.root) as store:
            prior, outbox = self._outbox_head(store, outbox_id)
            if outbox.state not in {"approved", "failed"}:
                raise NotificationError("Outbox transition is invalid")
            self._require_allowlisted(outbox.destination_id)
            return self._advance_outbox(store, prior, outbox, state="sending", actor_id=actor_id)

    def mark_sent(self, outbox_id: str, *, actor_id: str) -> NotificationOutbox:
        self._require_external_enabled()
        with initialize_platform_store(self.root) as store:
            prior, outbox = self._outbox_head(store, outbox_id)
            if outbox.state != "sending":
                raise NotificationError("Outbox transition is invalid")
            return self._advance_outbox(store, prior, outbox, state="sent", actor_id=actor_id)

    def mark_failed(self, outbox_id: str, *, actor_id: str) -> NotificationOutbox:
        self._require_external_enabled()
        with initialize_platform_store(self.root) as store:
            prior, outbox = self._outbox_head(store, outbox_id)
            if outbox.state != "sending":
                raise NotificationError("Outbox transition is invalid")
            attempts = outbox.attempt_count + 1
            retry_limit = int(self.features.policy.get(_RETRY_LIMIT_POLICY_KEY, 3))
            state: Literal["failed", "dead_letter"] = (
                "dead_letter" if attempts >= retry_limit else "failed"
            )
            return self._advance_outbox(
                store, prior, outbox, state=state, actor_id=actor_id, attempt_count=attempts
            )

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotificationError("External notification delivery is not implemented in v1")

    def snapshot(self, *, state: str | None = None, limit: int = 200) -> dict[str, Any]:
        if not self.features.enabled("notifications_enabled"):
            return {
                "snapshot_id": "pview_disabled",
                "state": "disabled",
                "unread_count": 0,
                "notifications": [],
                "external_outbox_enabled": False,
            }
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {"snapshot_id": "pview_unavailable", "state": "unavailable", "notifications": []}
        with store:
            records = store.list_heads("notification", state=state, limit=limit)
            notifications = []
            for record in records:
                payload = dict(record.payload)
                payload.pop("redacted", None)
                notifications.append(payload)
            unread = len(store.list_heads("notification", state="unread", limit=500))
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready",
                "unread_count": unread,
                "notifications": notifications,
                "external_outbox_enabled": self.features.enabled("external_notifications_enabled"),
            }

    def _bump_unresolved(
        self, store: PlatformStore, dedup_key: str, *, severity: Severity, actor_id: str
    ) -> Notification | None:
        pointer = store.head(_DEDUP_KIND, _dedup_record_id(dedup_key))
        if pointer is None or pointer.state != "active":
            return None
        target = str(pointer.payload.get("notification_id", ""))
        prior = store.head("notification", target) if target else None
        if prior is None:
            return None
        notice = _notification_from_payload(prior.payload)
        if notice.state == "resolved":
            store.append_record(
                kind=_DEDUP_KIND,
                record_id=pointer.record_id,
                payload=dict(pointer.payload),
                state="released",
                expected_revision=pointer.revision,
            )
            return None
        occurrence_count = int(prior.payload.get("occurrence_count", 1)) + 1
        updated = notice.model_copy(update={"severity": _max_severity(notice.severity, severity)})
        store.append_record(
            kind="notification",
            record_id=notice.notification_id,
            payload=updated.model_dump(mode="json")
            | {
                "redacted": prior.payload.get("redacted", False),
                "occurrence_count": occurrence_count,
                "payload": prior.payload.get("payload", {}),
            },
            state=updated.state,
            expected_revision=prior.revision,
        )
        store.append_event(
            stream_id=notice.notification_id,
            aggregate_id=notice.notification_id,
            event_type="notification_deduplicated",
            actor_id=actor_id,
            payload_schema="notification_v1",
            payload={"occurrence_count": occurrence_count, "severity": updated.severity},
        )
        return updated

    def _point_dedup(self, store: PlatformStore, dedup_key: str, notification_id: str) -> None:
        record_id = _dedup_record_id(dedup_key)
        pointer = store.head(_DEDUP_KIND, record_id)
        store.append_record(
            kind=_DEDUP_KIND,
            record_id=record_id,
            payload={"deduplication_key": dedup_key, "notification_id": notification_id},
            state="active",
            expected_revision=None if pointer is None else pointer.revision,
        )

    def _release_dedup(self, store: PlatformStore, dedup_key: str, notification_id: str) -> None:
        pointer = store.head(_DEDUP_KIND, _dedup_record_id(dedup_key))
        if (
            pointer is None
            or pointer.state != "active"
            or str(pointer.payload.get("notification_id", "")) != notification_id
        ):
            return
        store.append_record(
            kind=_DEDUP_KIND,
            record_id=pointer.record_id,
            payload=dict(pointer.payload),
            state="released",
            expected_revision=pointer.revision,
        )

    def _outbox_head(
        self, store: PlatformStore, outbox_id: str
    ) -> tuple[StoredRecord, NotificationOutbox]:
        prior = store.head(_OUTBOX_KIND, outbox_id)
        if prior is None:
            raise NotificationError("Notification outbox entry does not exist")
        return prior, NotificationOutbox.model_validate(prior.payload)

    def _advance_outbox(
        self,
        store: PlatformStore,
        prior: StoredRecord,
        outbox: NotificationOutbox,
        *,
        state: Literal["approved", "sending", "sent", "failed", "dead_letter"],
        actor_id: str,
        attempt_count: int | None = None,
    ) -> NotificationOutbox:
        updates: dict[str, Any] = {"state": state}
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        updated = outbox.model_copy(update=updates)
        store.append_record(
            kind=_OUTBOX_KIND,
            record_id=outbox.outbox_id,
            payload=updated.model_dump(mode="json"),
            state=state,
            expected_revision=prior.revision,
        )
        store.append_event(
            stream_id=outbox.outbox_id,
            aggregate_id=outbox.outbox_id,
            event_type=f"notification_outbox_{state}",
            actor_id=actor_id,
            payload_schema="notification_outbox_v1",
            payload={"state": state, "attempt_count": updated.attempt_count},
        )
        return updated

    def _require_allowlisted(self, destination_id: str) -> None:
        raw = self.features.policy.get(_DESTINATION_POLICY_KEY, [])
        allowed = (
            frozenset(str(item) for item in raw)
            if isinstance(raw, list | tuple)
            else frozenset[str]()
        )
        if destination_id not in allowed:
            raise NotificationError("Notification destination is not allowlisted")

    def _redact(self, value: str) -> tuple[str, bool]:
        if self.scanner.scan(value.encode("utf-8")).blocks_processing:
            return "[REDACTED]", True
        return value, False

    def _redact_payload(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        redacted = False

        def clean(value: Any) -> Any:
            nonlocal redacted
            if isinstance(value, str):
                replacement, hit = self._redact(value)
                redacted = redacted or hit
                return replacement
            if isinstance(value, Mapping):
                return {clean(str(key)): clean(item) for key, item in value.items()}
            if isinstance(value, list | tuple):
                return [clean(item) for item in value]
            return value

        return cast(dict[str, Any], clean(dict(payload))), redacted

    def _require_enabled(self) -> None:
        if not self.features.enabled("notifications_enabled"):
            raise NotificationError("Notifications are disabled")

    def _require_external_enabled(self) -> None:
        self._require_enabled()
        if not self.features.enabled("external_notifications_enabled"):
            raise NotificationError("External notifications are disabled")
