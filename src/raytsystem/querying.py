from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from raytsystem.codegraph.contracts import CodeGraphQueryResult
from raytsystem.codegraph.detect import load_code_graph_config
from raytsystem.codegraph.projection import CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryError, CodeGraphQueryService
from raytsystem.contracts import (
    AnswerProposal,
    AnswerSection,
    CitationVerification,
    ComponentRef,
    ProducerRef,
    QueryCitation,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.base import ProducerKind
from raytsystem.corpus import ActiveCorpus, CorpusIntegrityError
from raytsystem.projections import ProjectionError, ProjectionService
from raytsystem.rendering import escape_untrusted_markdown
from raytsystem.search import (
    FTS5SearchAdapter,
    SearchHit,
    SearchQueryError,
    SearchUnavailable,
    StaleIndexError,
    literal_search_tokens,
)
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import read_current_generation


class QueryRejected(RuntimeError):
    """A query was unsafe, unbounded, stale or lacked trustworthy evidence."""


@dataclass(frozen=True)
class QueryResult:
    answer: AnswerProposal
    citations: tuple[QueryCitation, ...]
    hits: tuple[SearchHit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer.model_dump(mode="json"),
            "citations": [citation.model_dump(mode="json") for citation in self.citations],
            "hits": [asdict(hit) for hit in self.hits],
        }


class QueryScope(StrEnum):
    AUTO = "auto"
    KNOWLEDGE = "knowledge"
    CODE = "code"


@dataclass(frozen=True)
class RoutedQueryResult:
    requested_scope: QueryScope
    resolved_scope: QueryScope
    knowledge: QueryResult | None = None
    code: CodeGraphQueryResult | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_scope": self.requested_scope.value,
            "resolved_scope": self.resolved_scope.value,
            "fallback_reason": self.fallback_reason,
            "knowledge": None if self.knowledge is None else self.knowledge.to_dict(),
            "code": None if self.code is None else self.code.model_dump(mode="json"),
        }

    def render(self) -> str:
        if self.knowledge is not None:
            return self.knowledge.answer.rendered_answer
        assert self.code is not None
        lines = [
            f"Code graph {self.code.snapshot_id} · {len(self.code.nodes)} nodes · "
            f"{self.code.estimated_context_bytes} bytes"
        ]
        for node in self.code.nodes:
            location = ""
            if node.path is not None:
                location = node.path
                if node.location is not None:
                    location += f":{node.location.start_line}"
            lines.append(
                " · ".join(value for value in (node.kind.value, node.label, location) if value)
            )
        if self.fallback_reason is not None:
            lines.append(f"fallback: {self.fallback_reason}")
        return "\n".join(lines)


class IntentRouter:
    version = "1.0.0"

    @staticmethod
    def classify(query: str) -> str:
        normalized = query.casefold()
        if any(token in normalized for token in (" vs ", "compare", "comparison", "сравн")):
            return "comparison"
        if any(token in normalized for token in ("relationship", "related", "relation", "связ")):
            return "relationship"
        if any(token in normalized for token in ("when", "latest", "history", "когда", "последн")):
            return "temporal"
        if any(token in normalized for token in ("all ", "every", "corpus", "все ", "сколько")):
            return "corpus_wide"
        if any(token in normalized for token in ("should", "decision", "решен", "стоит ли")):
            return "workflow_decision"
        return "fact"


