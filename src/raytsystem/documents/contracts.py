from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class DocumentError(RuntimeError):
    """Base error for the managed document workspace."""


class DocumentConfigError(DocumentError):
    """Document roots or limits are missing, malformed, or unsafe."""


class DocumentPolicyError(DocumentError):
    """A document operation is outside the configured policy."""


class DocumentIndexError(DocumentError):
    """The disposable document projection is unavailable or invalid."""


class DocumentNotFound(DocumentError):
    """An opaque document identifier is not present in the current projection."""


class DocumentRestricted(DocumentPolicyError):
    """Document content is withheld by the sensitivity policy."""


class DocumentConflict(DocumentError):
    """A write was bound to stale content, policy, or projection state."""

    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class DocumentMode(StrEnum):
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    PROTECTED_READ_ONLY = "protected_read_only"
    HIDDEN = "hidden"

    @property
    def editable(self) -> bool:
        return self is DocumentMode.READ_WRITE


@dataclass(frozen=True)
class DocumentRoot:
    root_id: str
    path: str
    mode: DocumentMode
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "path": self.path,
            "mode": self.mode.value,
            "kind": self.kind,
            "editable": self.mode.editable,
        }


@dataclass(frozen=True)
class DocumentConfig:
    index_db: str
    roots: tuple[DocumentRoot, ...]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    search_page_size: int
    search_timeout_ms: int
    allow_maintainer_docs_write: bool
    config_sha256: str


@dataclass(frozen=True)
class ExtractedLink:
    raw_target: str
    target: str
    heading: str | None
    alias: str | None
    link_type: str
    embed: bool
    context: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarkdownMetadata:
    title: str
    headings: tuple[str, ...]
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    properties: dict[str, Any]
    links: tuple[ExtractedLink, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class IndexedDocument:
    document_id: str
    root_id: str
    relative_path: str
    filename: str
    extension: str
    size_bytes: int
    content_sha256: str
    title: str
    headings: tuple[str, ...]
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    properties: dict[str, Any]
    links: tuple[ExtractedLink, ...]
    mtime_ns: int
    first_seen_at: str
    git_status: str
    mode: DocumentMode
    kind: str
    sensitivity: str
    content_indexed: bool
    last_indexed_at: str
    warnings: tuple[str, ...]
    text: str | None = None
