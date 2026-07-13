from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    CircuitBreaker,
    EmergencyState,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.governance import (
    CircuitBreakerState,
    EmergencyAction,
)
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PLATFORM_DB_RELATIVE,
    PlatformStoreError,
    StoredRecord,
    initialize_platform_store,
    open_platform_store_read_only,
)

_SECURITY_TRIGGERS = frozenset(
    {"protected_path", "forbidden_egress", "policy_violations", "failed_approvals"}
)
_RUNTIME_BLOCKING_ACTIONS = frozenset(
    {
        EmergencyAction.PAUSE_ALL_EMPLOYEES.value,
        EmergencyAction.CANCEL_ACTIVE_RUNS.value,
        EmergencyAction.DISABLE_RUNTIME_EXECUTION.value,
        EmergencyAction.EMERGENCY_BUDGET_STOP.value,
    }
)


class EmergencyError(RuntimeError):
    """Emergency control or circuit-breaker policy rejected an operation."""


class EmergencyService:
    def __init__(self, root: Path, *, features: FeatureConfig | None = None) -> None:
        self.root = root.resolve()
        self.features = features or load_feature_config(self.root)

    def activate(
        self,
        actions: tuple[EmergencyAction, ...],
        *,
        reason: str,
        actor_id: str,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        if not actions:
            raise EmergencyError("At least one emergency action is required")
        request = {
            "actions": sorted(action.value for action in actions),
            "reason": reason,
            "actor_id": actor_id,
            "approval_id": approval_id,
        }
        with initialize_platform_store(self.root) as store:
            prior_receipt = store.idempotent_receipt(
                scope="emergency_activate",
                idempotency_key=idempotency_key,
                request=request,
            )
            if prior_receipt is not None:
                return prior_receipt
            prior = store.head("emergency", "emergency_global")
            prior_active: set[str] = set()
            revision = 1
            if prior is not None:
                prior_active = {str(value) for value in prior.payload.get("active_actions", [])}
                revision = prior.revision + 1
            current = prior_active | {action.value for action in actions}
            now = datetime.now(UTC)
            state = EmergencyState(
                emergency_state_id=derive_id(
                    "emergency",
                    {
                        "actions": sorted(current),
                        "revision": revision,
                        "actor_id": actor_id,
                        "reason": reason,
                    },
                ),
                active_actions=tuple(EmergencyAction(value) for value in sorted(current)),
                reason=reason,
                activated_by=actor_id,
                approval_id=approval_id,
                security_lock=True,
                revision=revision,
                activated_at=now,
                extensions={
                    "action_activated_at": _activation_stamps(prior, prior_active, current, now)
                },
            )
            try:
                record = store.append_record(
                    kind="emergency",
                    record_id="emergency_global",
                    payload=state.model_dump(mode="json"),
                    state="active",
                    expected_revision=None if prior is None else prior.revision,
                )
            except PlatformStoreError as error:
                raise EmergencyError(
                    "Emergency state changed concurrently; retry safely"
                ) from error
            event = store.append_event(
                stream_id="emergency_global",
                aggregate_id="emergency_global",
                event_type="emergency_activated",
                actor_id=actor_id,
                payload_schema="emergency_state_v1",
                payload={
                    "emergency_state_id": state.emergency_state_id,
                    "actions": sorted(current),
                    "reason_sha256": derive_id("reason", {"reason": reason}),
                },
            )
            receipt = {
                "command_id": derive_id("ecmd", {"idempotency_key": idempotency_key, **request}),
                "state": "active",
                "revision": record.revision,
                "snapshot_id": store.snapshot_id(),
                "event_id": event["event_id"],
                "expected_effect": sorted(current),
                "approval_status": "recorded" if approval_id else "local_emergency_authority",
                "recovery": "manual_recovery_required",
            }
            store.idempotent_receipt(
                scope="emergency_activate",
                idempotency_key=idempotency_key,
                request=request,
                receipt=receipt,
            )
            return receipt

    def recover(
        self,
        actions: tuple[EmergencyAction, ...],
        *,
        actor_id: str,
        approval_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        if not approval_id:
            raise EmergencyError("Manual recovery requires a fresh approval")
        request = {
            "actions": sorted(action.value for action in actions),
            "actor_id": actor_id,
            "approval_id": approval_id,
            "reason": reason,
        }
        try:
            AuthorityResolver(self.root).require_approval(
                approval_id,
                action="recover_emergency",
                target_id="emergency_global",
                artifact_sha256=sha256_hex(
                    canonical_json_bytes({"actions": request["actions"], "reason": reason})
                ),
                required_scope=frozenset({"emergency_recovery"}),
            )
        except AuthorityError as error:
            raise EmergencyError("Emergency recovery approval is invalid") from error
        with initialize_platform_store(self.root) as store:
            prior_receipt = store.idempotent_receipt(
                scope="emergency_recover",
                idempotency_key=idempotency_key,
                request=request,
            )
            if prior_receipt is not None:
                return prior_receipt
            prior = store.head("emergency", "emergency_global")
            if prior is None or prior.state != "active":
                raise EmergencyError("No active emergency state exists")
            remaining = set(str(value) for value in prior.payload.get("active_actions", []))
            requested = {action.value for action in actions}
            if not requested.issubset(remaining):
                raise EmergencyError("Recovery requested inactive emergency actions")
            security_open = self._open_security_breakers(store)
            if security_open and EmergencyAction.DISABLE_RUNTIME_EXECUTION.value in requested:
                raise EmergencyError(
                    "Security breakers must be closed manually before runtime recovery"
                )
            remaining -= requested
            payload = dict(prior.payload)
            payload.update(
                {
                    "emergency_state_id": derive_id(
                        "emergency",
                        {
                            "remaining": sorted(remaining),
                            "revision": prior.revision + 1,
                            "reason": reason,
                        },
                    ),
                    "active_actions": sorted(remaining),
                    "reason": reason,
                    "revision": prior.revision + 1,
                    "recovered_at": datetime.now(UTC).isoformat(),
                    "recovered_by": actor_id,
                    "approval_id": approval_id,
                    "extensions": {
                        "action_activated_at": {
                            action: stamp
                            for action, stamp in _recorded_stamps(prior).items()
                            if action in remaining
                        }
                    },
                }
            )
            final_state = "active" if remaining else "recovered"
            try:
                recovered = EmergencyState.model_validate(payload)
            except ValidationError as error:
                raise EmergencyError("Emergency recovery state is invalid") from error
            store.append_record(
                kind="emergency",
                record_id="emergency_global",
                payload=recovered.model_dump(mode="json"),
                state=final_state,
                expected_revision=prior.revision,
            )
            event = store.append_event(
                stream_id="emergency_global",
                aggregate_id="emergency_global",
                event_type="emergency_recovered",
                actor_id=actor_id,
                payload_schema="emergency_state_v1",
                payload={
                    "recovered_actions": sorted(requested),
                    "remaining_actions": sorted(remaining),
                    "reason_sha256": derive_id("reason", {"reason": reason}),
                },
            )
            receipt = {
                "command_id": derive_id("ecmd", {"idempotency_key": idempotency_key, **request}),
                "state": final_state,
                "snapshot_id": store.snapshot_id(),
                "event_id": event["event_id"],
                "expected_effect": sorted(requested),
                "approval_status": "fresh_approval_recorded",
                "recovery": "manual_recovery_applied",
            }
            store.idempotent_receipt(
                scope="emergency_recover",
                idempotency_key=idempotency_key,
                request=request,
                receipt=receipt,
            )
            return receipt

    def observe_breaker(
        self,
        trigger: str,
        *,
        observed: int,
        scope: str,
        actor_id: str = "raytsystem_kernel",
    ) -> CircuitBreaker:
        self._require_enabled()
        threshold = self.features.circuit_breakers.get(trigger)
        if threshold is None:
            raise EmergencyError("Unknown circuit-breaker trigger")
        breaker_id = derive_id("breaker", {"trigger": trigger, "scope": scope})
        with initialize_platform_store(self.root) as store:
            prior = store.head("breaker", breaker_id)
            revision = 1 if prior is None else prior.revision + 1
            security_breaker = trigger in _SECURITY_TRIGGERS
            recovery_limit = 0 if security_breaker else 1
            prior_state = None if prior is None else prior.state
            recoveries_used = 0 if prior is None else _recoveries_used(prior)
            tripped = observed >= threshold
            if tripped:
                state = CircuitBreakerState.OPEN
            elif prior_state == CircuitBreakerState.OPEN.value:
                if recoveries_used < recovery_limit:
                    state = CircuitBreakerState.HALF_OPEN
                    recoveries_used += 1
                else:
                    state = CircuitBreakerState.OPEN
            elif prior_state == CircuitBreakerState.HALF_OPEN.value:
                state = CircuitBreakerState.CLOSED
            else:
                state = CircuitBreakerState.CLOSED
            if state is CircuitBreakerState.OPEN and not tripped and prior is not None:
                observed = max(observed, int(prior.payload.get("observed", 0)))
            opened_now = state is CircuitBreakerState.OPEN and (
                prior is None or prior.state != CircuitBreakerState.OPEN.value
            )
            reason: str | None = None
            if state is CircuitBreakerState.OPEN:
                reason = f"{trigger}:{observed}/{threshold}"
            elif state is CircuitBreakerState.HALF_OPEN:
                reason = f"{trigger}:automatic_recovery_probe"
            now = datetime.now(UTC)
            breaker = CircuitBreaker(
                breaker_id=breaker_id,
                scope=scope,
                trigger=trigger,
                state=state,
                threshold=threshold,
                observed=observed,
                reason=reason,
                automatic_recovery_limit=recovery_limit,
                security_breaker=security_breaker,
                revision=revision,
                opened_at=now if state is CircuitBreakerState.OPEN else None,
                updated_at=now,
                extensions={"automatic_recoveries_used": recoveries_used},
            )
            store.append_record(
                kind="breaker",
                record_id=breaker_id,
                payload=breaker.model_dump(mode="json"),
                state=state.value,
                expected_revision=None if prior is None else prior.revision,
            )
            store.append_event(
                stream_id=breaker_id,
                aggregate_id=breaker_id,
                event_type="circuit_breaker_observed",
                actor_id=actor_id,
                payload_schema="circuit_breaker_v1",
                payload={
                    "trigger": trigger,
                    "observed": observed,
                    "state": state.value,
                    "reason": reason,
                    "recoveries_used": recoveries_used,
                },
            )
        if opened_now:
            self.activate(
                (EmergencyAction.DISABLE_RUNTIME_EXECUTION,),
                reason=f"Circuit breaker opened: {trigger}",
                actor_id=actor_id,
                idempotency_key=derive_id(
                    "idem", {"breaker_id": breaker_id, "revision": revision, "state": state.value}
                ),
            )
        return breaker

    def close_breaker(
        self,
        breaker_id: str,
        *,
        approval_id: str,
        reason: str,
        actor_id: str,
    ) -> CircuitBreaker:
        self._require_enabled()
        if not approval_id:
            raise EmergencyError("Manual breaker close requires a fresh approval")
        try:
            AuthorityResolver(self.root).require_approval(
                approval_id,
                action="close_circuit_breaker",
                target_id=breaker_id,
                artifact_sha256=sha256_hex(
                    canonical_json_bytes({"breaker_id": breaker_id, "reason": reason})
                ),
                required_scope=frozenset({"emergency_recovery"}),
            )
        except AuthorityError as error:
            raise EmergencyError("Circuit-breaker close approval is invalid") from error
        with initialize_platform_store(self.root) as store:
            prior = store.head("breaker", breaker_id)
            if prior is None:
                raise EmergencyError("Unknown circuit breaker")
            payload = dict(prior.payload)
            payload.update(
                {
                    "state": CircuitBreakerState.CLOSED.value,
                    "observed": 0,
                    "reason": None,
                    "revision": prior.revision + 1,
                    "opened_at": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "extensions": {"automatic_recoveries_used": 0},
                }
            )
            try:
                breaker = CircuitBreaker.model_validate(payload)
            except ValidationError as error:
                raise EmergencyError("Stored circuit breaker is invalid") from error
            try:
                store.append_record(
                    kind="breaker",
                    record_id=breaker_id,
                    payload=breaker.model_dump(mode="json"),
                    state=CircuitBreakerState.CLOSED.value,
                    expected_revision=prior.revision,
                )
            except PlatformStoreError as error:
                raise EmergencyError(
                    "Circuit breaker changed concurrently; retry safely"
                ) from error
            store.append_event(
                stream_id=breaker_id,
                aggregate_id=breaker_id,
                event_type="circuit_breaker_closed",
                actor_id=actor_id,
                payload_schema="circuit_breaker_v1",
                payload={
                    "trigger": str(prior.payload.get("trigger")),
                    "approval_id": approval_id,
                    "reason_sha256": derive_id("reason", {"reason": reason}),
                },
            )
        return breaker

    def snapshot(self) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {
                "snapshot_id": "pview_unavailable",
                "state": "unavailable",
                "active_actions": [],
                "breakers": [],
            }
        with store:
            emergency = store.head("emergency", "emergency_global")
            breakers = store.list_heads("breaker", limit=200)
            active = []
            if emergency is not None and emergency.state == "active":
                active = list(emergency.payload.get("active_actions", []))
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "blocked" if active else "ready",
                "active_actions": active,
                "revision": None if emergency is None else emergency.revision,
                "breakers": [
                    {
                        key: record.payload.get(key)
                        for key in (
                            "breaker_id",
                            "scope",
                            "trigger",
                            "state",
                            "threshold",
                            "observed",
                            "reason",
                            "security_breaker",
                            "updated_at",
                        )
                    }
                    for record in breakers
                ],
            }

    def assert_runtime_allowed(self) -> None:
        snapshot = self.snapshot()
        if snapshot.get("state") == "unavailable":
            raise EmergencyError("Emergency state is unavailable; runtime fails closed")
        active = set(str(value) for value in snapshot.get("active_actions", []))
        blocking = active & _RUNTIME_BLOCKING_ACTIONS
        if blocking:
            raise EmergencyError(
                "Runtime is stopped by emergency controls: " + ",".join(sorted(blocking))
            )

    def assert_network_allowed(self) -> None:
        self._assert_action_inactive(EmergencyAction.DISABLE_NETWORK_ADAPTERS)

    def assert_provider_egress_allowed(self) -> None:
        self._assert_action_inactive(EmergencyAction.DISABLE_EXTERNAL_PROVIDERS)

    def assert_task_checkout_allowed(self) -> None:
        self._assert_action_inactive(EmergencyAction.FREEZE_TASK_CHECKOUT)

    def assert_session_grant_allowed(self) -> None:
        self._assert_action_inactive(EmergencyAction.REVOKE_RUNTIME_SESSIONS)

    def _assert_action_inactive(self, action: EmergencyAction) -> None:
        if not (self.root / PLATFORM_DB_RELATIVE).is_file():
            return
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise EmergencyError(
                f"Emergency state is unavailable; the {action.value} gate fails closed"
            )
        with store:
            try:
                record = store.head("emergency", "emergency_global")
            except (PlatformStoreError, sqlite3.Error) as error:
                raise EmergencyError(
                    f"Emergency state is unreadable; the {action.value} gate fails closed"
                ) from error
        if record is None or record.state != "active":
            return
        active = {str(value) for value in record.payload.get("active_actions", [])}
        if action.value in active:
            raise EmergencyError(f"Emergency controls block {action.value}")

    @staticmethod
    def _open_security_breakers(store: Any) -> bool:
        return any(
            bool(record.payload.get("security_breaker")) and record.state == "open"
            for record in store.list_heads("breaker", state="open", limit=200)
        )

    def _require_enabled(self) -> None:
        if not self.features.enabled("emergency_controls_enabled"):
            raise EmergencyError("Emergency controls are disabled")


def _recorded_stamps(prior: StoredRecord) -> dict[str, str]:
    raw = prior.payload.get("extensions")
    if not isinstance(raw, dict):
        return {}
    per_action = raw.get("action_activated_at")
    if not isinstance(per_action, dict):
        return {}
    return {str(action): str(stamp) for action, stamp in per_action.items()}


def _activation_stamps(
    prior: StoredRecord | None,
    prior_active: set[str],
    current: set[str],
    now: datetime,
) -> dict[str, str]:
    recorded = {} if prior is None else _recorded_stamps(prior)
    stamps: dict[str, str] = {}
    for value in sorted(current):
        if value in recorded:
            stamps[value] = recorded[value]
        elif prior is not None and value in prior_active:
            stamps[value] = str(prior.payload.get("activated_at", now.isoformat()))
        else:
            stamps[value] = now.isoformat()
    return stamps


def _recoveries_used(prior: StoredRecord) -> int:
    raw = prior.payload.get("extensions")
    if not isinstance(raw, dict):
        return 0
    value = raw.get("automatic_recoveries_used")
    return value if isinstance(value, int) else 0
