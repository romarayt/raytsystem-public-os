from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from raytsystem.contracts.base import (
    ComponentRef,
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sensitivity,
    Sha256,
    TrustClass,
    VersionedModel,
    derive_id,
)


class Origin(VersionedModel):
    schema_name: Literal["OriginV1"] = "OriginV1"
    kind: Identifier
    locator: NonEmptyStr | None = None
    locator_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _one_locator(self) -> Origin:
        if (self.locator is None) == (self.locator_sha256 is None):
            raise ValueError("Exactly one origin locator representation is required")
        return self


class Source(VersionedModel):
    schema_name: Literal["SourceV1"] = "SourceV1"
    source_id: Identifier
    identity_scheme: Identifier
    identity_key_sha256: Sha256
    origin: Origin
    source_type: Identifier
    display_name: NonEmptyStr | None = None
    trust_class: TrustClass = TrustClass.UNTRUSTED
    rights: NonEmptyStr = "unknown"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class SourceRevision(VersionedModel):
    schema_name: Literal["SourceRevisionV1"] = "SourceRevisionV1"
    source_revision_id: Identifier
    source_id: Identifier
    content_sha256: Sha256
    byte_length: int = Field(default=0, ge=0)
    media_type: NonEmptyStr = "application/octet-stream"
    raw_path: RelativePath
    captured_at: AwareDatetime
    published_at: AwareDatetime | None = None
    fetcher_ref: ComponentRef | None = None
    origin_snapshot: Origin | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    retention: Identifier = "retain"
    derived_from_revision_id: Identifier | None = None

    @field_validator("captured_at", "published_at")
    @classmethod
    def _timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @classmethod
    def create(
        cls,
        *,
        source_id: str,
        content_sha256: str,
        raw_path: str,
        retrieved_at: datetime,
        byte_length: int = 0,
        media_type: str = "application/octet-stream",
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
    ) -> SourceRevision:
        revision_id = derive_id(
            "srev",
            {"source_id": source_id, "content_sha256": content_sha256},
        )
        return cls(
            source_revision_id=revision_id,
            source_id=source_id,
            content_sha256=content_sha256,
            byte_length=byte_length,
            media_type=media_type,
            raw_path=raw_path,
            captured_at=retrieved_at,
            sensitivity=sensitivity,
        )


class Normalization(VersionedModel):
    schema_name: Literal["NormalizationV1"] = "NormalizationV1"
    normalization_id: Identifier
    source_revision_id: Identifier
    extractor_ref: ComponentRef
    config_sha256: Sha256
    document_sha256: Sha256
    segments_sha256: Sha256 | None = None
    document_path: RelativePath | None = None
    segments_path: RelativePath | None = None
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")] | None = None
    segment_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @classmethod
    def create(
        cls,
        *,
        source_revision_id: str,
        adapter: str,
        parser_version: str,
        config_sha256: str,
        document_sha256: str,
        created_at: datetime,
    ) -> Normalization:
        extractor = ComponentRef(
            name=adapter,
            version=parser_version,
            config_sha256=config_sha256,
        )
        normalization_id = derive_id(
            "norm",
            {
                "source_revision_id": source_revision_id,
                "extractor": extractor,
                "normalization_config_sha256": config_sha256,
            },
        )
        return cls(
            normalization_id=normalization_id,
            source_revision_id=source_revision_id,
            extractor_ref=extractor,
            config_sha256=config_sha256,
            document_sha256=document_sha256,
            created_at=created_at,
        )


class TextLocator(VersionedModel):
    schema_name: Literal["TextLocatorV1"] = "TextLocatorV1"
    kind: Literal["text"] = "text"
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _valid_range(self) -> TextLocator:
        if self.line_start is None and self.char_start is None:
            raise ValueError("A text locator needs a line or character range")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end precedes line_start")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end precedes char_start")
        return self


DecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"),
]


class PdfLocator(VersionedModel):
    schema_name: Literal["PdfLocatorV1"] = "PdfLocatorV1"
    kind: Literal["pdf"] = "pdf"
    page_index: int = Field(ge=0)
    bbox: tuple[DecimalString, DecimalString, DecimalString, DecimalString]


class TimeLocator(VersionedModel):
    schema_name: Literal["TimeLocatorV1"] = "TimeLocatorV1"
    kind: Literal["time"] = "time"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_time(self) -> TimeLocator:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms precedes start_ms")
        return self


class ImageLocator(VersionedModel):
    schema_name: Literal["ImageLocatorV1"] = "ImageLocatorV1"
    kind: Literal["image"] = "image"
    bbox: tuple[int, int, int, int]
    frame_index: int | None = Field(default=None, ge=0)


class JsonLocator(VersionedModel):
    schema_name: Literal["JsonLocatorV1"] = "JsonLocatorV1"
    kind: Literal["json"] = "json"
    pointer: Annotated[str, StringConstraints(pattern=r"^(|/.*)$")]


class TableLocator(VersionedModel):
    schema_name: Literal["TableLocatorV1"] = "TableLocatorV1"
    kind: Literal["table"] = "table"
    table: NonEmptyStr
    row_start: int = Field(ge=0)
    row_end: int = Field(ge=0)
    column_start: int = Field(ge=0)
    column_end: int = Field(ge=0)


SegmentLocator = Annotated[
    TextLocator | PdfLocator | TimeLocator | ImageLocator | JsonLocator | TableLocator,
    Field(discriminator="kind"),
]


class Segment(VersionedModel):
    schema_name: Literal["SegmentV1"] = "SegmentV1"
    segment_id: Identifier
    source_revision_id: Identifier
    normalization_id: Identifier
    ordinal: int = Field(ge=0)
    locator: SegmentLocator
    excerpt_sha256: Sha256
    language: str | None = None
    modality: Identifier = "text"

    @classmethod
    def create(
        cls,
        *,
        source_revision_id: str,
        normalization_id: str,
        ordinal: int,
        locator: SegmentLocator,
        excerpt_sha256: str,
        language: str | None = None,
        modality: str = "text",
    ) -> Segment:
        segment_id = derive_id(
            "seg",
            {
                "normalization_id": normalization_id,
                "locator": locator,
                "excerpt_sha256": excerpt_sha256,
            },
        )
        return cls(
            segment_id=segment_id,
            source_revision_id=source_revision_id,
            normalization_id=normalization_id,
            ordinal=ordinal,
            locator=locator,
            excerpt_sha256=excerpt_sha256,
            language=language,
            modality=modality,
        )


class EvidenceItem(VersionedModel):
    schema_name: Literal["EvidenceItemV1"] = "EvidenceItemV1"
    source_revision_id: Identifier
    normalization_id: Identifier
    segment_id: Identifier
    locator: SegmentLocator
    excerpt: NonEmptyStr
    excerpt_sha256: Sha256
    trust_class: TrustClass
    captured_at: AwareDatetime | None = None


class EvidencePack(VersionedModel):
    schema_name: Literal["EvidencePackV1"] = "EvidencePackV1"
    evidence_pack_id: Identifier
    run_id: Identifier
    purpose: Identifier
    items: tuple[EvidenceItem, ...]
    classification: Sensitivity
    pack_sha256: Sha256
    created_at: AwareDatetime
