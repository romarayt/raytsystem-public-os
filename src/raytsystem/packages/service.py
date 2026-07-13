from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    PackageManifest,
    PackageRevision,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.lifecycle import PackageLifecycleState
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner

_PINNED_VERSION = re.compile(r"^(?:[0-9]+\.[0-9]+\.[0-9]+|[0-9a-f]{40,64})$")
_MAX_FILES = 2_000
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_DISCOVERY_PREFIX = "pkgdisc_"
_ROLLBACK_SOURCE_STATES = frozenset(
    {
        PackageLifecycleState.SUPERSEDED,
        PackageLifecycleState.INSTALLED,
        PackageLifecycleState.ROLLED_BACK,
    }
)


class PackageLifecycleError(RuntimeError):
    """A package lifecycle transition violates quarantine, integrity, or approval policy."""


class PackageLifecycleService:
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

    def discover(self, source_relative: str) -> dict[str, Any]:
        self._require_enabled()
        self._source_directory(source_relative)
        discovery_id = derive_id("pkgdisc", {"source_relative": source_relative})
        payload: dict[str, Any] = {
            "discovery_id": discovery_id,
            "source_relative": source_relative,
            "state": PackageLifecycleState.DISCOVERED.value,
        }
        with initialize_platform_store(self.root) as store:
            existing = store.head("package_discovery", discovery_id)
            if existing is not None:
                return dict(existing.payload)
            store.append_record(
                kind="package_discovery",
                record_id=discovery_id,
                payload=payload,
                state=PackageLifecycleState.DISCOVERED.value,
                expected_revision=None,
            )
            store.append_event(
                stream_id=discovery_id,
                aggregate_id=discovery_id,
                event_type="package_discovered",
                actor_id="raytsystem_package_inspector",
                payload_schema="package_discovery_v1",
                payload=payload,
            )
        return payload

    def inspect(self, source: str) -> tuple[PackageManifest, PackageRevision]:
        self._require_enabled()
        return self._ingest(source, as_update=False)

    def update(self, source: str) -> tuple[PackageManifest, PackageRevision]:
        self._require_enabled()
        manifest, revision = self._ingest(source, as_update=True)
        return manifest, self.validate(revision.revision_id)

    def _ingest(self, source: str, *, as_update: bool) -> tuple[PackageManifest, PackageRevision]:
        source_relative, discovery_id = self._resolve_source(source)
        source_dir = self._source_directory(source_relative)
        files, content_sha256 = self._scan_tree(source_dir)
        manifest_path = source_dir / "package.yaml"
        if manifest_path not in files:
            raise PackageLifecycleError("Package manifest is missing")
        try:
            raw = yaml.safe_load(manifest_path.read_bytes())
        except (OSError, yaml.YAMLError) as error:
            raise PackageLifecycleError("Package manifest is invalid") from error
        if not isinstance(raw, dict):
            raise PackageLifecycleError("Package manifest must be a mapping")
        raw = dict(raw)
        raw["content_sha256"] = content_sha256
        try:
            manifest = PackageManifest.model_validate(raw)
        except ValidationError as error:
            raise PackageLifecycleError("Package manifest contract validation failed") from error
        manifest_sha256 = sha256_hex(canonical_json_bytes(manifest.model_dump(mode="json")))
        with initialize_platform_store(self.root) as store:
            previous_revision_id: str | None = None
            if as_update:
                active = store.head("package_active", manifest.package_id)
                if active is None:
                    raise PackageLifecycleError("Package update requires an active revision")
                previous_revision_id = str(active.payload["revision_id"])
            identity: dict[str, str] = {
                "package_id": manifest.package_id,
                "manifest_sha256": manifest_sha256,
                "content_sha256": content_sha256,
            }
            if previous_revision_id is not None:
                identity["previous_revision_id"] = previous_revision_id
            revision_id = derive_id("pkgrev", identity)
            revision = PackageRevision(
                revision_id=revision_id,
                package_id=manifest.package_id,
                manifest_sha256=manifest_sha256,
                content_sha256=content_sha256,
                state=PackageLifecycleState.QUARANTINED,
                previous_revision_id=previous_revision_id,
                created_at=datetime.now(UTC),
            )
            if store.head("package_revision", revision_id) is None:
                store.append_record(
                    kind="package_manifest",
                    record_id=revision_id,
                    payload=manifest.model_dump(mode="json")
                    | {"source_relative": source_relative, "file_count": len(files)},
                    state="quarantined",
                    expected_revision=None,
                )
                store.append_record(
                    kind="package_revision",
                    record_id=revision_id,
                    payload=revision.model_dump(mode="json"),
                    state=revision.state.value,
                    expected_revision=None,
                )
                store.append_event(
                    stream_id=manifest.package_id,
                    aggregate_id=revision_id,
                    event_type="package_inspected",
                    actor_id="raytsystem_package_inspector",
                    payload_schema="package_revision_v1",
                    payload={
                        "revision_id": revision_id,
                        "content_sha256": content_sha256,
                        "state": "quarantined",
                    },
                )
            if discovery_id is not None:
                discovery = store.head("package_discovery", discovery_id)
                if discovery is not None and discovery.payload.get("state") != "inspected":
                    store.append_record(
                        kind="package_discovery",
                        record_id=discovery_id,
                        payload=dict(discovery.payload)
                        | {"state": "inspected", "revision_id": revision_id},
                        state="inspected",
                        expected_revision=discovery.revision,
                    )
        return manifest, revision

    def validate(self, revision_id: str) -> PackageRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior = store.head("package_revision", revision_id)
            manifest_record = store.head("package_manifest", revision_id)
            if prior is None or manifest_record is None:
                raise PackageLifecycleError("Package revision does not exist")
            revision = _clean_revision(prior.payload)
            manifest = _manifest_from_record(manifest_record.payload)
            failures: list[str] = []
            # Integrity self-hash is not a signature: authenticity is never established here.
            signature_verified = False
            if (
                manifest.signature is not None
                and manifest.signature != f"sha256:{manifest.content_sha256}"
            ):
                failures.append("invalid_package_signature")
            if any(
                _PINNED_VERSION.fullmatch(value) is None for value in manifest.dependencies.values()
            ):
                failures.append("dependency_not_pinned")
            if any(
                store.head("package_active", dependency_id) is None
                for dependency_id in manifest.dependencies
            ):
                failures.append("dependency_unresolved")
            if any(_unsafe_reference_path(value) for value in _manifest_references(manifest)):
                failures.append("unsafe_reference_path")
            if "self_modify" in manifest.permissions:
                failures.append("self_modifying_skill")
            state = PackageLifecycleState.BLOCKED if failures else PackageLifecycleState.VALIDATED
            report = {
                "revision_id": revision_id,
                "failures": failures,
                "file_count": manifest_record.payload["file_count"],
                "content_sha256": manifest.content_sha256,
                "signature_verified": signature_verified,
            }
            validated = revision.model_copy(
                update={
                    "state": state,
                    "validation_report_sha256": sha256_hex(canonical_json_bytes(report)),
                }
            )
            store.append_record(
                kind="package_revision",
                record_id=revision_id,
                payload=validated.model_dump(mode="json")
                | {"validation_failures": failures, "signature_verified": signature_verified},
                state=state.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=revision.package_id,
                aggregate_id=revision_id,
                event_type="package_validated",
                actor_id="raytsystem_package_validator",
                payload_schema="package_revision_v1",
                payload={"revision_id": revision_id, "state": state.value, "failures": failures},
            )
            if failures:
                raise PackageLifecycleError("Package validation failed: " + ",".join(failures))
            return validated

    def approve(
        self,
        revision_id: str,
        *,
        actor_id: str,
        approval_id: str,
        eval_run_ids: tuple[str, ...],
    ) -> PackageRevision:
        self._require_enabled()
        if not approval_id:
            raise PackageLifecycleError("Package approval is required")
        with initialize_platform_store(self.root) as store:
            prior = store.head("package_revision", revision_id)
            manifest_record = store.head("package_manifest", revision_id)
            if prior is None or manifest_record is None:
                raise PackageLifecycleError("Package revision does not exist")
            revision = _clean_revision(prior.payload)
            if revision.state is not PackageLifecycleState.VALIDATED:
                raise PackageLifecycleError("Only validated packages may be approved")
            manifest = _manifest_from_record(manifest_record.payload)
            covered_suites = self._verified_eval_suites(store, eval_run_ids)
            required_suites = set(manifest.eval_suite_ids)
            if revision.previous_revision_id is not None:
                previous_manifest = store.head("package_manifest", revision.previous_revision_id)
                if previous_manifest is None:
                    raise PackageLifecycleError("Previous package revision manifest is missing")
                required_suites |= set(
                    _manifest_from_record(previous_manifest.payload).eval_suite_ids
                )
            if not required_suites.issubset(covered_suites):
                raise PackageLifecycleError(
                    "Package approval does not cover the required eval suites"
                )
            signature_verified = prior.payload.get("signature_verified") is True
            required_scope = {"package_activation"}
            if not signature_verified:
                required_scope.add("unsigned_pack")
            try:
                AuthorityResolver(self.root).require_approval(
                    approval_id,
                    action="activate_package",
                    target_id=revision_id,
                    artifact_sha256=revision.content_sha256,
                    required_scope=frozenset(required_scope),
                )
            except AuthorityError as error:
                raise PackageLifecycleError("Package approval authority is invalid") from error
            approved = revision.model_copy(
                update={
                    "state": PackageLifecycleState.APPROVED,
                    "eval_run_ids": eval_run_ids,
                    "approval_id": approval_id,
                    "activated_by": actor_id,
                }
            )
            store.append_record(
                kind="package_revision",
                record_id=revision_id,
                payload=approved.model_dump(mode="json")
                | {"signature_verified": signature_verified},
                state=approved.state.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=revision.package_id,
                aggregate_id=revision_id,
                event_type="package_approved",
                actor_id=actor_id,
                payload_schema="package_revision_v1",
                payload={"revision_id": revision_id, "approval_id": approval_id},
            )
            return approved

    def install(self, revision_id: str) -> PackageRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior = store.head("package_revision", revision_id)
            manifest_record = store.head("package_manifest", revision_id)
            if prior is None or manifest_record is None:
                raise PackageLifecycleError("Package revision does not exist")
            revision = _clean_revision(prior.payload)
            if revision.state is not PackageLifecycleState.APPROVED:
                raise PackageLifecycleError("Package must be approved before installation")
            source = self._source_directory(str(manifest_record.payload["source_relative"]))
            _files, observed_hash = self._scan_tree(source)
            if observed_hash != revision.content_sha256:
                raise PackageLifecycleError("Package changed after approval")
            staging = self.root / "ops" / "staging" / "packages" / revision_id
            installed = self._installed_directory(revision_id)
            if installed.exists():
                if not installed.is_dir() or self._scan_tree(installed)[1] != observed_hash:
                    raise PackageLifecycleError("Installed package path is inconsistent")
            else:
                staging.mkdir(parents=True, exist_ok=False, mode=0o700)
                self._copy_tree(source, staging)
                if self._scan_tree(staging)[1] != observed_hash:
                    raise PackageLifecycleError("Staged package hash mismatch")
                installed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(staging, installed)
            installed_revision = revision.model_copy(
                update={"state": PackageLifecycleState.INSTALLED}
            )
            store.append_record(
                kind="package_revision",
                record_id=revision_id,
                payload=installed_revision.model_dump(mode="json")
                | _signature_marker(prior.payload),
                state=installed_revision.state.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=revision.package_id,
                aggregate_id=revision_id,
                event_type="package_installed",
                actor_id="raytsystem_package_installer",
                payload_schema="package_revision_v1",
                payload={"revision_id": revision_id, "content_sha256": observed_hash},
            )
            return installed_revision

    def activate(self, revision_id: str, *, actor_id: str, approval_id: str) -> PackageRevision:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior = store.head("package_revision", revision_id)
            if prior is None:
                raise PackageLifecycleError("Package revision does not exist")
            revision = _clean_revision(prior.payload)
            if revision.state is not PackageLifecycleState.INSTALLED:
                raise PackageLifecycleError("Only installed packages may be activated")
            if revision.approval_id != approval_id:
                raise PackageLifecycleError(
                    "Activation approval does not match the approved revision"
                )
            installed = self._installed_directory(revision_id)
            if not installed.is_dir() or self._scan_tree(installed)[1] != revision.content_sha256:
                raise PackageLifecycleError("Installed package content hash does not verify")
            prior_active = store.head("package_active", revision.package_id)
            if prior_active is not None:
                self._supersede(store, str(prior_active.payload.get("revision_id", "")), revision)
            active = revision.model_copy(
                update={"state": PackageLifecycleState.ACTIVE, "activated_by": actor_id}
            )
            store.append_record(
                kind="package_revision",
                record_id=revision_id,
                payload=active.model_dump(mode="json") | _signature_marker(prior.payload),
                state=active.state.value,
                expected_revision=prior.revision,
            )
            store.append_record(
                kind="package_active",
                record_id=revision.package_id,
                payload={
                    "package_id": revision.package_id,
                    "revision_id": revision_id,
                    "content_sha256": revision.content_sha256,
                    "activated_by": actor_id,
                    "approval_id": approval_id,
                },
                state="active",
                expected_revision=None if prior_active is None else prior_active.revision,
            )
            store.append_event(
                stream_id=revision.package_id,
                aggregate_id=revision_id,
                event_type="package_activated",
                actor_id=actor_id,
                payload_schema="package_revision_v1",
                payload={"revision_id": revision_id, "content_sha256": revision.content_sha256},
            )
            return active

    def rollback(
        self,
        package_id: str,
        to_revision_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> PackageRevision:
        self._require_enabled()
        if not reason.strip():
            raise PackageLifecycleError("Package rollback requires an explicit reason")
        with initialize_platform_store(self.root) as store:
            active_head = store.head("package_active", package_id)
            if active_head is None:
                raise PackageLifecycleError("Package has no active revision to roll back")
            current_revision_id = str(active_head.payload.get("revision_id", ""))
            if current_revision_id == to_revision_id:
                raise PackageLifecycleError("Package is already at the requested revision")
            target_prior = store.head("package_revision", to_revision_id)
            if target_prior is None:
                raise PackageLifecycleError("Package revision does not exist")
            target = _clean_revision(target_prior.payload)
            if target.package_id != package_id:
                raise PackageLifecycleError("Rollback target belongs to another package")
            if target.state not in _ROLLBACK_SOURCE_STATES or target.approval_id is None:
                raise PackageLifecycleError(
                    "Rollback target was never approved and installed for this package"
                )
            installed = self._installed_directory(to_revision_id)
            if not installed.is_dir() or self._scan_tree(installed)[1] != target.content_sha256:
                raise PackageLifecycleError("Rollback target content hash does not verify")
            current_prior = store.head("package_revision", current_revision_id)
            if current_prior is not None:
                rolled_back = _clean_revision(current_prior.payload).model_copy(
                    update={"state": PackageLifecycleState.ROLLED_BACK}
                )
                store.append_record(
                    kind="package_revision",
                    record_id=current_revision_id,
                    payload=rolled_back.model_dump(mode="json")
                    | _signature_marker(current_prior.payload),
                    state=rolled_back.state.value,
                    expected_revision=current_prior.revision,
                )
            restored = target.model_copy(
                update={"state": PackageLifecycleState.ACTIVE, "activated_by": actor_id}
            )
            store.append_record(
                kind="package_revision",
                record_id=to_revision_id,
                payload=restored.model_dump(mode="json") | _signature_marker(target_prior.payload),
                state=restored.state.value,
                expected_revision=target_prior.revision,
            )
            store.append_record(
                kind="package_active",
                record_id=package_id,
                payload={
                    "package_id": package_id,
                    "revision_id": to_revision_id,
                    "content_sha256": target.content_sha256,
                    "activated_by": actor_id,
                    "approval_id": target.approval_id,
                    "rolled_back_from": current_revision_id,
                    "rollback_reason": reason,
                },
                state="active",
                expected_revision=active_head.revision,
            )
            store.append_event(
                stream_id=package_id,
                aggregate_id=to_revision_id,
                event_type="package_rolled_back",
                actor_id=actor_id,
                payload_schema="package_revision_v1",
                payload={
                    "revision_id": to_revision_id,
                    "rolled_back_from": current_revision_id,
                    "content_sha256": target.content_sha256,
                    "reason": reason,
                },
            )
            return restored

    def snapshot(self) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {"snapshot_id": "pview_unavailable", "state": "unavailable", "packages": []}
        with store:
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready" if self.features.enabled("pack_lifecycle_enabled") else "disabled",
                "packages": [
                    record.payload for record in store.list_heads("package_revision", limit=200)
                ],
                "active": [
                    record.payload for record in store.list_heads("package_active", limit=200)
                ],
            }

    def _supersede(
        self,
        store: PlatformStore,
        previous_revision_id: str,
        incoming: PackageRevision,
    ) -> None:
        if not previous_revision_id or previous_revision_id == incoming.revision_id:
            return
        previous_head = store.head("package_revision", previous_revision_id)
        if previous_head is None or previous_head.state != PackageLifecycleState.ACTIVE.value:
            return
        superseded = _clean_revision(previous_head.payload).model_copy(
            update={"state": PackageLifecycleState.SUPERSEDED}
        )
        store.append_record(
            kind="package_revision",
            record_id=previous_revision_id,
            payload=superseded.model_dump(mode="json") | _signature_marker(previous_head.payload),
            state=superseded.state.value,
            expected_revision=previous_head.revision,
        )
        store.append_event(
            stream_id=incoming.package_id,
            aggregate_id=previous_revision_id,
            event_type="package_superseded",
            actor_id="raytsystem_package_installer",
            payload_schema="package_revision_v1",
            payload={
                "revision_id": previous_revision_id,
                "superseded_by": incoming.revision_id,
            },
        )

    @staticmethod
    def _verified_eval_suites(store: PlatformStore, eval_run_ids: tuple[str, ...]) -> set[str]:
        covered: set[str] = set()
        for eval_run_id in eval_run_ids:
            record = store.head("eval_run", eval_run_id)
            if record is None:
                raise PackageLifecycleError("Package approval references an unknown eval run")
            if record.state != "passed" or record.payload.get("state") != "passed":
                raise PackageLifecycleError("Package approval references a failed eval run")
            suite_id = record.payload.get("suite_id")
            if not isinstance(suite_id, str) or not suite_id:
                raise PackageLifecycleError("Package approval references an invalid eval run")
            covered.add(suite_id)
        return covered

    def _resolve_source(self, source: str) -> tuple[str, str | None]:
        if not source.startswith(_DISCOVERY_PREFIX):
            return source, None
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise PackageLifecycleError("Package discovery record is unavailable")
        with store:
            record = store.head("package_discovery", source)
        if record is None:
            raise PackageLifecycleError("Package discovery record is unavailable")
        relative = record.payload.get("source_relative")
        if not isinstance(relative, str) or not relative:
            raise PackageLifecycleError("Package discovery record is unavailable")
        return relative, source

    def _installed_directory(self, revision_id: str) -> Path:
        return self.root / ".raytsystem" / "packages" / revision_id

    def _source_directory(self, relative: str) -> Path:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise PackageLifecycleError("Package source must be workspace-relative")
        candidate = self.root.joinpath(*path.parts)
        current = self.root
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise PackageLifecycleError("Package source cannot cross a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise PackageLifecycleError("Package source does not exist") from error
        if not resolved.is_relative_to(self.root) or not resolved.is_dir() or resolved.is_symlink():
            raise PackageLifecycleError("Package source escapes the workspace")
        return resolved

    def _scan_tree(self, source: Path) -> tuple[tuple[Path, ...], str]:
        files: list[Path] = []
        total = 0
        hashes: list[dict[str, str]] = []
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise PackageLifecycleError("Package symlinks are forbidden")
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if len(files) >= _MAX_FILES:
                raise PackageLifecycleError("Package contains too many files")
            try:
                data = read_regular_file(source, relative, max_bytes=_MAX_FILE_BYTES).data
            except (OSError, PathPolicyError) as error:
                raise PackageLifecycleError("Package file is unsafe") from error
            total += len(data)
            if total > _MAX_TOTAL_BYTES:
                raise PackageLifecycleError("Package is too large")
            if self.scanner.scan(data, path=relative).blocks_processing:
                raise PackageLifecycleError("Package contains restricted content")
            files.append(path)
            hashes.append({"path": relative, "sha256": sha256_hex(_hash_material(relative, data))})
        if not files:
            raise PackageLifecycleError("Package is empty")
        return tuple(files), sha256_hex(canonical_json_bytes(hashes))

    @staticmethod
    def _copy_tree(source: Path, destination: Path) -> None:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            target = destination / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with path.open("rb") as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                target.chmod(0o600)

    def _require_enabled(self) -> None:
        if not self.features.enabled("pack_lifecycle_enabled"):
            raise PackageLifecycleError("Package lifecycle is disabled")


def _clean_revision(payload: dict[str, Any]) -> PackageRevision:
    clean = {
        key: value
        for key, value in payload.items()
        if key not in {"validation_failures", "signature_verified"}
    }
    try:
        return PackageRevision.model_validate(clean)
    except ValidationError as error:
        raise PackageLifecycleError("Package revision is invalid") from error


def _manifest_from_record(payload: dict[str, Any]) -> PackageManifest:
    clean = {
        key: value for key, value in payload.items() if key not in {"source_relative", "file_count"}
    }
    try:
        return PackageManifest.model_validate(clean)
    except ValidationError as error:
        raise PackageLifecycleError("Package manifest record is invalid") from error


def _signature_marker(payload: dict[str, Any]) -> dict[str, Any]:
    return {"signature_verified": payload.get("signature_verified") is True}


def _manifest_references(manifest: PackageManifest) -> tuple[str, ...]:
    return (
        *manifest.dependencies,
        *manifest.permissions,
        *manifest.runtime_requirements,
        *manifest.tool_ids,
        *manifest.skill_ids,
        *manifest.agent_ids,
        *manifest.workflow_ids,
        *manifest.template_ids,
        *manifest.fixture_ids,
        *manifest.eval_suite_ids,
    )


def _unsafe_reference_path(value: str) -> bool:
    if value.startswith(("/", "~")) or "\\" in value:
        return True
    parts = PurePosixPath(value).parts
    return ".." in parts


def _hash_material(relative: str, data: bytes) -> bytes:
    """Avoid a circular package signature while binding every executable byte."""

    if relative != "package.yaml":
        return data
    try:
        payload = yaml.safe_load(data)
    except yaml.YAMLError:
        return data
    if not isinstance(payload, dict):
        return data
    normalized = dict(payload)
    normalized.pop("content_sha256", None)
    normalized.pop("signature", None)
    return canonical_json_bytes(normalized)
