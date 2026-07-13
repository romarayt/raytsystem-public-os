from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    AcpCapabilitySet,
    AcpEvent,
    AcpSession,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.execution import ExecutionSession, ExecutionSessionStatus
from raytsystem.execution.store import ExecutionStore, ExecutionStoreError
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    StoredRecord,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.sensitivity import SecretScanner

_SUPPORTED_CAPABILITIES = frozenset(
    {
        "messages",
        "streaming_events",
        "tool_calls",
        "permission_requests",
        "cancellation",
        "terminal_output",
        "file_changes",
        "session_resume",
    }
)
_EVENT_CAPABILITIES = {
    "message": "messages",
    "tool_call": "tool_calls",
    "permission_request": "permission_requests",
    "terminal_output": "terminal_output",
    "file_change": "file_changes",
    "cancelled": "cancellation",
}
_ACTIVE_STATES = frozenset({"ready", "streaming"})
_SessionState = Literal["streaming", "cancelled", "closed", "failed"]
_TERMINAL_EVENT_STATES: dict[str, _SessionState] = {
    "cancelled": "cancelled",
    "completed": "closed",
    "error": "failed",
}


class AcpAdapterError(RuntimeError):
    """ACP event or capability negotiation violates the shared runtime boundary."""


class AcpAdapter:
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

    def _require_enabled(self) -> None:
        if not self.features.enabled("acp_adapter_enabled"):
            raise AcpAdapterError("ACP adapter is disabled")

    def initialize(
        self,
        *,
        runtime_session_id: str,
        adapter_id: str,
        workspace_id: str,
        protocol_version: str,
        requested_capabilities: tuple[str, ...],
    ) -> tuple[AcpCapabilitySet, AcpSession, str]:
        self._require_enabled()
        try:
            execution = ExecutionStore.open_for_read(self.root / "ops" / "control.sqlite")
        except ExecutionStoreError as error:
            raise AcpAdapterError("Execution session store is unavailable") from error
        if execution is None:
            raise AcpAdapterError("Execution session store is unavailable")
        with execution:
            runtime_session = execution.get(ExecutionSession, runtime_session_id)
        if (
            runtime_session is None
            or runtime_session.status is not ExecutionSessionStatus.ACTIVE
            or runtime_session.runtime_adapter_id != adapter_id
            or runtime_session.workspace_id != workspace_id
        ):
            raise AcpAdapterError("ACP session is not bound to an active runtime session")
        negotiated = tuple(sorted(set(requested_capabilities) & _SUPPORTED_CAPABILITIES))
        capability_set = AcpCapabilitySet(
            capability_set_id=derive_id(
                "acpcap", {"version": protocol_version, "capabilities": negotiated}
            ),
            protocol_version=protocol_version,
            capabilities=negotiated,
        )
        resume_token = secrets.token_hex(32)
        now = datetime.now(UTC)
        session = AcpSession(
            acp_session_id=derive_id(
                "acpsess",
                {
                    "runtime_session_id": runtime_session_id,
                    "adapter_id": adapter_id,
                    "workspace_id": workspace_id,
                    "capability_set_id": capability_set.capability_set_id,
                },
            ),
            runtime_session_id=runtime_session_id,
            adapter_id=adapter_id,
            capability_set_id=capability_set.capability_set_id,
            workspace_id=workspace_id,
            state="ready",
            resume_token_sha256=sha256_hex(resume_token.encode("utf-8")),
            created_at=now,
            updated_at=now,
        )
        with initialize_platform_store(self.root) as store, store.transaction():
            if store.head("acp_session", session.acp_session_id) is not None:
                raise AcpAdapterError("ACP session is already initialized")
            if store.head("acp_capability", capability_set.capability_set_id) is None:
                store.append_record(
                    kind="acp_capability",
                    record_id=capability_set.capability_set_id,
                    payload=capability_set.model_dump(mode="json"),
                    state="active",
                    expected_revision=None,
                )
            store.append_record(
                kind="acp_session",
                record_id=session.acp_session_id,
                payload=session.model_dump(mode="json"),
                state=session.state,
                expected_revision=None,
            )
            store.append_record(
                kind="acp_sequence",
                record_id=session.acp_session_id,
                payload={"last_sequence": 0},
                state="active",
                expected_revision=None,
            )
            store.append_event(
                stream_id=session.acp_session_id,
                aggregate_id=session.acp_session_id,
                event_type="acp_initialized",
                actor_id="raytsystem_acp_adapter",
                payload_schema="acp_session_v1",
                payload={
                    "capability_set_id": capability_set.capability_set_id,
                    "capabilities": negotiated,
                    "resume_token_issued": True,
                },
            )
        return capability_set, session, resume_token

    def accept_event(self, raw: dict[str, Any]) -> AcpEvent:
        self._require_enabled()
        try:
            rendered_raw = canonical_json_bytes(raw)
        except (TypeError, ValueError) as error:
            raise AcpAdapterError("Malformed ACP event") from error
        if len(rendered_raw) > 256 * 1024:
            raise AcpAdapterError("ACP event exceeds the bounded limit")
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            raise AcpAdapterError("ACP event payload must be an object")
        decision = self.scanner.scan(canonical_json_bytes(payload))
        redacted = decision.blocks_processing
        payload_sha256 = sha256_hex(canonical_json_bytes(payload))
        normalized = dict(raw)
        normalized.pop("payload", None)
        normalized["payload_sha256"] = payload_sha256
        normalized["redacted"] = True
        try:
            event = AcpEvent.model_validate(normalized)
        except ValidationError as error:
            raise AcpAdapterError("Malformed ACP event") from error
        if not event.verify_id():
            raise AcpAdapterError("ACP event ID is not hash-bound")
        if event.event_type == "permission_request" and event.approval_id is not None:
            raise AcpAdapterError("ACP permission requests never carry pre-granted approvals")
        if (
            event.event_type in {"tool_call", "permission_request", "file_change"}
            and event.policy_decision_id is None
        ):
            raise AcpAdapterError("ACP privileged event bypasses policy")
        if event.event_type in {"tool_call", "file_change"} and event.approval_id is None:
            raise AcpAdapterError("ACP privileged event requires approval")
        if event.event_type in {"tool_call", "permission_request", "file_change"}:
            action = f"acp_{event.event_type}"
            try:
                resolver = AuthorityResolver(self.root)
                resolver.require_policy_decision(
                    str(event.policy_decision_id),
                    action=action,
                    target_id=event.event_id,
                    payload_sha256=payload_sha256,
                )
                if event.event_type in {"tool_call", "file_change"}:
                    resolver.require_approval(
                        str(event.approval_id),
                        action=action,
                        target_id=event.event_id,
                        artifact_sha256=payload_sha256,
                        required_scope=frozenset({"acp_privileged_event"}),
                    )
            except AuthorityError as error:
                raise AcpAdapterError("ACP event authority is invalid") from error
        with initialize_platform_store(self.root) as writer, writer.transaction():
            record = writer.head("acp_session", event.acp_session_id)
            sequence = writer.head("acp_sequence", event.acp_session_id)
            if record is None:
                raise AcpAdapterError("ACP event references an unknown session")
            session = self._validated_session(record)
            if session.state not in _ACTIVE_STATES:
                raise AcpAdapterError("ACP event references an inactive session")
            if sequence is None:
                raise AcpAdapterError("ACP sequence state is unavailable")
            required_capability = _EVENT_CAPABILITIES.get(event.event_type)
            if required_capability is not None:
                capability_set = self._validated_capability_set(writer, session.capability_set_id)
                if required_capability not in capability_set.capabilities:
                    raise AcpAdapterError("ACP event capability was never negotiated")
            expected_sequence = int(sequence.payload.get("last_sequence", -1)) + 1
            if event.sequence != expected_sequence:
                raise AcpAdapterError("ACP event sequence is invalid")
            writer.append_record(
                kind="acp_sequence",
                record_id=event.acp_session_id,
                payload={"last_sequence": event.sequence},
                state="active",
                expected_revision=sequence.revision,
            )
            writer.append_event(
                stream_id=event.acp_session_id,
                aggregate_id=event.event_id,
                event_type="acp_event_received",
                actor_id="raytsystem_acp_adapter",
                payload_schema="acp_event_v1",
                payload={
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload_sha256": payload_sha256,
                    "redacted": redacted,
                },
            )
            terminal_state = _TERMINAL_EVENT_STATES.get(event.event_type)
            if terminal_state is not None:
                self._transition(
                    writer,
                    record=record,
                    session=session,
                    target=terminal_state,
                    event_type=f"acp_session_{terminal_state}",
                    detail={"cause_event_id": event.event_id},
                )
            elif session.state == "ready":
                self._transition(
                    writer,
                    record=record,
                    session=session,
                    target="streaming",
                    event_type="acp_session_streaming",
                    detail={"cause_event_id": event.event_id},
                )
        return event

    def resume(self, session_id: str, resume_token: str) -> tuple[AcpSession, str]:
        self._require_enabled()
        if not resume_token or len(resume_token) > 512:
            raise AcpAdapterError("ACP resume token is invalid")
        presented_sha256 = sha256_hex(resume_token.encode("utf-8"))
        with initialize_platform_store(self.root) as store, store.transaction():
            record = store.head("acp_session", session_id)
            if record is None:
                raise AcpAdapterError("ACP session does not exist")
            session = self._validated_session(record)
            if session.state not in _ACTIVE_STATES:
                raise AcpAdapterError("ACP session is not resumable")
            capability_set = self._validated_capability_set(store, session.capability_set_id)
            if "session_resume" not in capability_set.capabilities:
                raise AcpAdapterError("ACP session resume was never negotiated")
            if session.resume_token_sha256 is None or not secrets.compare_digest(
                session.resume_token_sha256, presented_sha256
            ):
                raise AcpAdapterError("ACP resume token is invalid")
            rotated_token = secrets.token_hex(32)
            resumed = session.model_copy(
                update={
                    "resume_token_sha256": sha256_hex(rotated_token.encode("utf-8")),
                    "updated_at": datetime.now(UTC),
                }
            )
            store.append_record(
                kind="acp_session",
                record_id=session_id,
                payload=resumed.model_dump(mode="json"),
                state=resumed.state,
                expected_revision=record.revision,
            )
            store.append_event(
                stream_id=session_id,
                aggregate_id=session_id,
                event_type="acp_session_resumed",
                actor_id="raytsystem_acp_adapter",
                payload_schema="acp_session_v1",
                payload={"state": resumed.state, "resume_token_rotated": True},
            )
        return resumed, rotated_token

    def cancel(self, session_id: str, *, reason: str = "operator_cancelled") -> AcpSession:
        self._require_enabled()
        if not reason or len(reason) > 1_024:
            raise AcpAdapterError("ACP cancellation reason is invalid")
        return self._terminate(
            session_id,
            target="cancelled",
            event_type="acp_session_cancelled",
            detail={"reason": reason},
        )

    def close(self, session_id: str) -> AcpSession:
        self._require_enabled()
        return self._terminate(
            session_id, target="closed", event_type="acp_session_closed", detail={}
        )

    def fail(self, session_id: str, *, reason: str) -> AcpSession:
        self._require_enabled()
        if not reason or len(reason) > 1_024:
            raise AcpAdapterError("ACP failure reason is invalid")
        return self._terminate(
            session_id,
            target="failed",
            event_type="acp_session_failed",
            detail={"reason": reason},
        )

    def snapshot(self) -> dict[str, Any]:
        enabled = self.features.enabled("acp_adapter_enabled")
        sessions: list[dict[str, Any]] = []
        snapshot_id = "pview_unavailable"
        store = open_platform_store_read_only(self.root)
        if store is not None:
            with store:
                if enabled:
                    sessions = [
                        record.payload for record in store.list_heads("acp_session", limit=100)
                    ]
                snapshot_id = store.snapshot_id()
        return {
            "snapshot_id": snapshot_id,
            "state": "ready" if enabled else "disabled",
            "sessions": sessions,
            "native_adapters_required": False,
        }

    def _terminate(
        self,
        session_id: str,
        *,
        target: _SessionState,
        event_type: str,
        detail: dict[str, Any],
    ) -> AcpSession:
        with initialize_platform_store(self.root) as store, store.transaction():
            record = store.head("acp_session", session_id)
            if record is None:
                raise AcpAdapterError("ACP session does not exist")
            session = self._validated_session(record)
            if session.state == target:
                return session
            if session.state not in _ACTIVE_STATES:
                raise AcpAdapterError("ACP session is already terminal")
            return self._transition(
                store,
                record=record,
                session=session,
                target=target,
                event_type=event_type,
                detail=detail,
            )

    def _transition(
        self,
        store: PlatformStore,
        *,
        record: StoredRecord,
        session: AcpSession,
        target: _SessionState,
        event_type: str,
        detail: dict[str, Any],
    ) -> AcpSession:
        updated = session.model_copy(update={"state": target, "updated_at": datetime.now(UTC)})
        store.append_record(
            kind="acp_session",
            record_id=session.acp_session_id,
            payload=updated.model_dump(mode="json"),
            state=target,
            expected_revision=record.revision,
        )
        store.append_event(
            stream_id=session.acp_session_id,
            aggregate_id=session.acp_session_id,
            event_type=event_type,
            actor_id="raytsystem_acp_adapter",
            payload_schema="acp_session_v1",
            payload={"state": target, **detail},
        )
        return updated

    @staticmethod
    def _validated_session(record: StoredRecord) -> AcpSession:
        try:
            return AcpSession.model_validate(record.payload)
        except ValidationError as error:
            raise AcpAdapterError("Stored ACP session contract is invalid") from error

    @staticmethod
    def _validated_capability_set(store: PlatformStore, capability_set_id: str) -> AcpCapabilitySet:
        record = store.head("acp_capability", capability_set_id)
        if record is None:
            raise AcpAdapterError("ACP capability set is unavailable")
        try:
            return AcpCapabilitySet.model_validate(record.payload)
        except ValidationError as error:
            raise AcpAdapterError("Stored ACP capability set is invalid") from error
