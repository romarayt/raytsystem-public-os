from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx  # type: ignore[import-untyped]

from raytsystem.contracts import (
    Claim,
    EntityObject,
    LedgerGeneration,
    PromotionEvent,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.corpus import ActiveCorpus
from raytsystem.derived import assert_safe_replace_target
from raytsystem.io import UnsafeWritePath, ensure_safe_directory, write_bytes_atomic
from raytsystem.rendering import escape_untrusted_markdown
from raytsystem.search import FTS5SearchAdapter, SearchError, SearchUnavailable
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import IntegrityError, read_current_generation


class ProjectionError(IntegrityError):
    """A derived projection failed its generation, path or sensitivity gate."""


@dataclass(frozen=True)
class ProjectionResult:
    generation_id: str
    generation_sha256: str
    projection_input_sha256: str
    projection_sha256: str
    logical_index_sha256: str
    document_count: int
    graph_nodes: int
    graph_edges: int


class ProjectionService:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def rebuild(self) -> ProjectionResult:
        try:
            return self._rebuild()
        except ProjectionError:
            raise
        except (OSError, PathPolicyError, SearchError, UnsafeWritePath) as error:
            raise ProjectionError("Derived projection rebuild failed closed") from error

    def _rebuild(self) -> ProjectionResult:
        corpus = ActiveCorpus.load(self.root)
        index = FTS5SearchAdapter(self.root).rebuild(corpus)
        files, graph_nodes, graph_edges = self._render(corpus)
        if read_current_generation(self.root) != corpus.generation.generation_id:
            raise ProjectionError("ledger/CURRENT changed during projection rendering")
        file_hashes: dict[str, str] = {}
        for relative, data in sorted(files.items()):
            self._assert_safe_output(relative, data)
            target = self.root / relative
            assert_safe_replace_target(target)
            write_bytes_atomic(target, data)
            file_hashes[relative] = sha256_hex(data)
        materialized_marker = self.root / "knowledge" / ".materialized-generation"
        assert_safe_replace_target(materialized_marker)
        write_bytes_atomic(
            materialized_marker,
            f"{corpus.generation.generation_id}\n".encode("ascii"),
        )
        marker_material: dict[str, Any] = {
            "schema_version": "1.0.0",
            "projector_version": self.version,
            "generation_id": corpus.generation.generation_id,
            "generation_sha256": corpus.generation_sha256,
            "projection_input_sha256": corpus.projection_input_sha256,
            "logical_index_sha256": index.logical_index_sha256,
            "files": file_hashes,
            "created_at": corpus.generation.created_at,
        }
        projection_sha256 = sha256_hex(canonical_json_bytes(marker_material))
        marker = {**marker_material, "projection_sha256": projection_sha256}
        marker_bytes = canonical_json_bytes(marker) + b"\n"
        self._assert_safe_output("knowledge/.projection.json", marker_bytes)
        marker_path = self.root / "knowledge" / ".projection.json"
        assert_safe_replace_target(marker_path)
        if read_current_generation(self.root) != corpus.generation.generation_id:
            raise ProjectionError("ledger/CURRENT changed before projection marker install")
        self._prune_pages("knowledge/claims", {f"{claim_id}.md" for claim_id in corpus.claims})
        self._prune_pages("knowledge/sources", {f"{source_id}.md" for source_id in corpus.sources})
        self._prune_pages(
            "knowledge/entities", {f"{entity_id}.md" for entity_id in corpus.entities}
        )
        write_bytes_atomic(marker_path, marker_bytes)
        return ProjectionResult(
            generation_id=corpus.generation.generation_id,
            generation_sha256=corpus.generation_sha256,
            projection_input_sha256=corpus.projection_input_sha256,
            projection_sha256=projection_sha256,
            logical_index_sha256=index.logical_index_sha256,
            document_count=index.document_count,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
        )

    def is_current(self, corpus: ActiveCorpus | None = None) -> bool:
        snapshot = corpus or ActiveCorpus.load(self.root)
        try:
            marker_bytes = read_regular_file(
                self.root,
                "knowledge/.projection.json",
                max_bytes=4 * 1024 * 1024,
            ).data
            marker = json.loads(marker_bytes)
            if not isinstance(marker, dict) or marker_bytes != canonical_json_bytes(marker) + b"\n":
                return False
        except (json.JSONDecodeError, OSError, PathPolicyError, ValueError):
            return False
        material = dict(marker)
        recorded_projection_sha256 = material.pop("projection_sha256", None)
        if (
            recorded_projection_sha256 != sha256_hex(canonical_json_bytes(material))
            or marker.get("generation_id") != snapshot.generation.generation_id
            or marker.get("generation_sha256") != snapshot.generation_sha256
            or marker.get("projection_input_sha256") != snapshot.projection_input_sha256
        ):
            return False
        files = marker.get("files")
        if not isinstance(files, dict):
            return False
        for relative, expected in files.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                return False
            try:
                data = read_regular_file(self.root, relative, max_bytes=25 * 1024 * 1024).data
            except (OSError, PathPolicyError):
                return False
            if sha256_hex(data) != expected:
                return False
        try:
            adapter = FTS5SearchAdapter(self.root)
            metadata = adapter.metadata()
            logical_sha256 = adapter.logical_fingerprint()
        except SearchUnavailable:
            return False
        return (
            metadata.get("generation_id") == snapshot.generation.generation_id
            and metadata.get("generation_sha256") == snapshot.generation_sha256
            and metadata.get("projection_input_sha256") == snapshot.projection_input_sha256
            and metadata.get("logical_index_sha256") == marker.get("logical_index_sha256")
            and logical_sha256 == marker.get("logical_index_sha256")
        )

    def _render(self, corpus: ActiveCorpus) -> tuple[dict[str, bytes], int, int]:
        files: dict[str, bytes] = {}
        generation_id = corpus.generation.generation_id
        for claim_id, claim in sorted(corpus.claims.items()):
            files[f"knowledge/claims/{claim_id}.md"] = self._claim_page(generation_id, claim)
        for source_id, source in sorted(corpus.sources.items()):
            revisions = sorted(
                revision.source_revision_id
                for revision in corpus.revisions.values()
                if revision.source_id == source_id
            )
            files[f"knowledge/sources/{source_id}.md"] = (
                "---\n"
                "generated: true\n"
                f"generation_id: {generation_id}\n"
                f"source_id: {source_id}\n"
                "---\n\n"
                f"# {escape_untrusted_markdown(source.display_name or source_id)}\n\n"
                f"- Type: `{source.source_type}`\n"
                f"- Revisions: `{', '.join(revisions)}`\n"
            ).encode()
        for entity_id, entity in sorted(corpus.entities.items()):
            aliases = ", ".join(escape_untrusted_markdown(alias.value) for alias in entity.aliases)
            files[f"knowledge/entities/{entity_id}.md"] = (
                "---\n"
                "generated: true\n"
                f"generation_id: {generation_id}\n"
                f"entity_id: {entity_id}\n"
                "---\n\n"
                f"# {escape_untrusted_markdown(entity.canonical_label)}\n\n"
                f"- Type: `{entity.entity_type}`\n"
                f"- Status: `{entity.lifecycle_status.value}`\n"
                f"- Aliases: {aliases or '_none_'}\n"
            ).encode()
        files["knowledge/index.md"] = self._index_page(corpus)
        files["knowledge/hot.md"] = self._hot_page(corpus)
        graph_bytes, graph_nodes, graph_edges = self._graph(corpus)
        files["knowledge/graph.json"] = graph_bytes
        return files, graph_nodes, graph_edges

    @staticmethod
    def _claim_page(generation_id: str, claim: Claim) -> bytes:
        return (
            "---\n"
            "generated: true\n"
            f"generation_id: {generation_id}\n"
            f"claim_id: {claim.claim_id}\n"
            "---\n\n"
            f"# {escape_untrusted_markdown(claim.statement)}\n\n"
            f"- Status: `{claim.status.value}`\n"
            f"- Evidence: `{', '.join(claim.evidence_ids)}`\n"
        ).encode()

    @staticmethod
    def _index_page(corpus: ActiveCorpus) -> bytes:
        lines = [
            "---",
            "generated: true",
            f"generation_id: {corpus.generation.generation_id}",
            "---",
            "",
            "# Knowledge index",
            "",
            "## Claims",
        ]
        if corpus.claims:
            lines.extend(
                f"- [{escape_untrusted_markdown(claim.statement)}](claims/{claim_id}.md)"
                for claim_id, claim in sorted(corpus.claims.items())
            )
        else:
            lines.append("_None._")
        lines.extend(("", "## Entities"))
        if corpus.entities:
            lines.extend(
                f"- [{escape_untrusted_markdown(entity.canonical_label)}](entities/{entity_id}.md)"
                for entity_id, entity in sorted(corpus.entities.items())
            )
        else:
            lines.append("_None._")
        lines.extend(("", "## Sources"))
        if corpus.sources:
            lines.extend(
                f"- [{escape_untrusted_markdown(source.display_name or source_id)}]"
                f"(sources/{source_id}.md)"
                for source_id, source in sorted(corpus.sources.items())
            )
        else:
            lines.append("_None._")
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")

    def _hot_page(self, corpus: ActiveCorpus) -> bytes:
        max_items = self._hot_max_items()
        open_claims = [
            claim for claim in corpus.claims.values() if claim.status.value in {"disputed", "stale"}
        ]
        events = self._canonical_events(corpus)[:max_items]
        lines = [
            "---",
            "generated: true",
            f"generation_id: {corpus.generation.generation_id}",
            "---",
            "",
            "# Hot memory",
            "",
            f"- Active generation: `{corpus.generation.generation_id}`",
            f"- Active claims: `{len(corpus.claims)}`",
            f"- Open disputed/stale claims: `{len(open_claims)}`",
            "",
            "## Recent promotions",
        ]
        if events:
            lines.extend(
                f"- `{event['committed_at']}` — `{event['event_id']}` → "
                f"`{event['new_generation_id']}`"
                for event in events
            )
        else:
            lines.append("_None._")
        lines.extend(("", "## Open knowledge issues"))
        if open_claims:
            lines.extend(
                f"- `{claim.status.value}` — {escape_untrusted_markdown(claim.statement)}"
                for claim in sorted(open_claims, key=lambda value: value.claim_id)[:max_items]
            )
        else:
            lines.append("_None._")
        rendered = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        if len(rendered) > 128 * 1024:
            raise ProjectionError("Generated hot.md exceeds its byte budget")
        return rendered

    def _canonical_events(self, corpus: ActiveCorpus) -> list[dict[str, Any]]:
        ancestors: dict[str, LedgerGeneration] = {}
        cursor = corpus.generation
        while cursor.generation_id not in ancestors:
            ancestors[cursor.generation_id] = cursor
            parent_id = cursor.parent_generation_id
            if parent_id is None:
                break
            relative = f"ledger/generations/{parent_id}.json"
            data = read_regular_file(self.root, relative, max_bytes=4 * 1024 * 1024).data
            try:
                parent = LedgerGeneration.model_validate_json(data)
            except ValueError as error:
                raise ProjectionError("Generation ancestry is invalid") from error
            if (
                parent.generation_id != parent_id
                or not parent.verify_id()
                or (parent_id != "genesis" and data != canonical_json_bytes(parent))
            ):
                raise ProjectionError("Generation ancestry failed canonical validation")
            cursor = parent
        else:
            raise ProjectionError("Generation ancestry contains a cycle")
        expected_events = {
            generation.promotion_event_id: generation
            for generation in ancestors.values()
            if generation.promotion_event_id is not None
        }
        events: list[dict[str, Any]] = []
        seen_expected: set[str] = set()
        root = self.root / "ops" / "events"
        if not root.exists():
            if expected_events:
                raise ProjectionError("Canonical promotion events are missing")
            return []
        for path in sorted(root.glob("evt_*.json")):
            relative = path.relative_to(self.root).as_posix()
            data = read_regular_file(self.root, relative, max_bytes=4 * 1024 * 1024).data
            try:
                event = PromotionEvent.model_validate_json(data)
            except ValueError as error:
                raise ProjectionError("Promotion event is invalid") from error
            if path.name != f"{event.event_id}.json" or data != canonical_json_bytes(event):
                raise ProjectionError("Promotion event failed canonical validation")
            generation = expected_events.get(event.event_id)
            if generation is None:
                continue
            if (
                event.new_generation_id != generation.generation_id
                or event.parent_generation_id != generation.parent_generation_id
                or event.txn_id != generation.promotion_txn_id
            ):
                raise ProjectionError("Promotion event disagrees with generation ancestry")
            seen_expected.add(event.event_id)
            events.append(json.loads(data))
        if seen_expected != set(expected_events):
            raise ProjectionError("Generation ancestry has unresolved promotion events")
        events.sort(
            key=lambda value: (str(value.get("committed_at", "")), str(value["event_id"])),
            reverse=True,
        )
        return events

    @staticmethod
    def _graph(corpus: ActiveCorpus) -> tuple[bytes, int, int]:
        graph = nx.MultiDiGraph(generation_id=corpus.generation.generation_id)
        for claim_id, claim in sorted(corpus.claims.items()):
            if claim.status.value in {"retracted", "superseded"}:
                continue
            graph.add_node(claim_id, kind="claim", label=claim.statement, status=claim.status.value)
            for evidence_id in claim.evidence_ids:
                item = corpus.resolve_evidence(evidence_id)
                graph.add_node(evidence_id, kind="segment", label=evidence_id)
                graph.add_node(
                    item.revision.source_revision_id,
                    kind="source_revision",
                    label=item.revision.source_revision_id,
                )
                graph.add_node(
                    item.source.source_id,
                    kind="source",
                    label=item.source.display_name or item.source.source_id,
                )
                graph.add_edge(claim_id, evidence_id, kind="supported_by")
                graph.add_edge(evidence_id, item.revision.source_revision_id, kind="from_revision")
                graph.add_edge(
                    item.revision.source_revision_id, item.source.source_id, kind="revision_of"
                )
            for target in claim.supersedes:
                graph.add_edge(claim_id, target, kind="supersedes")
            for target in claim.contradicts:
                graph.add_edge(claim_id, target, kind="contradicts")
        for entity_id, entity in sorted(corpus.entities.items()):
            if entity.lifecycle_status.value in {"retracted", "superseded"}:
                continue
            graph.add_node(entity_id, kind="entity", label=entity.canonical_label)
        for relation_id, relation in sorted(corpus.relations.items()):
            if relation.lifecycle_status.value in {"retracted", "superseded"}:
                continue
            graph.add_node(relation_id, kind="relation", label=relation.predicate)
            graph.add_edge(relation.subject_entity_id, relation_id, kind="relation_subject")
            if isinstance(relation.object, EntityObject):
                graph.add_edge(relation_id, relation.object.entity_id, kind="relation_object")
        nodes = [
            {"id": node_id, **{key: value for key, value in sorted(attributes.items())}}
            for node_id, attributes in graph.nodes(data=True)
        ]
        nodes.sort(key=lambda value: str(value["id"]))
        edges = [
            {
                "source": source,
                "target": target,
                "key": str(key),
                **dict(sorted(attributes.items())),
            }
            for source, target, key, attributes in graph.edges(keys=True, data=True)
        ]
        edges.sort(
            key=lambda value: (
                str(value["source"]),
                str(value["target"]),
                str(value["kind"]),
                str(value["key"]),
            )
        )
        payload = {
            "schema_version": "1.0.0",
            "generation_id": corpus.generation.generation_id,
            "directed": True,
            "multigraph": True,
            "nodes": nodes,
            "edges": edges,
        }
        return canonical_json_bytes(payload) + b"\n", len(nodes), len(edges)

    def _assert_safe_output(self, relative: str, data: bytes) -> None:
        decision = self.scanner.scan(data, path=relative)
        if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
            raise ProjectionError("Generated projection failed the sensitivity gate")

    def _hot_max_items(self) -> int:
        try:
            data = read_regular_file(
                self.root,
                "config/raytsystem.toml",
                max_bytes=1024 * 1024,
            ).data
            config = tomllib.loads(data.decode("utf-8"))
        except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ProjectionError("raytsystem config is invalid") from error
        value = int(config.get("limits", {}).get("hot_page_max_items", 50))
        if not 1 <= value <= 500:
            raise ProjectionError("hot_page_max_items is outside 1..500")
        return value

    def _prune_pages(self, relative_directory: str, keep: set[str]) -> None:
        directory = self.root / relative_directory
        ensure_safe_directory(directory)
        for path in sorted(directory.glob("*.md")):
            if path.name in keep:
                continue
            if path.is_symlink():
                raise ProjectionError("Generated page directory contains a symlink")
            metadata = os.lstat(path)
            if metadata.st_nlink != 1:
                raise ProjectionError("Generated page has an unsafe hardlink")
            relative = path.relative_to(self.root).as_posix()
            prefix = read_regular_file(
                self.root,
                relative,
                max_bytes=4 * 1024 * 1024,
            ).data[:256]
            if b"generated: true" in prefix:
                path.unlink()
