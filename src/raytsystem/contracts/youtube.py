from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from raytsystem.contracts.artifacts import Artifact
from raytsystem.contracts.base import (
    FrozenModel,
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sha256,
    VersionedModel,
)

YouTubeProjectId = Annotated[
    str,
    StringConstraints(pattern=r"^ytp_[a-z0-9_]{2,120}$"),
]


class YouTubeChannel(StrEnum):
    ROMARAYT = "romarayt"
    NEUROPROS = "neuropros"


class YouTubeScriptMode(StrEnum):
    ANCHOR = "anchor"
    VERBATIM = "verbatim"


class YouTubeStage(StrEnum):
    BRIEF = "brief"
    VERDICT = "verdict"
    RESEARCH = "research"
    TITLES = "titles"
    THUMBNAILS = "thumbnails"
    SCRIPT = "script"
    PRESENTATION = "presentation"
    PACKAGING = "packaging"
    WARMUP = "warmup"
    MASTER = "master"
    REVIEW = "review"


class YouTubeFactStatus(StrEnum):
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"


class YouTubeLineKind(StrEnum):
    FACTUAL = "factual"
    INFERENCE = "inference"
    EDITORIAL = "editorial"


class YouTubePromise(StrEnum):
    BENEFIT = "benefit"
    REVELATION = "revelation"
    RESULT = "result"
    PAIN = "pain"
    LIST = "list"
    MECHANISM = "mechanism"
    INTRIGUE = "intrigue"


class YouTubeAlignment(StrEnum):
    EXACT = "exact"
    COMPATIBLE = "compatible"
    MISMATCH = "mismatch"
    CONTRADICT = "contradict"


class YouTubeBrief(VersionedModel):
    schema_name: Literal["YouTubeBriefV1"] = "YouTubeBriefV1"
    brief_id: Identifier
    project_id: YouTubeProjectId
    synthetic_fixture: Literal[True] = True
    channel: YouTubeChannel
    topic: NonEmptyStr
    audience: NonEmptyStr
    pain: NonEmptyStr
    promised_artifact: NonEmptyStr
    angle: NonEmptyStr
    target_length_minutes: int = Field(ge=5, le=180)
    mode: YouTubeScriptMode
    needs_review: bool = True
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class YouTubeResearchSeedFact(FrozenModel):
    fact_id: Identifier
    claim: NonEmptyStr
    display_value: NonEmptyStr | None = None
    quote: NonEmptyStr
    status: YouTubeFactStatus
    contradicts: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()


class YouTubeResearchSeed(VersionedModel):
    schema_name: Literal["YouTubeResearchSeedV1"] = "YouTubeResearchSeedV1"
    research_seed_id: Identifier
    project_id: YouTubeProjectId
    synthetic_fixture: Literal[True] = True
    source_path: RelativePath
    facts: tuple[YouTubeResearchSeedFact, ...] = Field(min_length=1)
    checked_at: AwareDatetime

    @field_validator("checked_at")
    @classmethod
    def _checked_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _unique_facts(self) -> YouTubeResearchSeed:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Research seed fact IDs must be unique")
        return self


class YouTubeEvidenceSpan(FrozenModel):
    source_path: RelativePath
    source_sha256: Sha256
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    quote_sha256: Sha256

    @model_validator(mode="after")
    def _ordered_span(self) -> YouTubeEvidenceSpan:
        if self.end_byte <= self.start_byte:
            raise ValueError("Evidence end must follow start")
        return self


