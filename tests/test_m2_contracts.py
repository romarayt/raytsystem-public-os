from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from raytsystem.contracts import (
    AnswerProposal,
    AnswerSection,
    CitationVerification,
    ComponentRef,
    ProducerRef,
    QueryCitation,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.contracts.base import ProducerKind


def _producer() -> ProducerRef:
    return ProducerRef(
        kind=ProducerKind.KERNEL,
        component=ComponentRef(
            name="raytsystem_query",
            version="1.0.0",
            config_sha256=sha256_hex(canonical_json_bytes({"mode": "fts5"})),
        ),
    )


def test_answer_contract_binds_generation_sections_citations_and_rendering() -> None:
    created_at = datetime(2026, 7, 11, tzinfo=UTC)
    fact = AnswerSection(text="Canonical fact.", citation_ids=("qcit_verified",))
    answer = AnswerProposal.create(
        run_id="run_query",
        generation_id="gen_active",
        generation_sha256="b" * 64,
        query_text="What is canonical?",
        intent="fact",
        facts=(fact,),
        inferences=(),
        gaps=(),
        citation_ids=("qcit_verified",),
        producer=_producer(),
        created_at=created_at,
    )

    assert answer.generation_id == "gen_active"
    assert answer.generation_ref.object_sha256 == "b" * 64
    assert answer.rendered_answer == AnswerProposal.render_sections(
        facts=answer.facts,
        inferences=answer.inferences,
        gaps=answer.gaps,
        citation_ids=answer.citation_ids,
    )

    payload = answer.model_dump(mode="python")
    payload["rendered_answer"] += "\nUnsupported model-memory fact."
    with pytest.raises(ValidationError, match="rendered_answer"):
        AnswerProposal.model_validate(payload)


def test_answer_contract_rejects_uncited_fact_and_unused_citation() -> None:
    base = {
        "run_id": "run_query",
        "generation_id": "gen_active",
        "generation_sha256": "b" * 64,
        "query_text": "Question",
        "intent": "fact",
        "inferences": (),
        "gaps": (),
        "producer": _producer(),
        "created_at": datetime(2026, 7, 11, tzinfo=UTC),
    }
    with pytest.raises(ValueError, match="Fact sections require citations"):
        AnswerProposal.create(
            **base,
            facts=(AnswerSection(text="Unsupported fact."),),
            citation_ids=(),
        )
    with pytest.raises(ValueError, match="exactly match"):
        AnswerProposal.create(
            **base,
            facts=(AnswerSection(text="Fact.", citation_ids=("qcit_used",)),),
            citation_ids=("qcit_used", "qcit_unused"),
        )


def test_query_citation_lifecycle_and_identity_are_closed() -> None:
    verified_at = datetime(2026, 7, 11, tzinfo=UTC)
    citation = QueryCitation.create(
        answer_proposal_id="ans_verified",
        generation_id="gen_active",
        generation_sha256="b" * 64,
        answer_char_start=10,
        answer_char_end=20,
        claim_id="clm_supported",
        source_revision_id="srev_exact",
        normalization_id="norm_exact",
        segment_id="seg_exact",
        cited_excerpt_sha256="a" * 64,
        source_locator="normalized/srev_exact/norm_exact/document.txt#line=1",
        verification=CitationVerification.VERIFIED,
        failure_codes=(),
        verified_at=verified_at,
    )
    assert citation.verification is CitationVerification.VERIFIED
    assert citation.verified_at == verified_at

    payload = citation.model_dump(mode="python")
    payload["verified_at"] = None
    with pytest.raises(ValidationError, match="Verified citation"):
        QueryCitation.model_validate(payload)

    payload = citation.model_dump(mode="python")
    payload["failure_codes"] = ("raw_hash_mismatch",)
    with pytest.raises(ValidationError, match="Verified citation"):
        QueryCitation.model_validate(payload)

    payload = citation.model_dump(mode="python")
    payload["query_citation_id"] = "qcit_tampered"
    with pytest.raises(ValidationError, match="identity"):
        QueryCitation.model_validate(payload)
