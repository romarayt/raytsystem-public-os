from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    ComponentRef,
    Identifier,
    NonEmptyStr,
    ProducerRef,
    RecordRef,
    Sha256,
    VersionedModel,
    derive_id,
    sha256_hex,
)


class ProposalPurpose(StrEnum):
    EXTRACT_KNOWLEDGE = "extract_knowledge"
    ANSWER_QUERY = "answer_query"
    SAVE_SYNTHESIS = "save_synthesis"
    REVIEW = "review"


class ProposalItem(VersionedModel):
    schema_name: Literal["ProposalItemV1"] = "ProposalItemV1"
    proposal_item_id: Identifier
    kind: Identifier
    payload: dict[str, Any]
    evidence_ids: tuple[Identifier, ...]


class ProposalRequest(VersionedModel):
    schema_name: Literal["ProposalRequestV1"] = "ProposalRequestV1"
    proposal_request_id: Identifier
    run_id: Identifier
    operation_key: Identifier
    purpose: ProposalPurpose
    evidence_pack_ref: RecordRef
    allowed_evidence_ids: tuple[Identifier, ...]
    target_schema_refs: tuple[RecordRef, ...]
    prompt_or_skill_ref: ComponentRef
    policy_constraints: tuple[Identifier, ...] = ()
    created_at: AwareDatetime


class ProposalResponse(VersionedModel):
    schema_name: Literal["ProposalResponseV1"] = "ProposalResponseV1"
    proposal_response_id: Identifier
    request_ref: RecordRef
    producer: ProducerRef
    allowed_evidence_ids: tuple[Identifier, ...]
    proposed_items: tuple[ProposalItem, ...]
    raw_response_sha256: Sha256 | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _evidence_subset(self) -> ProposalResponse:
        allowed = set(self.allowed_evidence_ids)
        referenced = {evidence for item in self.proposed_items for evidence in item.evidence_ids}
        unknown = sorted(referenced - allowed)
        if unknown:
            raise ValueError(f"Proposal references evidence outside its pack: {unknown}")
        return self


class AnswerSection(VersionedModel):
    schema_name: Literal["AnswerSectionV1"] = "AnswerSectionV1"
    text: NonEmptyStr
    citation_ids: tuple[Identifier, ...] = ()

    @field_validator("text")
    @classmethod
    def _single_line_text(cls, value: str) -> str:
        if any(character in value for character in ("\x00", "\r", "\n")):
            raise ValueError("Answer section text must be one inert line")
        return value


