from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex
from raytsystem.documents.contracts import (
    DocumentConfig,
    DocumentConfigError,
    DocumentMode,
    DocumentRoot,
)
from raytsystem.documents.policy import DocumentPolicy
from raytsystem.security.paths import PathPolicyError, read_regular_file

_DOCUMENT_KEYS = {
    "index_db",
    "roots",
    "max_files",
    "max_file_bytes",
    "max_total_bytes",
    "search_page_size",
    "search_timeout_ms",
    "allow_maintainer_docs_write",
}
_ROOT_KEYS = {"id", "path", "mode", "kind"}


def _bounded_int(
    payload: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise DocumentConfigError(f"documents.{key} is outside the supported range")
    return value


def load_document_config(root: Path) -> DocumentConfig:
    resolved = root.resolve()
    try:
        data = read_regular_file(
            resolved,
            "config/raytsystem.toml",
            max_bytes=1024 * 1024,
        ).data
        document = tomllib.loads(data.decode("utf-8"))
    except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise DocumentConfigError("raytsystem document configuration is unavailable") from error
    raw = document.get("documents", {})
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise DocumentConfigError("documents must be a TOML table")
    unknown = sorted(set(raw).difference(_DOCUMENT_KEYS))
    if unknown:
        raise DocumentConfigError(f"documents contains unknown keys: {', '.join(unknown)}")
    allow_docs = raw.get("allow_maintainer_docs_write", False)
    if not isinstance(allow_docs, bool):
        raise DocumentConfigError("documents.allow_maintainer_docs_write must be boolean")
    index_db = raw.get("index_db", ".raytsystem/documents.sqlite")
    if index_db != ".raytsystem/documents.sqlite":
        raise DocumentConfigError("Document index must remain in the protected derived zone")
    roots_payload = raw.get(
        "roots",
        [
            {
                "id": "manual",
                "path": "knowledge/manual",
                "mode": "read_write",
                "kind": "notes",
            }
        ],
    )
    if not isinstance(roots_payload, list) or not roots_payload:
        raise DocumentConfigError("documents.roots must be a non-empty array of tables")
    roots: list[DocumentRoot] = []
    for item in roots_payload:
        if not isinstance(item, dict) or any(not isinstance(key, str) for key in item):
            raise DocumentConfigError("Each document root must be a TOML table")
        extra = sorted(set(item).difference(_ROOT_KEYS))
        if extra:
            raise DocumentConfigError(f"Document root contains unknown keys: {', '.join(extra)}")
        raw_path = item.get("path")
        raw_mode = item.get("mode")
        kind = item.get("kind")
        if (
            not isinstance(raw_path, str)
            or not isinstance(raw_mode, str)
            or not isinstance(kind, str)
        ):
            raise DocumentConfigError("Document root requires string path, mode, and kind")
        if not kind or len(kind) > 64 or any(character.isspace() for character in kind):
            raise DocumentConfigError("Document root kind is malformed")
        try:
            mode = DocumentMode(raw_mode)
            path = DocumentPolicy.validate_root(
                raw_path,
                mode,
                allow_maintainer_docs_write=allow_docs,
            )
        except (ValueError, DocumentConfigError) as error:
            raise DocumentConfigError("Document root mode is invalid") from error
        except Exception as error:
            if isinstance(error, DocumentConfigError):
                raise
            raise DocumentConfigError(str(error)) from error
        supplied_id = item.get("id")
        if supplied_id is None:
            root_id = derive_id("droot", {"path": path, "kind": kind})
        elif isinstance(supplied_id, str):
            try:
                root_id = DocumentPolicy.validate_root_id(supplied_id)
            except Exception as error:
                raise DocumentConfigError("Document root ID is malformed") from error
        else:
            raise DocumentConfigError("Document root ID must be a string")
        roots.append(DocumentRoot(root_id=root_id, path=path, mode=mode, kind=kind))
    if len({item.root_id for item in roots}) != len(roots):
        raise DocumentConfigError("Document root IDs must be unique")
    if len({item.path.casefold() for item in roots}) != len(roots):
        raise DocumentConfigError("Document root paths must be unique")
    identities: dict[tuple[int, int], str] = {}
    for item in roots:
        identity = _directory_identity(resolved, item.path)
        if identity is None:
            continue
        prior = identities.setdefault(identity, item.path)
        if prior != item.path:
            raise DocumentConfigError("Document roots resolve to the same directory")
    normalized = {
        "index_db": index_db,
        "roots": [item.to_dict() for item in sorted(roots, key=lambda value: value.root_id)],
        "max_files": _bounded_int(raw, "max_files", 100_000, minimum=1, maximum=100_000),
        "max_file_bytes": _bounded_int(
            raw,
            "max_file_bytes",
            5 * 1024 * 1024,
            minimum=1024,
            maximum=5 * 1024 * 1024,
        ),
        "max_total_bytes": _bounded_int(
            raw,
            "max_total_bytes",
            2 * 1024 * 1024 * 1024,
            minimum=1024,
            maximum=8 * 1024 * 1024 * 1024,
        ),
        "search_page_size": _bounded_int(
            raw,
            "search_page_size",
            50,
            minimum=1,
            maximum=200,
        ),
        "search_timeout_ms": _bounded_int(
            raw,
            "search_timeout_ms",
            250,
            minimum=25,
            maximum=2_000,
        ),
        "allow_maintainer_docs_write": allow_docs,
    }
    return DocumentConfig(
        index_db=index_db,
        roots=tuple(roots),
        max_files=int(normalized["max_files"]),
        max_file_bytes=int(normalized["max_file_bytes"]),
        max_total_bytes=int(normalized["max_total_bytes"]),
        search_page_size=int(normalized["search_page_size"]),
        search_timeout_ms=int(normalized["search_timeout_ms"]),
        allow_maintainer_docs_write=allow_docs,
        config_sha256=sha256_hex(canonical_json_bytes(normalized)),
    )


def _directory_identity(root: Path, relative_path: str) -> tuple[int, int] | None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root, os.O_RDONLY | directory | cloexec)
    current = root_fd
    opened: list[int] = []
    try:
        for component in Path(relative_path).parts:
            try:
                descriptor = os.open(
                    component,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=current,
                )
            except FileNotFoundError:
                return None
            except OSError as error:
                raise DocumentConfigError("Document root contains an unsafe component") from error
            opened.append(descriptor)
            current = descriptor
        metadata = os.fstat(current)
        return metadata.st_dev, metadata.st_ino
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(root_fd)
