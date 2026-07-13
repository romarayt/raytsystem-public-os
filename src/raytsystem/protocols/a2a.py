from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.contracts import (
    A2AAgentCard,
    A2ATaskRequest,
    A2ATaskStatus,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only

_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "localhost"})
_ACTOR_PATTERN = re.compile(r"^[a-z][a-z0-9_:.@/-]{1,255}$")
_CANCELLABLE_STATES = frozenset({"received", "quarantined", "proposed", "accepted", "running"})


class A2AGatewayError(RuntimeError):
    """A2A request violates the loopback-only quarantine boundary."""


class A2AGateway:
    def __init__(self, root: Path, *, features: FeatureConfig | None = None) -> None:
        self.root = root.resolve()
        self.features = features or load_feature_config(self.root)

    def _require_enabled(self) -> None:
        if not self.features.enabled("a2a_gateway_enabled"):
            raise A2AGatewayError("A2A gateway is disabled")
        if self.features.enabled("a2a_network_exposure_enabled"):
            raise A2AGatewayError("Remote A2A exposure is not supported by this gateway")

    def project_agent_card(
        self,
        *,
        local_agent_id: str,
        protocol_version: str,
        capability_ids: tuple[str, ...],
        authentication_schemes: tuple[str, ...],
        extension_ids: tuple[str, ...] = (),
        authentication: bytes | None = None,
    ) -> A2AAgentCard:
        self._require_enabled()
        if authentication is not None and (not authentication or len(authentication) > 4_096):
            raise A2AGatewayError("A2A local authentication token is invalid")
        card = A2AAgentCard(
            agent_card_id=derive_id(
                "a2acard",
                {
                    "local_agent_id": local_agent_id,
                    "protocol_version": protocol_version,
                    "capabilities": capability_ids,
                    "extensions": extension_ids,
                },
            ),
            local_agent_id=local_agent_id,
            protocol_version=protocol_version,
            capability_ids=capability_ids,
            authentication_schemes=authentication_schemes,
            extension_ids=extension_ids,
            loopback_only=True,
            published=False,
        )
        with initialize_platform_store(self.root) as store, store.transaction():
            existing = store.head("a2a_card", card.agent_card_id)
            payload = {
                "card": card.model_dump(mode="json"),
                "authentication_sha256": None
                if authentication is None
                else sha256_hex(authentication),
            }
            if existing is None:
                store.append_record(
                    kind="a2a_card",
                    record_id=card.agent_card_id,
                    payload=payload,
                    state="local_only",
                    expected_revision=None,
                )
            elif existing.payload != payload:
                raise A2AGatewayError("A2A card identity collision")
        return card

    def submit(
        self,
        *,
        remote_address: str,
        remote_identity: str,
        authentication: bytes,
        card: A2AAgentCard,
        task_payload: bytes,
        artifacts: dict[str, bytes],
        extension_ids: tuple[str, ...] = (),
    ) -> tuple[A2ATaskRequest, A2ATaskStatus]:
        self._require_enabled()
        if remote_address not in _LOOPBACK_ADDRESSES:
            raise A2AGatewayError("A2A v1 accepts loopback requests only")
        maximum = int(self.features.policy.get("max_a2a_artifact_bytes", 1_048_576))
        if (
            not authentication
            or len(authentication) > 4_096
            or len(artifacts) > 64
            or len(extension_ids) > 64
            or len(task_payload) > maximum
            or any(len(value) > maximum for value in artifacts.values())
            or len(task_payload) + sum(len(value) for value in artifacts.values()) > maximum
        ):
            raise A2AGatewayError("A2A payload or artifact exceeds the quarantine limit")
        declared_extensions = set(card.extension_ids)
        if any(extension not in declared_extensions for extension in extension_ids):
            raise A2AGatewayError("A2A extension was never declared by the target agent card")
        negotiated_extensions = tuple(
            extension for extension in card.extension_ids if extension in set(extension_ids)
        )
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise A2AGatewayError("A2A local card registry is unavailable")
        with store:
            registered = store.head("a2a_card", card.agent_card_id)
            if registered is None:
                raise A2AGatewayError("A2A agent card is not locally registered")
            stored_card = registered.payload.get("card")
            expected_authentication = registered.payload.get("authentication_sha256")
            if (
                not isinstance(stored_card, dict)
                or sha256_hex(canonical_json_bytes(stored_card))
                != sha256_hex(canonical_json_bytes(card.model_dump(mode="json")))
                or expected_authentication is None
                or expected_authentication != sha256_hex(authentication)
            ):
                raise A2AGatewayError("A2A identity authentication failed")
        artifact_hashes = {key: sha256_hex(value) for key, value in sorted(artifacts.items())}
        task_hash = sha256_hex(task_payload)
        request_id = derive_id(
            "a2areq",
            {
                "remote_identity": remote_identity,
                "authentication_sha256": sha256_hex(authentication),
                "agent_card_id": card.agent_card_id,
                "task_payload_sha256": task_hash,
                "artifact_hashes": artifact_hashes,
                "extension_ids": negotiated_extensions,
            },
        )
        local_proposal_id = derive_id("a2aprop", {"request_id": request_id})
        request = A2ATaskRequest(
            request_id=request_id,
            remote_identity=remote_identity,
            authentication_sha256=sha256_hex(authentication),
            agent_card_id=card.agent_card_id,
            protocol_version=card.protocol_version,
            task_payload_sha256=task_hash,
            artifact_hashes=artifact_hashes,
            extension_ids=negotiated_extensions,
            local_proposal_id=local_proposal_id,
            trusted=False,
            quarantined=True,
            created_at=datetime.now(UTC),
        )
        status = A2ATaskStatus(
            status_id=derive_id("a2astatus", {"request_id": request_id, "state": "quarantined"}),
            request_id=request_id,
            state="quarantined",
            policy_decision_id=derive_id(
                "policy", {"request_id": request_id, "outcome": "quarantine"}
            ),
            updated_at=datetime.now(UTC),
        )
        with initialize_platform_store(self.root) as writer, writer.transaction():
            existing_request = writer.head("a2a_request", request_id)
            if existing_request is not None:
                existing_status = writer.head("a2a_status", request_id)
                if existing_status is None:
                    raise A2AGatewayError("A2A task status record is unavailable")
                return (
                    self._validated_request(existing_request.payload),
                    self._validated_status(existing_status.payload),
                )
            writer.append_record(
                kind="a2a_request",
                record_id=request_id,
                payload=request.model_dump(mode="json"),
                state="quarantined",
                expected_revision=None,
            )
            writer.append_record(
                kind="a2a_status",
                record_id=request_id,
                payload=status.model_dump(mode="json"),
                state=status.state,
                expected_revision=None,
            )
            writer.append_record(
                kind="a2a_proposal",
                record_id=local_proposal_id,
                payload={
                    "proposal_id": local_proposal_id,
                    "request_id": request_id,
                    "agent_card_id": card.agent_card_id,
                    "task_payload_sha256": task_hash,
                    "artifact_hashes": artifact_hashes,
                    "extension_ids": list(negotiated_extensions),
                    "trusted": False,
                    "quarantined": True,
                    "canonical_state_changed": False,
                },
                state="quarantined",
                expected_revision=None,
            )
            writer.append_event(
                stream_id=request_id,
                aggregate_id=request_id,
                event_type="a2a_task_quarantined",
                actor_id="raytsystem_a2a_gateway",
                payload_schema="a2a_task_request_v1",
                payload={
                    "request_id": request_id,
                    "local_proposal_id": local_proposal_id,
                    "artifact_hashes": artifact_hashes,
                    "canonical_state_changed": False,
                },
            )
        return request, status

    def cancel_task(self, a2a_task_id: str, *, actor_id: str, reason: str) -> A2ATaskStatus:
        self._require_enabled()
        if _ACTOR_PATTERN.fullmatch(actor_id) is None:
            raise A2AGatewayError("A2A cancellation actor is invalid")
        if not reason or len(reason) > 1_024:
            raise A2AGatewayError("A2A cancellation reason is invalid")
        with initialize_platform_store(self.root) as store, store.transaction():
            head = store.head("a2a_status", a2a_task_id)
            if head is None:
                raise A2AGatewayError("A2A task is unknown")
            status = self._validated_status(head.payload)
            if status.state == "cancelled":
                return status
            if status.state not in _CANCELLABLE_STATES:
                raise A2AGatewayError("A2A task is already terminal")
            cancelled = A2ATaskStatus(
                status_id=derive_id(
                    "a2astatus", {"request_id": status.request_id, "state": "cancelled"}
                ),
                request_id=status.request_id,
                local_task_id=status.local_task_id,
                state="cancelled",
                artifact_ids=status.artifact_ids,
                policy_decision_id=derive_id(
                    "policy", {"request_id": status.request_id, "outcome": "cancel"}
                ),
                updated_at=datetime.now(UTC),
            )
            store.append_record(
                kind="a2a_status",
                record_id=a2a_task_id,
                payload=cancelled.model_dump(mode="json"),
                state=cancelled.state,
                expected_revision=head.revision,
            )
            store.append_event(
                stream_id=status.request_id,
                aggregate_id=status.request_id,
                event_type="a2a_task_cancelled",
                actor_id="raytsystem_a2a_gateway",
                payload_schema="a2a_task_status_v1",
                payload={
                    "request_id": status.request_id,
                    "actor_id": actor_id,
                    "reason": reason,
                    "canonical_state_changed": False,
                },
            )
        return cancelled

    def task_status(self, a2a_task_id: str) -> A2ATaskStatus:
        self._require_enabled()
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise A2AGatewayError("A2A task registry is unavailable")
        with store:
            head = store.head("a2a_status", a2a_task_id)
        if head is None:
            raise A2AGatewayError("A2A task is unknown")
        return self._validated_status(head.payload)

    def list_cards(self) -> tuple[A2AAgentCard, ...]:
        self._require_enabled()
        store = open_platform_store_read_only(self.root)
        if store is None:
            return ()
        cards: list[A2AAgentCard] = []
        with store:
            for record in store.list_heads("a2a_card", limit=100):
                stored_card = record.payload.get("card")
                if not isinstance(stored_card, dict):
                    raise A2AGatewayError("Stored A2A agent card is invalid")
                try:
                    cards.append(A2AAgentCard.model_validate(stored_card))
                except ValidationError as error:
                    raise A2AGatewayError("Stored A2A agent card is invalid") from error
        return tuple(cards)

    def snapshot(self) -> dict[str, Any]:
        enabled = self.features.enabled("a2a_gateway_enabled") and not self.features.enabled(
            "a2a_network_exposure_enabled"
        )
        requests: list[dict[str, Any]] = []
        cards: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        snapshot_id = "pview_unavailable"
        store = open_platform_store_read_only(self.root)
        if store is not None:
            with store:
                if enabled:
                    requests = [
                        record.payload for record in store.list_heads("a2a_request", limit=100)
                    ]
                    cards = [record.payload for record in store.list_heads("a2a_card", limit=100)]
                    statuses = [
                        record.payload for record in store.list_heads("a2a_status", limit=100)
                    ]
                snapshot_id = store.snapshot_id()
        return {
            "snapshot_id": snapshot_id,
            "state": "loopback_only" if enabled else "disabled",
            "network_exposure": False,
            "requests": requests,
            "cards": cards,
            "statuses": statuses,
        }

    @staticmethod
    def _validated_request(payload: dict[str, Any]) -> A2ATaskRequest:
        try:
            return A2ATaskRequest.model_validate(payload)
        except ValidationError as error:
            raise A2AGatewayError("Stored A2A task request is invalid") from error

    @staticmethod
    def _validated_status(payload: dict[str, Any]) -> A2ATaskStatus:
        try:
            return A2ATaskStatus.model_validate(payload)
        except ValidationError as error:
            raise A2AGatewayError("Stored A2A task status is invalid") from error