class AnswerProposal(VersionedModel):
    schema_name: Literal["AnswerProposalV1"] = "AnswerProposalV1"
    answer_proposal_id: Identifier
    run_id: Identifier
    generation_id: Identifier
    generation_ref: RecordRef
    query_sha256: Sha256
    query_text: NonEmptyStr
    intent: Identifier
    facts: tuple[AnswerSection, ...] = ()
    inferences: tuple[AnswerSection, ...] = ()
    gaps: tuple[AnswerSection, ...] = ()
    rendered_answer: NonEmptyStr
    citation_ids: tuple[Identifier, ...] = ()
    producer: ProducerRef
    created_at: AwareDatetime

    @staticmethod
    def _identity_material(
        *,
        run_id: str,
        generation_id: str,
        generation_ref: RecordRef,
        query_sha256: str,
        intent: str,
        facts: tuple[AnswerSection, ...],
        inferences: tuple[AnswerSection, ...],
        gaps: tuple[AnswerSection, ...],
        producer: ProducerRef,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "generation_id": generation_id,
            "generation_ref": generation_ref,
            "query_sha256": query_sha256,
            "intent": intent,
            "facts": [section.text for section in facts],
            "inferences": [section.text for section in inferences],
            "gaps": [section.text for section in gaps],
            "producer": producer,
        }

    @staticmethod
    def render_sections(
        *,
        facts: tuple[AnswerSection, ...],
        inferences: tuple[AnswerSection, ...],
        gaps: tuple[AnswerSection, ...],
        citation_ids: tuple[str, ...],
    ) -> str:
        labels = {citation_id: f"S{index}" for index, citation_id in enumerate(citation_ids, 1)}
        lines: list[str] = []
        for heading, sections in (
            ("Facts", facts),
            ("Inferences", inferences),
            ("Gaps", gaps),
        ):
            lines.append(f"## {heading}")
            if not sections:
                lines.append("_None._")
            else:
                for section in sections:
                    references = " ".join(
                        f"[{labels[citation_id]}]" for citation_id in section.citation_ids
                    )
                    suffix = "" if not references else f" {references}"
                    lines.append(f"- {section.text}{suffix}")
            lines.append("")
        if citation_ids:
            lines.append("## Sources")
            lines.extend(
                f"- [{labels[citation_id]}] `{citation_id}`" for citation_id in citation_ids
            )
        return "\n".join(lines).rstrip()

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        generation_id: str,
        generation_sha256: str,
        query_text: str,
        intent: str,
        facts: tuple[AnswerSection, ...],
        inferences: tuple[AnswerSection, ...],
        gaps: tuple[AnswerSection, ...],
        citation_ids: tuple[str, ...],
        producer: ProducerRef,
        created_at: datetime,
    ) -> AnswerProposal:
        query_sha256 = sha256_hex(query_text.encode("utf-8"))
        generation_ref = RecordRef(
            kind="generation",
            id=generation_id,
            object_sha256=generation_sha256,
        )
        identity = cls._identity_material(
            run_id=run_id,
            generation_id=generation_id,
            generation_ref=generation_ref,
            query_sha256=query_sha256,
            intent=intent,
            facts=facts,
            inferences=inferences,
            gaps=gaps,
            producer=producer,
        )
        return cls(
            answer_proposal_id=derive_id("ans", identity),
            run_id=run_id,
            generation_id=generation_id,
            generation_ref=generation_ref,
            query_sha256=query_sha256,
            query_text=query_text,
            intent=intent,
            facts=facts,
            inferences=inferences,
            gaps=gaps,
            rendered_answer=cls.render_sections(
                facts=facts,
                inferences=inferences,
                gaps=gaps,
                citation_ids=citation_ids,
            ),
            citation_ids=citation_ids,
            producer=producer,
            created_at=created_at,
        )

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _closed_answer(self) -> AnswerProposal:
        if any(not section.citation_ids for section in self.facts):
            raise ValueError("Fact sections require citations")
        if any(not section.citation_ids for section in self.inferences):
            raise ValueError("Inference sections require cited premises")
        if any(section.citation_ids for section in self.gaps):
            raise ValueError("Gap sections cannot masquerade as cited facts")
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("Answer citation IDs must be unique")
        referenced = {
            citation_id
            for section in (*self.facts, *self.inferences)
            for citation_id in section.citation_ids
        }
        if referenced != set(self.citation_ids):
            raise ValueError("Answer citation IDs must exactly match section citations")
        if self.query_sha256 != sha256_hex(self.query_text.encode("utf-8")):
            raise ValueError("Answer query hash mismatch")
        if self.generation_ref.kind != "generation" or self.generation_ref.id != self.generation_id:
            raise ValueError("Answer generation reference mismatch")
        expected_id = derive_id(
            "ans",
            self._identity_material(
                run_id=self.run_id,
                generation_id=self.generation_id,
                generation_ref=self.generation_ref,
                query_sha256=self.query_sha256,
                intent=self.intent,
                facts=self.facts,
                inferences=self.inferences,
                gaps=self.gaps,
                producer=self.producer,
            ),
        )
        if self.answer_proposal_id != expected_id:
            raise ValueError("Answer proposal identity mismatch")
        expected_rendered = self.render_sections(
            facts=self.facts,
            inferences=self.inferences,
            gaps=self.gaps,
            citation_ids=self.citation_ids,
        )
        if self.rendered_answer != expected_rendered:
            raise ValueError("Answer rendered_answer differs from its structured sections")
        return self