class QueryService:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def route(
        self,
        query_text: str,
        *,
        scope: QueryScope = QueryScope.AUTO,
        limit: int = 10,
        depth: int = 2,
    ) -> RoutedQueryResult:
        """Prefer a verified code graph for architecture intent, with an explicit fallback."""

        graph_config = load_code_graph_config(self.root)
        code_first = scope is QueryScope.CODE or (
            scope is QueryScope.AUTO
            and graph_config.graph_first_query_enabled
            and self._is_code_intent(query_text)
        )
        fallback_reason: str | None = None
        if code_first:
            try:
                result = CodeGraphQueryService(self.root, scanner=self.scanner).query(
                    query_text,
                    depth=depth,
                )
                return RoutedQueryResult(
                    requested_scope=scope,
                    resolved_scope=QueryScope.CODE,
                    code=result,
                )
            except (CodeGraphQueryError, CodeGraphUnavailable) as error:
                if scope is QueryScope.CODE:
                    raise QueryRejected("Verified code graph retrieval is unavailable") from error
                fallback_reason = "code_graph_unavailable_or_stale"
        else:
            fallback_reason = (
                "graph_first_disabled"
                if scope is QueryScope.AUTO
                and self._is_code_intent(query_text)
                and not graph_config.graph_first_query_enabled
                else None
            )
        knowledge = self.query(query_text, limit=limit)
        return RoutedQueryResult(
            requested_scope=scope,
            resolved_scope=QueryScope.KNOWLEDGE,
            knowledge=knowledge,
            fallback_reason=fallback_reason,
        )

    def query(self, query_text: str, *, limit: int = 10) -> QueryResult:
        self._validate_query(query_text, limit)
        force_rebuild = False
        for attempt in range(2):
            try:
                corpus = ActiveCorpus.load(self.root)
                projector = ProjectionService(self.root, scanner=self.scanner)
                if force_rebuild or not projector.is_current(corpus):
                    projector.rebuild()
                    corpus = ActiveCorpus.load(self.root)
                adapter = FTS5SearchAdapter(self.root)
                hits = adapter.search(query_text, kinds=("claim", "entity"), limit=limit)
                if any(
                    hit.generation_id != corpus.generation.generation_id
                    or hit.generation_sha256 != corpus.generation_sha256
                    for hit in hits
                ):
                    raise StaleIndexError("Search hits crossed a generation boundary")
                self._verify_hits(corpus, hits)
                result = self._answer(corpus, query_text, hits, requested_limit=limit)
                if read_current_generation(self.root) != corpus.generation.generation_id:
                    raise StaleIndexError("ledger/CURRENT changed before query completion")
                return result
            except StaleIndexError:
                if attempt == 0:
                    force_rebuild = True
                    continue
                raise QueryRejected(
                    "Query snapshot changed; retry against a stable generation"
                ) from None
            except CorpusIntegrityError as error:
                raise QueryRejected(
                    f"Query evidence integrity failed with code {error.code}"
                ) from None
            except (ProjectionError, SearchUnavailable) as error:
                raise QueryRejected("Local retrieval projection is unavailable") from error
        raise QueryRejected("Query snapshot could not be stabilized")

    @staticmethod
    def _is_code_intent(query_text: str) -> bool:
        normalized = query_text.casefold()
        code_markers = (
            "architecture",
            "call graph",
            "class ",
            "code",
            "dependency",
            "depends on",
            "function ",
            "impact",
            "import",
            "module",
            "ownership",
            "test coverage",
            "where is",
            ".py",
            ".tsx",
            ".ts",
            "архитектур",
            "влияни",
            "где определ",
            "зависимост",
            "импорт",
            "класс ",
            "код",
            "модул",
            "покрыт тест",
            "функци",
        )
        return any(marker in normalized for marker in code_markers)

    def _answer(
        self,
        corpus: ActiveCorpus,
        query_text: str,
        hits: tuple[SearchHit, ...],
        *,
        requested_limit: int,
    ) -> QueryResult:
        intent = IntentRouter.classify(query_text)
        fact_candidates: list[tuple[str, str, tuple[str, ...]]] = []
        for hit in hits:
            if hit.kind != "claim":
                continue
            claim = corpus.claims.get(hit.logical_id)
            if claim is None or claim.status.value not in {"supported", "confirmed"}:
                continue
            if not claim.evidence_ids:
                continue
            fact_candidates.append(
                (
                    claim.claim_id,
                    escape_untrusted_markdown(claim.statement),
                    tuple(sorted(claim.evidence_ids)),
                )
            )
        gap_sections: tuple[AnswerSection, ...] = ()
        if not fact_candidates:
            gap_sections = (
                AnswerSection(
                    text="No supported claim in the active generation matches this query."
                ),
            )
        producer = self._producer()
        run_id = derive_id(
            "run",
            {
                "operation": "query",
                "generation_id": corpus.generation.generation_id,
                "query_sha256": sha256_hex(query_text.encode("utf-8")),
                "limit": requested_limit,
                "service_version": self.version,
            },
        )
        bare_facts = tuple(AnswerSection(text=statement) for _, statement, _ in fact_candidates)
        query_sha256 = sha256_hex(query_text.encode("utf-8"))
        answer_id = derive_id(
            "ans",
            AnswerProposal._identity_material(
                run_id=run_id,
                generation_id=corpus.generation.generation_id,
                generation_ref=self._generation_ref(corpus),
                query_sha256=query_sha256,
                intent=intent,
                facts=bare_facts,
                inferences=(),
                gaps=gap_sections,
                producer=producer,
            ),
        )
        placeholder_groups: list[tuple[str, ...]] = []
        placeholder_ids: list[str] = []
        for _claim_id, _statement, evidence_ids in fact_candidates:
            group = tuple(
                f"qcit_placeholder_{len(placeholder_ids) + offset}"
                for offset in range(1, len(evidence_ids) + 1)
            )
            placeholder_groups.append(group)
            placeholder_ids.extend(group)
        placeholder_facts = tuple(
            AnswerSection(text=statement, citation_ids=placeholder_groups[index])
            for index, (_, statement, _) in enumerate(fact_candidates)
        )
        placeholder_render = AnswerProposal.render_sections(
            facts=placeholder_facts,
            inferences=(),
            gaps=gap_sections,
            citation_ids=tuple(placeholder_ids),
        )
        citations: list[QueryCitation] = []
        cursor = 0
        for claim_id, statement, evidence_ids in fact_candidates:
            start = placeholder_render.find(statement, cursor)
            if start < 0:
                raise QueryRejected("Structured answer renderer lost a factual section")
            end = start + len(statement)
            cursor = end
            for evidence_id in evidence_ids:
                resolved = corpus.resolve_evidence(evidence_id)
                citation = QueryCitation.create(
                    answer_proposal_id=answer_id,
                    generation_id=corpus.generation.generation_id,
                    generation_sha256=corpus.generation_sha256,
                    answer_char_start=start,
                    answer_char_end=end,
                    claim_id=claim_id,
                    source_revision_id=resolved.revision.source_revision_id,
                    normalization_id=resolved.normalization.normalization_id,
                    segment_id=resolved.segment.segment_id,
                    cited_excerpt_sha256=resolved.segment.excerpt_sha256,
                    source_locator=resolved.source_locator,
                    verification=CitationVerification.VERIFIED,
                    failure_codes=(),
                    verified_at=corpus.generation.created_at,
                )
                citations.append(citation)
        citation_ids = tuple(citation.query_citation_id for citation in citations)
        citation_cursor = 0
        facts_list: list[AnswerSection] = []
        for _claim_id, statement, evidence_ids in fact_candidates:
            group = citation_ids[citation_cursor : citation_cursor + len(evidence_ids)]
            citation_cursor += len(evidence_ids)
            facts_list.append(AnswerSection(text=statement, citation_ids=group))
        facts = tuple(facts_list)
        answer = AnswerProposal.create(
            run_id=run_id,
            generation_id=corpus.generation.generation_id,
            generation_sha256=corpus.generation_sha256,
            query_text=query_text,
            intent=intent,
            facts=facts,
            inferences=(),
            gaps=gap_sections,
            citation_ids=citation_ids,
            producer=producer,
            created_at=corpus.generation.created_at,
        )
        if answer.answer_proposal_id != answer_id:
            raise QueryRejected("Answer identity changed after citation verification")
        statements = {claim_id: statement for claim_id, statement, _ in fact_candidates}
        for citation in citations:
            expected_statement = statements.get(citation.claim_id or "")
            if (
                expected_statement is None
                or answer.rendered_answer[citation.answer_char_start : citation.answer_char_end]
                != expected_statement
            ):
                raise QueryRejected("Verified citation answer span changed during rendering")
        return QueryResult(answer=answer, citations=tuple(citations), hits=hits)

    @staticmethod
    def _verify_hits(corpus: ActiveCorpus, hits: tuple[SearchHit, ...]) -> None:
        for hit in hits:
            if hit.kind not in {"claim", "entity"}:
                raise StaleIndexError("Search returned an unexpected record kind")
            record = corpus.records.get(f"{hit.kind}:{hit.logical_id}")
            if record is None or hit.object_sha256 != record.object_sha256:
                raise StaleIndexError("Search hit no longer matches its canonical record")

    def _validate_query(self, query_text: str, limit: int) -> None:
        if not 1 <= limit <= 20:
            raise QueryRejected("Query limits require 1..20 results")
        try:
            literal_search_tokens(query_text)
        except SearchQueryError as error:
            raise QueryRejected("Query exceeds safety limits") from error
        decision = self.scanner.scan(query_text.encode("utf-8"), path=None)
        if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
            raise QueryRejected("Query rejected by sensitivity policy")

    @staticmethod
    def _generation_ref(corpus: ActiveCorpus) -> Any:
        from raytsystem.contracts import RecordRef

        return RecordRef(
            kind="generation",
            id=corpus.generation.generation_id,
            object_sha256=corpus.generation_sha256,
        )

    @classmethod
    def _producer(cls) -> ProducerRef:
        return ProducerRef(
            kind=ProducerKind.KERNEL,
            component=ComponentRef(
                name="raytsystem_query",
                version=cls.version,
                config_sha256=sha256_hex(
                    canonical_json_bytes(
                        {
                            "backend": "fts5",
                            "facts": "active_supported_claims_only",
                            "renderer": "structured_sections_v1",
                        }
                    )
                ),
            ),
        )
