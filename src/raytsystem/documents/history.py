from __future__ import annotations

import builtins
import difflib
import json
import re
import stat
from pathlib import Path
from typing import Any

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.documents.contracts import DocumentIndexError, DocumentNotFound, DocumentPolicyError
from raytsystem.documents.index import DocumentIndex
from raytsystem.documents.sensitivity import contains_restricted_content
from raytsystem.documents.subprocesses import (
    hardened_git_arguments,
    hardened_git_environment,
    run_bounded,
)
from raytsystem.platform_store import PlatformStoreError, open_platform_store_read_only
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner

_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REVISION = re.compile(r"^drev_[0-9a-f]{64}$")


class DocumentHistory:
    """Bounded Git and raytsystem revision history without invoking diff drivers."""

    def __init__(
        self,
        root: Path,
        *,
        index: DocumentIndex | None = None,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.index = index or DocumentIndex(self.root)
        self.scanner = scanner or SecretScanner()
        self.records_root = self.root / "ops" / "document-revisions" / "records"

    def list(
        self,
        document_id: str,
        *,
        expected_snapshot_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise DocumentIndexError("History limit is outside 1..200")
        offset = self._cursor(cursor)
        row = self.index.row_for_id(document_id)
        snapshot_id = str(row["snapshot_id"])
        if expected_snapshot_id is not None and expected_snapshot_id != snapshot_id:
            raise DocumentIndexError("Document index snapshot changed")
        relative = str(row["relative_path"])
        entries = [*self._git_entries(relative, limit=limit + offset + 1)]
        entries.extend(self._local_entries(document_id, limit=limit + offset + 1))
        entries.sort(
            key=lambda item: (str(item.get("recorded_at", "")), str(item["history_id"])),
            reverse=True,
        )
        page = entries[offset : offset + limit]
        return {
            "snapshot_id": snapshot_id,
            "document_id": document_id,
            "items": page,
            "next_cursor": str(offset + limit) if len(entries) > offset + limit else None,
            "sources": {
                "git": self._git_repository_available(),
                "raytsystem_revisions": self.records_root.is_dir(),
                "unsaved_diff": "client_session_only",
            },
        }

    def revision_bytes(
        self,
        revision_id: str,
        *,
        document_id: str,
        max_bytes: int,
    ) -> bytes:
        if _REVISION.fullmatch(revision_id) is None:
            raise DocumentNotFound("Document revision was not found")
        record = self._find_local_revision(revision_id)
        if record.get("document_id") != document_id:
            raise DocumentNotFound("Document revision was not found")
        relative = record.get("blob_path")
        expected = record.get("content_sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise DocumentIndexError("Document revision record is malformed")
        try:
            data = read_regular_file(self.root, relative, max_bytes=max_bytes).data
        except (OSError, PathPolicyError) as error:
            raise DocumentIndexError("Document revision bytes are unavailable") from error
        if sha256_hex(data) != expected:
            raise DocumentIndexError("Document revision hash changed")
        relative_path = str(record.get("relative_path", ""))
        if contains_restricted_content(self.scanner, data, path=relative_path or None):
            raise DocumentIndexError("Document revision content is restricted")
        return data

    def version_bytes(self, document_id: str, history_id: str, *, max_bytes: int) -> bytes:
        if _REVISION.fullmatch(history_id) is not None:
            return self.revision_bytes(
                history_id,
                document_id=document_id,
                max_bytes=max_bytes,
            )
        if history_id.startswith("git:") and _COMMIT.fullmatch(history_id[4:]) is not None:
            row = self.index.row_for_id(document_id)
            return self._git_bytes(history_id[4:], str(row["relative_path"]), max_bytes=max_bytes)
        raise DocumentNotFound("Document history version was not found")

    def detail(
        self,
        document_id: str,
        history_id: str,
        *,
        expected_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.index.row_for_id(document_id)
        if expected_snapshot_id is not None and expected_snapshot_id != str(row["snapshot_id"]):
            raise DocumentIndexError("Document index snapshot changed")
        relative = str(row["relative_path"])
        current = read_regular_file(
            self.root,
            relative,
            max_bytes=self.index.config.max_file_bytes,
        ).data
        version = self.version_bytes(
            document_id,
            history_id,
            max_bytes=self.index.config.max_file_bytes,
        )
        if contains_restricted_content(
            self.scanner, current, path=relative
        ) or contains_restricted_content(self.scanner, version, path=relative):
            raise DocumentIndexError("Document history content is restricted")
        try:
            current_text = current.decode("utf-8")
            version_text = version.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentIndexError("Document history version is not UTF-8") from error
        diff, truncated = _bounded_diff(current_text, version_text, tofile=history_id)
        return {
            "snapshot_id": row["snapshot_id"],
            "document_id": document_id,
            "revision_id": history_id,
            "content": version_text,
            "content_sha256": sha256_hex(version),
            "current_sha256": sha256_hex(current),
            "diff": diff,
            "diff_truncated": truncated,
        }

    def _git_entries(self, relative: str, *, limit: int) -> builtins.list[dict[str, Any]]:
        if not self._git_repository_available():
            return []
        try:
            completed = run_bounded(
                (
                    *hardened_git_arguments(self.root),
                    "log",
                    "--no-show-signature",
                    "--follow",
                    f"--max-count={min(limit, 201)}",
                    "--format=%x1e%H%x1f%cI%x1f%an",
                    "--name-status",
                    "-z",
                    "--",
                    relative,
                ),
                environment=hardened_git_environment(),
                stdout_limit=2 * 1024 * 1024,
            )
        except (OSError, ValueError):
            return []
        if completed.returncode != 0 or completed.overflowed or completed.timed_out:
            return []
        entries: list[dict[str, Any]] = []
        historical_path = relative
        for raw in completed.stdout.split(b"\x1e"):
            header, separator, changes = raw.partition(b"\0")
            if not separator:
                continue
            try:
                fields = header.decode("utf-8").strip().split("\x1f")
            except UnicodeDecodeError:
                return []
            if len(fields) != 3 or _COMMIT.fullmatch(fields[0]) is None:
                continue
            exists, prior_path = self._git_change_paths(changes, historical_path)
            try:
                self.index.policy.require_visible(historical_path)
            except DocumentPolicyError:
                break
            author = fields[2].strip()[:128]
            if not author or contains_restricted_content(
                self.scanner, author.encode("utf-8"), path=None
            ):
                safe_author: str | None = None
            else:
                safe_author = author
            if exists:
                entries.append(
                    {
                        "history_id": f"git:{fields[0]}",
                        "revision_id": f"git:{fields[0]}",
                        "source": "git",
                        "commit_sha": fields[0],
                        "recorded_at": fields[1],
                        "author": safe_author,
                        "content_sha256": None,
                        "operation": "commit",
                        "summary": "Git commit",
                        "historical_path": historical_path,
                    }
                )
            if prior_path is not None:
                historical_path = prior_path
        return entries

    def _local_entries(self, document_id: str, *, limit: int) -> builtins.list[dict[str, Any]]:
        indexed = self._indexed_local_entries(document_id, limit=limit)
        if indexed:
            return indexed
        if not self.records_root.is_dir() or self.records_root.is_symlink():
            return []
        entries: list[dict[str, Any]] = []
        inspected = 0
        for path in sorted(self.records_root.glob("drev_*.json"), reverse=True):
            inspected += 1
            if inspected > 10_000 or len(entries) >= limit:
                break
            relative = path.relative_to(self.root).as_posix()
            try:
                data = read_regular_file(self.root, relative, max_bytes=64 * 1024).data
                payload = json.loads(data)
            except (OSError, PathPolicyError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("document_id") != document_id:
                continue
            recorded_hash = payload.get("record_sha256")
            material = dict(payload)
            material.pop("record_sha256", None)
            if (
                not isinstance(recorded_hash, str)
                or recorded_hash != sha256_hex(canonical_json_bytes(material))
                or payload.get("revision_id") != path.stem
            ):
                continue
            entries.append(
                {
                    "history_id": str(payload["revision_id"]),
                    "revision_id": str(payload["revision_id"]),
                    "source": "raytsystem",
                    "commit_sha": None,
                    "recorded_at": str(payload.get("recorded_at", "")),
                    "author": "local_user",
                    "content_sha256": payload.get("content_sha256"),
                    "operation": payload.get("operation", "update"),
                    "summary": f"raytsystem {payload.get('operation', 'update')}",
                }
            )
        return entries

    def _indexed_local_entries(
        self,
        document_id: str,
        *,
        limit: int,
    ) -> builtins.list[dict[str, Any]]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return []
        try:
            events = store.list_events(f"document:{document_id}", limit=1000)
        except (PlatformStoreError, ValueError):
            return []
        finally:
            store.close()
        entries: builtins.list[dict[str, Any]] = []
        for event in reversed(events):
            payload = event.get("payload")
            revision_id = payload.get("revision_id") if isinstance(payload, dict) else None
            if not isinstance(revision_id, str) or _REVISION.fullmatch(revision_id) is None:
                continue
            try:
                revision = self._find_local_revision(revision_id)
            except (DocumentNotFound, DocumentIndexError):
                continue
            if revision.get("document_id") != document_id:
                continue
            operation = str(revision.get("operation", "update"))
            entries.append(
                {
                    "history_id": revision_id,
                    "revision_id": revision_id,
                    "source": "raytsystem",
                    "commit_sha": None,
                    "recorded_at": str(revision.get("recorded_at", event["recorded_at"])),
                    "author": "local_user",
                    "content_sha256": revision.get("content_sha256"),
                    "operation": operation,
                    "summary": f"raytsystem {operation}",
                }
            )
            if len(entries) >= limit:
                break
        return entries

    def _find_local_revision(self, revision_id: str) -> dict[str, Any]:
        relative = f"ops/document-revisions/records/{revision_id}.json"
        try:
            data = read_regular_file(self.root, relative, max_bytes=64 * 1024).data
            payload = json.loads(data)
        except (OSError, PathPolicyError, json.JSONDecodeError) as error:
            raise DocumentNotFound("Document revision was not found") from error
        if not isinstance(payload, dict) or payload.get("revision_id") != revision_id:
            raise DocumentIndexError("Document revision record is malformed")
        recorded_hash = payload.get("record_sha256")
        material = dict(payload)
        material.pop("record_sha256", None)
        if not isinstance(recorded_hash, str) or recorded_hash != sha256_hex(
            canonical_json_bytes(material)
        ):
            raise DocumentIndexError("Document revision record hash changed")
        return payload

    def _git_bytes(self, commit: str, relative: str, *, max_bytes: int) -> bytes:
        if not self._git_repository_available() or _COMMIT.fullmatch(commit) is None:
            raise DocumentNotFound("Git document version was not found")
        matching = next(
            (
                entry
                for entry in self._git_entries(relative, limit=201)
                if entry.get("commit_sha") == commit
            ),
            None,
        )
        historical_path = matching.get("historical_path") if matching is not None else None
        if not isinstance(historical_path, str):
            raise DocumentNotFound("Git document version was not found")
        environment = hardened_git_environment()
        try:
            object_result = run_bounded(
                (
                    *hardened_git_arguments(self.root),
                    "rev-parse",
                    "--verify",
                    f"{commit}:{historical_path}",
                ),
                environment=environment,
                stdout_limit=128,
            )
            object_id = object_result.stdout.decode("ascii").strip()
            if (
                object_result.returncode != 0
                or object_result.overflowed
                or object_result.timed_out
                or _COMMIT.fullmatch(object_id) is None
            ):
                raise DocumentNotFound("Git document version was not found")
            size_result = run_bounded(
                (*hardened_git_arguments(self.root), "cat-file", "-s", object_id),
                environment=environment,
                stdout_limit=64,
            )
            size = int(size_result.stdout.decode("ascii").strip())
            if (
                size_result.returncode != 0
                or size_result.overflowed
                or size_result.timed_out
                or not 0 <= size <= max_bytes
            ):
                raise DocumentNotFound("Git document version was not found")
            completed = run_bounded(
                (*hardened_git_arguments(self.root), "cat-file", "blob", object_id),
                environment=environment,
                stdout_limit=max_bytes,
            )
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise DocumentNotFound("Git document version was not found") from error
        if completed.returncode != 0 or completed.overflowed or completed.timed_out:
            raise DocumentNotFound("Git document version was not found")
        if contains_restricted_content(self.scanner, completed.stdout, path=relative):
            raise DocumentIndexError("Git document version is restricted")
        return completed.stdout

    def _git_repository_available(self) -> bool:
        try:
            metadata = (self.root / ".git").lstat()
        except OSError:
            return False
        return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)

    @staticmethod
    def _git_change_paths(changes: bytes, current: str) -> tuple[bool, str | None]:
        tokens = changes.split(b"\0")
        index = 0
        exists = True
        prior_path: str | None = None
        while index < len(tokens):
            raw_status = tokens[index].lstrip(b"\r\n")
            index += 1
            if not raw_status:
                continue
            try:
                status = raw_status.decode("ascii")
            except UnicodeDecodeError:
                continue
            path_count = 2 if status[:1] in {"R", "C"} else 1
            if index + path_count > len(tokens):
                break
            try:
                paths = [tokens[index + offset].decode("utf-8") for offset in range(path_count)]
            except UnicodeDecodeError:
                index += path_count
                continue
            index += path_count
            if status.startswith("D") and paths[0] == current:
                exists = False
            if status.startswith("R") and len(paths) == 2 and paths[1] == current:
                prior_path = paths[0]
        return exists, prior_path

    @staticmethod
    def _cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not cursor.isascii() or not cursor.isdigit() or not 0 <= int(cursor) <= 10_000:
            raise DocumentIndexError("History cursor is invalid")
        return int(cursor)


def _bounded_diff(current: str, version: str, *, tofile: str) -> tuple[str | None, bool]:
    if len(current.encode("utf-8")) > 1024 * 1024 or len(version.encode("utf-8")) > 1024 * 1024:
        return None, True
    current_lines = current.splitlines(keepends=True)
    version_lines = version.splitlines(keepends=True)
    if len(current_lines) > 20_000 or len(version_lines) > 20_000:
        return None, True
    diff = "".join(
        difflib.unified_diff(
            current_lines,
            version_lines,
            fromfile="current",
            tofile=tofile,
            n=3,
        )
    )
    if len(diff.encode("utf-8")) > 1024 * 1024:
        return diff[: 1024 * 1024] + "\n… diff truncated …\n", True
    return diff, False