class YouTubeFact(FrozenModel):
    fact_id: Identifier
    claim: NonEmptyStr
    display_value: NonEmptyStr | None = None
    status: YouTubeFactStatus
    evidence: YouTubeEvidenceSpan
    checked_at: AwareDatetime
    contradicts: tuple[Identifier, ...] = ()
    supersedes: tuple[Identifier, ...] = ()
    synthetic_only: Literal[True] = True

    @field_validator("checked_at")
    @classmethod
    def _fact_checked_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class YouTubeVerdict(VersionedModel):
    schema_name: Literal["YouTubeVerdictV1"] = "YouTubeVerdictV1"
    verdict_id: Identifier
    project_id: YouTubeProjectId
    verdict: Literal["green", "yellow", "red"]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    evidence_basis: Literal["synthetic_fixture_only"] = "synthetic_fixture_only"
    needs_review: Literal[True] = True
    state: Literal["draft"] = "draft"


class YouTubeResearch(VersionedModel):
    schema_name: Literal["YouTubeResearchV1"] = "YouTubeResearchV1"
    research_id: Identifier
    project_id: YouTubeProjectId
    facts: tuple[YouTubeFact, ...] = Field(min_length=1)
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _unique_facts(self) -> YouTubeResearch:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Research fact IDs must be unique")
        return self


class YouTubeTitleCandidate(FrozenModel):
    title_id: Identifier
    text: NonEmptyStr
    pattern: Identifier
    uppercase_anchor: NonEmptyStr
    length_chars: int = Field(ge=1, le=120)
    word_count: int = Field(ge=1, le=20)
    score: int = Field(ge=0, le=10)
    why: NonEmptyStr
    promise: YouTubePromise
    line_kind: YouTubeLineKind
    fact_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _derived_counts(self) -> YouTubeTitleCandidate:
        if self.length_chars != len(self.text):
            raise ValueError("Title length_chars does not match text")
        if self.word_count != len(self.text.split()):
            raise ValueError("Title word_count does not match text")
        if self.uppercase_anchor not in self.text:
            raise ValueError("Title uppercase anchor is absent from text")
        if (self.line_kind is YouTubeLineKind.FACTUAL) != bool(self.fact_ids):
            raise ValueError("Factual title classification must match fact references")
        return self


class YouTubeTitlePack(VersionedModel):
    schema_name: Literal["YouTubeTitlePackV1"] = "YouTubeTitlePackV1"
    title_pack_id: Identifier
    project_id: YouTubeProjectId
    titles: tuple[YouTubeTitleCandidate, ...] = Field(min_length=10, max_length=10)
    primary_title_id: Identifier
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _title_pack_integrity(self) -> YouTubeTitlePack:
        ids = [title.title_id for title in self.titles]
        if len(ids) != len(set(ids)):
            raise ValueError("Title IDs must be unique")
        if self.primary_title_id not in ids:
            raise ValueError("Primary title must exist in title pack")
        if list(self.titles) != sorted(self.titles, key=lambda item: item.score, reverse=True):
            raise ValueError("Titles must be sorted by descending score")
        return self


class YouTubeThumbnailConcept(FrozenModel):
    concept_id: Identifier
    subject_main: NonEmptyStr
    text_overlay: NonEmptyStr
    composition: Identifier
    colors: tuple[NonEmptyStr, ...] = Field(min_length=2)
    face: NonEmptyStr
    background: NonEmptyStr
    animation_accent: NonEmptyStr
    promise: YouTubePromise
    risk_score: int = Field(ge=0, le=10)
    line_kind: YouTubeLineKind
    fact_ids: tuple[Identifier, ...] = ()

    @field_validator("text_overlay")
    @classmethod
    def _overlay_word_count(cls, value: str) -> str:
        if not 2 <= len(value.split()) <= 4:
            raise ValueError("Thumbnail overlay must contain 2-4 words")
        return value

    @model_validator(mode="after")
    def _fact_classification(self) -> YouTubeThumbnailConcept:
        if (self.line_kind is YouTubeLineKind.FACTUAL) != bool(self.fact_ids):
            raise ValueError("Factual thumbnail classification must match fact references")
        return self


