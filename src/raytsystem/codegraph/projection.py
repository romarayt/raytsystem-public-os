from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from raytsystem.codegraph.cluster import cluster_graph
from raytsystem.codegraph.contracts import (
    CodeFileEntry,
    CodeGraphManifest,
    CodeGraphMetrics,
    CodeGraphSnapshot,
    CodeGraphState,
    CodeGraphStatus,
    CodeNodeKind,
    EdgeConfidence,
)
from raytsystem.codegraph.detect import (
    CodeGraphConfig,
    DetectedFile,
    candidate_paths,
    detect_files,
    load_code_graph_config,
)
from raytsystem.codegraph.extract import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    FileExtraction,
    extract_file_isolated,
    extractor_fingerprint,
    stable_edge_id,
    stable_node_id,
    validate_file_extraction,
)
from raytsystem.codegraph.resolve import resolve_graph
from raytsystem.codegraph.security import (
    CodeGraphSecurityError,
    contains_sensitive_text,
    sanitize_metadata,
    validate_code_path,
)
from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.control import ControlDB, LeaseBusy, LeaseToken
from raytsystem.io import ensure_safe_directory, write_bytes_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.storage import IntegrityError, publish_immutable

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CodeGraphUnavailable(IntegrityError):
    """The current derived code graph is absent, stale or invalid."""


class CodeGraphBuildInterrupted(RuntimeError):
    """Test-only injected interruption at a durable code-graph boundary."""


@dataclass(frozen=True)
class CodeGraphUpdateResult:
    operation: Literal["update", "rebuild"]
    snapshot_id: str
    snapshot_fingerprint: str
    snapshot_sha256: str
    manifest_id: str
    no_op: bool
    processed_files: int
    skipped_files: int
    deleted_files: int
    cache_hits: int
    node_count: int
    edge_count: int
    unresolved_references: int
    ambiguous_edges: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _CurrentProjection:
    snapshot: CodeGraphSnapshot
    manifest: CodeGraphManifest
    snapshot_sha256: str
    pointer: dict[str, Any]


