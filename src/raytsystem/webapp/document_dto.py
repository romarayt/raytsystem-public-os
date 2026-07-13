from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

DocumentId = Annotated[str, StringConstraints(pattern=r"^doc_[0-9a-f]{64}$")]
DocumentSnapshotId = Annotated[str, StringConstraints(pattern=r"^docsnap_[0-9a-f]{64}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RootId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[a-z][a-z0-9_-]{1,63}|droot_[0-9a-f]{64})$"),
]
RevisionId = Annotated[str, StringConstraints(pattern=r"^drev_[0-9a-f]{64}$")]
HistoryId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:drev_[0-9a-f]{64}|git:[0-9a-f]{40}(?:[0-9a-f]{24})?)$"),
]
RestorePreviewToken = Annotated[str, StringConstraints(pattern=r"^drp_[0-9a-f]{64}$")]


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class DocumentCreateRequest(DocumentRequest):
    root_id: RootId
    name: str = Field(min_length=1, max_length=255)
    folder: str = Field(default="", max_length=4096)
    content: str = Field(default="", max_length=5 * 1024 * 1024)
    template: Literal["empty", "note", "project", "meeting", "research", "daily"] = "empty"
    properties: dict[str, Any] = Field(default_factory=dict, max_length=128)
    tags: tuple[str, ...] = Field(default=(), max_length=128)
    expected_snapshot_id: DocumentSnapshotId

    @field_validator("tags")
    @classmethod
    def _safe_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not item or len(item) > 128 or any(character.isspace() for character in item)
            for item in value
        ):
            raise ValueError("Document tags are malformed")
        return value


class DocumentUpdateRequest(DocumentRequest):
    content: str = Field(max_length=5 * 1024 * 1024)
    expected_sha256: Sha256Digest
    expected_snapshot_id: DocumentSnapshotId
    format: Literal["markdown"] = "markdown"


class DocumentRenameRequest(DocumentRequest):
    name: str = Field(min_length=1, max_length=255)
    expected_sha256: Sha256Digest
    expected_snapshot_id: DocumentSnapshotId


class DocumentMoveRequest(DocumentRequest):
    destination_root_id: RootId
    destination_folder: str = Field(default="", max_length=4096)
    expected_sha256: Sha256Digest
    expected_snapshot_id: DocumentSnapshotId


class DocumentFolderCreateRequest(DocumentRequest):
    root_id: RootId
    folder: str = Field(min_length=1, max_length=4096)
    expected_snapshot_id: DocumentSnapshotId


class DocumentRestorePreviewRequest(DocumentRequest):
    history_id: HistoryId
    expected_sha256: Sha256Digest
    expected_snapshot_id: DocumentSnapshotId


class DocumentRestoreRequest(DocumentRequest):
    history_id: HistoryId
    expected_sha256: Sha256Digest
    expected_snapshot_id: DocumentSnapshotId
    preview_token: RestorePreviewToken
    confirmed: Literal[True]


class DocumentIndexRefreshRequest(DocumentRequest):
    expected_snapshot_id: DocumentSnapshotId | None = None
    document_id: DocumentId | None = None
    document_ids: tuple[DocumentId, ...] = Field(default=(), max_length=256)

    @field_validator("document_ids")
    @classmethod
    def _unique_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Document refresh IDs must be unique")
        return value
