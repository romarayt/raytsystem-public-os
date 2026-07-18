from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import closing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from raytsystem.security import osfd
from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.derived import assert_safe_sqlite_family
from raytsystem.documents.config import load_document_config
from raytsystem.documents.contracts import (
    DocumentConfig,
    DocumentIndexError,
    DocumentMode,
    DocumentNotFound,
    DocumentPolicyError,
    DocumentRestricted,
    IndexedDocument,
    MarkdownMetadata,
)
from raytsystem.documents.markdown import extract_markdown_metadata, line_ending_metadata
from raytsystem.documents.policy import DocumentPolicy
from raytsystem.documents.sensitivity import contains_restricted_content
from raytsystem.documents.subprocesses import (
    hardened_git_arguments,
    hardened_git_environment,
    run_bounded,
)
from raytsystem.io import UnsafeWritePath, ensure_safe_directory
from raytsystem.platform_store import (
    PlatformStoreError,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import fsync_directory

_SCHEMA_VERSION = "3"
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_PUBLIC_ID = re.compile(r"^doc_[0-9a-f]{64}$")
_QUERY_PART = re.compile(r'"([^"\r\n]+)"|(\S+)')
_WORD = re.compile(r"[^\W_]+", flags=re.UNICODE)
_HTML_FRAGMENT = re.compile(r"<\/?[A-Za-z][^>\r\n]*>")
_FOOTNOTE = re.compile(r"(?:^|\n)\[\^[^\]\r\n]+\]:|\[\^[^\]\r\n]+\]")
_NEW_WINDOW_SECONDS = 30 * 24 * 60 * 60
_MAX_TOTAL_LINKS = 2_000_000
_MODIFIED_STATUSES = frozenset(
    {"added", "deleted", "hash_modified", "modified", "mtime_modified", "renamed", "untracked"}
)
_SORT_SQL = {
    "modified_desc": "d.mtime_ns DESC, d.relative_path COLLATE NOCASE, d.document_id",
    "added_desc": "d.first_seen_at DESC, d.relative_path COLLATE NOCASE, d.document_id",
    "name_asc": "d.filename COLLATE NOCASE, d.relative_path COLLATE NOCASE, d.document_id",
    "name_desc": "d.filename COLLATE NOCASE DESC, d.relative_path COLLATE NOCASE, d.document_id",
    "size_desc": "d.size_bytes DESC, d.relative_path COLLATE NOCASE, d.document_id",
    "folder_asc": "d.relative_path COLLATE NOCASE, d.document_id",
    "backlinks_desc": "backlink_count DESC, d.relative_path COLLATE NOCASE, d.document_id",
    "links_desc": "outgoing_count DESC, d.relative_path COLLATE NOCASE, d.document_id",
}


@dataclass(frozen=True)
class _SearchSpec:
    expression: str | None
    path_prefix: str | None
    extension: str | None
    tag: str | None
    property_key: str | None
    property_value: str | None
    state: str | None
    after_ns: int | None
    before_ns: int | None


@dataclass(frozen=True)
class _ScannedDocument:
    document_id: str
    relative_path: str
    first_seen_at: str
    size_bytes: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mtime_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _flatten_properties(value: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key, item in sorted(value.items()):
        if isinstance(item, list):
            chunks.extend(f"{key}:{entry}" for entry in item)
        else:
            chunks.append(f"{key}:{item}")
    return " ".join(chunks)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isascii() or not cursor.isdigit():
        raise DocumentIndexError("Pagination cursor is invalid")
    value = int(cursor)
    if not 0 <= value <= 1_000_000:
        raise DocumentIndexError("Pagination cursor is outside the supported range")
    return value


def _image_info(data: bytes) -> tuple[str, int, int] | None:
    mime: str | None = None
    width = 0
    height = 0
    if (
        len(data) >= 24
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        and data[8:12] == b"\x00\x00\x00\r"
        and data[12:16] == b"IHDR"
        and len(data) >= 45
        and data[-12:-8] == b"\x00\x00\x00\x00"
        and data[-8:-4] == b"IEND"
    ):
        mime = "image/png"
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif len(data) >= 14 and data.startswith((b"GIF87a", b"GIF89a")) and data.endswith(b"\x3b"):
        mime = "image/gif"
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
    elif len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        position = 2
        start_of_frame = frozenset(
            {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        )
        while position + 4 <= len(data):
            if data[position] != 0xFF:
                position += 1
                continue
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or position + 2 > len(data):
                break
            length = int.from_bytes(data[position : position + 2], "big")
            if length < 2 or position + length > len(data):
                break
            if marker in start_of_frame and length >= 7:
                mime = "image/jpeg"
                height = int.from_bytes(data[position + 3 : position + 5], "big")
                width = int.from_bytes(data[position + 5 : position + 7], "big")
                break
            position += length
    elif (
        len(data) >= 25
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
        and int.from_bytes(data[4:8], "little") + 8 <= len(data)
    ):
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
        elif chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        if width and height:
            mime = "image/webp"
    if (
        mime is None
        or width <= 0
        or height <= 0
        or width > 16_384
        or height > 16_384
        or width * height > 100_000_000
    ):
        return None
    return mime, width, height


class DocumentIndex:
    """Disposable, policy-bound file metadata and FTS5 projection."""

    def __init__(
        self,
        root: Path,
        *,
        config: DocumentConfig | None = None,
        scanner: SecretScanner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config or load_document_config(self.root)
        self.policy = DocumentPolicy(self.config)
        self.scanner = scanner or SecretScanner()
        self.path = self.root / self.config.index_db

    def roots(self) -> list[dict[str, Any]]:
        return [
            {
                "root_id": item.root_id,
                "label": PurePosixPath(item.path).name or item.root_id,
                "path": item.path,
                "mode": item.mode.value,
                "kind": item.kind,
                "editable": item.mode.editable,
            }
            for item in sorted(self.config.roots, key=lambda value: value.path.casefold())
            if item.mode is not DocumentMode.HIDDEN
        ]

    def status(self) -> dict[str, Any]:
        if not self.path.exists() and not self.path.is_symlink():
            return {
                "state": "missing",
                "snapshot_id": None,
                "file_count": 0,
                "last_refresh_at": None,
                "error_count": 0,
                "roots": self.roots(),
            }
        try:
            with closing(self._read_connection()) as connection, connection:
                metadata = self._metadata(connection)
                count_row = connection.execute("SELECT COUNT(*) FROM documents").fetchone()
        except (OSError, sqlite3.Error, DocumentIndexError, UnsafeWritePath):
            return {
                "state": "error",
                "snapshot_id": None,
                "file_count": 0,
                "last_refresh_at": None,
                "error_count": 1,
                "roots": self.roots(),
            }
        state = metadata.get("state", "error")
        if metadata.get("config_sha256") != self.config.config_sha256:
            state = "stale"
        freshness_message: str | None = None
        built_at = metadata.get("built_at")
        if state == "current" and built_at is not None:
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(built_at.replace("Z", "+00:00"))
            except ValueError:
                state = "stale"
                freshness_message = "Index refresh time is invalid."
            else:
                if age.total_seconds() > 60:
                    state = "stale"
                    freshness_message = "Index freshness window expired; refresh is required."
        return {
            "state": state,
            "snapshot_id": metadata.get("snapshot_id"),
            "file_count": 0 if count_row is None else int(count_row[0]),
            "last_refresh_at": built_at,
            "error_count": int(metadata.get("error_count", "0")),
            "roots": self.roots(),
            "message": freshness_message,
        }

    def rebuild(self) -> dict[str, Any]:
        try:
            assert_safe_sqlite_family(self.path)
            ensure_safe_directory(self.path.parent, mode=0o700)
        except (OSError, UnsafeWritePath) as error:
            raise DocumentIndexError("Document index path is unsafe") from error
        previous_projection = self._previous_projection()
        identities = self._identity_map()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        osfd.close(descriptor)
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o600)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(temporary)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            active_connection = connection
            self._create_schema(connection)
            self._insert_roots(connection)
            total_links = 0

            def insert_document(item: IndexedDocument) -> None:
                nonlocal total_links
                total_links += len(item.links)
                if total_links > _MAX_TOTAL_LINKS:
                    raise DocumentIndexError("Document roots exceed the global link limit")
                self._insert_document(active_connection, item)

            documents, error_count = self._scan_stream(
                previous_projection,
                identities,
                on_document=insert_document,
            )
            folders = self._scan_folders(documents)
            self._insert_folders(connection, folders)
            self._resolve_links(connection)
            self._persist_identities(documents, identities)
            snapshot_id = self._database_snapshot(connection, error_count=error_count)
            built_at = _now()
            metadata = {
                "schema_version": _SCHEMA_VERSION,
                "config_sha256": self.config.config_sha256,
                "snapshot_id": snapshot_id,
                "state": "current",
                "built_at": built_at,
                "error_count": str(error_count),
                "file_count": str(len(documents)),
            }
            connection.executemany(
                "INSERT INTO meta(key,value) VALUES (?,?)", sorted(metadata.items())
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]) != "ok":
                raise DocumentIndexError("Document index integrity check failed")
            connection.close()
            connection = None
            # "rb+" instead of "rb": Windows FlushFileBuffers requires a
            # writable handle, while POSIX fsync accepts either.
            with temporary.open("rb+") as handle:
                osfd.fsync(handle.fileno())
            assert_safe_sqlite_family(self.path)
            osfd.replace(temporary, self.path)
            fsync_directory(self.path.parent)
        except (OSError, sqlite3.Error, UnsafeWritePath) as error:
            raise DocumentIndexError("Document index rebuild failed") from error
        finally:
            if connection is not None:
                connection.close()
            temporary.unlink(missing_ok=True)
            for suffix in ("-journal", "-wal", "-shm"):
                Path(f"{temporary}{suffix}").unlink(missing_ok=True)
        return self.status()

    def refresh(self, paths: tuple[str, ...] = ()) -> dict[str, Any]:
        """Refresh specified files when possible; a missing/stale DB rebuilds safely."""

        status = self.status()
        if not paths or status["state"] != "current":
            return self.rebuild()
        if len(paths) > 256:
            raise DocumentIndexError("Incremental refresh path count exceeds 256")
        normalized = tuple(dict.fromkeys(self.policy.decide(path).relative_path for path in paths))
        git = self._git_status_map()
        identities = self._identity_map()
        with closing(self._write_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for relative in normalized:
                    previous = connection.execute(
                        "SELECT document_id,first_seen_at,content_sha256,mtime_ns,git_status "
                        "FROM documents WHERE relative_path=?",
                        (relative,),
                    ).fetchone()
                    identity = identities.get(relative)
                    if identity is None and previous is not None:
                        identity = (str(previous["document_id"]), str(previous["first_seen_at"]))
                    first_seen = _now() if identity is None else identity[1]
                    self._delete_document(connection, relative)
                    self._sync_folder_projection(connection, relative)
                    item = self._scan_file(
                        relative,
                        first_seen=first_seen,
                        git=git,
                        document_id=None if identity is None else identity[0],
                        previous=(
                            None
                            if previous is None
                            else (
                                str(previous["content_sha256"]),
                                int(previous["mtime_ns"]),
                                str(previous["git_status"]),
                            )
                        ),
                    )
                    if item is not None:
                        existing_links = int(
                            connection.execute("SELECT COUNT(*) FROM document_links").fetchone()[0]
                        )
                        if existing_links + len(item.links) > _MAX_TOTAL_LINKS:
                            raise DocumentIndexError("Document roots exceed the global link limit")
                        self.ensure_identity(
                            relative,
                            document_id=item.document_id,
                            first_seen=item.first_seen_at,
                        )
                        identities[relative] = (item.document_id, item.first_seen_at)
                        self._insert_document(connection, item)
                self._resolve_links(connection)
                self._update_folder_counts(connection)
                error_count = int(self._metadata(connection).get("error_count", "0"))
                snapshot_id = self._database_snapshot(
                    connection,
                    error_count=error_count,
                )
                built_at = _now()
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES ('snapshot_id',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (snapshot_id,),
                )
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES ('built_at',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (built_at,),
                )
                count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES ('file_count',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(count),),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return self.status()

    def list_documents(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        root_id: str | None = None,
        mode: str | None = None,
        folder: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
        document_ids: tuple[str, ...] = (),
        modified_only: bool = False,
        sort: str = "modified_desc",
    ) -> dict[str, Any]:
        page_size = self._limit(limit)
        offset = _decode_cursor(cursor)
        order = self._sort(sort)
        clauses: list[str] = []
        values: list[object] = []
        if document_ids:
            if (
                len(document_ids) > 100
                or len(set(document_ids)) != len(document_ids)
                or any(_PUBLIC_ID.fullmatch(document_id) is None for document_id in document_ids)
            ):
                raise DocumentIndexError("Document ID filter is invalid")
            clauses.append("d.document_id IN (" + ",".join("?" for _ in document_ids) + ")")
            values.extend(document_ids)
        if root_id is not None:
            self.policy.root(root_id)
            clauses.append("d.root_id=?")
            values.append(root_id)
        if mode is not None:
            try:
                safe_mode = DocumentMode(mode)
            except ValueError as error:
                raise DocumentIndexError("Document mode filter is invalid") from error
            clauses.append("d.policy_mode=?")
            values.append(safe_mode.value)
        if folder is not None:
            safe_folder = self.policy.require_visible(folder).relative_path.rstrip("/")
            clauses.append("(d.relative_path=? OR d.relative_path LIKE ? ESCAPE '\\')")
            escaped = safe_folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend((safe_folder, f"{escaped}/%"))
        if kind is not None:
            if not kind or len(kind) > 64 or any(character.isspace() for character in kind):
                raise DocumentIndexError("Document kind filter is invalid")
            clauses.append("d.kind=?")
            values.append(kind)
        if tag is not None:
            safe_tag = tag.strip().lstrip("#")
            if not safe_tag or len(safe_tag) > 128:
                raise DocumentIndexError("Document tag filter is invalid")
            clauses.append(
                "EXISTS (SELECT 1 FROM document_tags explicit_tag "
                "WHERE explicit_tag.document_id=d.document_id AND explicit_tag.tag=?)"
            )
            values.append(safe_tag.casefold())
        if modified_only:
            placeholders = ",".join("?" for _ in _MODIFIED_STATUSES)
            clauses.append(f"d.git_status IN ({placeholders})")
            values.extend(sorted(_MODIFIED_STATUSES))
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        sql = self._document_select() + where + f" ORDER BY {order} LIMIT ? OFFSET ?"
        values.extend((page_size + 1, offset))
        with closing(self._read_connection()) as connection, connection:
            snapshot = self._require_current(connection)
            rows = connection.execute(sql, values).fetchall()
            index_status, folders = self._envelope_context(connection, snapshot)
        return self._page(
            snapshot,
            rows,
            page_size,
            offset,
            index_status=index_status,
            folders=folders,
        )

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        root_id: str | None = None,
        folder: str | None = None,
        kind: str | None = None,
        mode: str | None = None,
        tag: str | None = None,
        sort: str = "modified_desc",
    ) -> dict[str, Any]:
        page_size = self._limit(limit)
        offset = _decode_cursor(cursor)
        spec = self._parse_query(query)
        order = self._sort(sort)
        joins: list[str] = []
        clauses: list[str] = []
        values: list[object] = []
        select = self._document_select()
        if spec.expression is not None:
            joins.append("JOIN document_fts ON document_fts.document_id=d.document_id")
            clauses.append("document_fts MATCH ?")
            values.append(spec.expression)
            select = "SELECT d.*,snippet(document_fts,9,'⟦','⟧','…',24) AS snippet FROM documents d"
        if root_id is not None:
            self.policy.root(root_id)
            clauses.append("d.root_id=?")
            values.append(root_id)
        if folder is not None:
            safe_folder = self.policy.require_visible(folder).relative_path.rstrip("/")
            escaped_folder = (
                safe_folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append("(d.relative_path=? OR d.relative_path LIKE ? ESCAPE '\\')")
            values.extend((safe_folder, f"{escaped_folder}/%"))
        if kind is not None:
            if not kind or len(kind) > 64 or any(character.isspace() for character in kind):
                raise DocumentIndexError("Document kind filter is invalid")
            clauses.append("d.kind=?")
            values.append(kind)
        if mode is not None:
            try:
                safe_mode = DocumentMode(mode)
            except ValueError as error:
                raise DocumentIndexError("Document mode filter is invalid") from error
            clauses.append("d.policy_mode=?")
            values.append(safe_mode.value)
        if spec.path_prefix is not None:
            clauses.append("d.relative_path LIKE ? ESCAPE '\\'")
            escaped = spec.path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"{escaped}%")
        if spec.extension is not None:
            clauses.append("d.extension=?")
            values.append(spec.extension)
        if spec.tag is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM document_tags t "
                "WHERE t.document_id=d.document_id AND t.tag=?)"
            )
            values.append(spec.tag.casefold())
        if tag is not None:
            safe_tag = tag.strip().lstrip("#")
            if not safe_tag or len(safe_tag) > 128:
                raise DocumentIndexError("Document tag filter is invalid")
            clauses.append(
                "EXISTS (SELECT 1 FROM document_tags explicit_tag "
                "WHERE explicit_tag.document_id=d.document_id AND explicit_tag.tag=?)"
            )
            values.append(safe_tag.casefold())
        if spec.property_key is not None:
            property_clause = (
                "EXISTS (SELECT 1 FROM document_properties p WHERE p.document_id=d.document_id "
                "AND p.property_key=?"
            )
            values.append(spec.property_key.casefold())
            if spec.property_value is not None:
                property_clause += " AND p.property_value=?"
                values.append(spec.property_value.casefold())
            clauses.append(property_clause + ")")
        if spec.state == "modified":
            placeholders = ",".join("?" for _ in _MODIFIED_STATUSES)
            clauses.append(f"d.git_status IN ({placeholders})")
            values.extend(sorted(_MODIFIED_STATUSES))
        elif spec.state == "new":
            clauses.append("(d.git_status IN ('added','untracked') OR d.first_seen_at>=?)")
            values.append(
                (datetime.now(UTC) - timedelta(seconds=_NEW_WINDOW_SECONDS))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        elif spec.state == "readonly":
            clauses.append("d.policy_mode!='read_write'")
        if spec.after_ns is not None:
            clauses.append("d.mtime_ns>=?")
            values.append(spec.after_ns)
        if spec.before_ns is not None:
            clauses.append("d.mtime_ns<?")
            values.append(spec.before_ns)
        sql = select + (" " + " ".join(joins) if joins else "")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        values.extend((page_size + 1, offset))
        deadline = time.monotonic_ns() + self.config.search_timeout_ms * 1_000_000
        try:
            with closing(self._read_connection()) as connection, connection:
                snapshot = self._require_current(connection)
                connection.set_progress_handler(
                    lambda: 1 if time.monotonic_ns() > deadline else 0,
                    1_000,
                )
                rows = connection.execute(sql, values).fetchall()
                connection.set_progress_handler(None, 0)
                index_status, folders = self._envelope_context(connection, snapshot)
        except sqlite3.OperationalError as error:
            raise DocumentIndexError("Document search exceeded its safe query budget") from error
        return self._page(
            snapshot,
            rows,
            page_size,
            offset,
            index_status=index_status,
            folders=folders,
        )

    def recent(
        self,
        *,
        kind: Literal["recent", "modified", "added"],
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        sort = "added_desc" if kind == "added" else "modified_desc"
        result = self.list_documents(
            limit=limit,
            cursor=cursor,
            modified_only=kind == "modified",
            sort=sort,
        )
        result["recent_kind"] = kind
        for item in result["items"]:
            if kind == "added":
                item["recent_source"] = (
                    "git_untracked"
                    if item["git_status"] in {"added", "untracked"}
                    else "raytsystem_first_seen"
                )
            else:
                item["recent_source"] = (
                    "git_worktree"
                    if item["git_status"]
                    in {"added", "deleted", "modified", "renamed", "untracked"}
                    else "index_hash_or_mtime"
                )
        return result

    def detail(
        self, document_id: str, *, expected_snapshot_id: str | None = None
    ) -> dict[str, Any]:
        row, snapshot = self._document_row(document_id)
        if expected_snapshot_id is not None and expected_snapshot_id != snapshot:
            raise DocumentIndexError("Document index snapshot changed")
        public = self._public_document(row)
        if str(row["sensitivity"]) == "restricted":
            raise DocumentRestricted("Document content is restricted by the sensitivity gate")
        relative = str(row["relative_path"])
        decision = self.policy.require_visible(relative)
        try:
            result = read_regular_file(self.root, relative, max_bytes=self.config.max_file_bytes)
        except (OSError, PathPolicyError) as error:
            raise DocumentIndexError("Document is missing or unsafe") from error
        digest = sha256_hex(result.data)
        if digest != str(row["content_sha256"]):
            raise DocumentIndexError("Document changed after the current index snapshot")
        content: str | None
        line_ending: str | None
        final_newline: bool | None
        try:
            content = result.data.decode("utf-8")
            line_ending, final_newline = line_ending_metadata(content)
        except UnicodeDecodeError:
            content = None
            line_ending = None
            final_newline = None
        with closing(self._read_connection()) as connection, connection:
            asset_rows = connection.execute(
                "SELECT l.raw_target,l.target_document_id,d.extension,d.sensitivity "
                "FROM document_links l JOIN documents d ON d.document_id=l.target_document_id "
                "WHERE l.source_document_id=? AND l.embed=1 AND l.resolution='resolved'",
                (document_id,),
            ).fetchall()
        assets = {
            str(asset["raw_target"]): (f"/api/v1/documents/assets/{asset['target_document_id']}")
            for asset in asset_rows
            if str(asset["extension"]).casefold() in _IMAGE_SUFFIXES
            and str(asset["sensitivity"]) != "restricted"
        }
        extension = Path(relative).suffix.casefold()
        detected_image = _image_info(result.data) if extension in _IMAGE_SUFFIXES else None
        if extension in _MARKDOWN_SUFFIXES:
            document_format = "markdown"
        elif detected_image is not None:
            document_format = "image"
            content = None
            line_ending = None
            final_newline = None
        elif content is not None:
            document_format = "text"
        else:
            document_format = "unsupported"
        warning_list = list(json.loads(str(row["warnings_json"])))
        if line_ending == "mixed":
            warning_list.append("mixed_line_endings")
            line_ending = "lf"
        qualification = self._visual_qualification(content, warning_list)
        return {
            "snapshot_id": snapshot,
            "document": public | {"mode": decision.mode.value},
            "content": content,
            "format": document_format,
            "content_sha256": digest,
            "line_ending": line_ending,
            "final_newline": final_newline,
            "warnings": list(dict.fromkeys(warning_list)),
            "assets": assets,
            "asset_url": (
                f"/api/v1/documents/assets/{document_id}" if detected_image is not None else None
            ),
            "image": (
                {
                    "mime_type": detected_image[0],
                    "width": detected_image[1],
                    "height": detected_image[2],
                }
                if detected_image is not None
                else None
            ),
            "frontmatter": self._frontmatter_fields(public["properties"]),
            "visual_qualification": qualification,
        }

    def asset_bytes(self, asset_id: str) -> tuple[bytes, str]:
        row, _ = self._document_row(asset_id)
        relative = str(row["relative_path"])
        if (
            Path(relative).suffix.casefold() not in _IMAGE_SUFFIXES
            or str(row["sensitivity"]) == "restricted"
        ):
            raise DocumentNotFound("Document asset was not found")
        self.policy.require_visible(relative)
        try:
            result = read_regular_file(self.root, relative, max_bytes=self.config.max_file_bytes)
        except (OSError, PathPolicyError) as error:
            raise DocumentNotFound("Document asset was not found") from error
        if sha256_hex(result.data) != str(row["content_sha256"]):
            raise DocumentIndexError("Document asset changed after the current index snapshot")
        info = _image_info(result.data)
        if info is None:
            raise DocumentNotFound("Document asset was not found")
        return result.data, info[0]

    def links(
        self,
        document_id: str,
        *,
        backlinks: bool = False,
        expected_snapshot_id: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 2_000:
            raise DocumentIndexError("Link result limit is outside 1..2000")
        offset = _decode_cursor(cursor)
        _, snapshot = self._document_row(document_id)
        if expected_snapshot_id is not None and expected_snapshot_id != snapshot:
            raise DocumentIndexError("Document index snapshot changed")
        if backlinks:
            where = "l.target_document_id=?"
            join_id = "l.source_document_id"
        else:
            where = "l.source_document_id=?"
            join_id = "l.target_document_id"
        with closing(self._read_connection()) as connection, connection:
            rows = connection.execute(
                "SELECT l.*, d.relative_path AS related_path, d.title AS related_title, "
                "d.policy_mode AS related_mode FROM document_links l "
                f"LEFT JOIN documents d ON d.document_id={join_id} WHERE {where} "
                "ORDER BY l.context,l.link_id LIMIT ? OFFSET ?",
                (document_id, limit + 1, offset),
            ).fetchall()
            candidate_ids = sorted(
                {
                    str(candidate)
                    for row in rows[:limit]
                    for candidate in json.loads(str(row["candidates_json"]))[:20]
                }
            )
            candidate_map: dict[str, dict[str, str]] = {}
            for start in range(0, len(candidate_ids), 500):
                batch = candidate_ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                candidate_rows = connection.execute(
                    f"SELECT document_id,title,relative_path FROM documents "
                    f"WHERE document_id IN ({placeholders}) ORDER BY relative_path",
                    tuple(batch),
                ).fetchall()
                candidate_map.update(
                    {
                        str(candidate["document_id"]): {
                            "document_id": str(candidate["document_id"]),
                            "title": str(candidate["title"]),
                            "path": str(candidate["relative_path"]),
                        }
                        for candidate in candidate_rows
                    }
                )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items: list[dict[str, Any]] = []
        for row in rows:
            if backlinks:
                items.append(
                    {
                        "source_document_id": str(row["source_document_id"]),
                        "source_title": str(row["related_title"]),
                        "source_path": str(row["related_path"]),
                        "line": None,
                        "context": str(row["context"]),
                    }
                )
                continue
            candidate_ids = json.loads(str(row["candidates_json"]))
            candidates = [
                candidate_map[str(candidate)]
                for candidate in candidate_ids[:20]
                if str(candidate) in candidate_map
            ]
            items.append(
                {
                    "target": str(row["raw_target"]),
                    "target_document_id": row["target_document_id"],
                    "label": str(row["alias"] or row["raw_target"]),
                    "heading": row["heading"],
                    "line": None,
                    "context": str(row["context"]),
                    "ambiguous": str(row["resolution"]) == "ambiguous",
                    "candidate_count": int(row["candidate_count"]),
                    "candidates": candidates,
                }
            )
        return {
            "snapshot_id": snapshot,
            "document_id": document_id,
            "direction": "backlinks" if backlinks else "outgoing",
            "items": items,
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def focused_graph(
        self, document_id: str, *, max_nodes: int = 250, max_edges: int = 500
    ) -> dict[str, Any]:
        if not 1 <= max_nodes <= 500 or not 1 <= max_edges <= 2_000:
            raise DocumentIndexError("Focused graph budget is invalid")
        focus_row, snapshot = self._document_row(document_id)
        with closing(self._read_connection()) as connection, connection:
            link_rows = connection.execute(
                "SELECT * FROM document_links WHERE "
                "(source_document_id=? OR target_document_id=?) AND target_document_id IS NOT NULL "
                "ORDER BY link_id LIMIT ?",
                (document_id, document_id, max_edges + 1),
            ).fetchall()
            node_ids = {document_id}
            for link in link_rows[:max_edges]:
                node_ids.add(str(link["source_document_id"]))
                node_ids.add(str(link["target_document_id"]))
                if len(node_ids) >= max_nodes:
                    break
            placeholders = ",".join("?" for _ in node_ids)
            node_rows = connection.execute(
                f"SELECT * FROM documents WHERE document_id IN ({placeholders}) "
                "ORDER BY document_id",
                tuple(sorted(node_ids)),
            ).fetchall()
        selected = {str(row["document_id"]): row for row in node_rows[:max_nodes]}
        ordered_ids = [document_id, *sorted(item for item in selected if item != document_id)]
        nodes: list[dict[str, Any]] = []
        for index, node_id in enumerate(ordered_ids):
            row = selected.get(node_id)
            if row is None:
                continue
            angle = 0.0 if index == 0 else 2 * math.pi * (index - 1) / max(1, len(ordered_ids) - 1)
            radius = 0 if index == 0 else 420
            nodes.append(
                self._graph_node(
                    row, x=round(math.cos(angle) * radius), y=round(math.sin(angle) * radius)
                )
            )
        edges = [
            {
                "edge_id": str(row["link_id"]),
                "source": str(row["source_document_id"]),
                "target": str(row["target_document_id"]),
                "kind": "embeds" if bool(row["embed"]) else "links_to",
                "status": "active",
                "directed": True,
                "metadata": {
                    "heading": "" if row["heading"] is None else str(row["heading"]),
                    "link_type": str(row["link_type"]),
                },
            }
            for row in link_rows[:max_edges]
            if str(row["source_document_id"]) in selected
            and str(row["target_document_id"]) in selected
        ]
        return {
            "snapshot_id": snapshot,
            "focus_document_id": str(focus_row["document_id"]),
            "nodes": nodes,
            "edges": edges,
            "truncated": len(link_rows) > max_edges or len(node_ids) > max_nodes,
        }

    def row_for_id(self, document_id: str) -> dict[str, Any]:
        row, snapshot = self._document_row(document_id)
        return dict(zip(row.keys(), row, strict=True)) | {"snapshot_id": snapshot}

    def row_for_path(self, relative_path: str) -> dict[str, Any] | None:
        decision = self.policy.require_visible(relative_path)
        with closing(self._read_connection()) as connection, connection:
            snapshot = self._require_current(connection)
            row = connection.execute(
                "SELECT * FROM documents WHERE relative_path=?", (decision.relative_path,)
            ).fetchone()
        return (
            None
            if row is None
            else dict(zip(row.keys(), row, strict=True)) | {"snapshot_id": snapshot}
        )

    def ensure_identity(
        self,
        relative_path: str,
        *,
        document_id: str | None = None,
        first_seen: str | None = None,
    ) -> tuple[str, str]:
        """Persist the non-content identity used by disposable index rebuilds."""

        relative = self.policy.require_visible(relative_path).relative_path
        existing = self._identity_for_path(relative)
        if existing is not None:
            if document_id is not None and existing[0] != document_id:
                raise DocumentIndexError("Document identity changed")
            return existing
        decision = self.policy.require_visible(relative)
        if decision.root is None:
            raise DocumentIndexError("Document root is unavailable")
        safe_id = document_id or derive_id(
            "doc", {"root_id": decision.root.root_id, "relative_path": relative}
        )

        if _PUBLIC_ID.fullmatch(safe_id) is None:
            raise DocumentIndexError("Document identity is malformed")
        observed_at = first_seen or _now()
        record_id = derive_id("dpath", {"relative_path": relative})
        try:
            with initialize_platform_store(self.root) as store:
                store.append_record(
                    kind="document_identity",
                    record_id=record_id,
                    payload={
                        "document_id": safe_id,
                        "relative_path": relative,
                        "first_seen_at": observed_at,
                    },
                    state="active",
                    expected_revision=None,
                )
        except PlatformStoreError as error:
            # A racing scan may have installed the same identity first.
            raced = self._identity_for_path(relative)
            if raced is None or (document_id is not None and raced[0] != document_id):
                raise DocumentIndexError("Document identity could not be persisted") from error
            return raced
        return safe_id, observed_at

    def folders(
        self,
        *,
        root_id: str | None = None,
        parent_path: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise DocumentIndexError("Folder page limit is outside 1..500")
        offset = _decode_cursor(cursor)
        clauses: list[str] = []
        values: list[Any] = []
        if root_id is not None:
            self.policy.root(root_id)
            clauses.append("root_id=?")
            values.append(root_id)
        if parent_path is None:
            clauses.append("parent_path IS NULL")
        else:
            safe_parent = self.policy.require_visible(parent_path).relative_path
            clauses.append("parent_path=?")
            values.append(safe_parent)
        where = " WHERE " + " AND ".join(clauses)
        with closing(self._read_connection()) as connection, connection:
            snapshot = self._require_current(connection)
            rows = connection.execute(
                "SELECT * FROM folders"
                + where
                + " ORDER BY name COLLATE NOCASE,folder_id LIMIT ? OFFSET ?",
                (*values, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        return {
            "snapshot_id": snapshot,
            "items": [self._public_folder(row) for row in rows[:limit]],
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def move_identity(self, old_path: str, new_path: str, *, document_id: str) -> None:
        old_relative = self.policy.require_visible(old_path).relative_path
        new_relative = self.policy.require_visible(new_path).relative_path
        new_identity = self._identity_for_path(new_relative)
        if new_identity is not None:
            if new_identity[0] == document_id:
                return
            raise DocumentIndexError("Destination document identity already exists")
        old_record_id = derive_id("dpath", {"relative_path": old_relative})
        new_record_id = derive_id("dpath", {"relative_path": new_relative})
        try:
            with initialize_platform_store(self.root) as store, store.transaction():
                old = store.head("document_identity", old_record_id)
                if (
                    old is None
                    or old.state != "active"
                    or old.payload.get("document_id") != document_id
                ):
                    raise DocumentIndexError("Document identity changed before move")
                if store.head("document_identity", new_record_id) is not None:
                    raise DocumentIndexError("Destination document identity already exists")
                first_seen = str(old.payload.get("first_seen_at", _now()))
                store.append_record(
                    kind="document_identity",
                    record_id=old_record_id,
                    payload={**old.payload, "moved_to": new_relative},
                    state="moved",
                    expected_revision=old.revision,
                )
                store.append_record(
                    kind="document_identity",
                    record_id=new_record_id,
                    payload={
                        "document_id": document_id,
                        "relative_path": new_relative,
                        "first_seen_at": first_seen,
                        "moved_from": old_relative,
                    },
                    state="active",
                    expected_revision=None,
                )
        except PlatformStoreError as error:
            raise DocumentIndexError("Document identity move failed") from error

    def _scan_stream(
        self,
        previous: dict[str, tuple[str, str, int, str]],
        identities: dict[str, tuple[str, str]],
        *,
        on_document: Callable[[IndexedDocument], None],
    ) -> tuple[list[_ScannedDocument], int]:
        git = self._git_status_map()
        documents: list[_ScannedDocument] = []
        errors = 0
        total_bytes = 0
        seen_paths: set[str] = set()
        for root in sorted(self.config.roots, key=lambda item: item.path):
            if root.mode is DocumentMode.HIDDEN:
                continue
            absolute = self.root / root.path
            if not self._safe_directory_exists(root.path):
                errors += 1
                continue
            try:
                metadata = os.lstat(absolute)
            except OSError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                errors += 1
                continue
            for current, directories, files in os.walk(absolute, topdown=True, followlinks=False):
                current_path = Path(current)
                safe_directories: list[str] = []
                for name in sorted(directories):
                    candidate = current_path / name
                    try:
                        candidate_meta = os.lstat(candidate)
                        relative = candidate.relative_to(self.root).as_posix()
                        decision = self.policy.decide(relative)
                    except (OSError, ValueError, DocumentPolicyError):
                        continue
                    if (
                        stat.S_ISDIR(candidate_meta.st_mode)
                        and not stat.S_ISLNK(candidate_meta.st_mode)
                        and decision.visible
                        and decision.root is not None
                        and decision.root.root_id == root.root_id
                    ):
                        safe_directories.append(name)
                directories[:] = safe_directories
                for name in sorted(files):
                    relative = (current_path / name).relative_to(self.root).as_posix()
                    if relative in seen_paths:
                        continue
                    seen_paths.add(relative)
                    identity = identities.get(relative)
                    first_seen = (
                        identity[1]
                        if identity is not None
                        else previous.get(relative, (_now(), "", 0, ""))[0]
                    )
                    prior = previous.get(relative)
                    item = self._scan_file(
                        relative,
                        first_seen=first_seen,
                        git=git,
                        document_id=None if identity is None else identity[0],
                        previous=(None if prior is None else (prior[1], prior[2], prior[3])),
                    )
                    if item is None:
                        errors += 1
                        continue
                    total_bytes += item.size_bytes
                    if total_bytes > self.config.max_total_bytes:
                        raise DocumentIndexError("Document roots exceed the configured byte limit")
                    on_document(item)
                    documents.append(
                        _ScannedDocument(
                            document_id=item.document_id,
                            relative_path=item.relative_path,
                            first_seen_at=item.first_seen_at,
                            size_bytes=item.size_bytes,
                        )
                    )
                    if len(documents) > self.config.max_files:
                        raise DocumentIndexError("Document roots exceed the configured file limit")
        documents.sort(key=lambda item: item.relative_path)
        return documents, errors

    def _scan_folders(self, documents: list[_ScannedDocument]) -> list[dict[str, Any]]:
        direct: dict[str, int] = {}
        descendant: dict[str, int] = {}
        for document in documents:
            document_parent = PurePosixPath(document.relative_path).parent
            direct[document_parent.as_posix()] = direct.get(document_parent.as_posix(), 0) + 1
            for ancestor in document_parent.parents:
                descendant[ancestor.as_posix()] = descendant.get(ancestor.as_posix(), 0) + 1
            descendant[document_parent.as_posix()] = (
                descendant.get(document_parent.as_posix(), 0) + 1
            )
        folders: list[dict[str, Any]] = []
        for root in sorted(self.config.roots, key=lambda value: value.path):
            if root.mode is DocumentMode.HIDDEN:
                continue
            absolute = self.root / root.path
            if not self._safe_directory_exists(root.path):
                continue
            try:
                root_metadata = os.lstat(absolute)
            except OSError:
                continue
            if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
                continue
            for current, directories, _files in os.walk(absolute, topdown=True, followlinks=False):
                current_path = Path(current)
                safe_directories: list[str] = []
                for name in sorted(directories):
                    candidate = current_path / name
                    try:
                        metadata = os.lstat(candidate)
                        relative = candidate.relative_to(self.root).as_posix()
                        decision = self.policy.decide(relative)
                    except (OSError, ValueError, DocumentPolicyError):
                        continue
                    if (
                        stat.S_ISDIR(metadata.st_mode)
                        and not stat.S_ISLNK(metadata.st_mode)
                        and decision.visible
                        and decision.root is not None
                        and decision.root.root_id == root.root_id
                    ):
                        safe_directories.append(name)
                        parent_path = PurePosixPath(relative).parent.as_posix()
                        folders.append(
                            {
                                "folder_id": derive_id(
                                    "dfolder",
                                    {"root_id": root.root_id, "path": relative},
                                ),
                                "root_id": root.root_id,
                                "path": relative,
                                "name": name,
                                "parent_path": None if parent_path == root.path else parent_path,
                                "document_count": direct.get(relative, 0),
                                "descendant_count": descendant.get(relative, 0),
                                "mode": decision.mode.value,
                                "can_create": decision.editable,
                            }
                        )
                directories[:] = safe_directories
                if len(folders) > 200_000:
                    raise DocumentIndexError("Document roots exceed the folder projection limit")
        return folders

    def _sync_folder_projection(
        self,
        connection: sqlite3.Connection,
        relative_path: str,
    ) -> None:
        decision = self.policy.require_visible(relative_path)
        if decision.root is None:
            return
        root_path = PurePosixPath(decision.root.path)
        parent = PurePosixPath(relative_path).parent
        chain: list[PurePosixPath] = []
        cursor = parent
        while cursor != root_path and root_path in cursor.parents:
            chain.append(cursor)
            cursor = cursor.parent
        for folder in reversed(chain):
            path = folder.as_posix()
            if not self._safe_directory_exists(path):
                escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                connection.execute(
                    "DELETE FROM folders WHERE path=? OR path LIKE ? ESCAPE '\\'",
                    (path, f"{escaped}/%"),
                )
                break
            parent_path = folder.parent.as_posix()
            connection.execute(
                "INSERT INTO folders VALUES (?,?,?,?,?,0,0,?,?) "
                "ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id,name=excluded.name,"
                "parent_path=excluded.parent_path,mode=excluded.mode,can_create=excluded.can_create",
                (
                    derive_id(
                        "dfolder",
                        {"root_id": decision.root.root_id, "path": path},
                    ),
                    decision.root.root_id,
                    path,
                    folder.name,
                    None if parent_path == decision.root.path else parent_path,
                    decision.mode.value,
                    int(decision.editable),
                ),
            )

    def _safe_directory_exists(self, relative_path: str) -> bool:
        pure = PurePosixPath(relative_path)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        root_fd = osfd.open(self.root, os.O_RDONLY | directory | cloexec)
        current = root_fd
        opened: list[int] = []
        try:
            for component in pure.parts:
                try:
                    descriptor = osfd.open(
                        component,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=current,
                    )
                except OSError:
                    return False
                opened.append(descriptor)
                current = descriptor
            return True
        finally:
            for descriptor in reversed(opened):
                osfd.close(descriptor)
            osfd.close(root_fd)

    def _scan_file(
        self,
        relative: str,
        *,
        first_seen: str,
        git: dict[str, str] | None,
        document_id: str | None = None,
        previous: tuple[str, int, str] | None = None,
    ) -> IndexedDocument | None:
        try:
            decision = self.policy.decide(relative)
        except DocumentPolicyError:
            return None
        if not decision.visible or decision.root is None:
            return None
        try:
            result = read_regular_file(self.root, relative, max_bytes=self.config.max_file_bytes)
        except (OSError, PathPolicyError):
            return None
        digest = sha256_hex(result.data)
        restricted = contains_restricted_content(self.scanner, result.data, path=relative)
        text: str | None = None
        warnings: tuple[str, ...] = ()
        metadata = MarkdownMetadata(
            title=Path(relative).stem,
            headings=(),
            tags=(),
            aliases=(),
            properties={},
            links=(),
            warnings=(),
        )
        # Protected raytsystem projections are deliberately metadata-only in this index.
        allow_content = not restricted and decision.mode is not DocumentMode.PROTECTED_READ_ONLY
        if allow_content:
            try:
                decoded = result.data.decode("utf-8")
                if "\x00" not in decoded:
                    text = decoded
                    if Path(relative).suffix.casefold() in _MARKDOWN_SUFFIXES:
                        metadata = extract_markdown_metadata(decoded, path=relative)
            except UnicodeDecodeError:
                warnings = ("malformed_utf8",)
        warnings = tuple(dict.fromkeys((*warnings, *metadata.warnings)))
        if git is not None:
            status = git.get(relative, "clean")
        elif previous is None:
            status = "first_seen"
        elif previous[0] != digest:
            status = "hash_modified"
        elif previous[1] != result.mtime_ns:
            status = "mtime_modified"
        elif previous[2] in {"hash_modified", "mtime_modified"}:
            status = previous[2]
        else:
            status = "clean"
        stable_id = document_id or derive_id(
            "doc", {"root_id": decision.root.root_id, "relative_path": relative}
        )
        return IndexedDocument(
            document_id=stable_id,
            root_id=decision.root.root_id,
            relative_path=relative,
            filename=PurePosixPath(relative).name,
            extension=Path(relative).suffix.casefold(),
            size_bytes=result.size,
            content_sha256=digest,
            title=metadata.title,
            headings=metadata.headings,
            tags=metadata.tags,
            aliases=metadata.aliases,
            properties=metadata.properties,
            links=metadata.links,
            mtime_ns=result.mtime_ns,
            first_seen_at=first_seen,
            git_status=status,
            mode=decision.mode,
            kind=decision.root.kind,
            sensitivity="restricted" if restricted else "internal",
            content_indexed=text is not None,
            last_indexed_at=_now(),
            warnings=warnings,
            text=text,
        )

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;
            CREATE TABLE roots(
                root_id TEXT PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                mode TEXT NOT NULL,
                kind TEXT NOT NULL
            ) STRICT;
            CREATE TABLE folders(
                folder_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL REFERENCES roots(root_id),
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                parent_path TEXT,
                document_count INTEGER NOT NULL,
                descendant_count INTEGER NOT NULL,
                mode TEXT NOT NULL,
                can_create INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE documents(
                document_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL REFERENCES roots(root_id),
                relative_path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                title TEXT NOT NULL,
                headings_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                git_status TEXT NOT NULL,
                policy_mode TEXT NOT NULL,
                kind TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                content_indexed INTEGER NOT NULL,
                last_indexed_at TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                outgoing_count INTEGER NOT NULL,
                backlink_count INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE document_tags(
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY(document_id,tag)
            ) STRICT;
            CREATE TABLE document_properties(
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                property_key TEXT NOT NULL,
                property_value TEXT NOT NULL,
                PRIMARY KEY(document_id,property_key,property_value)
            ) STRICT;
            CREATE TABLE document_links(
                link_id TEXT PRIMARY KEY,
                source_document_id TEXT NOT NULL
                    REFERENCES documents(document_id) ON DELETE CASCADE,
                target_document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
                raw_target TEXT NOT NULL,
                target_text TEXT NOT NULL,
                heading TEXT,
                alias TEXT,
                link_type TEXT NOT NULL,
                embed INTEGER NOT NULL,
                context TEXT NOT NULL,
                resolution TEXT NOT NULL,
                candidates_json TEXT NOT NULL,
                candidate_count INTEGER NOT NULL
            ) STRICT;
            CREATE INDEX documents_root_path_idx ON documents(root_id,relative_path);
            CREATE INDEX folders_parent_idx ON folders(root_id,parent_path,name);
            CREATE INDEX documents_mtime_idx ON documents(mtime_ns DESC,document_id);
            CREATE INDEX documents_first_seen_idx ON documents(first_seen_at DESC,document_id);
            CREATE INDEX links_source_idx ON document_links(source_document_id);
            CREATE INDEX links_target_idx ON document_links(target_document_id);
            CREATE VIRTUAL TABLE document_fts USING fts5(
                document_id UNINDEXED,
                filename,
                path,
                title,
                headings,
                tags,
                aliases,
                properties,
                links,
                body,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )

    def _insert_roots(self, connection: sqlite3.Connection) -> None:
        connection.executemany(
            "INSERT INTO roots VALUES (?,?,?,?)",
            [
                (item.root_id, item.path, item.mode.value, item.kind)
                for item in sorted(self.config.roots, key=lambda value: value.root_id)
            ],
        )

    @staticmethod
    def _insert_folders(
        connection: sqlite3.Connection,
        folders: list[dict[str, Any]],
    ) -> None:
        connection.executemany(
            "INSERT INTO folders VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    item["folder_id"],
                    item["root_id"],
                    item["path"],
                    item["name"],
                    item["parent_path"],
                    item["document_count"],
                    item["descendant_count"],
                    item["mode"],
                    int(item["can_create"]),
                )
                for item in folders
            ],
        )

    def _insert_document(self, connection: sqlite3.Connection, item: IndexedDocument) -> None:
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.document_id,
                item.root_id,
                item.relative_path,
                item.filename,
                item.extension,
                item.size_bytes,
                item.content_sha256,
                item.title,
                _json(item.headings),
                _json(item.tags),
                _json(item.aliases),
                _json(item.properties),
                item.mtime_ns,
                item.first_seen_at,
                item.git_status,
                item.mode.value,
                item.kind,
                item.sensitivity,
                int(item.content_indexed),
                item.last_indexed_at,
                _json(item.warnings),
                len(item.links),
                0,
            ),
        )
        connection.executemany(
            "INSERT INTO document_tags VALUES (?,?)",
            [(item.document_id, tag.casefold()) for tag in item.tags],
        )
        properties: list[tuple[str, str, str]] = []
        for key, value in item.properties.items():
            values = value if isinstance(value, list) else [value]
            properties.extend(
                (item.document_id, key.casefold(), str(entry).casefold()) for entry in values
            )
        connection.executemany("INSERT INTO document_properties VALUES (?,?,?)", properties)
        for index, link in enumerate(item.links):
            link_id = derive_id(
                "dlink",
                {
                    "source": item.document_id,
                    "index": index,
                    "raw_target": link.raw_target,
                    "context": link.context,
                },
            )
            connection.execute(
                "INSERT INTO document_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    link_id,
                    item.document_id,
                    None,
                    link.raw_target,
                    link.target,
                    link.heading,
                    link.alias,
                    link.link_type,
                    int(link.embed),
                    link.context,
                    "unresolved",
                    "[]",
                    0,
                ),
            )
        if item.content_indexed and item.text is not None:
            connection.execute(
                "INSERT INTO document_fts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item.document_id,
                    item.filename,
                    item.relative_path,
                    item.title,
                    "\n".join(item.headings),
                    " ".join(item.tags),
                    " ".join(item.aliases),
                    _flatten_properties(item.properties),
                    " ".join(link.target for link in item.links),
                    item.text,
                ),
            )

    @staticmethod
    def _delete_document(connection: sqlite3.Connection, relative: str) -> None:
        row = connection.execute(
            "SELECT document_id FROM documents WHERE relative_path=?", (relative,)
        ).fetchone()
        if row is None:
            return
        document_id = str(row["document_id"])
        connection.execute("DELETE FROM document_fts WHERE document_id=?", (document_id,))
        connection.execute("DELETE FROM document_links WHERE source_document_id=?", (document_id,))
        connection.execute(
            "UPDATE document_links SET target_document_id=NULL,resolution='unresolved' "
            "WHERE target_document_id=?",
            (document_id,),
        )
        connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))

    @staticmethod
    def _resolve_links(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT document_id,relative_path,title,aliases_json "
            "FROM documents ORDER BY document_id"
        )
        by_path: dict[str, list[str]] = {}
        by_name: dict[str, list[str]] = {}
        for row in rows:
            document_id = str(row["document_id"])
            relative = str(row["relative_path"])
            by_path.setdefault(relative.casefold(), []).append(document_id)
            by_path.setdefault(str(PurePosixPath(relative).with_suffix("")).casefold(), []).append(
                document_id
            )
            by_name.setdefault(PurePosixPath(relative).name.casefold(), []).append(document_id)
            by_name.setdefault(Path(relative).stem.casefold(), []).append(document_id)
            by_name.setdefault(str(row["title"]).casefold(), []).append(document_id)
            for alias in json.loads(str(row["aliases_json"])):
                by_name.setdefault(str(alias).casefold(), []).append(document_id)
        links = connection.execute(
            "SELECT l.*,d.relative_path AS source_path FROM document_links l "
            "JOIN documents d ON d.document_id=l.source_document_id ORDER BY l.link_id"
        )
        resolution_cache: dict[
            tuple[str, str, str], tuple[str | None, str, tuple[str, ...], int]
        ] = {}
        for link in links:
            target = str(link["target_text"]).strip().replace("\\", "/")
            link_type = str(link["link_type"])
            source_parent = PurePosixPath(str(link["source_path"])).parent
            cache_key = (
                link_type,
                source_parent.as_posix().casefold()
                if link_type in {"markdown", "markdown_image"}
                else "",
                target.casefold(),
            )
            cached = resolution_cache.get(cache_key)
            if cached is None:
                relative_candidate = source_parent / PurePosixPath(target)
                relative_lookups: tuple[list[str], ...] = ()
                if (
                    link_type in {"markdown", "markdown_image"}
                    and ".." not in relative_candidate.parts
                ):
                    relative_lookups = (
                        by_path.get(relative_candidate.as_posix().casefold(), []),
                        by_path.get(relative_candidate.with_suffix("").as_posix().casefold(), []),
                    )
                relative_matches, relative_count = DocumentIndex._bounded_candidate_union(
                    relative_lookups
                )
                lookups = (
                    by_path.get(target.casefold(), []),
                    by_path.get(PurePosixPath(target).with_suffix("").as_posix().casefold(), []),
                    by_name.get(PurePosixPath(target).name.casefold(), []),
                    by_name.get(Path(target).stem.casefold(), []),
                )
                if relative_count:
                    candidates, candidate_count = relative_matches, relative_count
                else:
                    candidates, candidate_count = DocumentIndex._bounded_candidate_union(lookups)
                if candidate_count == 1:
                    cached = (candidates[0], "resolved", candidates, candidate_count)
                elif candidate_count:
                    cached = (None, "ambiguous", candidates, candidate_count)
                else:
                    cached = (None, "unresolved", (), 0)
                resolution_cache[cache_key] = cached
            resolved, resolution, candidates, candidate_count = cached
            connection.execute(
                "UPDATE document_links SET target_document_id=?,resolution=?,candidates_json=?,"
                "candidate_count=? "
                "WHERE link_id=?",
                (
                    resolved,
                    resolution,
                    _json(candidates),
                    candidate_count,
                    str(link["link_id"]),
                ),
            )
        connection.execute(
            "UPDATE documents SET outgoing_count=(SELECT COUNT(*) FROM document_links l "
            "WHERE l.source_document_id=documents.document_id),"
            "backlink_count=(SELECT COUNT(*) FROM document_links l "
            "WHERE l.target_document_id=documents.document_id)"
        )

    @staticmethod
    def _bounded_candidate_union(
        sources: tuple[list[str], ...],
        *,
        retained_limit: int = 20,
    ) -> tuple[tuple[str, ...], int]:
        retained: list[str] = []
        count = 0
        previous: str | None = None
        unique_sources = tuple({id(source): source for source in sources if source}.values())
        for candidate in heapq.merge(*unique_sources):
            if candidate == previous:
                continue
            previous = candidate
            count += 1
            if len(retained) < retained_limit:
                retained.append(candidate)
        return tuple(retained), count

    @staticmethod
    def _update_folder_counts(connection: sqlite3.Connection) -> None:
        folders = connection.execute("SELECT folder_id,path FROM folders").fetchall()
        for folder in folders:
            path = str(folder["path"])
            direct = connection.execute(
                "SELECT COUNT(*) FROM documents WHERE "
                "substr(relative_path,1,length(relative_path)-length(filename)-1)=?",
                (path,),
            ).fetchone()
            escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            descendants = connection.execute(
                "SELECT COUNT(*) FROM documents WHERE relative_path LIKE ? ESCAPE '\\'",
                (f"{escaped}/%",),
            ).fetchone()
            connection.execute(
                "UPDATE folders SET document_count=?,descendant_count=? WHERE folder_id=?",
                (
                    0 if direct is None else int(direct[0]),
                    0 if descendants is None else int(descendants[0]),
                    str(folder["folder_id"]),
                ),
            )

    def _previous_projection(self) -> dict[str, tuple[str, str, int, str]]:
        try:
            with closing(self._read_connection()) as connection, connection:
                return {
                    str(row["relative_path"]): (
                        str(row["first_seen_at"]),
                        str(row["content_sha256"]),
                        int(row["mtime_ns"]),
                        str(row["git_status"]),
                    )
                    for row in connection.execute(
                        "SELECT relative_path,first_seen_at,content_sha256,mtime_ns,git_status "
                        "FROM documents"
                    ).fetchall()
                }
        except (OSError, sqlite3.Error, DocumentIndexError):
            return {}

    def _identity_for_path(self, relative_path: str) -> tuple[str, str] | None:
        return self._identity_map().get(relative_path)

    def _identity_map(self) -> dict[str, tuple[str, str]]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {}
        result: dict[str, tuple[str, str]] = {}
        try:
            offset = 0
            while True:
                records = store.list_heads(
                    "document_identity", state="active", limit=500, offset=offset
                )
                for record in records:
                    relative = record.payload.get("relative_path")
                    document_id = record.payload.get("document_id")
                    first_seen = record.payload.get("first_seen_at")
                    if (
                        isinstance(relative, str)
                        and isinstance(document_id, str)
                        and isinstance(first_seen, str)
                        and _PUBLIC_ID.fullmatch(document_id) is not None
                        and self.policy.decide(relative).visible
                    ):
                        result.setdefault(relative, (document_id, first_seen))
                if len(records) < 500:
                    break
                offset += 500
        except (DocumentPolicyError, PlatformStoreError, ValueError):
            return {}
        finally:
            store.close()
        return result

    def _persist_identities(
        self,
        documents: list[_ScannedDocument],
        existing: dict[str, tuple[str, str]],
    ) -> None:
        missing = [item for item in documents if item.relative_path not in existing]
        if not missing:
            return
        try:
            with initialize_platform_store(self.root) as store, store.transaction():
                for item in missing:
                    record_id = derive_id("dpath", {"relative_path": item.relative_path})
                    current = store.head("document_identity", record_id)
                    if current is not None:
                        if (
                            current.state != "active"
                            or current.payload.get("document_id") != item.document_id
                        ):
                            raise DocumentIndexError("Document identity record conflicts")
                        continue
                    store.append_record(
                        kind="document_identity",
                        record_id=record_id,
                        payload={
                            "document_id": item.document_id,
                            "relative_path": item.relative_path,
                            "first_seen_at": item.first_seen_at,
                        },
                        state="active",
                        expected_revision=None,
                    )
        except PlatformStoreError as error:
            raise DocumentIndexError("Document identities could not be persisted") from error

    def _database_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        error_count: int,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(
            canonical_json_bytes(
                {
                    "config_sha256": self.config.config_sha256,
                    "schema_version": _SCHEMA_VERSION,
                    "error_count": error_count,
                }
            )
        )
        queries = (
            (
                "documents",
                "SELECT document_id,root_id,relative_path,filename,extension,size_bytes,"
                "content_sha256,title,headings_json,tags_json,aliases_json,frontmatter_json,"
                "mtime_ns,first_seen_at,git_status,policy_mode,kind,sensitivity,content_indexed,"
                "warnings_json,outgoing_count,backlink_count FROM documents ORDER BY document_id",
            ),
            (
                "folders",
                "SELECT folder_id,root_id,path,name,parent_path,document_count,descendant_count,"
                "mode,can_create FROM folders ORDER BY folder_id",
            ),
            (
                "links",
                "SELECT link_id,source_document_id,target_document_id,raw_target,target_text,"
                "heading,alias,link_type,embed,context,resolution,candidates_json,candidate_count "
                "FROM document_links ORDER BY link_id",
            ),
        )
        for table, query in queries:
            digest.update(table.encode("ascii") + b"\0")
            for row in connection.execute(query):
                payload = canonical_json_bytes(list(row))
                digest.update(len(payload).to_bytes(8, "big"))
                digest.update(payload)
        return f"docsnap_{digest.hexdigest()}"

    def _git_status_map(self) -> dict[str, str] | None:
        try:
            git_metadata = (self.root / ".git").lstat()
        except OSError:
            return None
        if stat.S_ISLNK(git_metadata.st_mode) or not stat.S_ISDIR(git_metadata.st_mode):
            return None
        roots = [item.path for item in self.config.roots if item.mode is not DocumentMode.HIDDEN]
        try:
            completed = run_bounded(
                (
                    *hardened_git_arguments(self.root),
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                    "--ignore-submodules=all",
                    "--",
                    *roots,
                ),
                environment=hardened_git_environment(),
                stdout_limit=16 * 1024 * 1024,
            )
        except (OSError, ValueError):
            return None
        if completed.returncode != 0 or completed.overflowed or completed.timed_out:
            return None
        entries = completed.stdout.split(b"\0")
        result: dict[str, str] = {}
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4:
                return None
            code = entry[:2].decode("ascii", errors="replace")
            try:
                relative = entry[3:].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if code == "??":
                status_value = "untracked"
            elif "R" in code:
                status_value = "renamed"
                index += 1  # porcelain -z appends the source path as a second field
            elif "A" in code:
                status_value = "added"
            elif "D" in code:
                status_value = "deleted"
            else:
                status_value = "modified"
            result[relative] = status_value
            if len(result) > self.config.max_files:
                return None
        return result

    def _read_connection(self) -> sqlite3.Connection:
        try:
            assert_safe_sqlite_family(self.path)
        except (OSError, UnsafeWritePath) as error:
            raise DocumentIndexError("Document index path is unsafe") from error
        if not self.path.is_file():
            raise DocumentIndexError("Document index is unavailable")
        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=1000")
        return connection

    def _write_connection(self) -> sqlite3.Connection:
        try:
            assert_safe_sqlite_family(self.path)
        except (OSError, UnsafeWritePath) as error:
            raise DocumentIndexError("Document index path is unsafe") from error
        if not self.path.is_file():
            raise DocumentIndexError("Document index is unavailable")
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        connection.row_factory = sqlite3.Row
        # Readers use immutable=1; DELETE journaling ensures committed pages are in the
        # main file before an immutable snapshot is opened.
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key,value FROM meta ORDER BY key")
        }

    def _require_current(self, connection: sqlite3.Connection) -> str:
        metadata = self._metadata(connection)
        if (
            metadata.get("state") != "current"
            or metadata.get("config_sha256") != self.config.config_sha256
            or metadata.get("schema_version") != _SCHEMA_VERSION
            or metadata.get("snapshot_id") is None
        ):
            raise DocumentIndexError("Document index is stale")
        return str(metadata["snapshot_id"])

    def _document_row(self, document_id: str) -> tuple[sqlite3.Row, str]:
        if _PUBLIC_ID.fullmatch(document_id) is None:
            raise DocumentNotFound("Document was not found")
        with closing(self._read_connection()) as connection, connection:
            snapshot = self._require_current(connection)
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
        if row is None:
            raise DocumentNotFound("Document was not found")
        return row, snapshot

    @staticmethod
    def _document_select() -> str:
        return "SELECT d.* FROM documents d"

    def _public_document(self, row: sqlite3.Row) -> dict[str, Any]:
        editable = (
            str(row["policy_mode"]) == DocumentMode.READ_WRITE.value
            and str(row["extension"]).casefold() in _MARKDOWN_SUFFIXES
            and str(row["sensitivity"]) != "restricted"
        )
        git_status = str(row["git_status"])
        modified = git_status in _MODIFIED_STATUSES
        new = git_status in {"added", "untracked"} or self._recent_first_seen(
            str(row["first_seen_at"])
        )
        return {
            "document_id": str(row["document_id"]),
            "root_id": str(row["root_id"]),
            "path": str(row["relative_path"]),
            "filename": str(row["filename"]),
            "extension": str(row["extension"]),
            "size_bytes": int(row["size_bytes"]),
            "content_sha256": str(row["content_sha256"]),
            "title": str(row["title"]),
            "headings": json.loads(str(row["headings_json"])),
            "tags": json.loads(str(row["tags_json"])),
            "aliases": json.loads(str(row["aliases_json"])),
            "properties": json.loads(str(row["frontmatter_json"])),
            "modified_at": _mtime_iso(int(row["mtime_ns"])),
            "first_seen_at": str(row["first_seen_at"]),
            "git_status": str(row["git_status"]),
            "mode": str(row["policy_mode"]),
            "kind": str(row["kind"]),
            "sensitivity": str(row["sensitivity"]),
            "content_indexed": bool(row["content_indexed"]),
            "editable": editable,
            "can_edit": editable,
            "is_modified": modified,
            "is_new": new,
            "modified_source": (
                "git_worktree"
                if git_status in {"added", "deleted", "modified", "renamed", "untracked"}
                else "index_hash_or_mtime"
            ),
            "added_source": (
                "git_untracked" if git_status in {"added", "untracked"} else "raytsystem_first_seen"
            ),
            "backlink_count": int(row["backlink_count"]) if "backlink_count" in row else 0,
            "outgoing_link_count": int(row["outgoing_count"]) if "outgoing_count" in row else 0,
            "warnings": json.loads(str(row["warnings_json"])),
            "snippet": str(row["snippet"]) if "snippet" in row else None,
            "asset_id": (
                str(row["document_id"])
                if str(row["extension"]).casefold() in _IMAGE_SUFFIXES
                and str(row["sensitivity"]) != "restricted"
                else None
            ),
        }

    @staticmethod
    def _recent_first_seen(value: str) -> bool:
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if observed.tzinfo is None:
            return False
        age = datetime.now(UTC) - observed.astimezone(UTC)
        return timedelta(0) <= age <= timedelta(seconds=_NEW_WINDOW_SECONDS)

    @staticmethod
    def _frontmatter_fields(properties: dict[str, Any]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for key, value in properties.items():
            if key in {"tags", "aliases", "alias"} and isinstance(value, list | str):
                field_type = "tags" if key == "tags" else "aliases"
            elif isinstance(value, bool):
                field_type = "boolean"
            elif isinstance(value, int | float):
                field_type = "number"
            elif isinstance(value, list):
                field_type = "list"
            elif value is None or isinstance(value, str):
                field_type = "string"
            else:
                field_type = "complex"
            fields.append(
                {
                    "key": key,
                    "value": value,
                    "type": field_type,
                    "editable": field_type != "complex",
                    "source": "yaml_frontmatter",
                }
            )
        return fields

    @staticmethod
    def _visual_qualification(
        content: str | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        unsupported: list[str] = []
        if content is not None:
            if _HTML_FRAGMENT.search(content):
                unsupported.append("html_fragment")
            if _FOOTNOTE.search(content):
                unsupported.append("footnote")
        unsafe = bool(warnings or unsupported)
        return {
            "can_open": content is not None,
            "can_save": content is not None and not unsafe,
            "round_trip_safe": content is not None and not unsafe,
            "warnings": warnings,
            "unsupported_syntax": unsupported,
        }

    def _page(
        self,
        snapshot: str,
        rows: list[sqlite3.Row],
        limit: int,
        offset: int,
        *,
        index_status: dict[str, Any],
        folders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        has_more = len(rows) > limit
        visible = rows[:limit]
        return {
            "snapshot_id": snapshot,
            "index": index_status,
            "roots": self.roots(),
            "folders": folders,
            "items": [self._public_document(row) for row in visible],
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def _envelope_context(
        self,
        connection: sqlite3.Connection,
        snapshot: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        metadata = self._metadata(connection)
        count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        folder_rows = connection.execute(
            "SELECT * FROM folders WHERE parent_path IS NULL "
            "ORDER BY name COLLATE NOCASE,folder_id LIMIT 200"
        ).fetchall()
        built_at = metadata.get("built_at")
        state = "current"
        message: str | None = None
        if built_at is not None:
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(built_at.replace("Z", "+00:00"))
            except ValueError:
                state = "stale"
                message = "Index refresh time is invalid."
            else:
                if age.total_seconds() > 60:
                    state = "stale"
                    message = "Index freshness window expired; refresh is required."
        return (
            {
                "state": state,
                "snapshot_id": snapshot,
                "file_count": count,
                "last_refresh_at": built_at,
                "error_count": int(metadata.get("error_count", "0")),
                "roots": self.roots(),
                "message": message,
            },
            [self._public_folder(row) for row in folder_rows],
        )

    @staticmethod
    def _public_folder(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "folder_id": str(row["folder_id"]),
            "root_id": str(row["root_id"]),
            "path": str(row["path"]),
            "name": str(row["name"]),
            "parent_path": row["parent_path"],
            "document_count": int(row["document_count"]),
            "descendant_count": int(row["descendant_count"]),
            "mode": str(row["mode"]),
            "can_create": bool(row["can_create"]),
        }

    def _limit(self, limit: int | None) -> int:
        value = self.config.search_page_size if limit is None else limit
        if not 1 <= value <= min(200, self.config.search_page_size):
            raise DocumentIndexError("Page size is outside the configured limit")
        return value

    @staticmethod
    def _sort(value: str) -> str:
        try:
            return _SORT_SQL[value]
        except KeyError as error:
            raise DocumentIndexError("Document sort is invalid") from error

    @staticmethod
    def _parse_query(query: str) -> _SearchSpec:
        if not query or len(query.encode("utf-8")) > 2_048 or "\x00" in query:
            raise DocumentIndexError("Document search query is invalid")
        terms: list[str] = []
        path_prefix: str | None = None
        extension: str | None = None
        tag: str | None = None
        property_key: str | None = None
        property_value: str | None = None
        state: str | None = None
        after_ns: int | None = None
        before_ns: int | None = None
        for match in _QUERY_PART.finditer(query):
            phrase = match.group(1)
            token = phrase if phrase is not None else str(match.group(2))
            lower = token.casefold()
            if phrase is None and lower.startswith("path:"):
                path_prefix = token[5:].strip().replace("\\", "/")
                if not path_prefix or ".." in PurePosixPath(path_prefix).parts:
                    raise DocumentIndexError("Search path filter is invalid")
            elif phrase is None and lower.startswith("type:"):
                extension = token[5:].strip().casefold()
                if extension and not extension.startswith("."):
                    extension = "." + extension
            elif phrase is None and lower.startswith("tag:"):
                tag = token[4:].strip().lstrip("#")
                if not tag:
                    raise DocumentIndexError("Search tag filter is invalid")
            elif phrase is None and lower.startswith("property:"):
                field = token[len("property:") :].strip()
                key, separator, value = field.partition("=")
                if not key:
                    raise DocumentIndexError("Search property filter is invalid")
                property_key = key
                property_value = value if separator else None
            elif phrase is None and lower.startswith("is:"):
                state = lower[3:]
                if state not in {"modified", "new", "readonly"}:
                    raise DocumentIndexError("Search state filter is invalid")
            elif phrase is None and lower.startswith(("after:", "before:")):
                key, value = lower.split(":", 1)
                try:
                    instant = datetime.fromisoformat(value).replace(tzinfo=UTC)
                except ValueError as error:
                    raise DocumentIndexError("Search date filter is invalid") from error
                nanoseconds = int(instant.timestamp() * 1_000_000_000)
                if key == "after":
                    after_ns = nanoseconds
                else:
                    before_ns = nanoseconds
            else:
                words = _WORD.findall(token.casefold())
                if phrase is not None:
                    if words:
                        terms.append('"' + " ".join(words).replace('"', '""') + '"')
                else:
                    terms.extend('"' + word.replace('"', '""') + '"*' for word in words)
        if len(terms) > 24:
            raise DocumentIndexError("Document search query has too many terms")
        return _SearchSpec(
            expression=" AND ".join(terms) or None,
            path_prefix=path_prefix,
            extension=extension,
            tag=tag,
            property_key=property_key,
            property_value=property_value,
            state=state,
            after_ns=after_ns,
            before_ns=before_ns,
        )

    @staticmethod
    def _graph_node(row: sqlite3.Row, *, x: int, y: int) -> dict[str, Any]:
        mode = str(row["policy_mode"])
        kind = str(row["kind"])
        if mode == DocumentMode.PROTECTED_READ_ONLY.value:
            node_kind = "generated_document"
        elif kind == "notes":
            node_kind = "manual_document"
        elif kind == "documentation":
            node_kind = "documentation_document"
        else:
            node_kind = "document"
        return {
            "node_id": str(row["document_id"]),
            "kind": node_kind,
            "label": str(row["title"]),
            "subtitle": str(row["relative_path"]),
            "status": mode,
            "ring": "document",
            "importance": 84 if x == 0 and y == 0 else 52,
            "x": x,
            "y": y,
            "recorded_at": _mtime_iso(int(row["mtime_ns"])),
            "source_ref": None,
            "metadata": {
                "root_id": str(row["root_id"]),
                "path": str(row["relative_path"]),
                "sensitivity": str(row["sensitivity"]),
            },
        }