class CodeGraphProjection:
    version = "1.0.0"

    def __init__(self, root: Path, *, fail_at: str | None = None) -> None:
        self.root = root.resolve()
        self.fail_at = fail_at

    @property
    def graph_root(self) -> Path:
        return self.root / ".raytsystem" / "graph"

    @property
    def current_path(self) -> Path:
        return self.graph_root / "CURRENT"

    @property
    def wal_path(self) -> Path:
        return self.graph_root / "WAL.json"

    def current_snapshot(self) -> CodeGraphSnapshot:
        current = self._load_current(required=True)
        assert current is not None
        return current.snapshot

    def current_snapshot_with_sha256(self) -> tuple[CodeGraphSnapshot, str]:
        """Return the verified immutable snapshot and its object hash."""

        current = self._load_current(required=True)
        assert current is not None
        return current.snapshot, current.snapshot_sha256

    def status(self, *, verify_hashes: bool = True) -> CodeGraphStatus:
        try:
            config = load_code_graph_config(self.root)
        except CodeGraphSecurityError:
            return CodeGraphStatus(state=CodeGraphState.ERROR, reason="configuration_invalid")
        if not config.enabled:
            return CodeGraphStatus(state=CodeGraphState.MISSING, reason="disabled")
        try:
            current = self._load_current(required=False)
        except CodeGraphUnavailable:
            return CodeGraphStatus(state=CodeGraphState.ERROR, reason="integrity_failed")
        if current is None:
            return CodeGraphStatus(state=CodeGraphState.MISSING, reason="not_built")
        try:
            if verify_hashes:
                detected = detect_files(self.root, config)
                current_hashes = {
                    entry.path: entry.content_sha256 for entry in current.snapshot.files
                }
                observed_hashes = {entry.path: entry.content_sha256 for entry in detected}
                changed_paths = sorted(
                    path
                    for path, digest in observed_hashes.items()
                    if current_hashes.get(path) != digest
                )
                deleted_paths = sorted(set(current_hashes).difference(observed_hashes))
            else:
                observed = set(candidate_paths(self.root, config))
                current_paths = {entry.path for entry in current.snapshot.files}
                changed_paths = sorted(observed.difference(current_paths))
                deleted_paths = sorted(current_paths.difference(observed))
            changed = len(changed_paths)
            deleted = len(deleted_paths)
        except CodeGraphSecurityError:
            return CodeGraphStatus(
                state=CodeGraphState.ERROR,
                snapshot_id=current.snapshot.snapshot_id,
                snapshot_fingerprint=current.snapshot.logical_fingerprint,
                file_count=len(current.snapshot.files),
                node_count=len(current.snapshot.nodes),
                edge_count=len(current.snapshot.edges),
                ambiguous_edges=current.snapshot.metrics.ambiguous_edges,
                reason="path_policy_failed",
            )
        stale_config = (
            current.snapshot.config_fingerprint != config.fingerprint()
            or current.snapshot.extractor_fingerprint != extractor_fingerprint()
        )
        wal_state = self._wal_state(current_snapshot_sha256=current.snapshot_sha256)
        git_changed = False
        if verify_hashes:
            git_head, git_branch, _dirty = self._git_metadata()
            git_changed = (
                current.snapshot.git_head != git_head or current.snapshot.git_branch != git_branch
            )
        state = (
            CodeGraphState.BUILDING
            if wal_state in {"building", "prepared"}
            else CodeGraphState.STALE
            if changed or deleted or stale_config or git_changed or wal_state == "abandoned"
            else CodeGraphState.CURRENT
            if verify_hashes
            else CodeGraphState.UNCHECKED
        )
        reason = (
            "update_in_progress"
            if state is CodeGraphState.BUILDING
            else "inputs_changed"
            if changed or deleted
            else "configuration_changed"
            if stale_config
            else "checkout_changed"
            if git_changed
            else "previous_update_abandoned"
            if wal_state == "abandoned"
            else "content_hashes_unchecked"
            if state is CodeGraphState.UNCHECKED
            else None
        )
        return CodeGraphStatus(
            state=state,
            snapshot_id=current.snapshot.snapshot_id,
            snapshot_fingerprint=current.snapshot.logical_fingerprint,
            file_count=len(current.snapshot.files),
            node_count=len(current.snapshot.nodes),
            edge_count=len(current.snapshot.edges),
            ambiguous_edges=current.snapshot.metrics.ambiguous_edges,
            changed_files=changed,
            deleted_files=deleted,
            changed_paths=tuple(changed_paths[:500]),
            deleted_paths=tuple(deleted_paths[:500]),
            paths_truncated=len(changed_paths) > 500 or len(deleted_paths) > 500,
            reason=reason,
        )

    def update(self) -> CodeGraphUpdateResult:
        return self._build(operation="update", use_cache=True)

    def rebuild(self) -> CodeGraphUpdateResult:
        return self._build(operation="rebuild", use_cache=False)

    def _build(
        self,
        *,
        operation: Literal["update", "rebuild"],
        use_cache: bool,
    ) -> CodeGraphUpdateResult:
        started_ns = time.monotonic_ns()
        config = load_code_graph_config(self.root)
        if not config.enabled:
            raise CodeGraphUnavailable("Code graph is disabled by project policy")
        ensure_safe_directory(self.graph_root)
        ensure_safe_directory(self.root / "ops")
        for child in ("cache", "manifests", "snapshots"):
            ensure_safe_directory(self.graph_root / child)
        control = ControlDB(self.root / "ops" / "control.sqlite")
        owner = derive_id(
            "cgrun",
            {
                "operation": operation,
                "pid": os.getpid(),
                "started_ns": started_ns,
            },
        )
        now_ms = int(time.time() * 1000)
        lease: LeaseToken | None = None
        try:
            lease = control.acquire_lease(
                "projection:code_graph",
                owner,
                ttl_ms=120_000,
                now_ms=now_ms,
            )
            with suppress(CodeGraphUnavailable):
                lease = self._recover_if_committed(control, lease)
            try:
                old = self._load_current(required=False)
            except CodeGraphUnavailable:
                old = None
            lease = self._write_fenced_wal(
                control,
                lease,
                {
                    "state": "building",
                    "operation": operation,
                    "owner_sha256": sha256_hex(owner.encode("utf-8")),
                    "started_at": datetime.now(UTC).isoformat(),
                    "processed_files": 0,
                },
            )
            detected = detect_files(self.root, config)
            prior_hashes = (
                {}
                if old is None
                else {entry.path: entry.content_sha256 for entry in old.snapshot.files}
            )
            current_paths = {item.path for item in detected}
            deleted = len(set(prior_hashes).difference(current_paths))
            extractions: list[FileExtraction] = []
            processed = 0
            cache_hits = 0
            for index, item in enumerate(detected, start=1):
                if lease.expires_at_ms - int(time.time() * 1000) < 45_000:
                    lease = control.renew_lease(
                        lease,
                        ttl_ms=120_000,
                        now_ms=int(time.time() * 1000),
                    )
                    lease = self._write_fenced_wal(
                        control,
                        lease,
                        {
                            "state": "building",
                            "operation": operation,
                            "owner_sha256": sha256_hex(owner.encode("utf-8")),
                            "started_at": datetime.now(UTC).isoformat(),
                            "processed_files": index - 1,
                        },
                    )
                cached = self._load_cache(item, config=config) if use_cache else None
                if cached is None:
                    cached = extract_file_isolated(
                        self.root,
                        item,
                        timeout_seconds=config.parser_timeout_seconds,
                        max_nodes=min(config.max_nodes, 10_000),
                        max_edges=min(config.max_edges, 40_000),
                    )
                    self._store_cache(item, cached)
                    processed += 1
                else:
                    cache_hits += 1
                extractions.append(cached)
            lease = self._write_fenced_wal(
                control,
                lease,
                {
                    "state": "building",
                    "operation": operation,
                    "phase": "resolve",
                    "processed_files": len(detected),
                },
            )
            resolved = resolve_graph(tuple(extractions))
            if len(resolved.nodes) > config.max_nodes or len(resolved.edges) > config.max_edges:
                raise CodeGraphSecurityError("Code graph exceeds configured graph limits")
            clustered = cluster_graph(resolved.nodes, resolved.edges)
            lease = self._write_fenced_wal(
                control,
                lease,
                {
                    "state": "building",
                    "operation": operation,
                    "phase": "serialize",
                    "processed_files": len(detected),
                },
            )
            files = tuple(
                CodeFileEntry(
                    path=item.path,
                    content_sha256=item.content_sha256,
                    size_bytes=item.size_bytes,
                    mtime_ns=item.mtime_ns,
                    language=item.language,
                    extractor=EXTRACTOR_NAME,
                    extractor_version=EXTRACTOR_VERSION,
                )
                for item in detected
            )
            manifest_fingerprint = sha256_hex(
                canonical_json_bytes([entry.identity_payload() for entry in files])
            )
            git_head, git_branch, dirty = self._git_metadata()
            duration_ms = max(0, round((time.monotonic_ns() - started_ns) / 1_000_000))
            metrics = CodeGraphMetrics(
                processed_files=processed,
                skipped_files=len(detected) - processed,
                deleted_files=deleted,
                cache_hits=cache_hits,
                node_count=len(clustered.nodes),
                edge_count=len(resolved.edges),
                unresolved_references=resolved.unresolved_references,
                ambiguous_edges=resolved.ambiguous_edges,
                duration_ms=duration_ms,
            )
            snapshot = CodeGraphSnapshot.create(
                manifest_fingerprint=manifest_fingerprint,
                config_fingerprint=config.fingerprint(),
                extractor_fingerprint=extractor_fingerprint(),
                files=files,
                nodes=clustered.nodes,
                edges=resolved.edges,
                communities=clustered.communities,
                metrics=metrics,
                git_head=git_head,
                git_branch=git_branch,
                dirty=dirty,
                created_at=datetime.now(UTC),
            )
            if (
                old is not None
                and old.snapshot.logical_fingerprint == snapshot.logical_fingerprint
                and old.snapshot.git_head == snapshot.git_head
                and old.snapshot.git_branch == snapshot.git_branch
                and old.snapshot.dirty == snapshot.dirty
            ):
                lease = self._write_fenced_wal(
                    control,
                    lease,
                    {
                        "state": "committed",
                        "operation": operation,
                        "result": "no_op",
                        "snapshot_sha256": old.snapshot_sha256,
                        "manifest_id": old.manifest.manifest_id,
                    },
                )
                return self._result(
                    operation,
                    old.snapshot,
                    old.snapshot_sha256,
                    old.manifest.manifest_id,
                    no_op=True,
                    processed_files=processed,
                    cache_hits=cache_hits,
                    deleted_files=deleted,
                    duration_ms=duration_ms,
                )
            observed_again = detect_files(self.root, config)
            if [(item.path, item.content_sha256) for item in detected] != [
                (item.path, item.content_sha256) for item in observed_again
            ]:
                raise CodeGraphSecurityError("Code graph inputs changed during extraction")
            snapshot_bytes = canonical_json_bytes(snapshot)
            snapshot_sha256 = sha256_hex(snapshot_bytes)
            snapshot_path = self.graph_root / "snapshots" / f"{snapshot_sha256}.json"
            publish_immutable(snapshot_path, snapshot_bytes)
            manifest = CodeGraphManifest.create(
                snapshot_id=snapshot.snapshot_id,
                snapshot_sha256=snapshot_sha256,
                config_fingerprint=snapshot.config_fingerprint,
                extractor_fingerprint=snapshot.extractor_fingerprint,
                files=files,
                created_at=snapshot.created_at,
            )
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_path = self.graph_root / "manifests" / f"{manifest.manifest_id}.json"
            publish_immutable(manifest_path, manifest_bytes)
            pointer = {
                "snapshot_sha256": snapshot_sha256,
                "manifest_id": manifest.manifest_id,
                "snapshot_id": snapshot.snapshot_id,
                "fencing_token": lease.fencing_token,
                "operation": operation,
            }
            lease = self._write_fenced_wal(
                control,
                lease,
                {
                    "state": "prepared",
                    "operation": operation,
                    "snapshot_sha256": snapshot_sha256,
                    "manifest_id": manifest.manifest_id,
                    "snapshot_id": snapshot.snapshot_id,
                },
            )
            self._maybe_fail("after_snapshot")
            self._maybe_fail("before_pointer")
            with control.hold_valid_leases(
                (lease,),
                now_ms=int(time.time() * 1000),
                renew_ttl_ms=120_000,
            ) as renewed:
                lease = renewed[0]
                write_bytes_atomic(self.current_path, canonical_json_bytes(pointer) + b"\n")
                self._maybe_fail("after_pointer")
                write_bytes_atomic(self.graph_root / "manifest.json", manifest_bytes + b"\n")
                self._write_wal(
                    self._wal_payload(
                        lease,
                        {
                            "state": "committed",
                            "operation": operation,
                            "snapshot_sha256": snapshot_sha256,
                            "manifest_id": manifest.manifest_id,
                            "snapshot_id": snapshot.snapshot_id,
                        },
                    )
                )
            duration_ms = max(0, round((time.monotonic_ns() - started_ns) / 1_000_000))
            return self._result(
                operation,
                snapshot,
                snapshot_sha256,
                manifest.manifest_id,
                no_op=False,
                processed_files=processed,
                cache_hits=cache_hits,
                deleted_files=deleted,
                duration_ms=duration_ms,
            )
        except (
            LeaseBusy,
            CodeGraphSecurityError,
            IntegrityError,
            MemoryError,
            OSError,
            RecursionError,
            ValidationError,
        ) as error:
            if lease is not None and control.verify_lease(
                lease,
                now_ms=int(time.time() * 1000),
            ):
                with suppress(Exception):
                    lease = self._write_fenced_wal(
                        control,
                        lease,
                        {
                            "state": "error",
                            "operation": operation,
                            "error_code": type(error).__name__,
                        },
                    )
            raise CodeGraphUnavailable("Code graph update failed closed") from error
        finally:
            if lease is not None:
                with suppress(Exception):
                    control.release_lease(lease)
            control.close()

    def _result(
        self,
        operation: Literal["update", "rebuild"],
        snapshot: CodeGraphSnapshot,
        snapshot_sha256: str,
        manifest_id: str,
        *,
        no_op: bool,
        processed_files: int,
        cache_hits: int,
        deleted_files: int,
        duration_ms: int,
    ) -> CodeGraphUpdateResult:
        return CodeGraphUpdateResult(
            operation=operation,
            snapshot_id=snapshot.snapshot_id,
            snapshot_fingerprint=snapshot.logical_fingerprint,
            snapshot_sha256=snapshot_sha256,
            manifest_id=manifest_id,
            no_op=no_op,
            processed_files=processed_files,
            skipped_files=max(0, len(snapshot.files) - processed_files),
            deleted_files=deleted_files,
            cache_hits=cache_hits,
            node_count=len(snapshot.nodes),
            edge_count=len(snapshot.edges),
            unresolved_references=snapshot.metrics.unresolved_references,
            ambiguous_edges=snapshot.metrics.ambiguous_edges,
            duration_ms=duration_ms,
        )

    def _cache_key(self, item: DetectedFile) -> str:
        return sha256_hex(
            canonical_json_bytes(
                {
                    "path": item.path,
                    "content_sha256": item.content_sha256,
                    "extractor_fingerprint": extractor_fingerprint(),
                }
            )
        )

    def _load_cache(
        self,
        item: DetectedFile,
        *,
        config: CodeGraphConfig,
    ) -> FileExtraction | None:
        key = self._cache_key(item)
        relative = f".raytsystem/graph/cache/{key}.json"
        try:
            data = read_regular_file(self.root, relative, max_bytes=32 * 1024 * 1024).data
            payload = json.loads(data)
            if not isinstance(payload, dict):
                return None
            if data != canonical_json_bytes(payload):
                return None
            extraction = FileExtraction.from_dict(payload)
            validate_file_extraction(
                extraction,
                item,
                max_nodes=min(config.max_nodes, 10_000),
                max_edges=min(config.max_edges, 40_000),
            )
        except (
            OSError,
            PathPolicyError,
            json.JSONDecodeError,
            ValidationError,
            CodeGraphSecurityError,
        ):
            return None
        if extraction.path != item.path or extraction.content_sha256 != item.content_sha256:
            return None
        return extraction

    def _store_cache(self, item: DetectedFile, extraction: FileExtraction) -> None:
        key = self._cache_key(item)
        path = self.graph_root / "cache" / f"{key}.json"
        publish_immutable(path, canonical_json_bytes(extraction.to_dict()))

    def _load_current(self, *, required: bool) -> _CurrentProjection | None:
        try:
            config = load_code_graph_config(self.root)
        except CodeGraphSecurityError as error:
            raise CodeGraphUnavailable("Code graph configuration is invalid") from error
        if not config.enabled:
            raise CodeGraphUnavailable("Code graph is disabled by project policy")
        try:
            pointer_bytes = read_regular_file(
                self.root,
                ".raytsystem/graph/CURRENT",
                max_bytes=4096,
            ).data
        except (OSError, PathPolicyError):
            if required:
                raise CodeGraphUnavailable("Code graph has not been built") from None
            return None
        try:
            pointer = json.loads(pointer_bytes)
        except json.JSONDecodeError as error:
            raise CodeGraphUnavailable("Code graph pointer is malformed") from error
        if not isinstance(pointer, dict) or pointer_bytes != canonical_json_bytes(pointer) + b"\n":
            raise CodeGraphUnavailable("Code graph pointer is not canonical")
        snapshot_sha256 = pointer.get("snapshot_sha256")
        manifest_id = pointer.get("manifest_id")
        if (
            not isinstance(snapshot_sha256, str)
            or _SHA256.fullmatch(snapshot_sha256) is None
            or not isinstance(manifest_id, str)
            or not manifest_id.startswith("cgmanifest_")
        ):
            raise CodeGraphUnavailable("Code graph pointer fields are invalid")
        try:
            snapshot_bytes = read_regular_file(
                self.root,
                f".raytsystem/graph/snapshots/{snapshot_sha256}.json",
                max_bytes=256 * 1024 * 1024,
            ).data
            manifest_bytes = read_regular_file(
                self.root,
                f".raytsystem/graph/manifests/{manifest_id}.json",
                max_bytes=32 * 1024 * 1024,
            ).data
            snapshot = CodeGraphSnapshot.model_validate_json(snapshot_bytes)
            manifest = CodeGraphManifest.model_validate_json(manifest_bytes)
        except (OSError, PathPolicyError, ValidationError) as error:
            raise CodeGraphUnavailable("Code graph immutable objects are invalid") from error
        if sha256_hex(snapshot_bytes) != snapshot_sha256:
            raise CodeGraphUnavailable("Code graph snapshot hash is invalid")
        if snapshot_bytes != canonical_json_bytes(snapshot):
            raise CodeGraphUnavailable("Code graph snapshot is not canonically serialized")
        if manifest_bytes != canonical_json_bytes(manifest):
            raise CodeGraphUnavailable("Code graph manifest is not canonically serialized")
        if not manifest.verify_id() or manifest.snapshot_sha256 != snapshot_sha256:
            raise CodeGraphUnavailable("Code graph manifest identity is invalid")
        if manifest.snapshot_id != snapshot.snapshot_id:
            raise CodeGraphUnavailable("Code graph manifest and snapshot disagree")
        if pointer.get("snapshot_id") != snapshot.snapshot_id:
            raise CodeGraphUnavailable("Code graph pointer references the wrong logical snapshot")
        try:
            self._validate_projection_semantics(snapshot, manifest, config=config)
        except CodeGraphSecurityError as error:
            raise CodeGraphUnavailable("Code graph semantic validation failed") from error
        return _CurrentProjection(
            snapshot=snapshot,
            manifest=manifest,
            snapshot_sha256=snapshot_sha256,
            pointer=pointer,
        )

    @staticmethod
    def _validate_projection_semantics(
        snapshot: CodeGraphSnapshot,
        manifest: CodeGraphManifest,
        *,
        config: CodeGraphConfig,
    ) -> None:
        if (
            manifest.files != snapshot.files
            or manifest.config_fingerprint != snapshot.config_fingerprint
            or manifest.extractor_fingerprint != snapshot.extractor_fingerprint
        ):
            raise CodeGraphSecurityError("Code graph manifest does not bind snapshot inputs")
        if (
            len(snapshot.files) > config.max_files
            or len(snapshot.nodes) > config.max_nodes
            or len(snapshot.edges) > config.max_edges
        ):
            raise CodeGraphSecurityError("Code graph snapshot exceeds configured limits")
        file_paths = {entry.path for entry in snapshot.files}
        if sum(entry.size_bytes for entry in snapshot.files) > config.max_total_bytes:
            raise CodeGraphSecurityError("Code graph snapshot corpus exceeds its byte limit")
        for entry in snapshot.files:
            validate_code_path(entry.path)
        grouped = Counter((node.kind, node.path, node.qualified_name) for node in snapshot.nodes)
        expected_node_ids: set[str] = set()
        for (kind, path, qualified_name), count in grouped.items():
            expected_node_ids.update(
                stable_node_id(
                    kind,
                    path=path,
                    qualified_name=qualified_name,
                    ordinal=ordinal,
                )
                for ordinal in range(1, count + 1)
            )
        if expected_node_ids != {node.node_id for node in snapshot.nodes}:
            raise CodeGraphSecurityError("Code graph snapshot contains forged node IDs")
        for node in snapshot.nodes:
            if node.path is not None:
                validate_code_path(node.path)
            if node.location is not None and node.path not in file_paths:
                raise CodeGraphSecurityError("Located code node has no manifest source")
            if node.path is None and node.kind not in {
                CodeNodeKind.DEPENDENCY,
                CodeNodeKind.REPOSITORY,
            }:
                raise CodeGraphSecurityError("Code graph node is missing source provenance")
            if (
                contains_sensitive_text(node.label)
                or contains_sensitive_text(node.qualified_name)
                or sanitize_metadata(node.metadata) != node.metadata
            ):
                raise CodeGraphSecurityError("Code graph snapshot contains unsafe node text")
        ambiguous = 0
        for edge in snapshot.edges:
            if edge.source_file not in file_paths:
                raise CodeGraphSecurityError("Code graph edge has no manifest source")
            if edge.edge_id != stable_edge_id(
                edge.source,
                edge.target,
                edge.relation,
                source_file=edge.source_file,
                source_location=edge.source_location,
            ):
                raise CodeGraphSecurityError("Code graph snapshot contains a forged edge ID")
            if edge.confidence is EdgeConfidence.AMBIGUOUS:
                ambiguous += 1
            if sanitize_metadata(edge.metadata) != edge.metadata:
                raise CodeGraphSecurityError("Code graph snapshot contains unsafe edge metadata")
        if ambiguous != snapshot.metrics.ambiguous_edges:
            raise CodeGraphSecurityError("Code graph ambiguity metrics are inconsistent")

    def _recover_if_committed(
        self,
        control: ControlDB,
        lease: LeaseToken,
    ) -> LeaseToken:
        wal = self._read_wal()
        if wal is None or wal.get("state") != "prepared":
            return lease
        current = self._load_current(required=False)
        if current is None or current.snapshot_sha256 != wal.get("snapshot_sha256"):
            return lease
        manifest_path = f".raytsystem/graph/manifests/{current.manifest.manifest_id}.json"
        manifest_bytes = read_regular_file(
            self.root,
            manifest_path,
            max_bytes=32 * 1024 * 1024,
        ).data
        with control.hold_valid_leases(
            (lease,),
            now_ms=int(time.time() * 1000),
            renew_ttl_ms=120_000,
        ) as renewed:
            lease = renewed[0]
            write_bytes_atomic(self.graph_root / "manifest.json", manifest_bytes + b"\n")
            self._write_wal(
                self._wal_payload(
                    lease,
                    wal | {"state": "committed", "recovered": True},
                )
            )
        return lease

    def _read_wal(self) -> dict[str, Any] | None:
        try:
            data = read_regular_file(self.root, ".raytsystem/graph/WAL.json", max_bytes=16_384).data
            payload = json.loads(data)
        except (OSError, PathPolicyError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or data != canonical_json_bytes(payload) + b"\n":
            return None
        return payload

    def _wal_state(self, *, current_snapshot_sha256: str | None = None) -> str | None:
        wal = self._read_wal()
        if wal is None:
            return None
        value = wal.get("state")
        if not isinstance(value, str):
            return None
        if value == "prepared" and current_snapshot_sha256 == wal.get("snapshot_sha256"):
            return "committed_pending_recovery"
        expires_at_ms = wal.get("lease_expires_at_ms")
        if value in {"building", "prepared"} and (
            not isinstance(expires_at_ms, int) or expires_at_ms <= int(time.time() * 1000)
        ):
            return "abandoned"
        if value in {"building", "prepared"} and not self._wal_lease_is_live(wal):
            return "abandoned"
        return value

    def _wal_lease_is_live(self, wal: dict[str, Any]) -> bool:
        database = self.root / "ops" / "control.sqlite"
        if not database.is_file():
            return False
        try:
            connection = sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=1.0,
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(
                    "SELECT control_epoch, owner_run_id, fencing_token, expires_at_ms "
                    "FROM leases WHERE partition_key = ?",
                    (wal.get("partition_key"),),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return False
        return bool(
            row is not None
            and row[0] == wal.get("control_epoch")
            and sha256_hex(str(row[1]).encode("utf-8")) == wal.get("owner_sha256")
            and row[2] == wal.get("fencing_token")
            and row[3] > int(time.time() * 1000)
        )

    def _write_fenced_wal(
        self,
        control: ControlDB,
        lease: LeaseToken,
        payload: dict[str, Any],
    ) -> LeaseToken:
        with control.hold_valid_leases(
            (lease,),
            now_ms=int(time.time() * 1000),
            renew_ttl_ms=120_000,
        ) as renewed:
            lease = renewed[0]
            self._write_wal(self._wal_payload(lease, payload))
        return lease

    @staticmethod
    def _wal_payload(lease: LeaseToken, payload: dict[str, Any]) -> dict[str, Any]:
        return payload | {
            "partition_key": lease.partition_key,
            "control_epoch": lease.control_epoch,
            "owner_sha256": sha256_hex(lease.owner_run_id.encode("utf-8")),
            "fencing_token": lease.fencing_token,
            "lease_expires_at_ms": lease.expires_at_ms,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _write_wal(self, payload: dict[str, Any]) -> None:
        write_bytes_atomic(self.wal_path, canonical_json_bytes(payload) + b"\n")

    def _maybe_fail(self, checkpoint: str) -> None:
        if self.fail_at == checkpoint:
            raise CodeGraphBuildInterrupted(checkpoint)

    def _git_metadata(self) -> tuple[str | None, str | None, bool]:
        def run(*args: str) -> str | None:
            try:
                result = subprocess.run(
                    ("git", "-C", str(self.root), *args),
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                return result.stdout.decode("utf-8").strip() or None
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                return None

        head = run("rev-parse", "HEAD")
        branch = run("branch", "--show-current")
        dirty = bool(run("status", "--porcelain=v1", "--untracked-files=normal"))
        return head, branch, dirty
