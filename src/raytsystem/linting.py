from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from raytsystem.contracts import SourceRevision, canonical_json_bytes, sha256_hex
from raytsystem.corpus import ActiveCorpus, CorpusIntegrityError
from raytsystem.projections import ProjectionService
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import IntegrityError, read_current_generation

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_FRONTMATTER_ID = re.compile(r"^([a-z][a-z0-9_]*_id):\s*([^\s]+)\s*$")
_SEVERITY_ORDER = {"critical": 0, "high": 1, "error": 2, "warning": 3, "info": 4}


@dataclass(frozen=True)
class LintFinding:
    code: str
    severity: str
    subject: str
    message: str

    def sort_key(self) -> tuple[int, str, str, str]:
        return (_SEVERITY_ORDER[self.severity], self.code, self.subject, self.message)


@dataclass(frozen=True)
class LintReport:
    generation_id: str
    semantic: bool
    findings: tuple[LintFinding, ...]
    report_sha256: str

    @property
    def ok(self) -> bool:
        return not any(
            finding.severity in {"critical", "high", "error"} for finding in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "semantic": self.semantic,
            "ok": self.ok,
            "findings": [asdict(finding) for finding in self.findings],
            "report_sha256": self.report_sha256,
        }


class LintService:
    version = "1.1.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def run(self, *, semantic: bool = False) -> LintReport:
        findings: list[LintFinding] = []
        try:
            generation_id = read_current_generation(self.root)
        except IntegrityError:
            generation_id = "unknown"
            findings.append(
                self._finding(
                    "generation_pointer_invalid",
                    "critical",
                    "ledger/CURRENT",
                    "Canonical generation pointer is missing or invalid",
                )
            )
        corpus: ActiveCorpus | None = None
        try:
            corpus = ActiveCorpus.load(self.root)
            generation_id = corpus.generation.generation_id
        except CorpusIntegrityError as error:
            findings.append(
                self._finding(
                    error.code,
                    "error",
                    error.subject or "canonical_corpus",
                    "Canonical corpus or citation closure failed deterministic validation",
                )
            )
        findings.extend(self._raw_findings())
        findings.extend(self._markdown_findings())
        findings.extend(self._secret_findings())
        findings.extend(self._operation_findings())
        if corpus is None:
            findings.append(
                self._finding(
                    "projection_stale",
                    "error",
                    "knowledge/.projection.json",
                    "Derived projections cannot match an invalid canonical corpus",
                )
            )
        else:
            try:
                current = ProjectionService(self.root, scanner=self.scanner).is_current(corpus)
            except (OSError, IntegrityError, CorpusIntegrityError):
                current = False
            if not current:
                findings.append(
                    self._finding(
                        "projection_stale",
                        "error",
                        "knowledge/.projection.json",
                        "Derived index, graph or generated memory is stale or modified",
                    )
                )
            findings.extend(self._alias_findings(corpus))
            if semantic:
                findings.extend(self._semantic_findings(corpus))
        unique = {
            (finding.code, finding.severity, finding.subject, finding.message): finding
            for finding in findings
        }
        ordered = tuple(sorted(unique.values(), key=lambda finding: finding.sort_key()))
        material = {
            "generation_id": generation_id,
            "semantic": semantic,
            "linter_version": self.version,
            "findings": [asdict(finding) for finding in ordered],
        }
        return LintReport(
            generation_id=generation_id,
            semantic=semantic,
            findings=ordered,
            report_sha256=sha256_hex(canonical_json_bytes(material)),
        )

    def _raw_findings(self) -> list[LintFinding]:
        findings: list[LintFinding] = []
        root = self.root / "_raw" / "revisions" / "sha256"
        if not root.exists():
            return findings
        seen: set[str] = set()
        for path in sorted(root.glob("*/*.json")):
            relative = path.relative_to(self.root).as_posix()
            try:
                data = read_regular_file(self.root, relative, max_bytes=4 * 1024 * 1024).data
                revision = SourceRevision.model_validate(json.loads(data))
                if data != canonical_json_bytes(revision) or path.stem != sha256_hex(data):
                    raise ValueError
            except (OSError, PathPolicyError, ValueError, json.JSONDecodeError):
                findings.append(
                    self._finding(
                        "source_revision_invalid",
                        "error",
                        relative,
                        "Source revision object is invalid or changed",
                    )
                )
                continue
            if revision.source_revision_id in seen:
                findings.append(
                    self._finding(
                        "duplicate_source_revision_id",
                        "error",
                        revision.source_revision_id,
                        "Source revision ID has multiple definitions",
                    )
                )
            seen.add(revision.source_revision_id)
            try:
                raw = read_regular_file(
                    self.root,
                    revision.raw_path,
                    max_bytes=256 * 1024 * 1024,
                ).data
            except (OSError, PathPolicyError):
                findings.append(
                    self._finding(
                        "raw_missing_or_unsafe",
                        "error",
                        revision.source_revision_id,
                        "Exact raw evidence is missing or unsafe",
                    )
                )
                continue
            if (
                sha256_hex(raw) != revision.content_sha256
                or PurePosixPath(revision.raw_path).name != revision.content_sha256
            ):
                findings.append(
                    self._finding(
                        "raw_hash_mismatch",
                        "critical",
                        revision.source_revision_id,
                        "Exact raw evidence hash changed",
                    )
                )
        return findings

    def _markdown_findings(self) -> list[LintFinding]:
        knowledge = self.root / "knowledge"
        if not knowledge.exists():
            return []
        pages = [path for path in sorted(knowledge.rglob("*.md")) if path.is_file()]
        relative_pages = {path.relative_to(knowledge).as_posix(): path for path in pages}
        page_bytes: dict[str, bytes] = {}
        stem_targets: dict[str, list[str]] = {}
        frontmatter_ids: dict[tuple[str, str], list[str]] = {}
        referenced: set[str] = set()
        findings: list[LintFinding] = []
        for relative in relative_pages:
            stem_targets.setdefault(Path(relative).stem.casefold(), []).append(relative)
        for relative, path in relative_pages.items():
            try:
                data = read_regular_file(
                    self.root,
                    path.relative_to(self.root).as_posix(),
                    max_bytes=4 * 1024 * 1024,
                ).data
                text = data.decode("utf-8")
                page_bytes[relative] = data
            except (OSError, UnicodeDecodeError, PathPolicyError):
                findings.append(
                    self._finding(
                        "knowledge_page_invalid",
                        "error",
                        f"knowledge/{relative}",
                        "Knowledge page is missing, unsafe or invalid UTF-8",
                    )
                )
                continue
            for line in text.splitlines()[:50]:
                match = _FRONTMATTER_ID.fullmatch(line.strip())
                if match and match.group(1) != "generation_id":
                    frontmatter_ids.setdefault((match.group(1), match.group(2)), []).append(
                        relative
                    )
            for target in _WIKILINK.findall(text):
                matches = stem_targets.get(Path(target.strip()).stem.casefold(), [])
                if len(matches) != 1:
                    findings.append(
                        self._finding(
                            "dead_wikilink",
                            "error",
                            f"knowledge/{relative}",
                            "Local wikilink does not resolve to exactly one page",
                        )
                    )
                elif matches[0] == relative:
                    findings.append(
                        self._finding(
                            "self_wikilink",
                            "error",
                            f"knowledge/{relative}",
                            "Local wikilink points back to the same page",
                        )
                    )
                else:
                    referenced.add(matches[0])
            for target in _MARKDOWN_LINK.findall(text):
                clean = target.split("#", 1)[0].strip()
                if not clean or "://" in clean or clean.startswith("mailto:"):
                    continue
                candidate = (PurePosixPath(relative).parent / clean).as_posix()
                if candidate.startswith("../") or candidate not in relative_pages:
                    findings.append(
                        self._finding(
                            "dead_markdown_link",
                            "error",
                            f"knowledge/{relative}",
                            "Local Markdown link escapes or does not resolve",
                        )
                    )
                else:
                    referenced.add(candidate)
        for (field, value), owners in sorted(frontmatter_ids.items()):
            if len(owners) > 1:
                findings.append(
                    self._finding(
                        "duplicate_frontmatter_id",
                        "error",
                        f"{field}:{sha256_hex(value.encode('utf-8'))}",
                        "Frontmatter logical ID is duplicated across knowledge pages",
                    )
                )
        for relative in relative_pages:
            if relative in {"index.md", "hot.md"} or relative in referenced:
                continue
            cached_data = page_bytes.get(relative)
            if cached_data is None:
                continue
            prefix = cached_data[:256]
            if relative.startswith("manual/"):
                findings.append(
                    self._finding(
                        "manual_orphan",
                        "warning",
                        f"knowledge/{relative}",
                        "Manual knowledge page is not referenced by another page",
                    )
                )
            elif b"generated: true" in prefix:
                findings.append(
                    self._finding(
                        "generated_orphan",
                        "error",
                        f"knowledge/{relative}",
                        "Generated knowledge page is not linked from the generated index",
                    )
                )
        return findings

    def _secret_findings(self) -> list[LintFinding]:
        findings: list[LintFinding] = []
        roots = (self.root / "knowledge", self.root / "ops" / "events", self.root / "ops" / "runs")
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    data = read_regular_file(
                        self.root,
                        relative,
                        max_bytes=25 * 1024 * 1024,
                    ).data
                except (OSError, PathPolicyError):
                    continue
                decision = self.scanner.scan(data, path=relative)
                if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
                    findings.append(
                        self._finding(
                            "secret_detected",
                            "critical",
                            self._safe_subject(relative),
                            "Restricted secret pattern detected in a generated or audit artifact",
                        )
                    )
        return findings

    def _operation_findings(self) -> list[LintFinding]:
        runs = self.root / "ops" / "runs"
        if not runs.exists():
            return []
        owners: dict[str, list[dict[str, Any]]] = {}
        findings: list[LintFinding] = []
        for path in sorted(runs.glob("run_*/manifest.json")):
            relative = path.relative_to(self.root).as_posix()
            try:
                data = read_regular_file(
                    self.root,
                    relative,
                    max_bytes=4 * 1024 * 1024,
                ).data
                payload = json.loads(data)
            except (OSError, PathPolicyError, json.JSONDecodeError):
                findings.append(
                    self._finding(
                        "run_manifest_invalid",
                        "error",
                        relative,
                        "Run manifest is missing, unsafe or invalid JSON",
                    )
                )
                continue
            if isinstance(payload, dict) and isinstance(payload.get("operation_key"), str):
                owners.setdefault(str(payload["operation_key"]), []).append(payload)
        for operation_key, manifests in sorted(owners.items()):
            if len(manifests) <= 1:
                continue
            divergent = {
                (str(item.get("generation_id")), bool(item.get("semantic_noop", False)))
                for item in manifests
            }
            if len(divergent) > 1 and not all(item.get("semantic_noop") for item in manifests[1:]):
                findings.append(
                    self._finding(
                        "duplicate_operation_key",
                        "error",
                        sha256_hex(operation_key.encode("utf-8")),
                        "Operation key has divergent durable run manifests",
                    )
                )
        return findings

    def _alias_findings(self, corpus: ActiveCorpus) -> list[LintFinding]:
        aliases: dict[str, set[str]] = {}
        slugs: dict[str, set[str]] = {}
        for entity_id, entity in corpus.entities.items():
            for alias in entity.aliases:
                key = unicodedata.normalize("NFC", alias.value).casefold()
                aliases.setdefault(key, set()).add(entity_id)
            slug = re.sub(r"[^a-z0-9]+", "-", entity.canonical_label.casefold()).strip("-")
            slugs.setdefault(slug, set()).add(entity_id)
        findings: list[LintFinding] = []
        for value, owners in sorted(aliases.items()):
            if len(owners) > 1:
                findings.append(
                    self._finding(
                        "duplicate_alias",
                        "error",
                        sha256_hex(value.encode("utf-8")),
                        "Entity alias resolves to multiple canonical entities",
                    )
                )
        for value, owners in sorted(slugs.items()):
            if value and len(owners) > 1:
                findings.append(
                    self._finding(
                        "slug_collision",
                        "error",
                        sha256_hex(value.encode("utf-8")),
                        "Entity labels collide after slug normalization",
                    )
                )
        return findings

    def _semantic_findings(self, corpus: ActiveCorpus) -> list[LintFinding]:
        findings: list[LintFinding] = []
        propositions: dict[str, list[str]] = {}
        for claim_id, claim in corpus.claims.items():
            propositions.setdefault(claim.proposition_key or claim.statement.casefold(), []).append(
                claim_id
            )
            if claim.status.value in {"stale", "disputed"}:
                findings.append(
                    self._finding(
                        "claim_needs_semantic_review",
                        "warning",
                        claim_id,
                        "Claim lifecycle requires semantic review",
                    )
                )
            for target in (*claim.contradicts, *claim.supersedes):
                if target not in corpus.claims:
                    findings.append(
                        self._finding(
                            "claim_relation_unresolved",
                            "error",
                            claim_id,
                            "Claim contradiction or supersession target is unresolved",
                        )
                    )
        for key, claim_ids in propositions.items():
            if len(claim_ids) > 1:
                findings.append(
                    self._finding(
                        "duplicate_proposition",
                        "warning",
                        sha256_hex(key.encode("utf-8")),
                        "Multiple claims share the same normalized proposition",
                    )
                )
        return findings

    def _safe_subject(self, value: str) -> str:
        decision = self.scanner.scan(value.encode("utf-8"), path=None)
        if isinstance(decision, SensitivityDecision) and decision.disposition == "allow":
            return value
        return f"path_sha256:{sha256_hex(value.encode('utf-8'))}"

    def _finding(self, code: str, severity: str, subject: str, message: str) -> LintFinding:
        return LintFinding(
            code=code,
            severity=severity,
            subject=self._safe_subject(subject),
            message=message,
        )
