from __future__ import annotations

import difflib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml

from raytsystem.security import osfd
from raytsystem.catalog import CatalogError, CatalogService, CatalogSnapshot
from raytsystem.contracts import (
    Sensitivity,
    SkillDefinition,
    TrustClass,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.platform_store import (
    PlatformStore,
    PlatformStoreError,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner

_SKILL_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_:.@/-]{1,255}$")
_PUBLIC_STORE_ID = re.compile(r"^[a-z][a-z0-9_:.@-]{1,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION_KIND = "skill_authoring_revision"
_SAVE_SCOPE = "skill_authoring_save"
_FORK_SCOPE = "skill_authoring_fork"
PINNED_SKILL_POLICY_UNKNOWN = "*"
_RECOVERY_SCHEMA = "SkillAuthoringRecoveryV2"
_RECOVERY_DIRECTORY = "skill-authoring-recovery"
_RECOVERY_FILE = re.compile(r"^txn-([0-9a-f]{48})\.json(?:\.next)?$")
_RECOVERY_TXN = re.compile(r"^[0-9a-f]{48}$")
_RECOVERY_MAX_BYTES = 16 * 1024
_RECOVERY_MAX_PENDING = 128


def active_pinned_skill_ids(store: PlatformStore) -> frozenset[str]:
    """Resolve active package manifests through the caller's store snapshot.

    Write callers pass the ``PlatformStore`` connection that already owns their
    ``BEGIN IMMEDIATE`` transaction.  This keeps the package pin decision inside
    the same serialization fence as the subsequent skill mutation.  Read-only
    callers may pass a read-only store to retain the body-free list/detail policy.
    """

    skill_ids: set[str] = set()
    offset = 0
    while True:
        page = store.list_heads("package_active", limit=500, offset=offset)
        for active in page:
            revision_id = active.payload.get("revision_id")
            if not isinstance(revision_id, str) or _PUBLIC_STORE_ID.fullmatch(revision_id) is None:
                return frozenset({PINNED_SKILL_POLICY_UNKNOWN})
            manifest = store.head("package_manifest", revision_id)
            if manifest is None:
                return frozenset({PINNED_SKILL_POLICY_UNKNOWN})
            declared = manifest.payload.get("skill_ids")
            if not isinstance(declared, list | tuple):
                return frozenset({PINNED_SKILL_POLICY_UNKNOWN})
            for skill_id in declared:
                if not isinstance(skill_id, str) or _PUBLIC_STORE_ID.fullmatch(skill_id) is None:
                    return frozenset({PINNED_SKILL_POLICY_UNKNOWN})
                skill_ids.add(skill_id)
        if len(page) < 500:
            break
        offset += len(page)
    return frozenset(skill_ids)


class SkillAuthoringError(RuntimeError):
    """A typed, redacted failure at the local skill-authoring boundary."""

    code: ClassVar[str] = "skill_authoring_error"
    status_code: ClassVar[int] = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "details": self.details,
            }
        }


class SkillNotFoundError(SkillAuthoringError):
    code = "skill_not_found"
    status_code = 404


class SkillReadOnlyError(SkillAuthoringError):
    code = "skill_read_only"
    status_code = 403


class SkillValidationError(SkillAuthoringError):
    code = "skill_validation_failed"
    status_code = 422

    def __init__(
        self,
        errors: Iterable[dict[str, Any]],
        *,
        warnings: Iterable[dict[str, Any]] = (),
    ) -> None:
        error_items = list(errors)
        super().__init__(
            "Skill content failed validation",
            details={"errors": error_items, "warnings": list(warnings)},
        )


class SkillConflictError(SkillAuthoringError):
    code = "skill_edit_conflict"
    status_code = 409


class SkillIdempotencyError(SkillAuthoringError):
    code = "skill_idempotency_conflict"
    status_code = 409


class SkillPathError(SkillAuthoringError):
    code = "unsafe_skill_path"
    status_code = 422


class SkillPersistenceError(SkillAuthoringError):
    code = "skill_persistence_failed"
    status_code = 500


class _InvalidRecoveryIntent(SkillPersistenceError):
    """A bounded journal file was readable but did not contain a valid intent."""


@dataclass(frozen=True)
class _ValidationResult:
    content: str
    data: bytes
    metadata: dict[str, Any]
    requested_test_status: str
    warnings: tuple[dict[str, Any], ...]
    sensitivity: str

    def summary(self) -> dict[str, Any]:
        return {
            "valid": True,
            "errors": [],
            "warnings": list(self.warnings),
            "size_bytes": len(self.data),
            "source_sha256": sha256_hex(self.data),
            "sensitivity": self.sensitivity,
            "requested_test_status": self.requested_test_status,
            "effective_test_status": "pending",
        }


@dataclass(frozen=True)
class _SkillContext:
    snapshot: CatalogSnapshot
    definition: SkillDefinition
    content: str
    data: bytes


@dataclass(frozen=True)
class _SourceChangedDuringWrite(Exception):
    data: bytes


@dataclass(frozen=True)
class _RecoveryIntent:
    transaction_id: str
    operation: str
    source_skill_id: str
    target_skill_id: str
    scope: str
    idempotency_key: str
    request_sha256: str
    original_source_sha256: str | None
    proposed_source_sha256: str
    installed_dev: int | None = None
    installed_ino: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_name": _RECOVERY_SCHEMA,
            "transaction_id": self.transaction_id,
            "operation": self.operation,
            "source_skill_id": self.source_skill_id,
            "target_skill_id": self.target_skill_id,
            "scope": self.scope,
            "idempotency_key": self.idempotency_key,
            "request_sha256": self.request_sha256,
            "original_source_sha256": self.original_source_sha256,
            "proposed_source_sha256": self.proposed_source_sha256,
            "installed_dev": self.installed_dev,
            "installed_ino": self.installed_ino,
        }