class YouTubeThumbnailPack(VersionedModel):
    schema_name: Literal["YouTubeThumbnailPackV1"] = "YouTubeThumbnailPackV1"
    thumbnail_pack_id: Identifier
    project_id: YouTubeProjectId
    concepts: tuple[YouTubeThumbnailConcept, ...] = Field(min_length=10, max_length=10)
    best_concept_id: Identifier
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _thumbnail_pack_integrity(self) -> YouTubeThumbnailPack:
        ids = [concept.concept_id for concept in self.concepts]
        if len(ids) != len(set(ids)):
            raise ValueError("Thumbnail concept IDs must be unique")
        if self.best_concept_id not in ids:
            raise ValueError("Best concept must exist in thumbnail pack")
        if len({concept.composition for concept in self.concepts}) < 5:
            raise ValueError("Thumbnail concepts are not sufficiently orthogonal")
        return self


class YouTubeScriptBeat(FrozenModel):
    beat_id: Identifier
    heading: NonEmptyStr
    text: NonEmptyStr
    line_kind: YouTubeLineKind
    fact_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _fact_classification(self) -> YouTubeScriptBeat:
        if (self.line_kind is YouTubeLineKind.FACTUAL) != bool(self.fact_ids):
            raise ValueError("Factual script beat classification must match fact references")
        return self


class YouTubeScript(VersionedModel):
    schema_name: Literal["YouTubeScriptV1"] = "YouTubeScriptV1"
    script_id: Identifier
    project_id: YouTubeProjectId
    mode: YouTubeScriptMode
    hook: NonEmptyStr
    hook_kind: YouTubeLineKind
    hook_fact_ids: tuple[Identifier, ...] = ()
    beats: tuple[YouTubeScriptBeat, ...] = Field(min_length=2)
    cta: NonEmptyStr
    source_fact_ids: tuple[Identifier, ...] = ()
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _hook_fact_classification(self) -> YouTubeScript:
        if (self.hook_kind is YouTubeLineKind.FACTUAL) != bool(self.hook_fact_ids):
            raise ValueError("Factual script hook classification must match fact references")
        return self


class YouTubeSlide(FrozenModel):
    slide_id: Identifier
    title: NonEmptyStr
    purpose: NonEmptyStr
    graphic_type: Identifier
    elements: tuple[NonEmptyStr, ...] = Field(min_length=1)
    animation: NonEmptyStr
    line_kind: YouTubeLineKind
    fact_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _fact_classification(self) -> YouTubeSlide:
        if (self.line_kind is YouTubeLineKind.FACTUAL) != bool(self.fact_ids):
            raise ValueError("Factual slide classification must match fact references")
        return self


class YouTubePresentation(VersionedModel):
    schema_name: Literal["YouTubePresentationV1"] = "YouTubePresentationV1"
    presentation_id: Identifier
    project_id: YouTubeProjectId
    slides: tuple[YouTubeSlide, ...] = Field(min_length=3)
    design_instructions_included: Literal[False] = False
    state: Literal["draft"] = "draft"
    needs_review: bool = True


class YouTubePairScore(FrozenModel):
    clarity: int = Field(ge=0, le=5)
    curiosity: int = Field(ge=0, le=5)
    expectation_match: int = Field(ge=0, le=5)
    mobile: int = Field(ge=0, le=5)
    roma_fit: int = Field(ge=0, le=5)
    next_step: int = Field(ge=0, le=5)

    def total(self) -> int:
        return (
            self.clarity
            + self.curiosity
            + self.expectation_match
            + self.mobile
            + self.roma_fit
            + self.next_step
        )


class YouTubePackagePair(FrozenModel):
    pair_id: Identifier
    title_id: Identifier
    concept_id: Identifier
    alignment: YouTubeAlignment
    score: YouTubePairScore
    total: int = Field(ge=0, le=30)
    first_seven_seconds: NonEmptyStr

    @model_validator(mode="after")
    def _score_total(self) -> YouTubePackagePair:
        if self.total != self.score.total():
            raise ValueError("Package pair total does not match component scores")
        return self


