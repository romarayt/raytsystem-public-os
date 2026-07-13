from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from raytsystem.contracts import (
    SCHEMA_VERSION,
    BackupManifest,
    EncryptedBlob,
    RestorePlan,
    RestoreReport,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.lifecycle import BackupKind
from raytsystem.derived import assert_safe_sqlite_family
from raytsystem.documents import DocumentConfigError, DocumentMode, load_document_config
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.io import (
    UnsafeWritePath,
    ensure_safe_directory,
    ensure_safe_parent,
    write_bytes_atomic,
)
from raytsystem.platform_store import (
    PLATFORM_DB_RELATIVE,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import fsync_directory

# Export surfaces (spec section 19). Each BackupKind exposes a distinct root set:
# - PRIVATE (private_backup): full local workspace state including _raw and
#   canonical knowledge plus a platform-store snapshot; nothing is redacted.
# - PUBLIC (public_release_export): source/docs release surface; secret-bearing
#   files are removed, absolute local paths are redacted, license notices kept.
# - DIAGNOSTIC (diagnostic_export): configs, ops run manifests/reports/events,
#   and a synthesized platform status summary; no canonical knowledge
#   (_raw/normalized/ledger), no restricted data; public-grade redaction.
# - TRANSFER (workspace_transfer): the private set minus machine-local state
#   (the platform-store snapshot); absolute local paths are stripped because
#   the bundle moves between machines.
_PRIVATE_ROOTS = (
    "AGENTS.md",
    "WORK.md",
    "README.md",
    "LICENSE",
    "NOTICE",
    "config",
    "_raw",
    "normalized",
    "ledger",
    "ops/events",
    "ops/runs",
    "ops/task-ledger",
    "ops/document-revisions",
    "knowledge/manual",
    "docs",
    "website/docs",
    "packs",
    "skills",
)
_PUBLIC_ROOTS = (
    "AGENTS.md",
    "WORK.md",
    "README.md",
    "LICENSE",
    "NOTICE",
    "config",
    "docs",
    "website/docs",
    "ops/decisions",
    "packs",
    "skills",
    "src",
    "tests",
    "web/src",
    "web/public",
    "pyproject.toml",
    "uv.lock",
    "web/package.json",
    "web/package-lock.json",
)
_DIAGNOSTIC_ROOTS = (
    "config",
    "ops/events",
    "ops/runs",
)
_TRANSFER_ROOTS = _PRIVATE_ROOTS
_KIND_ROOTS: dict[BackupKind, tuple[str, ...]] = {
    BackupKind.PRIVATE: _PRIVATE_ROOTS,
    BackupKind.PUBLIC: _PUBLIC_ROOTS,
    BackupKind.DIAGNOSTIC: _DIAGNOSTIC_ROOTS,
    BackupKind.TRANSFER: _TRANSFER_ROOTS,
}
_KIND_REDACTION: dict[BackupKind, tuple[bool, bool]] = {
    BackupKind.PRIVATE: (False, False),
    BackupKind.PUBLIC: (True, True),
    BackupKind.DIAGNOSTIC: (True, True),
    BackupKind.TRANSFER: (False, True),
}
_PLATFORM_STATUS_MEMBER = "ops/platform-status.json"
_EXCLUDED_NAMES = frozenset({".env", ".DS_Store"})
_ABSOLUTE_PATH = re.compile(
    rb"(?:(?:/Users|/home|/private|/tmp|/var|/opt)/|[A-Za-z]:\\|\\\\)[^\s\"']+"
)
_MAX_BUNDLE_MEMBERS = 150_000
_MAX_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_BUNDLE_METADATA_BYTES = 64 * 1024 * 1024


def _iter_backup_paths(candidate: Path) -> Iterator[Path]:
    """Walk deterministically without materializing an unbounded recursive path list."""

    if candidate.is_symlink() or candidate.is_file():
        yield candidate
        return
    if not candidate.is_dir():
        return
    for current, directories, files in os.walk(candidate, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                yield path
            else:
                safe_directories.append(name)
        directories[:] = safe_directories
        for name in files:
            yield current_path / name


def _compact_backup_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[PurePosixPath] = []
    ordered = sorted(
        dict.fromkeys(roots),
        key=lambda item: (len(PurePosixPath(item).parts), item),
    )
    for raw in ordered:
        candidate = PurePosixPath(raw)
        if any(parent == candidate or parent in candidate.parents for parent in selected):
            continue
        selected.append(candidate)
    return tuple(item.as_posix() for item in selected)


class BackupError(RuntimeError):
    """Backup, export, or restore failed an integrity or disclosure boundary."""


class BackupService:
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

    def create(
        self,
        destination: Path,
        *,
        kind: BackupKind = BackupKind.PRIVATE,
        include_restricted_encrypted: bool = False,
    ) -> BackupManifest:
        self._require_enabled()
        if include_restricted_encrypted:
            if kind in (BackupKind.PUBLIC, BackupKind.DIAGNOSTIC):
                raise BackupError("Restricted data cannot be included in disclosure exports")
            if not self.features.enabled("restricted_encryption_enabled"):
                raise BackupError("Restricted data cannot be included without encryption")
        destination = Path(os.path.abspath(destination))
        try:
            ensure_safe_parent(destination)
        except UnsafeWritePath as error:
            raise BackupError("Backup destination is unsafe") from error
        if destination.exists() or destination.is_symlink():
            raise BackupError("Backup destination already exists")
        roots = _KIND_ROOTS[kind]
        if kind in {BackupKind.PRIVATE, BackupKind.TRANSFER}:
            roots = (*roots, *self._configured_document_roots())
        roots = _compact_backup_roots(roots)
        if include_restricted_encrypted:
            roots = (*roots, "ops/encrypted")
        redact_secrets, redact_paths = _KIND_REDACTION[kind]
        with tempfile.TemporaryDirectory(prefix="raytsystem-backup-members-") as spool_name:
            # macOS exposes its temporary root through /var -> /private/var. Resolve
            # that system alias before the no-symlink atomic-write helper validates
            # parents; member names below remain server-generated.
            spool = Path(spool_name).resolve()
            members, redactions = self._collect(
                roots,
                spool=spool,
                redact_secrets=redact_secrets,
                redact_paths=redact_paths,
            )
            if include_restricted_encrypted:
                encrypted_members = {
                    path: member
                    for path, member in members.items()
                    if path.startswith("ops/encrypted/")
                }
                if not encrypted_members:
                    raise BackupError("Restricted backup requested but no encrypted blobs exist")
                for path, member in encrypted_members.items():
                    try:
                        EncryptedBlob.model_validate_json(member.read_bytes())
                    except (OSError, ValueError) as error:
                        message = f"Restricted backup member is not encrypted: {path}"
                        raise BackupError(message) from error
            if (redact_secrets or redact_paths) and redactions.get("unresolved", []):
                raise BackupError("Export contains unresolved disclosure findings")
            collected_bytes = self._validate_spooled_members(members)
            if kind is BackupKind.PRIVATE:
                platform_member = spool / "platform.sqlite"
                remaining = max(0, _MAX_BUNDLE_BYTES - collected_bytes)
                if self._platform_snapshot_file(
                    platform_member,
                    max_bytes=min(_MAX_MEMBER_BYTES, remaining),
                ):
                    members[PLATFORM_DB_RELATIVE.as_posix()] = platform_member
            if kind is BackupKind.DIAGNOSTIC:
                status_member = spool / "platform-status.json"
                write_bytes_atomic(status_member, self._platform_status_bytes(), mode=0o600)
                members[_PLATFORM_STATUS_MEMBER] = status_member
            excluded_paths = [
                ".raytsystem/index.sqlite",
                ".raytsystem/documents.sqlite",
                "knowledge/graph.json",
            ]
            if kind is BackupKind.TRANSFER:
                excluded_paths.append(PLATFORM_DB_RELATIVE.as_posix())
            member_bytes = self._validate_spooled_members(members)
            file_hashes = {path: _sha256_file(member) for path, member in sorted(members.items())}
            redaction_report = {
                "kind": kind.value,
                "removed": redactions.get("removed", []),
                "redacted": redactions.get("redacted", []),
                "unresolved": redactions.get("unresolved", []),
            }
            redaction_hash = sha256_hex(canonical_json_bytes(redaction_report))
            values: dict[str, Any] = {
                "backup_id": "backup_pending",
                "kind": kind,
                "raytsystem_version": "0.1.0",
                "schema_versions": {
                    "contracts": SCHEMA_VERSION,
                    "platform_store": "1",
                    "bundle": "1",
                },
                "file_hashes": file_hashes,
                "excluded_paths": tuple(excluded_paths),
                "restricted_data_included": include_restricted_encrypted,
                "encrypted": include_restricted_encrypted,
                "redaction_report_sha256": redaction_hash,
                "manifest_sha256": "0" * 64,
                "created_at": datetime.now(UTC),
            }
            draft = BackupManifest.model_construct(**values)
            manifest_hash = sha256_hex(canonical_json_bytes(draft.identity_payload()))
            values["manifest_sha256"] = manifest_hash
            values["backup_id"] = derive_id(
                "backup", {"manifest_sha256": manifest_hash, "kind": kind.value}
            )
            manifest = BackupManifest.model_validate(values)
            if not manifest.verify_hash() or not manifest.verify_id():
                raise BackupError("Backup manifest identity is invalid")
            manifest_json = (
                json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            redaction_json = (json.dumps(redaction_report, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            )
            if (
                len(manifest_json) > _MAX_BUNDLE_METADATA_BYTES
                or len(redaction_json) > _MAX_BUNDLE_METADATA_BYTES
                or member_bytes + len(manifest_json) + len(redaction_json) > _MAX_BUNDLE_BYTES
            ):
                raise BackupError("Backup metadata exceeds the safe size limit")
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            published_identity: tuple[int, int] | None = None
            try:
                with zipfile.ZipFile(
                    temporary_path,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                ) as archive:
                    for path, member in sorted(members.items()):
                        with (
                            member.open("rb") as source,
                            archive.open(_zip_info(path), "w") as target,
                        ):
                            shutil.copyfileobj(source, target, length=1024 * 1024)
                    archive.writestr(_zip_info("META/manifest.json"), manifest_json)
                    archive.writestr(_zip_info("META/redaction-report.json"), redaction_json)
                with temporary_path.open("rb") as handle:
                    os.fsync(handle.fileno())
                temporary_metadata = os.lstat(temporary_path)
                published_identity = (temporary_metadata.st_dev, temporary_metadata.st_ino)
                try:
                    os.link(temporary_path, destination)
                except FileExistsError as error:
                    raise BackupError("Backup destination already exists") from error
                _require_owned(destination, published_identity)
                fsync_directory(destination.parent)
            except BaseException:
                if published_identity is not None:
                    _unlink_if_owned(destination, published_identity)
                temporary_path.unlink(missing_ok=True)
                raise
            try:
                with initialize_platform_store(self.root) as store, store.transaction():
                    store.append_record(
                        kind="backup",
                        record_id=manifest.backup_id,
                        payload={
                            "backup_id": manifest.backup_id,
                            "kind": kind.value,
                            "manifest_sha256": manifest.manifest_sha256,
                            "bundle_sha256": _sha256_file(temporary_path),
                            "created_at": manifest.created_at.isoformat(),
                        },
                        state="created",
                        expected_revision=None,
                    )
                    store.append_event(
                        stream_id=manifest.backup_id,
                        aggregate_id=manifest.backup_id,
                        event_type="backup_created",
                        actor_id="raytsystem_backup",
                        payload_schema="backup_manifest_v1",
                        payload={
                            "backup_id": manifest.backup_id,
                            "kind": kind.value,
                            "manifest_sha256": manifest.manifest_sha256,
                        },
                    )
                    if published_identity is None:
                        raise BackupError("Backup publication identity is unavailable")
                    _require_owned(destination, published_identity)
            except BaseException:
                if published_identity is not None:
                    _unlink_if_owned(destination, published_identity)
                raise
            finally:
                temporary_path.unlink(missing_ok=True)
                fsync_directory(destination.parent)
        return manifest

    def _configured_document_roots(self) -> tuple[str, ...]:
        """Include user-authorized document bytes, never the disposable projection."""

        config_path = self.root / "config" / "raytsystem.toml"
        if not config_path.exists():
            return ()
        try:
            config = load_document_config(self.root)
        except DocumentConfigError as error:
            raise BackupError("Document roots are invalid; backup refused") from error
        return tuple(
            dict.fromkeys(
                item.path for item in config.roots if item.mode is not DocumentMode.HIDDEN
            )
        )

    def verify(self, bundle: Path) -> BackupManifest:
        try:
            with zipfile.ZipFile(bundle, "r") as archive:
                names = archive.namelist()
                if (
                    len(names) > _MAX_BUNDLE_MEMBERS
                    or len(names) != len(set(names))
                    or "META/manifest.json" not in names
                    or "META/redaction-report.json" not in names
                ):
                    raise BackupError("Backup member list is invalid")
                total_size = 0
                for info in archive.infolist():
                    _validate_member(info.filename)
                    if info.is_dir() or info.file_size > _MAX_MEMBER_BYTES:
                        raise BackupError("Backup member size is invalid")
                    total_size += info.file_size
                    if total_size > _MAX_BUNDLE_BYTES:
                        raise BackupError("Backup bundle is too large")
                manifest_data = archive.read("META/manifest.json")
                if len(manifest_data) > _MAX_BUNDLE_METADATA_BYTES:
                    raise BackupError("Backup manifest is too large")
                manifest = BackupManifest.model_validate_json(manifest_data)
                if not manifest.verify_hash() or not manifest.verify_id():
                    raise BackupError("Backup manifest hash is invalid")
                redaction_data = archive.read("META/redaction-report.json")
                if len(redaction_data) > _MAX_BUNDLE_METADATA_BYTES:
                    raise BackupError("Backup redaction report is too large")
                redaction_report = json.loads(redaction_data)
                if (
                    not isinstance(redaction_report, dict)
                    or manifest.redaction_report_sha256 is None
                    or sha256_hex(canonical_json_bytes(redaction_report))
                    != manifest.redaction_report_sha256
                ):
                    raise BackupError("Backup redaction report hash is invalid")
                expected = set(manifest.file_hashes) | {
                    "META/manifest.json",
                    "META/redaction-report.json",
                }
                if set(names) != expected:
                    raise BackupError("Backup members do not match the manifest")
                for name, expected_hash in manifest.file_hashes.items():
                    data = archive.read(name)
                    if sha256_hex(data) != expected_hash:
                        raise BackupError(f"Backup member hash failed: {name}")
                return manifest
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise BackupError("Backup bundle is unreadable") from error

    def restore_plan(self, bundle: Path, destination: Path) -> RestorePlan:
        self._require_enabled()
        manifest = self.verify(bundle)
        conflicts: list[str] = []
        if destination.exists():
            conflicts = [
                path.relative_to(destination).as_posix() for path in destination.rglob("*")
            ]
        backup_contracts = manifest.schema_versions.get("contracts", "0")
        if backup_contracts == SCHEMA_VERSION:
            compatibility: Literal["compatible", "migration_required", "incompatible"] = (
                "compatible"
            )
        elif backup_contracts.split(".")[0] == SCHEMA_VERSION.split(".")[0]:
            compatibility = "migration_required"
        else:
            compatibility = "incompatible"
        return RestorePlan(
            restore_plan_id=derive_id(
                "restore",
                {
                    "backup_id": manifest.backup_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "destination_empty": not conflicts,
                },
            ),
            backup_id=manifest.backup_id,
            manifest_sha256=manifest.manifest_sha256,
            compatibility=compatibility,
            files_to_restore=tuple(sorted(manifest.file_hashes)),
            conflicts=tuple(sorted(conflicts)),
            rebuild_projections=True,
            dry_run=True,
            overwrite_existing=False,
        )

    def restore(
        self,
        bundle: Path,
        destination: Path,
        *,
        doctor: Callable[[Path], bool] | None = None,
    ) -> RestoreReport:
        self._require_enabled()
        if destination.is_symlink():
            raise BackupError("Restore destination cannot be a symlink")
        destination = destination.resolve()
        plan = self.restore_plan(bundle, destination)
        if plan.compatibility == "incompatible":
            raise BackupError("Backup schema is incompatible")
        if plan.conflicts or (destination.exists() and any(destination.iterdir())):
            raise BackupError("Restore requires an empty destination")
        ensure_safe_directory(destination.parent, mode=0o700)
        restored: dict[str, str] = {}
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.restore.", dir=destination.parent)
        )
        try:
            manifest = self.verify(bundle)
            with zipfile.ZipFile(bundle, "r") as archive:
                for relative in plan.files_to_restore:
                    _validate_member(relative)
                    data = archive.read(relative)
                    expected = manifest.file_hashes[relative]
                    if sha256_hex(data) != expected:
                        raise BackupError("Restore member hash changed")
                    target = staging.joinpath(*PurePosixPath(relative).parts)
                    if target.exists() or target.is_symlink():
                        raise BackupError("Restore refuses to overwrite an existing path")
                    write_bytes_atomic(target, data, mode=0o600)
                    restored[relative] = expected
            if destination.exists():
                destination.rmdir()
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if doctor is None:
            doctor_state: Literal["not_run", "passed", "failed"] = "not_run"
        else:
            try:
                doctor_state = "passed" if doctor(destination) else "failed"
            except Exception:  # doctor failures must not be reported as health
                doctor_state = "failed"
        report_payload = {
            "restore_plan_id": plan.restore_plan_id,
            "restored_hashes": restored,
            "projection_rebuild_required": True,
            "doctor_state": doctor_state,
            "doctor_passed": doctor_state == "passed",
        }
        report_hash = sha256_hex(canonical_json_bytes(report_payload))
        return RestoreReport(
            restore_report_id=derive_id(
                "rreport", {"restore_plan_id": plan.restore_plan_id, "report_sha256": report_hash}
            ),
            restore_plan_id=plan.restore_plan_id,
            state="restored",
            restored_hashes=restored,
            projection_rebuild_required=True,
            doctor_passed=doctor_state == "passed",
            doctor_state=doctor_state,
            report_sha256=report_hash,
            completed_at=datetime.now(UTC),
        )

    def leak_scan(self, bundle: Path) -> dict[str, Any]:
        manifest = self.verify(bundle)
        findings: list[dict[str, str]] = []
        with zipfile.ZipFile(bundle, "r") as archive:
            for name in manifest.file_hashes:
                data = archive.read(name)
                if self.scanner.scan(data, path=name).blocks_processing:
                    findings.append({"path": name, "code": "restricted_content"})
                if _ABSOLUTE_PATH.search(data):
                    findings.append({"path": name, "code": "absolute_path"})
        return {
            "backup_id": manifest.backup_id,
            "kind": manifest.kind.value,
            "passed": not findings,
            "findings": findings,
        }

    def _collect(
        self,
        roots: tuple[str, ...],
        *,
        spool: Path,
        redact_secrets: bool,
        redact_paths: bool,
    ) -> tuple[dict[str, Path], dict[str, list[str]]]:
        members: dict[str, Path] = {}
        report: dict[str, list[str]] = {"removed": [], "redacted": [], "unresolved": []}
        visited: set[str] = set()
        total_bytes = 0
        for relative_root in roots:
            candidate = self.root / relative_root
            for path in _iter_backup_paths(candidate):
                try:
                    relative = path.relative_to(self.root).as_posix()
                except ValueError as error:
                    raise BackupError("Backup source escaped the workspace") from error
                if relative in visited:
                    continue
                visited.add(relative)
                if len(visited) + 2 > _MAX_BUNDLE_MEMBERS:
                    raise BackupError("Backup contains too many source members")
                if path.is_symlink() or not path.is_file():
                    if path.is_symlink():
                        report["removed"].append(relative)
                    continue
                if path.name in _EXCLUDED_NAMES or path.name.startswith(".env"):
                    report["removed"].append(relative)
                    continue
                if any(part in {"__pycache__", "node_modules", ".git"} for part in path.parts):
                    continue
                try:
                    data = read_regular_file(self.root, relative, max_bytes=32 * 1024 * 1024).data
                except (OSError, PathPolicyError) as error:
                    raise BackupError(f"Unsafe backup source: {relative}") from error
                if redact_secrets and self.scanner.scan(data, path=relative).blocks_processing:
                    report["removed"].append(relative)
                    continue
                if redact_paths:
                    sanitized = _ABSOLUTE_PATH.sub(b"[LOCAL_PATH_REDACTED]", data)
                    if sanitized != data:
                        report["redacted"].append(relative)
                    data = sanitized
                    unresolved = _ABSOLUTE_PATH.search(data) is not None or (
                        redact_secrets and self.scanner.scan(data, path=relative).blocks_processing
                    )
                    if unresolved:
                        report["unresolved"].append(relative)
                        continue
                total_bytes += len(data)
                if total_bytes > _MAX_BUNDLE_BYTES:
                    raise BackupError("Backup source bytes exceed the safe bundle limit")
                staged = spool / f"member-{len(members):08d}"
                write_bytes_atomic(staged, data, mode=0o600)
                members[relative] = staged
        return members, report

    @staticmethod
    def _validate_spooled_members(members: dict[str, Path]) -> int:
        if len(members) + 2 > _MAX_BUNDLE_MEMBERS:
            raise BackupError("Backup contains too many members")
        total = 0
        for relative, member in members.items():
            _validate_member(relative)
            try:
                metadata = member.stat()
            except OSError as error:
                raise BackupError("Backup staging member is unavailable") from error
            if not member.is_file() or member.is_symlink() or metadata.st_size > _MAX_MEMBER_BYTES:
                raise BackupError("Backup member size is invalid")
            total += metadata.st_size
            if total > _MAX_BUNDLE_BYTES:
                raise BackupError("Backup source bytes exceed the safe bundle limit")
        return total

    def _platform_snapshot_file(self, snapshot_path: Path, *, max_bytes: int) -> bool:
        source_path = self.root / PLATFORM_DB_RELATIVE
        if not source_path.exists() and not source_path.is_symlink():
            return False
        try:
            assert_safe_sqlite_family(source_path)
            source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            try:
                destination = sqlite3.connect(snapshot_path)
            except BaseException:
                source.close()
                raise
            try:
                source.execute("PRAGMA query_only=ON")
                source.execute("PRAGMA trusted_schema=OFF")
                page_count = int(source.execute("PRAGMA page_count").fetchone()[0])
                page_size = int(source.execute("PRAGMA page_size").fetchone()[0])
                if max_bytes <= 0 or page_count * page_size > max_bytes:
                    raise BackupError("Platform-store snapshot exceeds the backup member limit")

                def progress(_status: int, _remaining: int, total: int) -> None:
                    if total * page_size > max_bytes:
                        raise BackupError(
                            "Platform-store snapshot grew beyond the backup member limit"
                        )
                    try:
                        if snapshot_path.exists() and snapshot_path.stat().st_size > max_bytes:
                            raise BackupError(
                                "Platform-store snapshot grew beyond the backup member limit"
                            )
                    except OSError as error:
                        raise BackupError("Platform-store snapshot became unavailable") from error

                source.backup(destination, pages=256, progress=progress, sleep=0.0)
            finally:
                destination.close()
                source.close()
            metadata = os.lstat(snapshot_path)
            if metadata.st_size > max_bytes:
                raise BackupError("Platform-store snapshot exceeds the backup member limit")
            os.chmod(snapshot_path, 0o600)
            return True
        except (OSError, sqlite3.Error, UnsafeWritePath) as error:
            raise BackupError("Platform-store snapshot is unsafe or unavailable") from error
        finally:
            try:
                if snapshot_path.exists() and snapshot_path.stat().st_size > max_bytes:
                    snapshot_path.unlink(missing_ok=True)
            except OSError:
                snapshot_path.unlink(missing_ok=True)

    def _platform_status_bytes(self) -> bytes:
        snapshot_id: str | None = None
        store = open_platform_store_read_only(self.root)
        if store is not None:
            with store:
                snapshot_id = store.snapshot_id()
        status = {
            "features": self.features.to_public_dict(),
            "platform_store_snapshot_id": snapshot_id,
        }
        return json.dumps(status, sort_keys=True, indent=2).encode("utf-8") + b"\n"

    def _require_enabled(self) -> None:
        if not self.features.enabled("backup_enabled"):
            raise BackupError("Backup and export are disabled")


def _validate_member(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise BackupError("Backup contains a path traversal member")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise BackupError("Backup member changed while hashing") from error
    return digest.hexdigest()


def _require_owned(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise BackupError("Backup destination changed during publication") from error
    if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise BackupError("Backup destination changed during publication")


def _unlink_if_owned(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = os.lstat(path)
        if stat.S_ISREG(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == identity:
            path.unlink()
            fsync_directory(path.parent)
    except OSError:
        return