class SkillAuthoringService:
    """Preview and commit bounded local SKILL.md edits without accepting paths.

    HTTP session, CSRF and approval checks deliberately remain at the web boundary. This service
    accepts only a typed skill ID, builds ``skills/<id>/SKILL.md`` itself, and applies filesystem,
    catalog, sensitivity, CAS, idempotency and audit invariants before returning JSON-ready data.
    """

    max_content_bytes = CatalogService.max_document_bytes

    def __init__(
        self,
        root: Path,
        *,
        scanner: SecretScanner | None = None,
        pinned_skill_ids: Iterable[str] = (),
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()
        self.catalog = CatalogService(self.root, scanner=self.scanner)
        self.pinned_skill_ids = frozenset(pinned_skill_ids)
        self._write_lock = threading.RLock()

    def edit_policy(self, skill_id: str) -> dict[str, Any]:
        """Return the computed edit/fork policy for one safe catalog skill."""

        with self.catalog_read_guard():
            context = self._context(skill_id)
            return self._policy(context)

    def recover_pending(self) -> dict[str, int]:
        """Reconcile durable authoring intents left by a terminated writer."""

        with self._write_lock, self._exclusive_authoring_lock():
            return {"recovered": self._recover_pending_journals()}

    @contextmanager
    def catalog_read_guard(self) -> Iterator[None]:
        """Hold a cross-process reader fence around catalog-derived visibility.

        A pending journal can only be observed after a writer terminated while holding the
        exclusive fence.  Release the shared fence, recover under the exclusive fence, and retry
        before allowing a catalog read to proceed.
        """

        while True:
            with self._authoring_lock(exclusive=False):
                if not self._has_pending_recovery():
                    yield
                    return
            self.recover_pending()

    @staticmethod
    def policy_for_definition(
        skill: SkillDefinition,
        *,
        pinned_skill_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Compute authoring authority from typed catalog metadata without reading a body."""

        expected_path = f"skills/{skill.skill_id}/SKILL.md"
        pinned = frozenset(pinned_skill_ids)
        reason: str | None = None
        editable = False
        forkable = False
        if _SKILL_ID.fullmatch(skill.skill_id) is None:
            reason = "non_authorable_skill_id"
        elif skill.source_path != expected_path:
            reason = "source_path_not_allowlisted"
        elif skill.sensitivity in {Sensitivity.RESTRICTED, Sensitivity.SECRET}:
            reason = "sensitivity_restricted"
        elif not skill.enabled:
            reason = "skill_disabled"
        elif PINNED_SKILL_POLICY_UNKNOWN in pinned:
            reason = "installed_pack_state_unavailable"
        elif skill.skill_id in pinned:
            reason = "installed_pinned_pack"
            forkable = True
        elif skill.pack_id == "pack_local" and skill.trust_class is TrustClass.USER:
            editable = True
            forkable = True
        elif skill.trust_class is TrustClass.OFFICIAL:
            reason = "official_skill"
            forkable = True
        elif skill.trust_class is TrustClass.GENERATED:
            reason = "generated_skill"
            forkable = True
        elif skill.trust_class in {TrustClass.UNTRUSTED, TrustClass.RESEARCH}:
            reason = "unverified_provenance"
        else:
            reason = "non_local_pack"
            forkable = True
        return {
            "skill_id": skill.skill_id,
            "source_path": expected_path,
            "pack_id": skill.pack_id,
            "trust_class": skill.trust_class.value,
            "sensitivity": skill.sensitivity.value,
            "editable": editable,
            "read_only_reason": reason,
            "forkable": forkable,
        }

    def preview_save(
        self,
        skill_id: str,
        *,
        content: str,
        expected_catalog_sha256: str,
        expected_source_sha256: str,
    ) -> dict[str, Any]:
        """Validate and diff a local edit without writing filesystem or store state."""

        validated_id = self._validate_skill_id(skill_id)
        expected_catalog = self._validate_sha(expected_catalog_sha256, "catalog")
        expected_source = self._validate_sha(expected_source_sha256, "source")
        proposed = self._validate_content(validated_id, content)
        with self.catalog_read_guard():
            context = self._context(validated_id)
            policy = self._policy(context)
            self._require_editable(validated_id, policy)
            self._require_cas(
                context,
                expected_catalog=expected_catalog,
                expected_source=expected_source,
                proposed=proposed.content,
            )
            return self._save_preview(context, policy, proposed, expected_catalog, expected_source)

    def save(
        self,
        skill_id: str,
        *,
        content: str,
        expected_catalog_sha256: str,
        expected_source_sha256: str,
        idempotency_key: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """CAS-save an editable skill and append revision/audit/idempotency records."""

        validated_id = self._validate_skill_id(skill_id)
        expected_catalog = self._validate_sha(expected_catalog_sha256, "catalog")
        expected_source = self._validate_sha(expected_source_sha256, "source")
        actor = self._validate_token(actor_id, "actor_id")
        idempotency = self._validate_token(idempotency_key, "idempotency_key")
        proposed = self._validate_content(validated_id, content)
        request = {
            "operation": "save",
            "skill_id": validated_id,
            "proposed_source_sha256": sha256_hex(proposed.data),
            "expected_catalog_sha256": expected_catalog,
            "expected_source_sha256": expected_source,
            "actor_id": actor,
        }
        intent: _RecoveryIntent | None = None
        committed = False
        result: dict[str, Any] | None = None
        with self._write_lock, self._exclusive_authoring_lock():
            self._recover_pending_journals()
            try:
                with initialize_platform_store(self.root) as store, store.transaction():
                    replay = self._receipt(store, _SAVE_SCOPE, idempotency, request)
                    if replay is not None:
                        return replay
                    context = self._context(validated_id)
                    policy = self._policy(context, store=store)
                    self._require_editable(validated_id, policy)
                    self._require_cas(
                        context,
                        expected_catalog=expected_catalog,
                        expected_source=expected_source,
                        proposed=proposed.content,
                    )
                    intent = self._new_recovery_intent(
                        operation="save",
                        source_skill_id=validated_id,
                        target_skill_id=validated_id,
                        scope=_SAVE_SCOPE,
                        idempotency_key=idempotency,
                        request=request,
                        original_source_sha256=expected_source,
                        proposed_source_sha256=sha256_hex(proposed.data),
                    )
                    try:
                        intent = self._atomic_replace(
                            validated_id,
                            proposed.data,
                            expected_source_sha256=expected_source,
                            intent=intent,
                        )
                    except _SourceChangedDuringWrite as error:
                        self._raise_conflict(
                            context,
                            kind="source_sha256",
                            expected_catalog=expected_catalog,
                            expected_source=expected_source,
                            current_source=sha256_hex(error.data),
                            current_data=error.data,
                            proposed_content=proposed.content,
                        )
                    updated = self._load_updated(validated_id, proposed.data)
                    self._require_non_target_catalog_unchanged(
                        context.snapshot,
                        updated.snapshot,
                        target_skill_id=validated_id,
                    )
                    result = self._record_change(
                        store,
                        operation="save",
                        source_skill_id=validated_id,
                        target=updated,
                        previous_catalog_sha256=context.snapshot.catalog_sha256,
                        previous_source_sha256=context.definition.source_sha256,
                        actor_id=actor,
                        idempotency_key=idempotency,
                        validation=proposed,
                        diff=_diff(
                            context.content,
                            proposed.content,
                            source_path=self._relative_path(validated_id),
                        ),
                    )
                    store.idempotent_receipt(
                        scope=_SAVE_SCOPE,
                        idempotency_key=idempotency,
                        request=request,
                        receipt=result,
                    )
                committed = True
            except SkillAuthoringError:
                raise
            except PlatformStoreError as error:
                if "Idempotency key" in str(error):
                    raise SkillIdempotencyError(
                        "Idempotency key was reused for another skill edit",
                        details={"skill_id": validated_id},
                    ) from error
                raise SkillPersistenceError("Skill authoring store update failed") from error
            except OSError as error:
                raise SkillPersistenceError("Skill authoring write failed") from error
            finally:
                if intent is not None:
                    if committed:
                        self._finalize_recovery_intent(intent)
                    else:
                        self._rollback_recovery_intent(intent)
        if result is None:  # pragma: no cover - defensive invariant
            raise SkillPersistenceError("Skill authoring produced no result")
        return result

    def preview_fork(
        self,
        skill_id: str,
        *,
        new_skill_id: str | None = None,
        expected_catalog_sha256: str,
        expected_source_sha256: str,
    ) -> dict[str, Any]:
        """Preview a unique local copy without changing the source or destination."""

        source_id = self._validate_skill_id(skill_id)
        expected_catalog = self._validate_sha(expected_catalog_sha256, "catalog")
        expected_source = self._validate_sha(expected_source_sha256, "source")
        with self.catalog_read_guard():
            context = self._context(source_id)
            policy = self._policy(context)
            self._require_forkable(source_id, policy)
            self._require_cas(
                context,
                expected_catalog=expected_catalog,
                expected_source=expected_source,
                proposed=context.content,
            )
            target_id = (
                self._suggest_fork_id(source_id, context.snapshot)
                if new_skill_id is None
                else self._validate_skill_id(new_skill_id)
            )
            self._require_destination_available(target_id, context.snapshot)
            proposed = self._fork_content(context, target_id)
            return {
                "operation": "skill_fork_preview",
                "source_skill_id": source_id,
                "new_skill_id": target_id,
                "destination": self._relative_path(target_id),
                "source_unchanged": True,
                "expected_catalog_sha256": expected_catalog,
                "expected_source_sha256": expected_source,
                "proposed_source_sha256": sha256_hex(proposed.data),
                "diff": _diff(
                    context.content,
                    proposed.content,
                    source_path=self._relative_path(source_id),
                    target_path=self._relative_path(target_id),
                ),
                "validation": proposed.summary(),
                "ownership_after_create": {"pack_id": "pack_local", "trust_class": "user"},
            }

    def create_fork(
        self,
        skill_id: str,
        *,
        new_skill_id: str,
        expected_catalog_sha256: str,
        expected_source_sha256: str,
        idempotency_key: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Create an isolated pack_local copy; never mutate the read-only source."""

        source_id = self._validate_skill_id(skill_id)
        target_id = self._validate_skill_id(new_skill_id)
        expected_catalog = self._validate_sha(expected_catalog_sha256, "catalog")
        expected_source = self._validate_sha(expected_source_sha256, "source")
        actor = self._validate_token(actor_id, "actor_id")
        idempotency = self._validate_token(idempotency_key, "idempotency_key")
        request = {
            "operation": "fork",
            "source_skill_id": source_id,
            "new_skill_id": target_id,
            "expected_catalog_sha256": expected_catalog,
            "expected_source_sha256": expected_source,
            "actor_id": actor,
        }
        intent: _RecoveryIntent | None = None
        committed = False
        result: dict[str, Any] | None = None
        with self._write_lock, self._exclusive_authoring_lock():
            self._recover_pending_journals()
            try:
                with initialize_platform_store(self.root) as store, store.transaction():
                    replay = self._receipt(store, _FORK_SCOPE, idempotency, request)
                    if replay is not None:
                        return replay
                    context = self._context(source_id)
                    policy = self._policy(context, store=store)
                    self._require_forkable(source_id, policy)
                    self._require_cas(
                        context,
                        expected_catalog=expected_catalog,
                        expected_source=expected_source,
                        proposed=context.content,
                    )
                    self._require_destination_available(target_id, context.snapshot)
                    proposed = self._fork_content(context, target_id)
                    intent = self._new_recovery_intent(
                        operation="fork",
                        source_skill_id=source_id,
                        target_skill_id=target_id,
                        scope=_FORK_SCOPE,
                        idempotency_key=idempotency,
                        request=request,
                        original_source_sha256=expected_source,
                        proposed_source_sha256=sha256_hex(proposed.data),
                    )
                    intent = self._create_skill_file(target_id, proposed.data, intent=intent)
                    updated = self._load_updated(target_id, proposed.data)
                    self._require_non_target_catalog_unchanged(
                        context.snapshot,
                        updated.snapshot,
                        target_skill_id=target_id,
                    )
                    source_after = self._read_source(source_id)
                    if sha256_hex(source_after) != expected_source:
                        raise SkillConflictError(
                            "Source skill changed while its local copy was created",
                            details={
                                "skill_id": source_id,
                                "kind": "source_sha256",
                                "expected_source_sha256": expected_source,
                                "current_source_sha256": sha256_hex(source_after),
                            },
                        )
                    result = self._record_change(
                        store,
                        operation="fork",
                        source_skill_id=source_id,
                        target=updated,
                        previous_catalog_sha256=context.snapshot.catalog_sha256,
                        previous_source_sha256=None,
                        actor_id=actor,
                        idempotency_key=idempotency,
                        validation=proposed,
                        diff=_diff(
                            context.content,
                            proposed.content,
                            source_path=self._relative_path(source_id),
                            target_path=self._relative_path(target_id),
                        ),
                    )
                    store.idempotent_receipt(
                        scope=_FORK_SCOPE,
                        idempotency_key=idempotency,
                        request=request,
                        receipt=result,
                    )
                committed = True
            except SkillAuthoringError:
                raise
            except PlatformStoreError as error:
                if "Idempotency key" in str(error):
                    raise SkillIdempotencyError(
                        "Idempotency key was reused for another skill fork",
                        details={"source_skill_id": source_id, "new_skill_id": target_id},
                    ) from error
                raise SkillPersistenceError("Skill fork store update failed") from error
            except OSError as error:
                raise SkillPersistenceError("Skill fork write failed") from error
            finally:
                if intent is not None:
                    if committed:
                        self._finalize_recovery_intent(intent)
                    else:
                        self._rollback_recovery_intent(intent)
        if result is None:  # pragma: no cover - defensive invariant
            raise SkillPersistenceError("Skill fork produced no result")
        return result

    def _context(self, skill_id: str) -> _SkillContext:
        validated_id = self._validate_skill_id(skill_id)
        # Read the typed target first so symlink/hardlink/oversize failures retain a typed path
        # error instead of being flattened by a broader catalog load failure.
        data = self._read_source(validated_id)
        snapshot = self._snapshot()
        definition = snapshot.skill(validated_id)
        if definition is None:
            raise SkillNotFoundError(
                "Skill does not exist",
                details={"skill_id": validated_id},
            )
        expected_path = self._relative_path(validated_id)
        if definition.source_path != expected_path:
            raise SkillPathError(
                "Skill source is outside the authoring allowlist",
                details={"skill_id": validated_id, "source_path": definition.source_path},
            )
        observed_sha = sha256_hex(data)
        if definition.source_sha256 != observed_sha:
            # One retry distinguishes a concurrent replacement from a persistently inconsistent
            # catalog projection without ever accepting the stale bytes.
            data = self._read_source(validated_id)
            snapshot = self._snapshot()
            definition = snapshot.skill(validated_id)
            if definition is None or definition.source_sha256 != sha256_hex(data):
                raise SkillConflictError(
                    "Skill changed while the catalog was being read",
                    details={"skill_id": validated_id, "kind": "catalog_source_drift"},
                )
        return _SkillContext(
            snapshot=snapshot,
            definition=definition,
            content=self._safe_decode(data),
            data=data,
        )

    def _snapshot(self) -> CatalogSnapshot:
        try:
            return self.catalog.load()
        except CatalogError as error:
            raise SkillAuthoringError(
                "Catalog is unavailable for skill authoring",
                details={"reason": "catalog_invalid"},
            ) from error

    def _policy(
        self,
        context: _SkillContext,
        *,
        store: PlatformStore | None = None,
    ) -> dict[str, Any]:
        return self.policy_for_definition(
            context.definition,
            pinned_skill_ids=(
                self.pinned_skill_ids if store is None else active_pinned_skill_ids(store)
            ),
        )

    def _validate_content(self, skill_id: str, content: str) -> _ValidationResult:
        errors: list[dict[str, Any]] = []
        if not isinstance(content, str):
            raise SkillValidationError(
                [{"field": "content", "code": "invalid_type", "message": "Must be text"}]
            )
        try:
            data = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise SkillValidationError(
                [{"field": "content", "code": "invalid_utf8", "message": "Must be UTF-8"}]
            ) from error
        if len(data) > self.max_content_bytes:
            raise SkillValidationError(
                [
                    {
                        "field": "content",
                        "code": "content_too_large",
                        "message": "Skill exceeds the configured size limit",
                        "max_bytes": self.max_content_bytes,
                    }
                ]
            )
        if b"\x00" in data:
            raise SkillValidationError(
                [{"field": "content", "code": "nul_byte", "message": "NUL is forbidden"}]
            )
        try:
            metadata = CatalogService._frontmatter(data)
        except CatalogError as error:
            raise SkillValidationError(
                [
                    {
                        "field": "frontmatter",
                        "code": "invalid_frontmatter",
                        "message": str(error),
                    }
                ]
            ) from error
        required = ("name", "description", "version", "permissions", "test_status")
        for field in required:
            if field not in metadata:
                errors.append(
                    {
                        "field": field,
                        "code": "required",
                        "message": f"Frontmatter field '{field}' is required",
                    }
                )
        name = metadata.get("name")
        description = metadata.get("description")
        version = metadata.get("version")
        permissions = metadata.get("permissions")
        requested_status = metadata.get("test_status")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            errors.append(
                {"field": "name", "code": "invalid_type", "message": "Must be non-empty text"}
            )
        elif isinstance(name, str) and name != skill_id:
            errors.append(
                {
                    "field": "name",
                    "code": "directory_name_mismatch",
                    "message": "Frontmatter name must equal skill_id",
                    "expected": skill_id,
                }
            )
        if description is not None and (
            not isinstance(description, str) or not description.strip()
        ):
            errors.append(
                {
                    "field": "description",
                    "code": "invalid_type",
                    "message": "Must be non-empty text",
                }
            )
        elif isinstance(description, str) and len(description) > 4096:
            errors.append(
                {
                    "field": "description",
                    "code": "value_too_long",
                    "message": "Must be at most 4096 characters",
                }
            )
        if version is not None and (not isinstance(version, str) or not version.strip()):
            errors.append(
                {
                    "field": "version",
                    "code": "invalid_type",
                    "message": "Must be non-empty text",
                }
            )
        elif isinstance(version, str) and len(version) > 4096:
            errors.append(
                {
                    "field": "version",
                    "code": "value_too_long",
                    "message": "Must be at most 4096 characters",
                }
            )
        if permissions is not None:
            if not isinstance(permissions, list):
                errors.append(
                    {
                        "field": "permissions",
                        "code": "invalid_type",
                        "message": "Must be a list of permission IDs",
                    }
                )
            else:
                invalid_permissions = [
                    item
                    for item in permissions
                    if not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None
                ]
                if invalid_permissions:
                    errors.append(
                        {
                            "field": "permissions",
                            "code": "invalid_permission",
                            "message": "Every permission must be a canonical identifier",
                        }
                    )
                elif len(permissions) != len(set(permissions)):
                    errors.append(
                        {
                            "field": "permissions",
                            "code": "duplicate_permission",
                            "message": "Permissions must be unique",
                        }
                    )
        if requested_status is not None and requested_status not in {
            "pass",
            "pending",
            "unavailable",
        }:
            errors.append(
                {
                    "field": "test_status",
                    "code": "invalid_status",
                    "message": "Must be pass, pending or unavailable",
                }
            )
        if errors:
            raise SkillValidationError(errors)
        if not isinstance(requested_status, str):  # narrowed by required/type checks above
            raise SkillValidationError(
                [{"field": "test_status", "code": "invalid_type", "message": "Must be text"}]
            )
        normalized_metadata = dict(metadata)
        normalized_metadata["test_status"] = "pending"
        boundary = data.find(b"\n---\n", 4)
        if boundary < 0:  # pragma: no cover - CatalogService already enforces this
            raise SkillValidationError(
                [
                    {
                        "field": "frontmatter",
                        "code": "invalid_frontmatter",
                        "message": "Frontmatter is not terminated",
                    }
                ]
            )
        body = data[boundary + len(b"\n---\n") :].decode("utf-8", errors="strict")
        rendered_metadata = yaml.safe_dump(
            normalized_metadata,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).rstrip("\n")
        normalized = f"---\n{rendered_metadata}\n---\n{body}"
        normalized_data = normalized.encode("utf-8")
        if len(normalized_data) > self.max_content_bytes:
            raise SkillValidationError(
                [
                    {
                        "field": "content",
                        "code": "content_too_large",
                        "message": "Normalized skill exceeds the configured size limit",
                        "max_bytes": self.max_content_bytes,
                    }
                ]
            )
        try:
            decision = self.scanner.scan(normalized_data, path=self._relative_path(skill_id))
        except Exception as error:
            raise SkillValidationError(
                [
                    {
                        "field": "content",
                        "code": "sensitivity_scan_failed",
                        "message": "Sensitivity scanner failed closed",
                    }
                ]
            ) from error
        if decision.blocks_processing:
            raise SkillValidationError(
                [
                    {
                        "field": "content",
                        "code": "restricted_content",
                        "message": "Sensitivity policy rejected this skill",
                        "reason_codes": list(decision.reason_codes),
                    }
                ]
            )
        warnings: list[dict[str, Any]] = []
        if requested_status != "pending":
            warnings.append(
                {
                    "field": "test_status",
                    "code": "test_status_reset",
                    "message": "Local edits remain pending until an external verifier attests them",
                    "requested": requested_status,
                    "effective": "pending",
                }
            )
        return _ValidationResult(
            content=normalized,
            data=normalized_data,
            metadata=normalized_metadata,
            requested_test_status=requested_status,
            warnings=tuple(warnings),
            sensitivity=decision.sensitivity,
        )

    def _fork_content(self, context: _SkillContext, new_skill_id: str) -> _ValidationResult:
        source_data = context.data
        # Keep the boundary search below consistent with the CRLF
        # normalization CatalogService._frontmatter applies.
        if source_data.startswith(b"---\r\n"):
            source_data = source_data.replace(b"\r\n", b"\n")
        try:
            metadata = CatalogService._frontmatter(source_data)
        except CatalogError as error:
            raise SkillValidationError(
                [
                    {
                        "field": "frontmatter",
                        "code": "invalid_source_frontmatter",
                        "message": "Source skill frontmatter is invalid",
                    }
                ]
            ) from error
        metadata = dict(metadata)
        metadata.update(
            {
                "name": new_skill_id,
                "description": context.definition.description,
                "version": context.definition.version,
                "permissions": list(context.definition.permissions),
                "test_status": "pending",
            }
        )
        boundary = source_data.find(b"\n---\n", 4)
        if boundary < 0:  # pragma: no cover - parser above enforces this
            raise SkillValidationError(
                [{"field": "frontmatter", "code": "invalid_source_frontmatter"}]
            )
        body = source_data[boundary + len(b"\n---\n") :].decode("utf-8", errors="strict")
        rendered = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).rstrip("\n")
        return self._validate_content(new_skill_id, f"---\n{rendered}\n---\n{body}")

    def _save_preview(
        self,
        context: _SkillContext,
        policy: dict[str, Any],
        proposed: _ValidationResult,
        expected_catalog: str,
        expected_source: str,
    ) -> dict[str, Any]:
        return {
            "operation": "skill_save_preview",
            "skill_id": context.definition.skill_id,
            "source_path": self._relative_path(context.definition.skill_id),
            "policy": policy,
            "expected_catalog_sha256": expected_catalog,
            "expected_source_sha256": expected_source,
            "current_catalog_sha256": context.snapshot.catalog_sha256,
            "current_source_sha256": context.definition.source_sha256,
            "proposed_source_sha256": sha256_hex(proposed.data),
            "normalized_content": proposed.content,
            "diff": _diff(
                context.content,
                proposed.content,
                source_path=self._relative_path(context.definition.skill_id),
            ),
            "validation": proposed.summary(),
            "affected_agents": self._affected_agents(context.snapshot, context.definition.skill_id),
        }

    def _require_cas(
        self,
        context: _SkillContext,
        *,
        expected_catalog: str,
        expected_source: str,
        proposed: str,
    ) -> None:
        if context.definition.source_sha256 != expected_source:
            self._raise_conflict(
                context,
                kind="source_sha256",
                expected_catalog=expected_catalog,
                expected_source=expected_source,
                current_source=context.definition.source_sha256,
                current_data=context.data,
                proposed_content=proposed,
            )
        if context.snapshot.catalog_sha256 != expected_catalog:
            self._raise_conflict(
                context,
                kind="catalog_sha256",
                expected_catalog=expected_catalog,
                expected_source=expected_source,
                current_source=context.definition.source_sha256,
                current_data=context.data,
                proposed_content=proposed,
            )

    def _raise_conflict(
        self,
        context: _SkillContext,
        *,
        kind: str,
        expected_catalog: str,
        expected_source: str,
        current_source: str,
        current_data: bytes,
        proposed_content: str,
    ) -> None:
        details: dict[str, Any] = {
            "skill_id": context.definition.skill_id,
            "kind": kind,
            "expected_catalog_sha256": expected_catalog,
            "current_catalog_sha256": context.snapshot.catalog_sha256,
            "expected_source_sha256": expected_source,
            "current_source_sha256": current_source,
            "content_withheld": True,
        }
        try:
            content_must_be_withheld = self.scanner.scan(
                current_data,
                path=self._relative_path(context.definition.skill_id),
            ).blocks_processing
        except Exception:
            content_must_be_withheld = True
        if not content_must_be_withheld:
            try:
                current_content = current_data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                pass
            else:
                details.update(
                    {
                        "content_withheld": False,
                        "current_content": current_content,
                        "proposed_content": proposed_content,
                        "diff": _diff(
                            current_content,
                            proposed_content,
                            source_path=self._relative_path(context.definition.skill_id),
                        ),
                    }
                )
        raise SkillConflictError("Skill changed after the editor was opened", details=details)

    @staticmethod
    def _require_editable(skill_id: str, policy: dict[str, Any]) -> None:
        if policy["editable"]:
            return
        raise SkillReadOnlyError(
            "Skill is read-only",
            details={
                "skill_id": skill_id,
                "read_only_reason": policy["read_only_reason"],
                "forkable": policy["forkable"],
            },
        )

    @staticmethod
    def _require_forkable(skill_id: str, policy: dict[str, Any]) -> None:
        if policy["forkable"]:
            return
        raise SkillReadOnlyError(
            "Skill cannot be copied under the current policy",
            details={
                "skill_id": skill_id,
                "read_only_reason": policy["read_only_reason"],
                "forkable": False,
            },
        )

    def _record_change(
        self,
        store: PlatformStore,
        *,
        operation: str,
        source_skill_id: str,
        target: _SkillContext,
        previous_catalog_sha256: str,
        previous_source_sha256: str | None,
        actor_id: str,
        idempotency_key: str,
        validation: _ValidationResult,
        diff: str,
    ) -> dict[str, Any]:
        skill = target.definition
        prior = store.head(_REVISION_KIND, skill.skill_id)
        changed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        revision_material = {
            "operation": operation,
            "source_skill_id": source_skill_id,
            "skill_id": skill.skill_id,
            "source_sha256": skill.source_sha256,
            "previous_source_sha256": previous_source_sha256,
            "catalog_sha256": target.snapshot.catalog_sha256,
            "previous_catalog_sha256": previous_catalog_sha256,
            "previous_revision_sha256": None if prior is None else prior.payload_sha256,
            "actor_id": actor_id,
            "changed_at": changed_at,
        }
        revision_id = derive_id("skillrev", revision_material)
        payload = {
            "schema_name": "SkillAuthoringRevisionV1",
            "schema_version": "1.0.0",
            "skill_revision_id": revision_id,
            **revision_material,
            "source_path": self._relative_path(skill.skill_id),
            "test_status": "pending",
            "trust_class": skill.trust_class.value,
            "pack_id": skill.pack_id,
            "validation_sha256": sha256_hex(canonical_json_bytes(validation.summary())),
            "diff_sha256": sha256_hex(diff.encode("utf-8")),
        }
        record = store.append_record(
            kind=_REVISION_KIND,
            record_id=skill.skill_id,
            payload=payload,
            state="pending",
            expected_revision=None if prior is None else prior.revision,
        )
        event = store.append_event(
            stream_id=f"skill_authoring_{skill.skill_id}",
            aggregate_id=skill.skill_id,
            event_type="skill_saved" if operation == "save" else "skill_forked",
            actor_id=actor_id,
            payload_schema="skill_authoring_audit_v1",
            payload={
                "skill_revision_id": revision_id,
                "record_revision": record.revision,
                "source_sha256": skill.source_sha256,
                "previous_source_sha256": previous_source_sha256,
                "catalog_sha256": target.snapshot.catalog_sha256,
                "operation": operation,
                "idempotency_key_sha256": sha256_hex(idempotency_key.encode("utf-8")),
            },
        )
        return {
            "operation": operation,
            "skill_id": skill.skill_id,
            "source_skill_id": source_skill_id,
            "source_path": self._relative_path(skill.skill_id),
            "source_sha256": skill.source_sha256,
            "catalog_sha256": target.snapshot.catalog_sha256,
            "skill_revision_id": revision_id,
            "record_revision": record.revision,
            "audit_event_id": event["event_id"],
            "test_status": "pending",
            "validation": validation.summary(),
            "affected_agents": self._affected_agents(target.snapshot, skill.skill_id),
            "cache_invalidation": {
                "scope": "related_skill_queries",
                "skill_ids": [skill.skill_id],
            },
        }

    def _load_updated(self, skill_id: str, expected_data: bytes) -> _SkillContext:
        context = self._context(skill_id)
        expected_sha = sha256_hex(expected_data)
        if context.definition.source_sha256 != expected_sha or context.data != expected_data:
            raise SkillPersistenceError(
                "Catalog did not rehydrate the exact saved skill",
                details={"skill_id": skill_id},
            )
        if context.definition.test_status != "pending":
            raise SkillPersistenceError(
                "Edited skill test status was not reset to pending",
                details={"skill_id": skill_id},
            )
        return context

    def _require_non_target_catalog_unchanged(
        self,
        before: CatalogSnapshot,
        after: CatalogSnapshot,
        *,
        target_skill_id: str,
    ) -> None:
        """Reject a write if any catalog object except its typed target changed.

        The catalog hash necessarily changes for a successful save/fork. Comparing a normalized
        non-target projection catches a concurrent edit to another skill, pack, agent,
        instruction, or adapter without treating the intended target update as a conflict.
        """

        before_digest = self._non_target_catalog_sha256(before, target_skill_id)
        after_digest = self._non_target_catalog_sha256(after, target_skill_id)
        if secrets.compare_digest(before_digest, after_digest):
            return
        raise SkillConflictError(
            "Catalog changed while the skill write was being committed",
            details={
                "skill_id": target_skill_id,
                "kind": "non_target_catalog_changed",
                "expected_catalog_sha256": before.catalog_sha256,
                "current_catalog_sha256": after.catalog_sha256,
                "content_withheld": True,
            },
        )

    @staticmethod
    def _non_target_catalog_sha256(snapshot: CatalogSnapshot, target_skill_id: str) -> str:
        packs: list[dict[str, Any]] = []
        for pack in snapshot.packs:
            public_pack = pack.model_dump(mode="json")
            public_pack["skill_ids"] = [
                skill_id
                for skill_id in public_pack.get("skill_ids", [])
                if skill_id != target_skill_id
            ]
            # ``pack_local`` is synthesized by CatalogService. Adding the first local fork may
            # create it, so an empty normalized synthetic pack is not a non-target mutation.
            if public_pack.get("pack_id") == "pack_local" and not public_pack["skill_ids"]:
                continue
            packs.append(public_pack)
        material = {
            "packs": packs,
            "agents": [item.model_dump(mode="json") for item in snapshot.agents],
            "skills": [
                item.model_dump(mode="json")
                for item in snapshot.skills
                if item.skill_id != target_skill_id
            ],
            "instructions": [item.model_dump(mode="json") for item in snapshot.instructions],
            "adapters": [item.model_dump(mode="json") for item in snapshot.adapters],
        }
        return sha256_hex(canonical_json_bytes(material))

    def _atomic_replace(
        self,
        skill_id: str,
        data: bytes,
        *,
        expected_source_sha256: str,
        intent: _RecoveryIntent,
    ) -> _RecoveryIntent:
        """Install ``data`` without an overwrite-at-the-last-boundary race.

        Portable ``replace`` has no compare-and-swap predicate.  The target is therefore linked
        to an original witness, moved to a transaction-scoped displaced name, verified again,
        and the proposed witness is linked into the now-empty name with no-replace semantics.
        Every namespace transition remains recoverable from the durable intent.
        """

        skills_fd, skill_fd = self._open_skill_directory(skill_id)
        target_fd: int | None = None
        temp_fd: int | None = None
        temp_name, guard_name, displaced_name = self._save_recovery_names(intent.transaction_id)
        try:
            target_fd = self._open_regular_at(skill_fd, "SKILL.md", writable=False)
            before = os.fstat(target_fd)
            current = self._read_fd(target_fd, before, max_bytes=self.max_content_bytes)
            if sha256_hex(current) != expected_source_sha256:
                raise _SourceChangedDuringWrite(current)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            temp_fd = osfd.open(
                temp_name,
                flags,
                stat.S_IMODE(before.st_mode),
                dir_fd=skill_fd,
            )
            self._write_fd(temp_fd, data)
            temp_metadata = os.fstat(temp_fd)
            if not stat.S_ISREG(temp_metadata.st_mode) or temp_metadata.st_nlink != 1:
                raise SkillPathError("Temporary skill file is unsafe")
            osfd.close(temp_fd)
            temp_fd = None
            osfd.fsync(skill_fd)

            self._link_no_replace(skill_fd, "SKILL.md", guard_name)
            guard, guard_data = self._read_recovery_file_at(
                skill_fd,
                guard_name,
                allowed_links={1, 2},
            )
            if (before.st_dev, before.st_ino) != (guard.st_dev, guard.st_ino) or sha256_hex(
                guard_data
            ) != expected_source_sha256:
                osfd.unlink(guard_name, dir_fd=skill_fd)
                raise _SourceChangedDuringWrite(guard_data)
            osfd.fsync(skill_fd)

            osfd.rename(
                "SKILL.md",
                displaced_name,
                src_dir_fd=skill_fd,
                dst_dir_fd=skill_fd,
            )
            osfd.fsync(skill_fd)
            displaced, displaced_data = self._read_recovery_file_at(
                skill_fd,
                displaced_name,
                allowed_links={1, 2},
            )
            if (displaced.st_dev, displaced.st_ino) != (guard.st_dev, guard.st_ino):
                self._restore_displaced_no_replace(
                    skill_fd,
                    displaced_name=displaced_name,
                )
                raise _SourceChangedDuringWrite(displaced_data)
            osfd.unlink(displaced_name, dir_fd=skill_fd)

            try:
                self._link_no_replace(skill_fd, temp_name, "SKILL.md")
            except FileExistsError:
                concurrent = self._read_recovery_file_at(
                    skill_fd,
                    "SKILL.md",
                    allowed_links={1},
                )[1]
                raise _SourceChangedDuringWrite(concurrent) from None
            osfd.fsync(skill_fd)
            installed = osfd.stat("SKILL.md", dir_fd=skill_fd, follow_symlinks=False)
            temp_metadata = osfd.stat(temp_name, dir_fd=skill_fd, follow_symlinks=False)
            if (installed.st_dev, installed.st_ino) != (
                temp_metadata.st_dev,
                temp_metadata.st_ino,
            ):
                concurrent = self._read_recovery_file_at(
                    skill_fd,
                    "SKILL.md",
                    allowed_links={1},
                )[1]
                raise _SourceChangedDuringWrite(concurrent)
            applied = replace(
                intent,
                installed_dev=installed.st_dev,
                installed_ino=installed.st_ino,
            )
            self._write_recovery_intent(applied)
            osfd.unlink(temp_name, dir_fd=skill_fd)
            osfd.fsync(skill_fd)
            return applied
        except _SourceChangedDuringWrite:
            raise
        except SkillAuthoringError:
            raise
        except OSError as error:
            raise SkillPathError(
                "Skill file could not be replaced safely",
                details={"skill_id": skill_id},
            ) from error
        finally:
            if target_fd is not None:
                osfd.close(target_fd)
            if temp_fd is not None:
                osfd.close(temp_fd)
            osfd.close(skill_fd)
            osfd.close(skills_fd)

    def _create_skill_file(
        self,
        skill_id: str,
        data: bytes,
        *,
        intent: _RecoveryIntent,
    ) -> _RecoveryIntent:
        skills_fd = self._open_skills_directory()
        skill_fd: int | None = None
        file_fd: int | None = None
        marker_fd: int | None = None
        directory_created = False
        created_identity: tuple[int, int] | None = None
        created_file_identity: tuple[int, int] | None = None
        marker_name = self._fork_marker_name(intent.transaction_id)
        try:
            try:
                osfd.mkdir(skill_id, mode=0o755, dir_fd=skills_fd)
            except FileExistsError as error:
                raise SkillConflictError(
                    "Fork destination already exists",
                    details={"skill_id": skill_id, "kind": "destination_exists"},
                ) from error
            directory_created = True
            created_metadata = osfd.stat(skill_id, dir_fd=skills_fd, follow_symlinks=False)
            if stat.S_ISLNK(created_metadata.st_mode) or not stat.S_ISDIR(created_metadata.st_mode):
                raise SkillPathError("Fork destination is not a real directory")
            created_identity = (created_metadata.st_dev, created_metadata.st_ino)
            skill_fd = osfd.open(
                skill_id,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=skills_fd,
            )
            directory_metadata = os.fstat(skill_fd)
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or (directory_metadata.st_dev, directory_metadata.st_ino) != created_identity
            ):
                osfd.close(skill_fd)
                skill_fd = None
                raise SkillPathError("Fork destination is not a real directory")
            marker_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            marker_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            marker_fd = osfd.open(marker_name, marker_flags, 0o600, dir_fd=skill_fd)
            self._write_fd(marker_fd, self._fork_marker_data(intent.transaction_id))
            marker_metadata = os.fstat(marker_fd)
            if not stat.S_ISREG(marker_metadata.st_mode) or marker_metadata.st_nlink != 1:
                raise SkillPathError("Fork recovery marker is unsafe")
            osfd.close(marker_fd)
            marker_fd = None
            osfd.fsync(skill_fd)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_fd = osfd.open("SKILL.md", flags, 0o644, dir_fd=skill_fd)
            self._write_fd(file_fd, data)
            file_metadata = os.fstat(file_fd)
            if not stat.S_ISREG(file_metadata.st_mode) or file_metadata.st_nlink != 1:
                raise SkillPathError("Forked skill file is unsafe")
            created_file_identity = (file_metadata.st_dev, file_metadata.st_ino)
            osfd.close(file_fd)
            file_fd = None
            osfd.fsync(skill_fd)
            osfd.fsync(skills_fd)
            applied = replace(
                intent,
                installed_dev=file_metadata.st_dev,
                installed_ino=file_metadata.st_ino,
            )
            self._write_recovery_intent(applied)
            return applied
        except SkillAuthoringError:
            if directory_created:
                self._cleanup_partial_create(
                    skills_fd,
                    skill_fd,
                    skill_id,
                    created_identity=created_identity,
                    created_file_identity=created_file_identity,
                )
            raise
        except OSError as error:
            if directory_created:
                self._cleanup_partial_create(
                    skills_fd,
                    skill_fd,
                    skill_id,
                    created_identity=created_identity,
                    created_file_identity=created_file_identity,
                )
            raise SkillPathError(
                "Fork destination could not be created safely",
                details={"skill_id": skill_id},
            ) from error
        finally:
            if file_fd is not None:
                osfd.close(file_fd)
            if marker_fd is not None:
                osfd.close(marker_fd)
            if skill_fd is not None:
                osfd.close(skill_fd)
            osfd.close(skills_fd)

    @staticmethod
    def _cleanup_partial_create(
        skills_fd: int,
        skill_fd: int | None,
        skill_id: str,
        *,
        created_identity: tuple[int, int] | None,
        created_file_identity: tuple[int, int] | None,
    ) -> None:
        if created_identity is None:
            return
        try:
            current = osfd.stat(skill_id, dir_fd=skills_fd, follow_symlinks=False)
        except OSError:
            return
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != created_identity
        ):
            return
        if skill_fd is not None:
            opened = os.fstat(skill_fd)
            if (
                opened.st_dev,
                opened.st_ino,
            ) == created_identity and created_file_identity is not None:
                with suppress(OSError):
                    skill = osfd.stat("SKILL.md", dir_fd=skill_fd, follow_symlinks=False)
                    if (
                        stat.S_ISREG(skill.st_mode)
                        and skill.st_nlink == 1
                        and (skill.st_dev, skill.st_ino) == created_file_identity
                    ):
                        osfd.unlink("SKILL.md", dir_fd=skill_fd)
        with suppress(OSError):
            osfd.rmdir(skill_id, dir_fd=skills_fd)
        with suppress(OSError):
            osfd.fsync(skills_fd)

    @contextmanager
    def _exclusive_authoring_lock(self) -> Iterator[None]:
        with self._authoring_lock(exclusive=True):
            yield

    @contextmanager
    def _authoring_lock(self, *, exclusive: bool) -> Iterator[None]:
        state_fd = self._open_recovery_directory()
        lock_fd: int | None = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            lock_fd = osfd.open("writer.lock", flags, 0o600, dir_fd=state_fd)
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SkillPersistenceError("Skill authoring lock is unsafe")
            if metadata.st_size == 0:
                os.write(lock_fd, b"0")
                osfd.fsync(lock_fd)
            os.lseek(lock_fd, 0, os.SEEK_SET)
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                msvcrt_api: Any = msvcrt
                msvcrt_api.locking(lock_fd, msvcrt_api.LK_LOCK, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(lock_fd, mode)
            try:
                yield
            finally:
                os.lseek(lock_fd, 0, os.SEEK_SET)
                if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                    import msvcrt

                    msvcrt_unlock_api: Any = msvcrt
                    msvcrt_unlock_api.locking(lock_fd, msvcrt_unlock_api.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except SkillAuthoringError:
            raise
        except OSError as error:
            raise SkillPersistenceError("Skill authoring lock is unavailable") from error
        finally:
            if lock_fd is not None:
                osfd.close(lock_fd)
            osfd.close(state_fd)

    def _has_pending_recovery(self) -> bool:
        state_fd = self._open_recovery_directory()
        try:
            pending = False
            for name in osfd.listdir(state_fd):
                if name == "writer.lock":
                    continue
                if _RECOVERY_FILE.fullmatch(name) is None:
                    raise SkillPersistenceError("Unexpected skill recovery state exists")
                pending = True
            return pending
        finally:
            osfd.close(state_fd)

    def _open_recovery_directory(self) -> int:
        root_fd = osfd.open(
            self.root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        ops_fd: int | None = None
        try:
            ops_fd = self._open_or_create_directory_at(root_fd, "ops", mode=0o700)
            state_fd = self._open_or_create_directory_at(
                ops_fd,
                _RECOVERY_DIRECTORY,
                mode=0o700,
            )
            osfd.fsync(ops_fd)
            return state_fd
        except BaseException:
            if ops_fd is not None:
                osfd.close(ops_fd)
            raise
        finally:
            osfd.close(root_fd)
            if ops_fd is not None:
                with suppress(OSError):
                    osfd.close(ops_fd)

    @staticmethod
    def _open_or_create_directory_at(parent_fd: int, name: str, *, mode: int) -> int:
        try:
            before = osfd.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            osfd.mkdir(name, mode=mode, dir_fd=parent_fd)
            osfd.fsync(parent_fd)
            before = osfd.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise SkillPersistenceError("Skill authoring state directory is unsafe")
        descriptor = osfd.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            osfd.close(descriptor)
            raise SkillPersistenceError("Skill authoring state directory changed")
        return descriptor

    def _new_recovery_intent(
        self,
        *,
        operation: str,
        source_skill_id: str,
        target_skill_id: str,
        scope: str,
        idempotency_key: str,
        request: dict[str, Any],
        original_source_sha256: str | None,
        proposed_source_sha256: str,
    ) -> _RecoveryIntent:
        intent = _RecoveryIntent(
            transaction_id=secrets.token_hex(24),
            operation=operation,
            source_skill_id=source_skill_id,
            target_skill_id=target_skill_id,
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=sha256_hex(canonical_json_bytes(request)),
            original_source_sha256=original_source_sha256,
            proposed_source_sha256=proposed_source_sha256,
        )
        self._write_recovery_intent(intent)
        return intent

    def _write_recovery_intent(self, intent: _RecoveryIntent) -> None:
        self._validate_recovery_intent(intent)
        data = canonical_json_bytes(intent.payload())
        if len(data) > _RECOVERY_MAX_BYTES:  # pragma: no cover - fixed schema is bounded
            raise SkillPersistenceError("Skill recovery intent is too large")
        state_fd = self._open_recovery_directory()
        next_name = f"txn-{intent.transaction_id}.json.next"
        final_name = f"txn-{intent.transaction_id}.json"
        descriptor: int | None = None
        try:
            with suppress(FileNotFoundError):
                osfd.unlink(next_name, dir_fd=state_fd)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = osfd.open(next_name, flags, 0o600, dir_fd=state_fd)
            self._write_fd(descriptor, data)
            osfd.close(descriptor)
            descriptor = None
            osfd.replace(next_name, final_name, src_dir_fd=state_fd, dst_dir_fd=state_fd)
            osfd.fsync(state_fd)
        except OSError as error:
            raise SkillPersistenceError("Skill recovery intent could not be persisted") from error
        finally:
            if descriptor is not None:
                osfd.close(descriptor)
            osfd.close(state_fd)

    def _recover_pending_journals(self) -> int:
        state_fd = self._open_recovery_directory()
        try:
            grouped: dict[str, list[str]] = {}
            for name in osfd.listdir(state_fd):
                if name == "writer.lock":
                    continue
                matched = _RECOVERY_FILE.fullmatch(name)
                if matched is None:
                    raise SkillPersistenceError("Unexpected skill recovery state exists")
                grouped.setdefault(matched.group(1), []).append(name)
            if len(grouped) > _RECOVERY_MAX_PENDING:
                raise SkillPersistenceError("Too many pending skill recovery intents")
        finally:
            osfd.close(state_fd)
        recovered = 0
        for transaction_id, names in sorted(grouped.items()):
            intent = self._read_latest_recovery_intent(transaction_id, set(names))
            if intent is None:  # pragma: no cover - grouped always contains one journal file
                raise SkillPersistenceError("Skill recovery intent is missing")
            if self._recovery_receipt_committed(intent):
                self._finalize_recovery_intent(intent)
            else:
                self._rollback_recovery_intent(intent)
            recovered += 1
        return recovered

    def _read_latest_recovery_intent(
        self,
        transaction_id: str,
        entries: set[str] | None = None,
    ) -> _RecoveryIntent | None:
        """Read the newest complete intent, ignoring only a torn candidate update.

        ``.json.next`` is fsync'd before it replaces ``.json``.  A terminated writer can leave
        a partial candidate beside the last complete final state, so parse/schema failure of the
        candidate falls back to that final state.  Unsafe filesystem objects still fail closed.
        """

        if entries is None:
            state_fd = self._open_recovery_directory()
            try:
                entries = set(osfd.listdir(state_fd))
            finally:
                osfd.close(state_fd)
        next_name = f"txn-{transaction_id}.json.next"
        final_name = f"txn-{transaction_id}.json"
        invalid_next: _InvalidRecoveryIntent | None = None
        if next_name in entries:
            try:
                return self._read_recovery_intent(next_name)
            except _InvalidRecoveryIntent as error:
                invalid_next = error
        if final_name in entries:
            return self._read_recovery_intent(final_name)
        if invalid_next is not None:
            raise invalid_next
        return None

    def _read_recovery_intent(self, name: str) -> _RecoveryIntent:
        state_fd = self._open_recovery_directory()
        try:
            descriptor = self._open_regular_at(state_fd, name, writable=False)
            try:
                before = os.fstat(descriptor)
                if before.st_size > _RECOVERY_MAX_BYTES:
                    raise _InvalidRecoveryIntent("Skill recovery intent is invalid")
                data = self._read_fd(descriptor, before, max_bytes=_RECOVERY_MAX_BYTES)
            finally:
                osfd.close(descriptor)
        finally:
            osfd.close(state_fd)
        try:
            payload = json.loads(data.decode("utf-8", errors="strict"))
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            intent = _RecoveryIntent(
                transaction_id=payload["transaction_id"],
                operation=payload["operation"],
                source_skill_id=payload["source_skill_id"],
                target_skill_id=payload["target_skill_id"],
                scope=payload["scope"],
                idempotency_key=payload["idempotency_key"],
                request_sha256=payload["request_sha256"],
                original_source_sha256=payload["original_source_sha256"],
                proposed_source_sha256=payload["proposed_source_sha256"],
                installed_dev=payload["installed_dev"],
                installed_ino=payload["installed_ino"],
            )
            if payload.get("schema_name") != _RECOVERY_SCHEMA or payload != intent.payload():
                raise ValueError("schema mismatch")
            matched_name = _RECOVERY_FILE.fullmatch(name)
            if matched_name is None or matched_name.group(1) != intent.transaction_id:
                raise ValueError("transaction filename mismatch")
            self._validate_recovery_intent(intent)
            return intent
        except (KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise _InvalidRecoveryIntent("Skill recovery intent is invalid") from error

    @staticmethod
    def _validate_recovery_intent(intent: _RecoveryIntent) -> None:
        if (
            _RECOVERY_TXN.fullmatch(intent.transaction_id) is None
            or intent.operation not in {"save", "fork"}
            or _SKILL_ID.fullmatch(intent.source_skill_id) is None
            or _SKILL_ID.fullmatch(intent.target_skill_id) is None
            or intent.scope not in {_SAVE_SCOPE, _FORK_SCOPE}
            or (intent.operation == "save") != (intent.scope == _SAVE_SCOPE)
            or (intent.operation == "fork") != (intent.scope == _FORK_SCOPE)
            or (intent.operation == "save" and intent.source_skill_id != intent.target_skill_id)
            or not isinstance(intent.idempotency_key, str)
            or not intent.idempotency_key
            or len(intent.idempotency_key) > 256
            or "\x00" in intent.idempotency_key
            or any(character.isspace() for character in intent.idempotency_key)
            or _SHA256.fullmatch(intent.request_sha256) is None
            or _SHA256.fullmatch(intent.proposed_source_sha256) is None
            or (
                intent.original_source_sha256 is not None
                and _SHA256.fullmatch(intent.original_source_sha256) is None
            )
            or (intent.installed_dev is None) != (intent.installed_ino is None)
            or (
                intent.installed_dev is not None
                and (
                    not isinstance(intent.installed_dev, int)
                    or not isinstance(intent.installed_ino, int)
                    or intent.installed_dev < 0
                    or intent.installed_ino < 0
                )
            )
        ):
            raise SkillPersistenceError("Skill recovery intent is invalid")

    def _recovery_receipt_committed(self, intent: _RecoveryIntent) -> bool:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return False
        try:
            row = store.connection.execute(
                "SELECT receipt_json FROM idempotency_receipts "
                "WHERE scope=? AND idempotency_key=? AND request_sha256=?",
                (intent.scope, intent.idempotency_key, intent.request_sha256),
            ).fetchone()
            if row is None:
                return False
            receipt = json.loads(str(row["receipt_json"]))
            return bool(
                isinstance(receipt, dict)
                and receipt.get("operation") == intent.operation
                and receipt.get("skill_id") == intent.target_skill_id
                and receipt.get("source_sha256") == intent.proposed_source_sha256
            )
        except (json.JSONDecodeError, sqlite3.Error) as error:
            raise SkillPersistenceError("Skill recovery receipt is invalid") from error
        finally:
            store.close()

    def _finalize_recovery_intent(self, intent: _RecoveryIntent) -> None:
        intent = self._latest_recovery_intent(intent)
        if intent.operation == "save":
            self._cleanup_save_artifacts(intent)
        else:
            self._cleanup_fork_marker(intent, committed=True)
        self._delete_recovery_intent(intent.transaction_id)

    def _rollback_recovery_intent(self, intent: _RecoveryIntent) -> None:
        try:
            intent = self._latest_recovery_intent(intent)
            if intent.operation == "save":
                self._rollback_save_intent(intent)
            else:
                self._cleanup_fork_marker(intent, committed=False)
            self._delete_recovery_intent(intent.transaction_id)
        except SkillPersistenceError:
            raise
        except (OSError, SkillAuthoringError) as error:
            raise self._manual_recovery_error(intent) from error

    def _latest_recovery_intent(self, intent: _RecoveryIntent) -> _RecoveryIntent:
        latest = self._read_latest_recovery_intent(intent.transaction_id)
        if latest is None:
            return intent
        if (
            latest.transaction_id != intent.transaction_id
            or latest.operation != intent.operation
            or latest.source_skill_id != intent.source_skill_id
            or latest.target_skill_id != intent.target_skill_id
            or latest.scope != intent.scope
            or latest.idempotency_key != intent.idempotency_key
            or latest.request_sha256 != intent.request_sha256
        ):
            raise SkillPersistenceError("Skill recovery intent identity changed")
        return latest

    @staticmethod
    def _manual_recovery_error(intent: _RecoveryIntent) -> SkillPersistenceError:
        return SkillPersistenceError(
            "Skill authoring recovery was blocked by an ambiguous filesystem state",
            details={
                "skill_id": intent.target_skill_id,
                "manual_recovery_required": True,
            },
        )

    def _rollback_save_intent(self, intent: _RecoveryIntent) -> None:
        if intent.original_source_sha256 is None:
            raise self._manual_recovery_error(intent)
        skills_fd, skill_fd = self._open_skill_directory(intent.target_skill_id)
        temp_name, guard_name, displaced_name = self._save_recovery_names(intent.transaction_id)
        try:
            displaced = self._optional_recovery_file_at(
                skill_fd,
                displaced_name,
                allowed_links={1, 2},
            )
            target = self._optional_recovery_file_at(
                skill_fd,
                "SKILL.md",
                allowed_links={1, 2},
            )
            if displaced is not None:
                if target is None:
                    self._link_no_replace(skill_fd, displaced_name, "SKILL.md")
                    # Persist the restored name before removing its displaced witness.
                    osfd.fsync(skill_fd)
                    osfd.unlink(displaced_name, dir_fd=skill_fd)
                elif self._same_identity(displaced[0], target[0]):
                    osfd.unlink(displaced_name, dir_fd=skill_fd)
                else:
                    raise self._manual_recovery_error(intent)
                osfd.fsync(skill_fd)

            target = self._optional_recovery_file_at(
                skill_fd,
                "SKILL.md",
                allowed_links={1, 2},
            )
            guard = self._optional_recovery_file_at(
                skill_fd,
                guard_name,
                allowed_links={1, 2},
            )
            temp = self._optional_recovery_file_at(
                skill_fd,
                temp_name,
                allowed_links={1, 2},
            )
            guard_is_original = (
                guard is not None and sha256_hex(guard[1]) == intent.original_source_sha256
            )
            target_is_original = (
                target is not None
                and sha256_hex(target[1]) == intent.original_source_sha256
                and (guard is None or self._same_identity(target[0], guard[0]))
            )
            target_is_proposed = False
            if target is not None and sha256_hex(target[1]) == intent.proposed_source_sha256:
                target_is_proposed = (
                    temp is not None and self._same_identity(target[0], temp[0])
                ) or (
                    intent.installed_dev is not None
                    and (target[0].st_dev, target[0].st_ino)
                    == (intent.installed_dev, intent.installed_ino)
                )

            if target_is_proposed:
                if not guard_is_original:
                    raise self._manual_recovery_error(intent)
                assert target is not None
                osfd.rename(
                    "SKILL.md",
                    displaced_name,
                    src_dir_fd=skill_fd,
                    dst_dir_fd=skill_fd,
                )
                moved = self._read_recovery_file_at(
                    skill_fd,
                    displaced_name,
                    allowed_links={1, 2},
                )
                if not self._same_identity(moved[0], target[0]):
                    self._restore_displaced_no_replace(
                        skill_fd,
                        displaced_name=displaced_name,
                    )
                    raise self._manual_recovery_error(intent)
                self._link_no_replace(skill_fd, guard_name, "SKILL.md")
                osfd.unlink(displaced_name, dir_fd=skill_fd)
                osfd.fsync(skill_fd)
            elif target is None:
                if not guard_is_original:
                    raise self._manual_recovery_error(intent)
                self._link_no_replace(skill_fd, guard_name, "SKILL.md")
                osfd.fsync(skill_fd)
            elif not target_is_original and intent.installed_dev is not None:
                raise self._manual_recovery_error(intent)

            self._cleanup_named_recovery_file(
                skill_fd,
                guard_name,
                expected_sha256=intent.original_source_sha256,
            )
            self._cleanup_named_recovery_file(
                skill_fd,
                temp_name,
                expected_sha256=intent.proposed_source_sha256,
            )
            if (
                self._optional_recovery_file_at(
                    skill_fd,
                    displaced_name,
                    allowed_links={1, 2},
                )
                is not None
            ):
                raise self._manual_recovery_error(intent)
            osfd.fsync(skill_fd)
        finally:
            osfd.close(skill_fd)
            osfd.close(skills_fd)

    def _cleanup_save_artifacts(self, intent: _RecoveryIntent) -> None:
        skills_fd, skill_fd = self._open_skill_directory(intent.target_skill_id)
        temp_name, guard_name, displaced_name = self._save_recovery_names(intent.transaction_id)
        try:
            if (
                self._optional_recovery_file_at(
                    skill_fd,
                    displaced_name,
                    allowed_links={1, 2},
                )
                is not None
            ):
                raise self._manual_recovery_error(intent)
            self._cleanup_named_recovery_file(
                skill_fd,
                guard_name,
                expected_sha256=intent.original_source_sha256,
            )
            self._cleanup_named_recovery_file(
                skill_fd,
                temp_name,
                expected_sha256=intent.proposed_source_sha256,
            )
            osfd.fsync(skill_fd)
        finally:
            osfd.close(skill_fd)
            osfd.close(skills_fd)

    def _cleanup_fork_marker(self, intent: _RecoveryIntent, *, committed: bool) -> None:
        skills_fd = self._open_skills_directory()
        marker_name = self._fork_marker_name(intent.transaction_id)
        try:
            try:
                before = osfd.stat(
                    intent.target_skill_id,
                    dir_fd=skills_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if committed:
                    raise self._manual_recovery_error(intent) from None
                return
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise self._manual_recovery_error(intent)
            skill_fd = osfd.open(
                intent.target_skill_id,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=skills_fd,
            )
            try:
                opened = os.fstat(skill_fd)
                if not self._same_identity(before, opened):
                    raise self._manual_recovery_error(intent)
                marker = self._optional_recovery_file_at(
                    skill_fd,
                    marker_name,
                    allowed_links={1},
                )
                if marker is None:
                    if committed and "SKILL.md" in osfd.listdir(skill_fd):
                        return
                    raise self._manual_recovery_error(intent)
                if marker[1] != self._fork_marker_data(intent.transaction_id):
                    raise self._manual_recovery_error(intent)
                if committed:
                    osfd.unlink(marker_name, dir_fd=skill_fd)
                    osfd.fsync(skill_fd)
                    return
                entries = set(osfd.listdir(skill_fd))
                if not entries.issubset({marker_name, "SKILL.md"}):
                    raise self._manual_recovery_error(intent)
                skill = self._optional_recovery_file_at(
                    skill_fd,
                    "SKILL.md",
                    allowed_links={1},
                )
                if skill is not None:
                    if (
                        intent.installed_dev is None
                        or intent.installed_ino is None
                        or sha256_hex(skill[1]) != intent.proposed_source_sha256
                        or (skill[0].st_dev, skill[0].st_ino)
                        != (intent.installed_dev, intent.installed_ino)
                    ):
                        raise self._manual_recovery_error(intent)
                    osfd.unlink("SKILL.md", dir_fd=skill_fd)
                osfd.unlink(marker_name, dir_fd=skill_fd)
                osfd.fsync(skill_fd)
                osfd.rmdir(intent.target_skill_id, dir_fd=skills_fd)
                osfd.fsync(skills_fd)
            finally:
                osfd.close(skill_fd)
        finally:
            osfd.close(skills_fd)

    def _delete_recovery_intent(self, transaction_id: str) -> None:
        if _RECOVERY_TXN.fullmatch(transaction_id) is None:
            raise SkillPersistenceError("Skill recovery transaction ID is invalid")
        state_fd = self._open_recovery_directory()
        try:
            for suffix in (".json", ".json.next"):
                with suppress(FileNotFoundError):
                    osfd.unlink(f"txn-{transaction_id}{suffix}", dir_fd=state_fd)
            osfd.fsync(state_fd)
        finally:
            osfd.close(state_fd)

    @staticmethod
    def _save_recovery_names(transaction_id: str) -> tuple[str, str, str]:
        if _RECOVERY_TXN.fullmatch(transaction_id) is None:
            raise SkillPersistenceError("Skill recovery transaction ID is invalid")
        # RecoveryV2 is a persisted protocol.  Its witness namespace intentionally retains the
        # pre-rebrand prefix so an interrupted AgentOS writer remains recoverable after upgrade.
        prefix = f".agentos-save-{transaction_id}"
        return f"{prefix}.proposed", f"{prefix}.original", f"{prefix}.displaced"

    @staticmethod
    def _fork_marker_name(transaction_id: str) -> str:
        if _RECOVERY_TXN.fullmatch(transaction_id) is None:
            raise SkillPersistenceError("Skill recovery transaction ID is invalid")
        return f".agentos-fork-{transaction_id}.marker"

    @staticmethod
    def _fork_marker_data(transaction_id: str) -> bytes:
        return f"agentos-skill-authoring:{transaction_id}\n".encode("ascii")

    @staticmethod
    def _link_no_replace(directory_fd: int, source_name: str, target_name: str) -> None:
        osfd.link(
            source_name,
            target_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )

    def _restore_displaced_no_replace(
        self,
        directory_fd: int,
        *,
        displaced_name: str,
    ) -> None:
        try:
            self._link_no_replace(directory_fd, displaced_name, "SKILL.md")
        except FileExistsError:
            raise SkillPersistenceError(
                "Concurrent skill version was preserved; recovery needs manual review",
                details={"manual_recovery_required": True},
            ) from None
        osfd.unlink(displaced_name, dir_fd=directory_fd)
        osfd.fsync(directory_fd)

    def _optional_recovery_file_at(
        self,
        directory_fd: int,
        name: str,
        *,
        allowed_links: set[int],
    ) -> tuple[os.stat_result, bytes] | None:
        try:
            return self._read_recovery_file_at(
                directory_fd,
                name,
                allowed_links=allowed_links,
            )
        except FileNotFoundError:
            return None

    def _read_recovery_file_at(
        self,
        directory_fd: int,
        name: str,
        *,
        allowed_links: set[int],
    ) -> tuple[os.stat_result, bytes]:
        before = osfd.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink not in allowed_links
        ):
            raise SkillPersistenceError("Skill recovery witness is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = osfd.open(name, flags, dir_fd=directory_fd)
        try:
            opened = os.fstat(descriptor)
            if not self._same_identity(before, opened):
                raise SkillPersistenceError("Skill recovery witness changed")
            data = self._read_fd(
                descriptor,
                opened,
                max_bytes=self.max_content_bytes,
                allowed_links=allowed_links,
            )
            return opened, data
        finally:
            osfd.close(descriptor)

    def _cleanup_named_recovery_file(
        self,
        directory_fd: int,
        name: str,
        *,
        expected_sha256: str | None,
    ) -> None:
        observed = self._optional_recovery_file_at(
            directory_fd,
            name,
            allowed_links={1, 2},
        )
        if observed is None:
            return
        if expected_sha256 is None or sha256_hex(observed[1]) != expected_sha256:
            raise SkillPersistenceError(
                "Skill recovery witness changed",
                details={"manual_recovery_required": True},
            )
        osfd.unlink(name, dir_fd=directory_fd)

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    def _open_skills_directory(self) -> int:
        path = self.root / "skills"
        try:
            before = os.lstat(path)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise SkillPathError("Skills root is not a real directory")
            descriptor = osfd.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise SkillPathError("Skills root is missing or unsafe") from error
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            osfd.close(descriptor)
            raise SkillPathError("Skills root is not a real directory")
        return descriptor

    def _open_skill_directory(self, skill_id: str) -> tuple[int, int]:
        skills_fd = self._open_skills_directory()
        try:
            before = osfd.stat(skill_id, dir_fd=skills_fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                osfd.close(skills_fd)
                raise SkillPathError("Skill directory is missing or unsafe")
            skill_fd = osfd.open(
                skill_id,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=skills_fd,
            )
        except OSError as error:
            osfd.close(skills_fd)
            raise SkillPathError(
                "Skill directory is missing or unsafe",
                details={"skill_id": skill_id},
            ) from error
        metadata = os.fstat(skill_fd)
        if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            osfd.close(skill_fd)
            osfd.close(skills_fd)
            raise SkillPathError("Skill directory is not a real directory")
        return skills_fd, skill_fd

    def _open_regular_at(self, directory_fd: int, name: str, *, writable: bool) -> int:
        before = osfd.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SkillPathError("Skill source must be one regular, non-hard-linked file")
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = osfd.open(name, flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino)
        ):
            osfd.close(descriptor)
            raise SkillPathError("Skill source must be one regular, non-hard-linked file")
        return descriptor

    @staticmethod
    def _read_fd(
        descriptor: int,
        before: os.stat_result,
        *,
        max_bytes: int,
        allowed_links: set[int] | None = None,
    ) -> bytes:
        permitted_links = {1} if allowed_links is None else allowed_links
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > max_bytes:
                raise SkillPathError("Skill source exceeds the configured size limit")
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or before.st_nlink not in permitted_links
            or after.st_nlink not in permitted_links
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise SkillPathError("Skill source changed while it was read")
        return b"".join(chunks)

    @staticmethod
    def _write_fd(descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - os.write either writes or raises
                raise OSError("short write")
            view = view[written:]
        osfd.fsync(descriptor)

    def _read_source(self, skill_id: str) -> bytes:
        relative = self._relative_path(skill_id)
        try:
            return read_regular_file(
                self.root,
                relative,
                max_bytes=self.max_content_bytes,
            ).data
        except (OSError, PathPolicyError) as error:
            target = self.root / relative
            try:
                os.lstat(target)
            except FileNotFoundError:
                raise SkillNotFoundError(
                    "Skill does not exist",
                    details={"skill_id": skill_id},
                ) from error
            except OSError:
                pass
            raise SkillPathError(
                "Skill source is missing, linked, oversized or unsafe",
                details={"skill_id": skill_id},
            ) from error

    def _require_destination_available(
        self,
        skill_id: str,
        snapshot: CatalogSnapshot,
    ) -> None:
        if snapshot.skill(skill_id) is not None:
            raise SkillConflictError(
                "Fork destination already exists",
                details={"skill_id": skill_id, "kind": "destination_exists"},
            )
        try:
            os.lstat(self.root / "skills" / skill_id)
        except FileNotFoundError:
            return
        except OSError as error:
            raise SkillPathError(
                "Fork destination could not be checked safely",
                details={"skill_id": skill_id},
            ) from error
        raise SkillConflictError(
            "Fork destination already exists",
            details={"skill_id": skill_id, "kind": "destination_exists"},
        )

    def _suggest_fork_id(self, source_skill_id: str, snapshot: CatalogSnapshot) -> str:
        base = f"{source_skill_id[:58].rstrip('_-')}-local"
        if _SKILL_ID.fullmatch(base) is None:  # pragma: no cover - source ID already validated
            base = "local-skill"
        for index in range(1, 1001):
            suffix = "" if index == 1 else f"-{index}"
            candidate = f"{base[: 64 - len(suffix)].rstrip('_-')}{suffix}"
            try:
                self._require_destination_available(candidate, snapshot)
            except SkillConflictError:
                continue
            return candidate
        raise SkillConflictError(
            "No unique local skill ID is available",
            details={"skill_id": source_skill_id, "kind": "destination_exhausted"},
        )

    @staticmethod
    def _affected_agents(snapshot: CatalogSnapshot, skill_id: str) -> list[dict[str, str]]:
        return [
            {"agent_id": agent.agent_id, "name": agent.name}
            for agent in snapshot.agents
            if skill_id in agent.skill_ids
        ]

    @staticmethod
    def _receipt(
        store: PlatformStore,
        scope: str,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            return store.idempotent_receipt(
                scope=scope,
                idempotency_key=idempotency_key,
                request=request,
            )
        except PlatformStoreError as error:
            raise SkillIdempotencyError("Idempotency key was reused for another request") from error

    @staticmethod
    def _validate_skill_id(skill_id: str) -> str:
        if not isinstance(skill_id, str) or _SKILL_ID.fullmatch(skill_id) is None:
            raise SkillPathError(
                "Invalid skill_id",
                details={"reason": "skill_id_format"},
            )
        return skill_id

    @staticmethod
    def _validate_sha(value: str, label: str) -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise SkillValidationError(
                [
                    {
                        "field": f"expected_{label}_sha256",
                        "code": "invalid_sha256",
                        "message": "Must be a lowercase SHA-256 digest",
                    }
                ]
            )
        return value

    @staticmethod
    def _validate_token(value: str, field: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 256
            or "\x00" in value
            or any(character.isspace() for character in value)
        ):
            raise SkillValidationError(
                [
                    {
                        "field": field,
                        "code": "invalid_token",
                        "message": "Must be a non-empty token without whitespace",
                    }
                ]
            )
        return value

    @staticmethod
    def _safe_decode(data: bytes) -> str:
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SkillValidationError(
                [{"field": "content", "code": "invalid_utf8", "message": "Must be UTF-8"}]
            ) from error

    @staticmethod
    def _relative_path(skill_id: str) -> str:
        return f"skills/{skill_id}/SKILL.md"


def _diff(
    before: str,
    after: str,
    *,
    source_path: str,
    target_path: str | None = None,
) -> str:
    destination = target_path or source_path
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=source_path,
            tofile=destination,
            n=3,
        )
    )