class YouTubePackaging(VersionedModel):
    schema_name: Literal["YouTubePackagingV1"] = "YouTubePackagingV1"
    packaging_id: Identifier
    project_id: YouTubeProjectId
    pairs: tuple[YouTubePackagePair, ...] = Field(min_length=3, max_length=3)
    recommended_pair_id: Identifier
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _packaging_integrity(self) -> YouTubePackaging:
        ids = [pair.pair_id for pair in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("Package pair IDs must be unique")
        if self.recommended_pair_id not in ids:
            raise ValueError("Recommended package pair does not exist")
        if list(self.pairs) != sorted(self.pairs, key=lambda item: item.total, reverse=True):
            raise ValueError("Package pairs must be sorted by descending score")
        return self


class YouTubeWarmup(VersionedModel):
    schema_name: Literal["YouTubeWarmupV1"] = "YouTubeWarmupV1"
    warmup_id: Identifier
    project_id: YouTubeProjectId
    text: NonEmptyStr
    line_kind: YouTubeLineKind
    fact_ids: tuple[Identifier, ...] = ()
    state: Literal["draft"] = "draft"
    needs_review: bool = True

    @model_validator(mode="after")
    def _fact_classification(self) -> YouTubeWarmup:
        if (self.line_kind is YouTubeLineKind.FACTUAL) != bool(self.fact_ids):
            raise ValueError("Factual warmup classification must match fact references")
        return self


class YouTubeMasterProjection(VersionedModel):
    schema_name: Literal["YouTubeMasterProjectionV1"] = "YouTubeMasterProjectionV1"
    master_id: Identifier
    project_id: YouTubeProjectId
    path: RelativePath
    content_sha256: Sha256
    source_stage_hashes: dict[str, Sha256]
    state: Literal["draft"] = "draft"
    synthetic_fixture: Literal[True] = True
    generated_at: AwareDatetime

    @field_validator("generated_at")
    @classmethod
    def _generated_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class YouTubePackageReview(VersionedModel):
    schema_name: Literal["YouTubePackageReviewV1"] = "YouTubePackageReviewV1"
    review_id: Identifier
    project_id: YouTubeProjectId
    verdict: Literal["pass", "warn", "fail"]
    review_kind: Literal["deterministic_validation"] = "deterministic_validation"
    checks: dict[str, bool]
    findings: tuple[NonEmptyStr, ...] = ()
    human_review_required: Literal[True] = True
    state: Literal["draft"] = "draft"


class YouTubeStageDependency(FrozenModel):
    stage: YouTubeStage
    output_sha256: Sha256


class YouTubeStageRecord(VersionedModel):
    schema_name: Literal["YouTubeStageRecordV1"] = "YouTubeStageRecordV1"
    stage_record_id: Identifier
    project_id: YouTubeProjectId
    run_id: Identifier
    stage: YouTubeStage
    fingerprint_sha256: Sha256
    dependencies: tuple[YouTubeStageDependency, ...] = ()
    external_input_sha256: Sha256 | None = None
    output_schema_name: NonEmptyStr
    output_sha256: Sha256
    output_path: RelativePath
    artifact: Artifact
    pipeline_version: NonEmptyStr
    status: Literal["succeeded"] = "succeeded"
    completed_at: AwareDatetime

    @field_validator("completed_at")
    @classmethod
    def _completed_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class YouTubeProjectManifest(VersionedModel):
    schema_name: Literal["YouTubeProjectManifestV1"] = "YouTubeProjectManifestV1"
    project_id: YouTubeProjectId
    run_id: Identifier
    brief_input_path: RelativePath
    research_input_path: RelativePath
    stage_record_sha256: dict[str, Sha256]
    status: Literal["running", "checkpointed", "draft_complete", "failed"]
    master_path: RelativePath | None = None
    synthetic_fixture: Literal[True] = True
    updated_at: AwareDatetime

    @field_validator("updated_at")
    @classmethod
    def _updated_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
