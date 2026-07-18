from __future__ import annotations

import ctypes
import difflib
import errno
import os
import re
import secrets
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import yaml

from raytsystem.security import osfd
from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.documents.contracts import (
    DocumentConflict,
    DocumentPolicyError,
    DocumentRestricted,
)
from raytsystem.documents.history import DocumentHistory
from raytsystem.documents.index import DocumentIndex
from raytsystem.documents.markdown import FrontmatterError, validate_frontmatter_properties
from raytsystem.documents.sensitivity import contains_restricted_content
from raytsystem.platform_store import PlatformStore, initialize_platform_store
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import publish_immutable

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_SNAPSHOT = re.compile(r"^docsnap_[0-9a-f]{64}$")
_MAX_CONFLICT_CONTENT = 256 * 1024
_TEMPLATES = frozenset({"empty", "note", "project", "meeting", "research", "daily"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _exchange_names_windows(parent_fd: int, left: str, right: str) -> None:
    # Windows exposes no renameat2/RENAME_EXCHANGE equivalent; emulate the
    # swap with a temporary name and best-effort rollback. Each step is a
    # no-replace rename inside one directory (os.rename never clobbers on
    # Windows) and the caller's recovery markers cover a crash mid-sequence.
    base = osfd.registered_path(parent_fd)
    left_path = base / left
    right_path = base / right
    temporary = base / f".{left}.xchg-{os.getpid()}"
    os.rename(left_path, temporary)
    try:
        os.rename(right_path, left_path)
    except BaseException:
        os.rename(temporary, left_path)
        raise
    try:
        os.rename(temporary, right_path)
    except BaseException:
        os.rename(left_path, right_path)
        os.rename(temporary, left_path)
        raise


def _exchange_names(parent_fd: int, left: str, right: str) -> None:
    """Atomically swap two directory entries or fail closed on unsupported kernels/filesystems."""

    if os.name == "nt":
        _exchange_names_windows(parent_fd, left, right)
        return
    library = ctypes.CDLL(None, use_errno=True)
    left_bytes = os.fsencode(left)
    right_bytes = os.fsencode(right)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(parent_fd, left_bytes, parent_fd, right_bytes, 0x00000002)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(parent_fd, left_bytes, parent_fd, right_bytes, 0x00000002)
    else:
        raise DocumentPolicyError("Kernel-backed atomic document exchange is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise DocumentPolicyError("Filesystem does not support atomic document exchange")
        raise OSError(error_number, os.strerror(error_number))


class DocumentService:
    """Policy-bound document mutations with CAS, atomic publication, audit and revisions."""

    _locks_guard = threading.Lock()
    _locks: ClassVar[dict[tuple[str, str], threading.RLock]] = {}

    def __init__(
        self,
        root: Path,
        *,
        index: DocumentIndex | None = None,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.index = index or DocumentIndex(self.root, scanner=scanner)
        self.config = self.index.config
        self.policy = self.index.policy
        self.scanner = scanner or self.index.scanner
        self.history = DocumentHistory(self.root, index=self.index, scanner=self.scanner)

    def create(
        self,
        *,
        root_id: str,
        name: str,
        folder: str = "",
        content: str = "",
        template: str = "empty",
        properties: dict[str, Any] | None = None,
        tags: tuple[str, ...] = (),
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str = "user_local_web",
    ) -> dict[str, Any]:
        self._ensure_index()
        root = self.policy.root(root_id)
        if not root.mode.editable:
            raise DocumentPolicyError("Document root is read-only")
        filename = self._markdown_name(name)
        relative = self._destination(root.path, folder, filename, root_id=root_id)
        rendered = self._new_content(
            content=content,
            template=template,
            properties=properties or {},
            tags=tags,
            title=Path(filename).stem,
        )
        data = self._content_bytes(rendered, relative)
        request = {
            "operation": "create",
            "root_id": root_id,
            "relative_path": relative,
            "content_sha256": sha256_hex(data),
            "expected_snapshot_id": expected_snapshot_id,
        }
        key = self._idempotency_key(idempotency_key)
        with self._path_locks(relative), initialize_platform_store(self.root) as store:
            prior = store.idempotent_receipt(
                scope="documents", idempotency_key=key, request=request
            )
            if prior is not None:
                return prior
            operation_id = derive_id("dop", {"scope": "documents", "idempotency_key": key})
            prepared = store.head("document_operation", operation_id)
            recovering = prepared is not None and prepared.state == "publishing"
            if prepared is None:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="prepared",
                    expected_revision=None,
                )
            elif prepared.payload != request:
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"operation_id": operation_id},
                )
            self._require_snapshot(expected_snapshot_id, allow_rebase=recovering)
            existing = self.index.row_for_path(relative)
            document_id: str | None = None
            if existing is not None and (
                not recovering or str(existing["content_sha256"]) != sha256_hex(data)
            ):
                raise DocumentConflict(
                    "A document already exists at the destination",
                    details={"path": relative, "current_sha256": existing["content_sha256"]},
                )
            if existing is not None:
                document_id = str(existing["document_id"])
            elif recovering:
                recovered_found = False
                try:
                    recovered = read_regular_file(
                        self.root,
                        relative,
                        max_bytes=self.config.max_file_bytes,
                    ).data
                    recovered_found = True
                except (OSError, PathPolicyError):
                    recovered = b""
                if recovered_found and sha256_hex(recovered) == sha256_hex(data):
                    document_id, _ = self.index.ensure_identity(relative)
                    self.index.refresh((relative,))
            if document_id is None:
                if not recovering:
                    prepared = store.append_record(
                        kind="document_operation",
                        record_id=operation_id,
                        payload=request,
                        state="publishing",
                        expected_revision=prepared.revision,
                    )
                self._atomic_create(relative, data)
                document_id, _ = self.index.ensure_identity(relative)
            try:
                status = self.index.refresh((relative,))
                result = self._write_result(
                    document_id,
                    status=status,
                    no_op=False,
                    revision_id=None,
                    audit_event_id=None,
                )
                with store.transaction():
                    event = self._audit(
                        store,
                        document_id=document_id,
                        event_type="document_created",
                        actor_id=actor_id,
                        idempotency_key=key,
                        payload={
                            "path": relative,
                            "new_sha256": sha256_hex(data),
                            "root_id": root_id,
                            "recovered": recovering,
                        },
                    )
                    result["audit_event_id"] = event["event_id"]
                    store.append_record(
                        kind="document_operation",
                        record_id=operation_id,
                        payload={**request, "document_id": document_id},
                        state="completed",
                        expected_revision=prepared.revision,
                    )
                    store.idempotent_receipt(
                        scope="documents", idempotency_key=key, request=request, receipt=result
                    )
                return result
            except BaseException:
                # The file remains authoritative. A retry with the same key can recover from
                # the content hash even if the disposable index failed to refresh.
                raise

    def update(
        self,
        document_id: str,
        *,
        content: str,
        expected_sha256: str,
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str = "user_local_web",
        operation: str = "update",
    ) -> dict[str, Any]:
        self._ensure_index()
        row = self.index.row_for_id(document_id)
        snapshot_rebased = self._require_snapshot(
            expected_snapshot_id,
            actual=str(row["snapshot_id"]),
            allow_rebase=True,
        )
        relative = str(row["relative_path"])
        self.policy.require_write(relative)
        if Path(relative).suffix.casefold() not in _MARKDOWN_SUFFIXES:
            raise DocumentPolicyError("Only Markdown documents can be edited")
        data = self._content_bytes(content, relative)
        proposed_sha256 = sha256_hex(data)
        request = {
            "operation": operation,
            "document_id": document_id,
            "expected_sha256": expected_sha256,
            "proposed_sha256": proposed_sha256,
            "expected_snapshot_id": expected_snapshot_id,
        }
        key = self._idempotency_key(idempotency_key)
        with self._path_locks(relative), initialize_platform_store(self.root) as store:
            prior = store.idempotent_receipt(
                scope="documents", idempotency_key=key, request=request
            )
            if prior is not None:
                return prior
            operation_id = derive_id("dop", {"scope": "documents", "idempotency_key": key})
            prepared = store.head("document_operation", operation_id)
            recovering = prepared is not None and prepared.state == "publishing"
            if prepared is None:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="prepared",
                    expected_revision=None,
                )
            elif prepared.payload != request:
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"operation_id": operation_id},
                )
            current = self._read_current(relative)
            current_sha256 = sha256_hex(current)
            if contains_restricted_content(self.scanner, current, path=relative):
                raise DocumentRestricted(
                    "Document changed to restricted content outside raytsystem"
                )
            recovered_publication = recovering and current_sha256 == proposed_sha256
            if current_sha256 != expected_sha256 and not recovered_publication:
                raise self._conflict(
                    document_id=document_id,
                    expected_sha256=expected_sha256,
                    current_sha256=current_sha256,
                    proposed_sha256=proposed_sha256,
                    current=current,
                    snapshot_id=str(row["snapshot_id"]),
                )
            if current_sha256 == proposed_sha256:
                status = self.index.refresh((relative,))
                result = self._write_result(
                    document_id,
                    status=status,
                    no_op=not recovered_publication,
                    revision_id=None,
                    audit_event_id=None,
                )
                result["snapshot_rebased"] = snapshot_rebased
                with store.transaction():
                    event = self._audit(
                        store,
                        document_id=document_id,
                        event_type=(
                            f"document_{operation}_recovered"
                            if recovered_publication
                            else f"document_{operation}_noop"
                        ),
                        actor_id=actor_id,
                        idempotency_key=key,
                        payload={
                            "path": relative,
                            "content_sha256": current_sha256,
                            "snapshot_rebased": snapshot_rebased,
                        },
                    )
                    result["audit_event_id"] = event["event_id"]
                    store.append_record(
                        kind="document_operation",
                        record_id=operation_id,
                        payload=request,
                        state="completed",
                        expected_revision=prepared.revision,
                    )
                    store.idempotent_receipt(
                        scope="documents", idempotency_key=key, request=request, receipt=result
                    )
                return result
            revision_id = self._record_revision(
                document_id=document_id,
                relative=relative,
                data=current,
                operation=operation,
                idempotency_key=key,
            )
            if not recovering:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="publishing",
                    expected_revision=prepared.revision,
                )
            self._atomic_replace(
                relative,
                data,
                expected_sha256=expected_sha256,
                document_id=document_id,
                proposed_sha256=proposed_sha256,
                snapshot_id=str(row["snapshot_id"]),
            )
            status = self.index.refresh((relative,))
            result = self._write_result(
                document_id,
                status=status,
                no_op=False,
                revision_id=revision_id,
                audit_event_id=None,
            )
            result["snapshot_rebased"] = snapshot_rebased
            with store.transaction():
                event = self._audit(
                    store,
                    document_id=document_id,
                    event_type=(
                        "document_restored" if operation == "restore" else "document_updated"
                    ),
                    actor_id=actor_id,
                    idempotency_key=key,
                    payload={
                        "path": relative,
                        "old_sha256": current_sha256,
                        "new_sha256": proposed_sha256,
                        "revision_id": revision_id,
                        "snapshot_rebased": snapshot_rebased,
                    },
                )
                result["audit_event_id"] = event["event_id"]
                store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="completed",
                    expected_revision=prepared.revision,
                )
                store.idempotent_receipt(
                    scope="documents", idempotency_key=key, request=request, receipt=result
                )
            return result

    def rename(
        self,
        document_id: str,
        *,
        new_name: str,
        expected_sha256: str,
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str = "user_local_web",
    ) -> dict[str, Any]:
        resumed = self._resumable_move(idempotency_key, document_id, operation="rename")
        if resumed is not None:
            receipt = resumed.get("receipt")
            if isinstance(receipt, dict):
                return receipt
            request = resumed["request"]
            if (
                PurePosixPath(str(request["new_path"])).name != self._markdown_name(new_name)
                or request["expected_sha256"] != expected_sha256
                or request["expected_snapshot_id"] != expected_snapshot_id
            ):
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"document_id": document_id},
                )
            return self._move(
                document_id,
                old=str(request["old_path"]),
                new=str(request["new_path"]),
                expected_sha256=expected_sha256,
                expected_snapshot_id=expected_snapshot_id,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                operation="rename",
            )
        row = self.index.row_for_id(document_id)
        old = str(row["relative_path"])
        new = (PurePosixPath(old).parent / self._markdown_name(new_name)).as_posix()
        return self._move(
            document_id,
            old=old,
            new=new,
            expected_sha256=expected_sha256,
            expected_snapshot_id=expected_snapshot_id,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            operation="rename",
        )

    def move(
        self,
        document_id: str,
        *,
        destination_root_id: str,
        destination_folder: str,
        expected_sha256: str,
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str = "user_local_web",
    ) -> dict[str, Any]:
        resumed = self._resumable_move(idempotency_key, document_id, operation="move")
        if resumed is not None:
            receipt = resumed.get("receipt")
            if isinstance(receipt, dict):
                return receipt
            request = resumed["request"]
            destination_root = self.policy.root(destination_root_id)
            expected_new = self._destination(
                destination_root.path,
                destination_folder,
                PurePosixPath(str(request["old_path"])).name,
                root_id=destination_root_id,
            )
            if (
                str(request["new_path"]) != expected_new
                or request["expected_sha256"] != expected_sha256
                or request["expected_snapshot_id"] != expected_snapshot_id
            ):
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"document_id": document_id},
                )
            return self._move(
                document_id,
                old=str(request["old_path"]),
                new=str(request["new_path"]),
                expected_sha256=expected_sha256,
                expected_snapshot_id=expected_snapshot_id,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                operation="move",
            )
        row = self.index.row_for_id(document_id)
        old = str(row["relative_path"])
        destination_root = self.policy.root(destination_root_id)
        new = self._destination(
            destination_root.path,
            destination_folder,
            PurePosixPath(old).name,
            root_id=destination_root_id,
        )
        return self._move(
            document_id,
            old=old,
            new=new,
            expected_sha256=expected_sha256,
            expected_snapshot_id=expected_snapshot_id,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            operation="move",
        )

    def create_folder(
        self,
        *,
        root_id: str,
        folder: str,
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str = "user_local_web",
    ) -> dict[str, Any]:
        self._ensure_index()
        root = self.policy.root(root_id)
        pure_folder = PurePosixPath(folder)
        if pure_folder.is_absolute() or not pure_folder.parts:
            raise DocumentPolicyError("Document folder is invalid")
        folder_name = self._component(pure_folder.name, label="folder name")
        parent = "" if len(pure_folder.parts) == 1 else pure_folder.parent.as_posix()
        relative = self._destination(root.path, parent, folder_name, root_id=root_id)
        request = {
            "operation": "create_folder",
            "root_id": root_id,
            "path": relative,
            "expected_snapshot_id": expected_snapshot_id,
        }
        key = self._idempotency_key(idempotency_key)
        with self._path_locks(relative), initialize_platform_store(self.root) as store:
            prior = store.idempotent_receipt(
                scope="documents", idempotency_key=key, request=request
            )
            if prior is not None:
                return prior
            operation_id = derive_id("dop", {"scope": "documents", "idempotency_key": key})
            prepared = store.head("document_operation", operation_id)
            recovering = prepared is not None and prepared.state == "publishing"
            if prepared is None:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="prepared",
                    expected_revision=None,
                )
            elif prepared.payload != request:
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"operation_id": operation_id},
                )
            self._require_snapshot(expected_snapshot_id, allow_rebase=recovering)
            if not recovering:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="publishing",
                    expected_revision=prepared.revision,
                )
                self._mkdir(relative)
            elif not self._directory_exists(relative):
                self._mkdir(relative)
            status = self.index.rebuild()
            result = {
                "snapshot_id": status.get("snapshot_id"),
                "folder": relative,
                "no_op": False,
                "audit_event_id": None,
            }
            with store.transaction():
                event = self._audit(
                    store,
                    document_id=root_id,
                    event_type="document_folder_created",
                    actor_id=actor_id,
                    idempotency_key=key,
                    payload={"path": relative, "root_id": root_id, "recovered": recovering},
                )
                result["audit_event_id"] = event["event_id"]
                store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="completed",
                    expected_revision=prepared.revision,
                )
                store.idempotent_receipt(
                    scope="documents", idempotency_key=key, request=request, receipt=result
                )
            return result

    def restore_preview(
        self,
        document_id: str,
        *,
        history_id: str,
        expected_sha256: str,
        expected_snapshot_id: str,
    ) -> dict[str, Any]:
        row = self.index.row_for_id(document_id)
        snapshot_rebased = self._require_snapshot(
            expected_snapshot_id,
            actual=str(row["snapshot_id"]),
            allow_rebase=True,
        )
        relative = str(row["relative_path"])
        self.policy.require_write(relative)
        current = self._read_current(relative)
        current_sha256 = sha256_hex(current)
        if current_sha256 != expected_sha256:
            raise self._conflict(
                document_id=document_id,
                expected_sha256=expected_sha256,
                current_sha256=current_sha256,
                proposed_sha256=None,
                current=current,
                snapshot_id=str(row["snapshot_id"]),
            )
        revision = self.history.version_bytes(
            document_id,
            history_id,
            max_bytes=self.config.max_file_bytes,
        )
        try:
            current_text = current.decode("utf-8")
            revision_text = revision.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentPolicyError("Only UTF-8 Markdown revisions can be restored") from error
        diff, diff_truncated = self._bounded_diff(
            current_text,
            revision_text,
            tofile=history_id,
        )
        restored_sha256 = sha256_hex(revision)
        preview_token = derive_id(
            "drp",
            {
                "document_id": document_id,
                "history_id": history_id,
                "current_sha256": current_sha256,
                "restored_sha256": restored_sha256,
                "snapshot_id": row["snapshot_id"],
            },
        )
        return {
            "snapshot_id": row["snapshot_id"],
            "document_id": document_id,
            "history_id": history_id,
            "current_sha256": current_sha256,
            "restored_sha256": restored_sha256,
            "restored_content": revision_text,
            "diff": diff,
            "diff_truncated": diff_truncated,
            "preview_token": preview_token,
            "snapshot_rebased": snapshot_rebased,
        }

    def restore(
        self,
        document_id: str,
        *,
        history_id: str,
        expected_sha256: str,
        expected_snapshot_id: str,
        preview_token: str,
        confirmed: bool,
        idempotency_key: str,
        actor_id: str = "user_local_web",
    ) -> dict[str, Any]:
        if not confirmed:
            raise DocumentPolicyError("Document restore requires explicit confirmation")
        preview = self.restore_preview(
            document_id,
            history_id=history_id,
            expected_sha256=expected_sha256,
            expected_snapshot_id=expected_snapshot_id,
        )
        if not secrets.compare_digest(str(preview["preview_token"]), preview_token):
            raise DocumentConflict(
                "Document restore preview changed",
                details={
                    "document_id": document_id,
                    "snapshot_id": preview["snapshot_id"],
                    "current_sha256": preview["current_sha256"],
                    "restored_sha256": preview["restored_sha256"],
                },
            )
        return self.update(
            document_id,
            content=str(preview["restored_content"]),
            expected_sha256=expected_sha256,
            expected_snapshot_id=expected_snapshot_id,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            operation="restore",
        )

    def _move(
        self,
        document_id: str,
        *,
        old: str,
        new: str,
        expected_sha256: str,
        expected_snapshot_id: str,
        idempotency_key: str,
        actor_id: str,
        operation: str,
    ) -> dict[str, Any]:
        self._ensure_index()
        self.policy.require_write(old)
        self.policy.require_write(new)
        if old == new:
            raise DocumentPolicyError("Document destination is unchanged")
        request = {
            "operation": operation,
            "document_id": document_id,
            "old_path": old,
            "new_path": new,
            "expected_sha256": expected_sha256,
            "expected_snapshot_id": expected_snapshot_id,
        }
        key = self._idempotency_key(idempotency_key)
        with self._path_locks(old, new), initialize_platform_store(self.root) as store:
            prior = store.idempotent_receipt(
                scope="documents", idempotency_key=key, request=request
            )
            if prior is not None:
                return prior
            operation_id = derive_id("dop", {"scope": "documents", "idempotency_key": key})
            prepared = store.head("document_operation", operation_id)
            recovering = prepared is not None and prepared.state == "publishing"
            if prepared is None:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="prepared",
                    expected_revision=None,
                )
            elif prepared.payload != request:
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"operation_id": operation_id},
                )
            snapshot_rebased = self._require_snapshot(
                expected_snapshot_id,
                allow_rebase=True,
            )
            published = False
            if recovering:
                self._recover_link_move(old, new)
                old_bytes = self._read_optional(old)
                new_bytes = self._read_optional(new)
                if new_bytes is not None and old_bytes is None:
                    current = new_bytes
                    published = True
                elif old_bytes is not None and new_bytes is None:
                    current = old_bytes
                else:
                    raise DocumentConflict(
                        "Document move recovery state is ambiguous",
                        details={"document_id": document_id, "old_path": old, "new_path": new},
                    )
            else:
                current = self._read_current(old)
            current_sha256 = sha256_hex(current)
            if contains_restricted_content(self.scanner, current, path=old):
                raise DocumentRestricted(
                    "Document changed to restricted content outside raytsystem"
                )
            if current_sha256 != expected_sha256:
                raise self._conflict(
                    document_id=document_id,
                    expected_sha256=expected_sha256,
                    current_sha256=current_sha256,
                    proposed_sha256=current_sha256,
                    current=current,
                    snapshot_id=str(self.index.status().get("snapshot_id")),
                )
            if not recovering:
                prepared = store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="publishing",
                    expected_revision=prepared.revision,
                )
            if not published:
                self._atomic_move(
                    old,
                    new,
                    expected_sha256=expected_sha256,
                    document_id=document_id,
                    snapshot_id=str(self.index.status().get("snapshot_id")),
                )
            try:
                self.index.move_identity(old, new, document_id=document_id)
            except BaseException:
                with suppress(BaseException):
                    self._atomic_move(
                        new,
                        old,
                        expected_sha256=expected_sha256,
                        document_id=document_id,
                        snapshot_id=str(self.index.status().get("snapshot_id")),
                    )
                raise
            status = self.index.refresh((old, new))
            result = self._write_result(
                document_id,
                status=status,
                no_op=False,
                revision_id=None,
                audit_event_id=None,
            )
            result["snapshot_rebased"] = snapshot_rebased
            with store.transaction():
                event = self._audit(
                    store,
                    document_id=document_id,
                    event_type=(
                        f"document_{operation}d" if operation == "rename" else "document_moved"
                    ),
                    actor_id=actor_id,
                    idempotency_key=key,
                    payload={
                        "old_path": old,
                        "new_path": new,
                        "content_sha256": current_sha256,
                        "recovered": recovering,
                        "snapshot_rebased": snapshot_rebased,
                    },
                )
                result["audit_event_id"] = event["event_id"]
                store.append_record(
                    kind="document_operation",
                    record_id=operation_id,
                    payload=request,
                    state="completed",
                    expected_revision=prepared.revision,
                )
                store.idempotent_receipt(
                    scope="documents", idempotency_key=key, request=request, receipt=result
                )
            return result

    def _ensure_index(self) -> None:
        status = self.index.status()
        freshness_only = (
            status.get("state") == "stale"
            and isinstance(status.get("snapshot_id"), str)
            and status.get("message") == "Index freshness window expired; refresh is required."
        )
        if status["state"] != "current" and not freshness_only:
            self.index.rebuild()

    def _resumable_move(
        self,
        idempotency_key: str,
        document_id: str,
        *,
        operation: str,
    ) -> dict[str, Any] | None:
        key = self._idempotency_key(idempotency_key)
        operation_id = derive_id("dop", {"scope": "documents", "idempotency_key": key})
        with initialize_platform_store(self.root) as store:
            record = store.head("document_operation", operation_id)
            if record is None or record.state not in {"publishing", "completed"}:
                return None
            request = record.payload
            if request.get("operation") != operation or request.get("document_id") != document_id:
                raise DocumentConflict(
                    "Document operation binding changed",
                    details={"operation_id": operation_id},
                )
            receipt = store.idempotent_receipt(
                scope="documents",
                idempotency_key=key,
                request=request,
            )
            if receipt is not None:
                return {"receipt": receipt}
            if record.state == "completed":
                raise DocumentConflict(
                    "Completed document operation is missing its receipt",
                    details={"operation_id": operation_id},
                )
            return {"request": request}

    def _destination(self, root_path: str, folder: str, name: str, *, root_id: str) -> str:
        if folder:
            if "\\" in folder or "\x00" in folder:
                raise DocumentPolicyError("Destination folder is malformed")
            pure_folder = PurePosixPath(folder)
            if pure_folder.is_absolute() or any(
                part in {"", ".", ".."} for part in pure_folder.parts
            ):
                raise DocumentPolicyError("Destination folder escapes its document root")
            relative = (PurePosixPath(root_path) / pure_folder / name).as_posix()
        else:
            relative = (PurePosixPath(root_path) / name).as_posix()
        decision = self.policy.require_write(relative)
        if decision.root is None or decision.root.root_id != root_id:
            raise DocumentPolicyError("Destination enters another document root")
        return relative

    def _new_content(
        self,
        *,
        content: str,
        template: str,
        properties: dict[str, Any],
        tags: tuple[str, ...],
        title: str,
    ) -> str:
        if template not in _TEMPLATES:
            raise DocumentPolicyError("Document template is invalid")
        if content:
            if properties or tags or template != "empty":
                raise DocumentPolicyError(
                    "Explicit content cannot be combined with template metadata"
                )
            return content
        try:
            frontmatter = validate_frontmatter_properties(dict(properties))
        except FrontmatterError as error:
            raise DocumentPolicyError("Document properties are too complex") from error
        if tags:
            frontmatter["tags"] = list(tags)
        body = {
            "empty": "",
            "note": f"# {title}\n\n",
            "project": f"# {title}\n\n## Goal\n\n## Tasks\n\n",
            "meeting": f"# {title}\n\n## Attendees\n\n## Notes\n\n## Actions\n\n",
            "research": f"# {title}\n\n## Question\n\n## Findings\n\n## Sources\n\n",
            "daily": f"# {title}\n\n## Notes\n\n## Tasks\n\n",
        }[template]
        if not frontmatter:
            return body
        try:
            rendered = yaml.safe_dump(
                frontmatter,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        except (yaml.YAMLError, RecursionError) as error:
            raise DocumentPolicyError("Document properties cannot be serialized safely") from error
        return f"---\n{rendered}---\n{body}"

    def _content_bytes(self, content: str, relative: str) -> bytes:
        if "\x00" in content:
            raise DocumentPolicyError("Markdown contains a NUL character")
        data = content.encode("utf-8")
        if len(data) > self.config.max_file_bytes:
            raise DocumentPolicyError("Markdown exceeds the configured 5 MiB limit")
        if contains_restricted_content(self.scanner, data, path=relative):
            raise DocumentRestricted("Restricted content cannot be written through Documents")
        return data

    def _read_current(self, relative: str) -> bytes:
        try:
            return read_regular_file(self.root, relative, max_bytes=self.config.max_file_bytes).data
        except (OSError, PathPolicyError) as error:
            raise DocumentConflict(
                "Document changed or became unsafe",
                details={"path": relative, "current_sha256": None},
            ) from error

    def _read_optional(self, relative: str) -> bytes | None:
        with self._parent_fd(relative) as (parent_fd, name):
            try:
                osfd.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
        return self._read_current(relative)

    def _record_revision(
        self,
        *,
        document_id: str,
        relative: str,
        data: bytes,
        operation: str,
        idempotency_key: str,
    ) -> str:
        digest = sha256_hex(data)
        recorded_at = _now()
        revision_id = derive_id(
            "drev",
            {
                "document_id": document_id,
                "content_sha256": digest,
                "recorded_at": recorded_at,
                "operation": operation,
                "idempotency_key": idempotency_key,
            },
        )
        blob = f"ops/document-revisions/blobs/{digest[:2]}/{digest}.md"
        publish_immutable(self.root / blob, data, mode=0o600)
        material = {
            "revision_id": revision_id,
            "document_id": document_id,
            "relative_path": relative,
            "content_sha256": digest,
            "blob_path": blob,
            "operation": operation,
            "recorded_at": recorded_at,
        }
        payload = material | {"record_sha256": sha256_hex(canonical_json_bytes(material))}
        publish_immutable(
            self.root / f"ops/document-revisions/records/{revision_id}.json",
            canonical_json_bytes(payload),
            mode=0o600,
        )
        return revision_id

    def _write_result(
        self,
        document_id: str,
        *,
        status: dict[str, Any],
        no_op: bool,
        revision_id: str | None,
        audit_event_id: str | None,
    ) -> dict[str, Any]:
        detail = self.index.detail(document_id)
        return {
            "snapshot_id": status["snapshot_id"],
            "document": detail["document"],
            "content_sha256": detail["content_sha256"],
            "no_op": no_op,
            "revision_id": revision_id,
            "audit_event_id": audit_event_id,
        }

    @staticmethod
    def _audit(
        store: PlatformStore,
        *,
        document_id: str,
        event_type: str,
        actor_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return store.append_event(
            stream_id=f"document:{document_id}",
            aggregate_id=document_id,
            event_type=event_type,
            actor_id=actor_id,
            payload_schema="raytsystem.document.audit.v1",
            payload=payload,
            sensitivity="internal",
            correlation_id=idempotency_key,
        )

    @staticmethod
    def _conflict(
        *,
        document_id: str,
        expected_sha256: str,
        current_sha256: str,
        proposed_sha256: str | None,
        current: bytes,
        snapshot_id: str,
    ) -> DocumentConflict:
        current_content: str | None = None
        truncated = len(current) > _MAX_CONFLICT_CONTENT
        if not truncated:
            try:
                current_content = current.decode("utf-8")
            except UnicodeDecodeError:
                current_content = None
        return DocumentConflict(
            "Document changed after it was opened",
            details={
                "document_id": document_id,
                "expected_sha256": expected_sha256,
                "current_sha256": current_sha256,
                "proposed_sha256": proposed_sha256,
                "snapshot_id": snapshot_id,
                "current_content": current_content,
                "current_content_truncated": truncated,
            },
        )

    @staticmethod
    def _component(value: str, *, label: str) -> str:
        if (
            not value
            or value in {".", ".."}
            or value.startswith(".")
            or "/" in value
            or "\\" in value
            or "\x00" in value
            or len(value.encode("utf-8")) > 240
            or any(ord(character) < 32 for character in value)
        ):
            raise DocumentPolicyError(f"Document {label} is invalid")
        return value

    def _markdown_name(self, value: str) -> str:
        name = self._component(value, label="name")
        if not Path(name).suffix:
            name += ".md"
        if Path(name).suffix.casefold() not in _MARKDOWN_SUFFIXES:
            raise DocumentPolicyError("Document name must use a Markdown extension")
        return name

    @staticmethod
    def _idempotency_key(value: str) -> str:
        if _IDEMPOTENCY_KEY.fullmatch(value) is None:
            raise DocumentPolicyError("Idempotency key is malformed")
        return value

    def _require_snapshot(
        self,
        value: str,
        *,
        actual: str | None = None,
        allow_rebase: bool,
    ) -> bool:
        if _SAFE_SNAPSHOT.fullmatch(value) is None:
            raise DocumentConflict(
                "Document snapshot binding is malformed",
                details={"snapshot_id": value},
            )
        current = actual or self.index.status().get("snapshot_id")
        if not isinstance(current, str) or _SAFE_SNAPSHOT.fullmatch(current) is None:
            raise DocumentConflict(
                "Document index snapshot is unavailable",
                details={"expected_snapshot_id": value, "current_snapshot_id": current},
            )
        changed = not secrets.compare_digest(value, current)
        if changed and not allow_rebase:
            raise DocumentConflict(
                "Document index changed after the command was prepared",
                details={"expected_snapshot_id": value, "current_snapshot_id": current},
            )
        return changed

    @staticmethod
    def _bounded_diff(
        current: str,
        proposed: str,
        *,
        tofile: str,
    ) -> tuple[str | None, bool]:
        if (
            len(current.encode("utf-8")) > 1024 * 1024
            or len(proposed.encode("utf-8")) > 1024 * 1024
        ):
            return None, True
        current_lines = current.splitlines(keepends=True)
        proposed_lines = proposed.splitlines(keepends=True)
        if len(current_lines) > 20_000 or len(proposed_lines) > 20_000:
            return None, True
        diff = "".join(
            difflib.unified_diff(
                current_lines,
                proposed_lines,
                fromfile="current",
                tofile=tofile,
                n=3,
            )
        )
        if len(diff.encode("utf-8")) > 1024 * 1024:
            return diff[: 1024 * 1024] + "\n… diff truncated …\n", True
        return diff, False

    @classmethod
    @contextmanager
    def _path_locks(cls, *paths: str) -> Iterator[None]:
        keys = sorted({("documents", path) for path in paths})
        locks: list[threading.RLock] = []
        with cls._locks_guard:
            for key in keys:
                lock = cls._locks.setdefault(key, threading.RLock())
                locks.append(lock)
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    @contextmanager
    def _parent_fd(self, relative: str) -> Iterator[tuple[int, str]]:
        pure = PurePosixPath(relative)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        root_fd = osfd.open(self.root, os.O_RDONLY | directory | cloexec)
        opened: list[int] = []
        parent = root_fd
        try:
            for component in pure.parts[:-1]:
                try:
                    descriptor = osfd.open(
                        component,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=parent,
                    )
                except OSError as error:
                    raise DocumentPolicyError(
                        "Document parent is missing, non-directory, or a symlink"
                    ) from error
                opened.append(descriptor)
                parent = descriptor
            yield parent, pure.name
        finally:
            for descriptor in reversed(opened):
                osfd.close(descriptor)
            osfd.close(root_fd)

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("Short document write")
            offset += written

    def _read_name(self, parent_fd: int, name: str) -> bytes:
        descriptor = osfd.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DocumentPolicyError("Document exchange entry is unsafe")
            chunks: list[bytes] = []
            consumed = 0
            while chunk := os.read(
                descriptor,
                min(1024 * 1024, self.config.max_file_bytes + 1 - consumed),
            ):
                chunks.append(chunk)
                consumed += len(chunk)
                if consumed > self.config.max_file_bytes:
                    raise DocumentPolicyError("Document exchange entry exceeds size limit")
            return b"".join(chunks)
        finally:
            osfd.close(descriptor)

    def _atomic_create(self, relative: str, data: bytes) -> None:
        with self._parent_fd(relative) as (parent_fd, name):
            temporary = f".{name}.{secrets.token_hex(12)}.tmp"
            descriptor: int | None = None
            try:
                descriptor = osfd.open(
                    temporary,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                self._write_all(descriptor, data)
                osfd.fsync(descriptor)
                osfd.close(descriptor)
                descriptor = None
                osfd.link(
                    temporary,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                osfd.unlink(temporary, dir_fd=parent_fd)
                osfd.fsync(parent_fd)
            except FileExistsError as error:
                raise DocumentConflict(
                    "A document already exists at the destination",
                    details={"path": relative},
                ) from error
            finally:
                if descriptor is not None:
                    osfd.close(descriptor)
                with suppress(OSError):
                    osfd.unlink(temporary, dir_fd=parent_fd)

    def _atomic_replace(
        self,
        relative: str,
        data: bytes,
        *,
        expected_sha256: str,
        document_id: str,
        proposed_sha256: str,
        snapshot_id: str,
    ) -> None:
        with self._parent_fd(relative) as (parent_fd, name):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                target_fd = osfd.open(name, flags, dir_fd=parent_fd)
            except OSError as error:
                raise DocumentConflict(
                    "Document changed or became unsafe",
                    details={"document_id": document_id, "current_sha256": None},
                ) from error
            try:
                before = os.fstat(target_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise DocumentPolicyError("Document target is not a safe regular file")
                chunks: list[bytes] = []
                consumed = 0
                while chunk := os.read(
                    target_fd, min(1024 * 1024, self.config.max_file_bytes + 1 - consumed)
                ):
                    chunks.append(chunk)
                    consumed += len(chunk)
                    if consumed > self.config.max_file_bytes:
                        raise DocumentPolicyError(
                            "Document changed beyond the configured size limit"
                        )
                current = b"".join(chunks)
            finally:
                osfd.close(target_fd)
            current_sha256 = sha256_hex(current)
            if current_sha256 != expected_sha256:
                raise self._conflict(
                    document_id=document_id,
                    expected_sha256=expected_sha256,
                    current_sha256=current_sha256,
                    proposed_sha256=proposed_sha256,
                    current=current,
                    snapshot_id=snapshot_id,
                )
            temporary = f".{name}.{secrets.token_hex(12)}.tmp"
            descriptor: int | None = None
            exchanged = False
            try:
                descriptor = osfd.open(
                    temporary,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    stat.S_IMODE(before.st_mode),
                    dir_fd=parent_fd,
                )
                self._write_all(descriptor, data)
                osfd.fsync(descriptor)
                osfd.close(descriptor)
                descriptor = None
                final = osfd.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
                if any(getattr(before, field) != getattr(final, field) for field in stable):
                    latest = self._read_current(relative)
                    raise self._conflict(
                        document_id=document_id,
                        expected_sha256=expected_sha256,
                        current_sha256=sha256_hex(latest),
                        proposed_sha256=proposed_sha256,
                        current=latest,
                        snapshot_id=snapshot_id,
                    )
                _exchange_names(parent_fd, temporary, name)
                exchanged = True
                displaced = self._read_name(parent_fd, temporary)
                displaced_sha256 = sha256_hex(displaced)
                if displaced_sha256 != expected_sha256:
                    _exchange_names(parent_fd, temporary, name)
                    exchanged = False
                    if contains_restricted_content(self.scanner, displaced, path=relative):
                        raise DocumentRestricted(
                            "Document changed to restricted content during save"
                        )
                    raise self._conflict(
                        document_id=document_id,
                        expected_sha256=expected_sha256,
                        current_sha256=displaced_sha256,
                        proposed_sha256=proposed_sha256,
                        current=displaced,
                        snapshot_id=snapshot_id,
                    )
                osfd.unlink(temporary, dir_fd=parent_fd)
                exchanged = False
                osfd.fsync(parent_fd)
            except BaseException:
                if exchanged:
                    try:
                        _exchange_names(parent_fd, temporary, name)
                        exchanged = False
                        osfd.fsync(parent_fd)
                    except BaseException as rollback_error:
                        raise DocumentPolicyError(
                            "Atomic document exchange rollback failed; recovery is required"
                        ) from rollback_error
                raise
            finally:
                if descriptor is not None:
                    osfd.close(descriptor)
                if not exchanged:
                    with suppress(OSError):
                        osfd.unlink(temporary, dir_fd=parent_fd)

    def _atomic_move(
        self,
        source: str,
        destination: str,
        *,
        expected_sha256: str,
        document_id: str,
        snapshot_id: str,
    ) -> None:
        with (
            self._parent_fd(source) as (source_parent, source_name),
            self._parent_fd(destination) as (destination_parent, destination_name),
        ):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            source_fd = osfd.open(source_name, flags, dir_fd=source_parent)
            try:
                before = os.fstat(source_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise DocumentPolicyError("Document source is not a safe regular file")
                chunks: list[bytes] = []
                consumed = 0
                while chunk := os.read(
                    source_fd,
                    min(1024 * 1024, self.config.max_file_bytes + 1 - consumed),
                ):
                    chunks.append(chunk)
                    consumed += len(chunk)
                    if consumed > self.config.max_file_bytes:
                        raise DocumentPolicyError("Document exceeds the configured size limit")
                current = b"".join(chunks)
                current_sha256 = sha256_hex(current)
                if current_sha256 != expected_sha256:
                    raise self._conflict(
                        document_id=document_id,
                        expected_sha256=expected_sha256,
                        current_sha256=current_sha256,
                        proposed_sha256=current_sha256,
                        current=current,
                        snapshot_id=snapshot_id,
                    )
                after = os.fstat(source_fd)
                stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
                if any(getattr(before, field) != getattr(after, field) for field in stable):
                    raise DocumentConflict(
                        "Document changed during move",
                        details={"document_id": document_id},
                    )
                try:
                    osfd.link(
                        source_name,
                        destination_name,
                        src_dir_fd=source_parent,
                        dst_dir_fd=destination_parent,
                        follow_symlinks=False,
                    )
                except FileExistsError as error:
                    raise DocumentConflict(
                        "A document already exists at the destination",
                        details={"path": destination},
                    ) from error
                try:
                    source_meta = osfd.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
                    destination_meta = osfd.stat(
                        destination_name,
                        dir_fd=destination_parent,
                        follow_symlinks=False,
                    )
                    if (
                        source_meta.st_dev != before.st_dev
                        or source_meta.st_ino != before.st_ino
                        or source_meta.st_size != before.st_size
                        or source_meta.st_mtime_ns != before.st_mtime_ns
                        or destination_meta.st_dev != before.st_dev
                        or destination_meta.st_ino != before.st_ino
                        or destination_meta.st_size != before.st_size
                        or destination_meta.st_mtime_ns != before.st_mtime_ns
                        or source_meta.st_nlink != 2
                        or destination_meta.st_nlink != 2
                    ):
                        raise DocumentConflict(
                            "Document changed during move",
                            details={"document_id": document_id},
                        )
                    osfd.unlink(source_name, dir_fd=source_parent)
                except BaseException:
                    with suppress(OSError):
                        osfd.unlink(destination_name, dir_fd=destination_parent)
                    raise
            finally:
                osfd.close(source_fd)
            osfd.fsync(source_parent)
            if destination_parent != source_parent:
                osfd.fsync(destination_parent)

    def _recover_link_move(self, source: str, destination: str) -> None:
        with (
            self._parent_fd(source) as (source_parent, source_name),
            self._parent_fd(destination) as (destination_parent, destination_name),
        ):
            try:
                source_meta = osfd.stat(
                    source_name,
                    dir_fd=source_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                source_meta = None
            try:
                destination_meta = osfd.stat(
                    destination_name,
                    dir_fd=destination_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                destination_meta = None
            if source_meta is None or destination_meta is None:
                return
            if (
                not stat.S_ISREG(source_meta.st_mode)
                or not stat.S_ISREG(destination_meta.st_mode)
                or source_meta.st_dev != destination_meta.st_dev
                or source_meta.st_ino != destination_meta.st_ino
                or source_meta.st_nlink != 2
                or destination_meta.st_nlink != 2
            ):
                raise DocumentConflict(
                    "Document move recovery encountered conflicting paths",
                    details={"old_path": source, "new_path": destination},
                )
            osfd.unlink(source_name, dir_fd=source_parent)
            osfd.fsync(source_parent)
            if destination_parent != source_parent:
                osfd.fsync(destination_parent)

    def _mkdir(self, relative: str) -> None:
        with self._parent_fd(relative) as (parent_fd, name):
            try:
                osfd.mkdir(name, mode=0o700, dir_fd=parent_fd)
                osfd.fsync(parent_fd)
            except FileExistsError as error:
                raise DocumentConflict(
                    "A file or folder already exists at the destination",
                    details={"path": relative},
                ) from error

    def _directory_exists(self, relative: str) -> bool:
        with self._parent_fd(relative) as (parent_fd, name):
            try:
                metadata = osfd.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise DocumentPolicyError("Folder recovery target is unsafe")
            return True
