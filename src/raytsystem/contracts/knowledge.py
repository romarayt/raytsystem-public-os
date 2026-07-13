from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    ProducerRef,
    RecordRef,
    TimeRange,
    VersionedModel,
)


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    STALE = "stale"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    STALE = "stale"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class ReviewVerdict(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NEEDS_HUMAN = "needs_human"


class Alias(VersionedModel):
    schema_name: Literal["AliasV1"] = "AliasV1"
    value: NonEmptyStr
    language: str | None = None
    kind: Identifier = "name"


class Claim(VersionedModel):
    schema_name: Literal["ClaimV1"] = "ClaimV1"
    claim_id: Identifier
    proposition_key: str | None = None
    statement: NonEmptyStr
    language: str = "und"
    scope: dict[str, Any] = Field(default_factory=dict)
    relation_ids: tuple[Identifier, ...] = ()
    evidence_ids: tuple[Identifier, ...] = ()
    status: ClaimStatus
    temporal: TimeRange = Field(default_factory=TimeRange)
    recorded_at: AwareDatetime
    supersedes: tuple[Identifier, ...] = ()
    contradicts: tuple[Identifier, ...] = ()
    confidence_components: dict[str, str] = Field(default_factory=dict)
    review_ids: tuple[Identifier, ...] = ()

    @field_validator("recorded_at")
    @classmethod
    def _recorded_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _evidence_required(self) -> Claim:
        if self.status in {ClaimStatus.SUPPORTED, ClaimStatus.CONFIRMED} and not self.evidence_ids:
            raise ValueError("Supported or confirmed claims require evidence")
        return self


class Entity(VersionedModel):
    schema_name: Literal["EntityV1"] = "EntityV1"
    entity_id: Identifier
    entity_type: Identifier
    canonical_label: NonEmptyStr
    aliases: tuple[Alias, ...] = ()
    disambiguators: dict[str, str] = Field(default_factory=dict)
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    superseded_by: tuple[Identifier, ...] = ()
    review_ids: tuple[Identifier, ...] = ()


class EntityObject(VersionedModel):
    schema_name: Literal["EntityObjectV1"] = "EntityObjectV1"
    kind: Literal["entity"] = "entity"
    entity_id: Identifier


class LiteralObject(VersionedModel):
    schema_name: Literal["LiteralObjectV1"] = "LiteralObjectV1"
    kind: Literal["literal"] = "literal"
    value: NonEmptyStr
    datatype: Identifier = "text"
    language: str | None = None


RelationObject = Annotated[EntityObject | LiteralObject, Field(discriminator="kind")]


class Relation(VersionedModel):
    schema_name: Literal["RelationV1"] = "RelationV1"
    relation_id: Identifier
    subject_entity_id: Identifier
    predicate: Identifier
    object: RelationObject
    qualifiers: dict[str, str] = Field(default_factory=dict)
    claim_ids: tuple[Identifier, ...] = Field(min_length=1)
    temporal: TimeRange = Field(default_factory=TimeRange)
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE


class ReviewFinding(VersionedModel):
    schema_name: Literal["ReviewFindingV1"] = "ReviewFindingV1"
    code: Identifier
    severity: Literal["info", "low", "medium", "high", "critical"]
    message: NonEmptyStr
    evidence_refs: tuple[RecordRef, ...] = ()


class Review(VersionedModel):
    schema_name: Literal["ReviewV1"] = "ReviewV1"
    review_id: Identifier
    target: RecordRef
    review_kind: Identifier
    rubric_ref: RecordRef | None = None
    reviewer: ProducerRef
    verdict: ReviewVerdict
    findings: tuple[ReviewFinding, ...] = ()
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


ConfidenceDecimal = Annotated[str, StringConstraints(pattern=r"^(0(\.[0-9]+)?|1(\.0+)?)$")]
