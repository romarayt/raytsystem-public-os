from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from raytsystem.contracts import (
    Claim,
    Entity,
    LedgerGeneration,
    Normalization,
    Relation,
    Segment,
    Source,
    SourceRevision,
    TextLocator,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.storage import IntegrityError, read_current_generation


class CorpusIntegrityError(IntegrityError):
    """Stable machine-readable failure while resolving canonical evidence."""

    def __init__(self, code: str, message: str, *, subject: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.subject = subject


@dataclass(frozen=True)
class CanonicalRecord:
    kind: str
    logical_id: str
    object_sha256: str
    value: Claim | Entity | Relation


@dataclass(frozen=True)
class ResolvedEvidence:
    segment: Segment
    normalization: Normalization
    revision: SourceRevision
    source: Source
    excerpt: str
    source_locator: str


@dataclass(frozen=True)
class ActiveCorpus:
    root: Path
    generation: LedgerGeneration
    generation_sha256: str
    records: dict[str, CanonicalRecord]
    claims: dict[str, Claim]
    entities: dict[str, Entity]
    relations: dict[str, Relation]
    sources: dict[str, Source]
    revisions: dict[str, SourceRevision]
    evidence: dict[str, ResolvedEvidence]
    run_manifests: tuple[dict[str, Any], ...]
    projection_input_sha256: str

    @classmethod
    def load(cls, root: Path, *, verify_evidence: bool = True) -> ActiveCorpus:
        resolved_root = root.resolve()
        generation_id = read_current_generation(resolved_root)
        generation_relative = f"ledger/generations/{generation_id}.json"
        generation_bytes = _read(resolved_root, generation_relative)
        generation = _model(
            generation_bytes,
            LedgerGeneration,
            "generation_invalid",
            generation_relative,
        )
        if not generation.verify_id() or generation.generation_id != generation_id:
            raise CorpusIntegrityError(
                "generation_id_mismatch",
                "Active generation identity does not match ledger/CURRENT",
                subject=generation_relative,
            )
        records, claims, entities, relations = _load_records(resolved_root, generation)
        sources = _load_content_addressed_models(
            resolved_root,
            "_raw/sources/sha256",
            Source,
            "source_id",
        )
        revisions = _load_content_addressed_models(
            resolved_root,
            "_raw/revisions/sha256",
            SourceRevision,
            "source_revision_id",
        )
        typed_sources = {key: value for key, value in sources.items() if isinstance(value, Source)}
        typed_revisions = {
            key: value for key, value in revisions.items() if isinstance(value, SourceRevision)
        }
        all_evidence = _load_evidence(
            resolved_root,
            typed_sources,
            typed_revisions,
            verify_raw=verify_evidence,
        )
        reachable_evidence_ids = {
            evidence_id for claim in claims.values() for evidence_id in claim.evidence_ids
        }
        if verify_evidence:
            for claim in claims.values():
                if claim.status.value in {"supported", "confirmed"} and not claim.evidence_ids:
                    raise CorpusIntegrityError(
                        "claim_missing_evidence",
                        "Factual claim has no evidence",
                        subject=claim.claim_id,
                    )
                for evidence_id in claim.evidence_ids:
                    if evidence_id not in all_evidence:
                        raise CorpusIntegrityError(
                            "claim_unresolved_evidence",
                            "Claim evidence does not resolve",
                            subject=claim.claim_id,
                        )
        evidence = {
            evidence_id: all_evidence[evidence_id]
            for evidence_id in sorted(reachable_evidence_ids)
            if evidence_id in all_evidence
        }
        reachable_revision_ids = {item.revision.source_revision_id for item in evidence.values()}
        typed_revisions = {
            revision_id: revision
            for revision_id, revision in typed_revisions.items()
            if revision_id in reachable_revision_ids
        }
        reachable_source_ids = {revision.source_id for revision in typed_revisions.values()}
        typed_sources = {
            source_id: source
            for source_id, source in typed_sources.items()
            if source_id in reachable_source_ids
        }
        run_manifests, run_hashes = _load_runs(resolved_root)
        input_material = {
            "generation_id": generation.generation_id,
            "generation_sha256": sha256_hex(generation_bytes),
            "records": {key: record.object_sha256 for key, record in sorted(records.items())},
            "sources": {
                key: sha256_hex(canonical_json_bytes(value))
                for key, value in sorted(typed_sources.items())
            },
            "revisions": {
                key: sha256_hex(canonical_json_bytes(value))
                for key, value in sorted(typed_revisions.items())
            },
            "evidence": {
                key: {
                    "normalization_id": item.normalization.normalization_id,
                    "excerpt_sha256": item.segment.excerpt_sha256,
                    "source_revision_id": item.segment.source_revision_id,
                }
                for key, item in sorted(evidence.items())
            },
            "runs": run_hashes,
        }
        return cls(
            root=resolved_root,
            generation=generation,
            generation_sha256=sha256_hex(generation_bytes),
            records=records,
            claims=claims,
            entities=entities,
            relations=relations,
            sources=typed_sources,
            revisions=typed_revisions,
            evidence=evidence,
            run_manifests=run_manifests,
            projection_input_sha256=sha256_hex(canonical_json_bytes(input_material)),
        )

    def resolve_evidence(self, evidence_id: str) -> ResolvedEvidence:
        try:
            return self.evidence[evidence_id]
        except KeyError as error:
            raise CorpusIntegrityError(
                "claim_unresolved_evidence",
                "Evidence ID does not resolve in the active corpus",
                subject=evidence_id,
            ) from error


def _read(root: Path, relative: str, *, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    try:
        return read_regular_file(root, relative, max_bytes=max_bytes).data
    except (OSError, PathPolicyError) as error:
        raise CorpusIntegrityError(
            "artifact_missing_or_unsafe",
            "Canonical artifact is missing or unsafe",
            subject=relative,
        ) from error


def _model(
    data: bytes,
    model: type[BaseModel],
    code: str,
    subject: str,
) -> Any:
    try:
        value = model.model_validate(json.loads(data))
    except (json.JSONDecodeError, ValueError) as error:
        raise CorpusIntegrityError(code, "Canonical model is invalid", subject=subject) from error
    if data != canonical_json_bytes(value):
        raise CorpusIntegrityError(
            "canonical_json_mismatch",
            "Canonical model bytes changed",
            subject=subject,
        )
    return value


def _load_records(
    root: Path,
    generation: LedgerGeneration,
) -> tuple[
    dict[str, CanonicalRecord],
    dict[str, Claim],
    dict[str, Entity],
    dict[str, Relation],
]:
    records: dict[str, CanonicalRecord] = {}
    claims: dict[str, Claim] = {}
    entities: dict[str, Entity] = {}
    relations: dict[str, Relation] = {}
    model_by_kind: dict[str, type[Claim] | type[Entity] | type[Relation]] = {
        "claim": Claim,
        "entity": Entity,
        "relation": Relation,
    }
    for key, entry in sorted(generation.records.items()):
        if entry.tombstone:
            continue
        if key != f"{entry.kind}:{entry.logical_id}" or entry.kind not in model_by_kind:
            raise CorpusIntegrityError(
                "generation_record_invalid",
                "Generation record key or kind is invalid",
                subject=key,
            )
        relative = (
            PurePosixPath("ledger")
            / "objects"
            / "sha256"
            / entry.object_sha256[:2]
            / f"{entry.object_sha256}.json"
        ).as_posix()
        data = _read(root, relative)
        if sha256_hex(data) != entry.object_sha256:
            raise CorpusIntegrityError(
                "ledger_object_hash_mismatch",
                "Active ledger object hash changed",
                subject=entry.logical_id,
            )
        value = _model(data, model_by_kind[entry.kind], "ledger_object_invalid", entry.logical_id)
        logical_id = getattr(value, f"{entry.kind}_id")
        if logical_id != entry.logical_id:
            raise CorpusIntegrityError(
                "ledger_logical_id_mismatch",
                "Ledger object logical ID changed",
                subject=entry.logical_id,
            )
        record = CanonicalRecord(entry.kind, entry.logical_id, entry.object_sha256, value)
        records[key] = record
        if isinstance(value, Claim):
            claims[value.claim_id] = value
        elif isinstance(value, Entity):
            entities[value.entity_id] = value
        else:
            relations[value.relation_id] = value
    return records, claims, entities, relations


def _load_content_addressed_models(
    root: Path,
    relative_root: str,
    model: type[Source] | type[SourceRevision],
    id_field: str,
) -> dict[str, Source | SourceRevision]:
    absolute = root / relative_root
    if not absolute.exists():
        return {}
    values: dict[str, Source | SourceRevision] = {}
    for path in sorted(absolute.glob("*/*.json")):
        relative = path.relative_to(root).as_posix()
        data = _read(root, relative, max_bytes=4 * 1024 * 1024)
        if path.stem != sha256_hex(data) or path.parent.name != path.stem[:2]:
            raise CorpusIntegrityError(
                "content_address_mismatch",
                "Content-addressed object path disagrees with its bytes",
                subject=relative,
            )
        value = _model(data, model, "source_model_invalid", relative)
        logical_id = str(getattr(value, id_field))
        if logical_id in values:
            raise CorpusIntegrityError(
                "duplicate_logical_id",
                "Logical source ID has multiple immutable definitions",
                subject=logical_id,
            )
        values[logical_id] = value
    return values


def _load_evidence(
    root: Path,
    sources: dict[str, Source],
    revisions: dict[str, SourceRevision],
    *,
    verify_raw: bool,
) -> dict[str, ResolvedEvidence]:
    evidence: dict[str, ResolvedEvidence] = {}
    normalized = root / "normalized"
    if not normalized.exists():
        return evidence
    for manifest_path in sorted(normalized.glob("*/*/normalization.json")):
        snapshot = manifest_path.parent
        relative_snapshot = snapshot.relative_to(root)
        if len(relative_snapshot.parts) != 3:
            raise CorpusIntegrityError(
                "normalization_path_invalid",
                "Normalization snapshot path is malformed",
                subject=relative_snapshot.as_posix(),
            )
        source_revision_id = relative_snapshot.parts[1]
        normalization_id = relative_snapshot.parts[2]
        manifest_relative = manifest_path.relative_to(root).as_posix()
        normalization_bytes = _read(root, manifest_relative, max_bytes=1024 * 1024)
        normalization = _model(
            normalization_bytes,
            Normalization,
            "normalization_invalid",
            normalization_id,
        )
        recreated = Normalization.create(
            source_revision_id=normalization.source_revision_id,
            adapter=normalization.extractor_ref.name,
            parser_version=normalization.extractor_ref.version,
            config_sha256=normalization.config_sha256,
            document_sha256=normalization.document_sha256,
            created_at=normalization.created_at,
        )
        expected_document = (relative_snapshot / "document.txt").as_posix()
        expected_segments = (relative_snapshot / "segments.jsonl").as_posix()
        expected_excerpts = (relative_snapshot / "excerpts.jsonl").as_posix()
        excerpt_binding = normalization.extensions.get(
            "raytsystem.excerpts"
        ) or normalization.extensions.get("agentos.excerpts")
        if (
            normalization.source_revision_id != source_revision_id
            or normalization.normalization_id != normalization_id
            or recreated.normalization_id != normalization_id
            or normalization.document_path != expected_document
            or normalization.segments_path != expected_segments
            or not isinstance(excerpt_binding, dict)
            or excerpt_binding.get("path") != expected_excerpts
        ):
            raise CorpusIntegrityError(
                "normalization_identity_mismatch",
                "Normalization identity or artifact paths changed",
                subject=normalization_id,
            )
        document = _read(root, expected_document)
        segments_bytes = _read(root, expected_segments)
        excerpts_bytes = _read(root, expected_excerpts)
        if (
            sha256_hex(document) != normalization.document_sha256
            or sha256_hex(segments_bytes) != normalization.segments_sha256
            or excerpt_binding.get("sha256") != sha256_hex(excerpts_bytes)
        ):
            raise CorpusIntegrityError(
                "normalization_hash_mismatch",
                "Normalization artifact hash changed",
                subject=normalization_id,
            )
        try:
            segments = [
                Segment.model_validate(json.loads(line)) for line in segments_bytes.splitlines()
            ]
            excerpt_rows = [json.loads(line) for line in excerpts_bytes.splitlines()]
        except (json.JSONDecodeError, ValueError) as error:
            raise CorpusIntegrityError(
                "segment_record_invalid",
                "Normalization segment records are invalid",
                subject=normalization_id,
            ) from error
        excerpts: dict[str, str] = {}
        for row in excerpt_rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"segment_id", "excerpt"}
                or not isinstance(row["segment_id"], str)
                or not isinstance(row["excerpt"], str)
                or not row["excerpt"]
                or row["segment_id"] in excerpts
            ):
                raise CorpusIntegrityError(
                    "excerpt_record_invalid",
                    "Normalization excerpt record is invalid",
                    subject=normalization_id,
                )
            excerpts[row["segment_id"]] = row["excerpt"]
        if len(segments) != normalization.segment_count or set(excerpts) != {
            segment.segment_id for segment in segments
        }:
            raise CorpusIntegrityError(
                "segment_set_mismatch",
                "Normalization segment/excerpt set does not close",
                subject=normalization_id,
            )
        revision = revisions.get(source_revision_id)
        if revision is None:
            raise CorpusIntegrityError(
                "source_revision_missing",
                "Evidence source revision is missing",
                subject=source_revision_id,
            )
        source = sources.get(revision.source_id)
        if source is None:
            raise CorpusIntegrityError(
                "source_missing",
                "Evidence logical source is missing",
                subject=revision.source_id,
            )
        if verify_raw:
            raw = _read(root, revision.raw_path)
            if (
                sha256_hex(raw) != revision.content_sha256
                or PurePosixPath(revision.raw_path).name != revision.content_sha256
            ):
                raise CorpusIntegrityError(
                    "raw_hash_mismatch",
                    "Exact raw evidence hash changed",
                    subject=source_revision_id,
                )
        lines = document.decode("utf-8").splitlines()
        for ordinal, segment in enumerate(segments):
            recreated_segment = Segment.create(
                source_revision_id=segment.source_revision_id,
                normalization_id=segment.normalization_id,
                ordinal=segment.ordinal,
                locator=segment.locator,
                excerpt_sha256=segment.excerpt_sha256,
                language=segment.language,
                modality=segment.modality,
            )
            excerpt = excerpts[segment.segment_id]
            if (
                segment.segment_id != recreated_segment.segment_id
                or segment.source_revision_id != source_revision_id
                or segment.normalization_id != normalization_id
                or segment.ordinal != ordinal
                or sha256_hex(excerpt.encode("utf-8")) != segment.excerpt_sha256
            ):
                raise CorpusIntegrityError(
                    "segment_identity_mismatch",
                    "Evidence segment identity or excerpt hash changed",
                    subject=segment.segment_id,
                )
            if isinstance(segment.locator, TextLocator):
                start = segment.locator.line_start
                if start is None or start > len(lines) or lines[start - 1] != excerpt:
                    raise CorpusIntegrityError(
                        "locator_mismatch",
                        "Evidence locator no longer resolves to its excerpt",
                        subject=segment.segment_id,
                    )
            if segment.segment_id in evidence:
                raise CorpusIntegrityError(
                    "duplicate_segment_id",
                    "Evidence segment ID resolves more than once",
                    subject=segment.segment_id,
                )
            locator_json = canonical_json_bytes(segment.locator).decode("utf-8")
            evidence[segment.segment_id] = ResolvedEvidence(
                segment=segment,
                normalization=normalization,
                revision=revision,
                source=source,
                excerpt=excerpt,
                source_locator=f"{expected_document}#{locator_json}",
            )
    return evidence


def _load_runs(root: Path) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    runs_root = root / "ops" / "runs"
    if not runs_root.exists():
        return (), {}
    manifests: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for path in sorted(runs_root.glob("*/manifest.json")):
        if not path.parent.name.startswith("run_"):
            continue
        relative = path.relative_to(root).as_posix()
        data = _read(root, relative, max_bytes=4 * 1024 * 1024)
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as error:
            raise CorpusIntegrityError(
                "run_manifest_invalid",
                "Run manifest is invalid JSON",
                subject=relative,
            ) from error
        if not isinstance(payload, dict) or payload.get("run_id") != path.parent.name:
            raise CorpusIntegrityError(
                "run_manifest_identity_mismatch",
                "Run manifest identity disagrees with its path",
                subject=relative,
            )
        manifests.append(payload)
        stable_projection = {
            "run_id": payload.get("run_id"),
            "operation_type": payload.get("operation_type"),
            "operation_key": payload.get("operation_key"),
            "input_path": payload.get("input_path"),
            "created_at": payload.get("created_at"),
        }
        hashes[path.parent.name] = sha256_hex(canonical_json_bytes(stable_projection))
    return tuple(manifests), hashes