class CitationVerification(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    PENDING = "pending"


class QueryCitation(VersionedModel):
    schema_name: Literal["QueryCitationV1"] = "QueryCitationV1"
    query_citation_id: Identifier
    answer_proposal_id: Identifier
    generation_id: Identifier
    generation_ref: RecordRef
    answer_char_start: int = Field(ge=0)
    answer_char_end: int = Field(ge=0)
    claim_id: Identifier | None = None
    source_revision_id: Identifier
    normalization_id: Identifier
    segment_id: Identifier
    cited_excerpt_sha256: Sha256
    source_locator: NonEmptyStr
    verification: CitationVerification = CitationVerification.PENDING
    failure_codes: tuple[Identifier, ...] = ()
    verified_at: AwareDatetime | None = None

    @field_validator("verified_at")
    @classmethod
    def _verified_at_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _answer_span(self) -> QueryCitation:
        if self.answer_char_end < self.answer_char_start:
            raise ValueError("Citation answer span is reversed")
        if self.verification is CitationVerification.VERIFIED:
            if self.verified_at is None or self.failure_codes:
                raise ValueError("Verified citation needs verified_at and no failure codes")
        elif self.verification is CitationVerification.FAILED:
            if self.verified_at is None or not self.failure_codes:
                raise ValueError("Failed citation verification needs a timestamp and failure code")
        elif self.verified_at is not None or self.failure_codes:
            raise ValueError("Pending citation cannot contain a verification result")
        expected_id = derive_id(
            "qcit",
            {
                "answer_proposal_id": self.answer_proposal_id,
                "generation_id": self.generation_id,
                "generation_ref": self.generation_ref,
                "answer_char_start": self.answer_char_start,
                "answer_char_end": self.answer_char_end,
                "claim_id": self.claim_id,
                "source_revision_id": self.source_revision_id,
                "normalization_id": self.normalization_id,
                "segment_id": self.segment_id,
                "cited_excerpt_sha256": self.cited_excerpt_sha256,
                "source_locator": self.source_locator,
            },
        )
        if self.query_citation_id != expected_id:
            raise ValueError("Query citation identity mismatch")
        if self.generation_ref.kind != "generation" or self.generation_ref.id != self.generation_id:
            raise ValueError("Query citation generation reference mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        answer_proposal_id: str,
        generation_id: str,
        generation_sha256: str,
        answer_char_start: int,
        answer_char_end: int,
        claim_id: str | None,
        source_revision_id: str,
        normalization_id: str,
        segment_id: str,
        cited_excerpt_sha256: str,
        source_locator: str,
        verification: CitationVerification,
        failure_codes: tuple[str, ...],
        verified_at: datetime | None,
    ) -> QueryCitation:
        generation_ref = RecordRef(
            kind="generation",
            id=generation_id,
            object_sha256=generation_sha256,
        )
        identity = {
            "answer_proposal_id": answer_proposal_id,
            "generation_id": generation_id,
            "generation_ref": generation_ref,
            "answer_char_start": answer_char_start,
            "answer_char_end": answer_char_end,
            "claim_id": claim_id,
            "source_revision_id": source_revision_id,
            "normalization_id": normalization_id,
            "segment_id": segment_id,
            "cited_excerpt_sha256": cited_excerpt_sha256,
            "source_locator": source_locator,
        }
        return cls(
            query_citation_id=derive_id("qcit", identity),
            answer_proposal_id=answer_proposal_id,
            generation_id=generation_id,
            generation_ref=generation_ref,
            answer_char_start=answer_char_start,
            answer_char_end=answer_char_end,
            claim_id=claim_id,
            source_revision_id=source_revision_id,
            normalization_id=normalization_id,
            segment_id=segment_id,
            cited_excerpt_sha256=cited_excerpt_sha256,
            source_locator=source_locator,
            verification=verification,
            failure_codes=failure_codes,
            verified_at=verified_at,
        )
