from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from raytsystem.contracts import (
    ApprovalRecord,
    PolicyDecision,
    PolicyOutcome,
    derive_id,
)
from raytsystem.contracts.execution import ExecutionApproval
from raytsystem.contracts.governance import EmergencyAction
from raytsystem.execution.store import ExecutionStore, ExecutionStoreError
from raytsystem.platform_store import PlatformStoreError, open_platform_store_read_only
from raytsystem.security.paths import PathPolicyError, read_regular_file


class AuthorityError(RuntimeError):
    """A policy decision or approval is missing, forged, stale, or out of scope."""


class AuthorityResolver:
    """Resolve hash-bound authority records from trusted local stores; fail closed."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def require_approval(
        self,
        approval_id: str,
        *,
        action: str,
        target_id: str,
        artifact_sha256: str,
        destination: str | None = None,
        required_scope: frozenset[str] = frozenset(),
        policy_sha256: str | None = None,
        at: datetime | None = None,
    ) -> ApprovalRecord | ExecutionApproval:
        approval = self._approval(approval_id)
        now = (at or datetime.now(UTC)).astimezone(UTC)
        if isinstance(approval, ApprovalRecord):
            expected = ApprovalRecord.create(
                action=approval.action,
                target_id=approval.target_id,
                artifact_sha256=approval.artifact_sha256,
                destination=approval.destination,
                scope=approval.scope,
                policy_version=approval.policy_version,
                policy_sha256=approval.policy_sha256,
                approver=approval.approver,
                approved_at=approval.approved_at,
                expires_at=approval.expires_at,
                conditions=approval.conditions,
            )
            valid = (
                expected.approval_id == approval.approval_id
                and approval.is_valid_for(
                    action=action,
                    target_id=target_id,
                    artifact_sha256=artifact_sha256,
                    destination=destination,
                    at=now,
                )
                and required_scope.issubset(approval.scope)
                and (policy_sha256 is None or approval.policy_sha256 == policy_sha256)
            )
        else:
            target_bindings = {
                value
                for value in (
                    approval.employee_id,
                    approval.task_id,
                    approval.run_id,
                    approval.workspace_id,
                )
                if value is not None
            }
            valid = bool(
                approval.verify_id()
                and approval.action == action
                and approval.payload_sha256 == artifact_sha256
                and approval.destination == destination
                and target_id in target_bindings
                and required_scope.issubset(approval.scope)
                and approval.approved_at <= now < approval.expires_at
            )
        if not valid:
            raise AuthorityError("Approval does not match the exact action scope")
        revoked_after = self._pending_approvals_revoked_at()
        if revoked_after is not None and approval.approved_at < revoked_after:
            raise AuthorityError("Approval was revoked by an active emergency control")
        return approval

    def require_policy_decision(
        self,
        policy_decision_id: str,
        *,
        action: str,
        target_id: str,
        payload_sha256: str,
        destination: str | None = None,
        policy_sha256: str | None = None,
        allow_requires_approval: bool = True,
    ) -> PolicyDecision:
        decision = self._policy_decision(policy_decision_id)
        material = {
            "action": decision.action,
            "target_id": decision.target_id,
            "payload_sha256": decision.payload_sha256,
            "destination": decision.destination,
            "policy_version": decision.policy_version,
            "policy_sha256": decision.policy_sha256,
            "outcome": decision.outcome,
            "reason_codes": decision.reason_codes,
            "required_approval_scope": decision.required_approval_scope,
            "evaluated_at": decision.evaluated_at,
        }
        allowed_outcomes = {PolicyOutcome.ALLOW}
        if allow_requires_approval:
            allowed_outcomes.add(PolicyOutcome.REQUIRE_APPROVAL)
        if (
            derive_id("pdec", material) != decision.policy_decision_id
            or decision.action != action
            or decision.target_id != target_id
            or decision.payload_sha256 != payload_sha256
            or decision.destination != destination
            or decision.outcome not in allowed_outcomes
            or (policy_sha256 is not None and decision.policy_sha256 != policy_sha256)
        ):
            raise AuthorityError("Policy decision does not match the exact action scope")
        return decision

    def _approval(self, approval_id: str) -> ApprovalRecord | ExecutionApproval:
        payload = self._platform_payload("authority_approval", approval_id)
        if payload is not None:
            try:
                return ApprovalRecord.model_validate(payload)
            except ValidationError as error:
                raise AuthorityError("Stored approval contract is invalid") from error
        execution = self._execution_record(ExecutionApproval, approval_id)
        if isinstance(execution, ExecutionApproval):
            return execution
        accepted = self._accepted_approval(approval_id)
        if accepted is not None:
            return accepted
        raise AuthorityError("Approval authority record is unavailable")

    def _policy_decision(self, decision_id: str) -> PolicyDecision:
        payload = self._platform_payload("authority_policy", decision_id)
        if payload is not None:
            try:
                return PolicyDecision.model_validate(payload)
            except ValidationError as error:
                raise AuthorityError("Stored policy decision is invalid") from error
        execution = self._execution_record(PolicyDecision, decision_id)
        if not isinstance(execution, PolicyDecision):
            raise AuthorityError("Policy decision authority record is unavailable")
        return execution

    def _pending_approvals_revoked_at(self) -> datetime | None:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return None
        with store:
            try:
                record = store.head("emergency", "emergency_global")
            except (PlatformStoreError, sqlite3.Error) as error:
                raise AuthorityError(
                    "Emergency state is unreadable; approvals fail closed"
                ) from error
        if record is None or record.state != "active":
            return None
        active = {str(value) for value in record.payload.get("active_actions", [])}
        if EmergencyAction.REVOKE_PENDING_APPROVALS.value not in active:
            return None
        stamp: object = record.payload.get("activated_at")
        extensions = record.payload.get("extensions")
        if isinstance(extensions, dict):
            per_action = extensions.get("action_activated_at")
            if isinstance(per_action, dict):
                stamp = per_action.get(EmergencyAction.REVOKE_PENDING_APPROVALS.value, stamp)
        try:
            revoked_at = datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError) as error:
            raise AuthorityError("Emergency revocation timestamp is invalid") from error
        if revoked_at.tzinfo is None:
            revoked_at = revoked_at.replace(tzinfo=UTC)
        return revoked_at.astimezone(UTC)

    def _platform_payload(self, kind: str, record_id: str) -> dict[str, object] | None:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return None
        with store:
            record = store.head(kind, record_id)
            return None if record is None else dict(record.payload)

    def _execution_record(
        self,
        model: type[PolicyDecision] | type[ExecutionApproval],
        record_id: str,
    ) -> PolicyDecision | ExecutionApproval | None:
        try:
            store = ExecutionStore.open_for_read(self.root / "ops" / "control.sqlite")
        except (OSError, ExecutionStoreError):
            return None
        if store is None:
            return None
        with store:
            return store.get(model, record_id)

    def _accepted_approval(self, approval_id: str) -> ApprovalRecord | None:
        relative = f"ops/approvals/accepted/{approval_id}.json"
        verification_relative = f"ops/approvals/accepted/{approval_id}.verification.json"
        try:
            payload = json.loads(read_regular_file(self.root, relative, max_bytes=256_000).data)
            verification = json.loads(
                read_regular_file(self.root, verification_relative, max_bytes=256_000).data
            )
            approval = ApprovalRecord.model_validate(payload)
        except (OSError, PathPolicyError, json.JSONDecodeError, ValidationError):
            return None
        if not isinstance(verification, dict) or verification.get("approval_id") != approval_id:
            return None
        return approval
