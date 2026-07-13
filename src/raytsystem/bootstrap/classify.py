"""Read-only, deterministic source-type classification for the bootstrap installer.

The classifier walks a target repository without following symlinks and without
mutating anything, samples a bounded number of markdown files, and derives a
reproducible :class:`SourceClassification`. It reuses the wikilink/frontmatter
regexes from :mod:`raytsystem.linting` and the language-suffix table from the code
graph detector so its notion of "code" and "links" matches the rest of raytsystem.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from raytsystem.codegraph.detect import LANGUAGE_BY_SUFFIX
from raytsystem.contracts.base import derive_id, sha256_hex
from raytsystem.contracts.installation import (
    RankedSourceType,
    SourceClassification,
    SourceSignalRecord,
    SourceType,
)
from raytsystem.linting import _FRONTMATTER_ID, _WIKILINK

# Directories never descended into while classifying (noise / vendored / managed).
# `.obsidian/` holds application config, not source content — detected separately.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".raytsystem",
        ".qmd",
        ".obsidian",
    }
)
# "Code" for source-type classification means actual programming languages; config
# and data formats (json/yaml/toml) are not treated as evidence of a software repo.
_CODE_SUFFIXES = frozenset(
    suffix
    for suffix, language in LANGUAGE_BY_SUFFIX.items()
    if language in {"python", "javascript", "typescript", "tsx", "sql"}
)
_PACKAGE_MANIFESTS = frozenset(
    {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml", "gemfile"}
)
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_MIXED_THRESHOLD = 55
_MAX_SAMPLE_FILES = 400
_MAX_PROBE_BYTES = 256 * 1024
_MAX_WALK_FILES = 20_000


class _Walk:
    """One bounded, no-follow pass over a target, collecting cheap signals."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.total_files = 0
        self.code_files = 0
        self.markdown_files = 0
        self.canvas_files = 0
        self.txt_files = 0
        self.manifests: set[str] = set()
        self.has_obsidian_dir = False
        self.has_graphify_export = False
        self._markdown_samples: list[Path] = []
        self._fingerprint_rows: list[str] = []
        self._run()

    def _run(self) -> None:
        self.has_obsidian_dir = self._is_real_dir(self.root / ".obsidian")
        self.has_graphify_export = self._graphify_export_present()
        for current, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            base = Path(current)
            for name in sorted(filenames):
                path = base / name
                try:
                    if stat.S_ISLNK(os.lstat(path).st_mode):
                        continue
                except OSError:
                    continue
                self._observe(path, name)
                if self.total_files >= _MAX_WALK_FILES:
                    return

    def _observe(self, path: Path, name: str) -> None:
        self.total_files += 1
        rel = path.relative_to(self.root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if len(self._fingerprint_rows) < _MAX_SAMPLE_FILES:
            self._fingerprint_rows.append(f"{rel}:{size}")
        suffix = path.suffix.lower()
        lower = name.lower()
        if lower in _PACKAGE_MANIFESTS:
            self.manifests.add(lower)
        if suffix in _CODE_SUFFIXES:
            self.code_files += 1
        elif suffix in _MARKDOWN_SUFFIXES:
            self.markdown_files += 1
            if len(self._markdown_samples) < _MAX_SAMPLE_FILES:
                self._markdown_samples.append(path)
        elif suffix == ".txt":
            self.txt_files += 1
        elif suffix == ".canvas":
            self.canvas_files += 1

    def _is_real_dir(self, path: Path) -> bool:
        try:
            return stat.S_ISDIR(os.lstat(path).st_mode) and not path.is_symlink()
        except OSError:
            return False

    def _graphify_export_present(self) -> bool:
        candidate = self.root / "graphify-out" / "graph.json"
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return False
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and isinstance(data.get("nodes"), list)

    def markdown_link_stats(self) -> tuple[int, int, int]:
        """Return (sampled files, files with wikilinks, files with frontmatter)."""

        sampled = 0
        wiki = 0
        frontmatter = 0
        for path in self._markdown_samples:
            try:
                text = path.read_text(encoding="utf-8")[:_MAX_PROBE_BYTES]
            except OSError:
                continue
            sampled += 1
            if _WIKILINK.search(text):
                wiki += 1
            head = text.lstrip()
            if head.startswith("---\n") or _FRONTMATTER_ID.search(text[:_MAX_PROBE_BYTES]):
                frontmatter += 1
        return sampled, wiki, frontmatter

    def fingerprint(self) -> str:
        return sha256_hex("\n".join(sorted(self._fingerprint_rows)).encode("utf-8"))


class RootClassifier:
    """Classify a repository root read-only into a reproducible source type."""

    version = "1.0.0"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def classify(self) -> SourceClassification:
        walk = _Walk(self.root)
        signals = self._signals(walk)
        scores = self._scores(signals)
        ranked = tuple(
            RankedSourceType(
                source_type=source_type,
                score=score,
                contributing_signals=tuple(s.kind for s in signals if s.source_type is source_type),
            )
            for source_type, score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0].value)
            )
            if score > 0
        )
        clearing = [r for r in ranked if r.score >= _MIXED_THRESHOLD]
        is_mixed = len(clearing) >= 2
        if not ranked:
            primary = SourceType.EMPTY if walk.total_files == 0 else SourceType.MARKDOWN
        elif is_mixed:
            primary = SourceType.MIXED
        else:
            primary = ranked[0].source_type
        fingerprint = walk.fingerprint()
        identity = {
            "root_fingerprint": fingerprint,
            "primary_type": primary.value,
            "is_mixed": is_mixed,
            "ranked_types": [
                {"source_type": r.source_type.value, "score": r.score} for r in ranked
            ],
        }
        return SourceClassification(
            classification_id=derive_id("srcclass", identity),
            root_fingerprint=fingerprint,
            primary_type=primary,
            is_mixed=is_mixed,
            ranked_types=ranked,
            signals=signals,
        )

    def _signals(self, walk: _Walk) -> tuple[SourceSignalRecord, ...]:
        signals: list[SourceSignalRecord] = []

        def add(kind: str, source_type: SourceType, weight: int, evidence: str) -> None:
            signals.append(
                SourceSignalRecord(
                    kind=kind, source_type=source_type, weight=weight, evidence=evidence
                )
            )

        if walk.has_obsidian_dir:
            add("obsidian_vault", SourceType.OBSIDIAN, 100, ".obsidian/ directory present")
        if walk.canvas_files:
            add("canvas_files", SourceType.OBSIDIAN, 60, f"{walk.canvas_files} .canvas files")
        if walk.has_graphify_export:
            add("graphify_export", SourceType.GRAPHIFY, 100, "graphify-out/graph.json present")
        if (self.root / ".git").exists() and walk.code_files:
            add(
                "software_repo",
                SourceType.SOFTWARE,
                90,
                f"git repo with {walk.code_files} code files",
            )
        elif walk.code_files:
            add("code_files", SourceType.SOFTWARE, 60, f"{walk.code_files} code files")
        if walk.manifests:
            add(
                "package_manifest",
                SourceType.SOFTWARE,
                min(60, 30 * len(walk.manifests)),
                "manifests: " + ", ".join(sorted(walk.manifests)),
            )
        # Obsidian is a markdown family: when a vault is present, markdown-family
        # signals reinforce OBSIDIAN rather than competing as a separate MARKDOWN
        # type, so "mixed" means genuinely different domains (e.g. notes + code).
        md_family = SourceType.OBSIDIAN if walk.has_obsidian_dir else SourceType.MARKDOWN
        sampled, wiki, frontmatter = walk.markdown_link_stats()
        if sampled and wiki:
            add(
                "wikilink_density",
                SourceType.OBSIDIAN,
                min(70, round(70 * wiki / sampled)),
                f"{wiki}/{sampled} sampled markdown files contain wikilinks",
            )
        if sampled and frontmatter:
            add(
                "frontmatter_density",
                md_family,
                min(50, round(50 * frontmatter / sampled)),
                f"{frontmatter}/{sampled} sampled markdown files carry frontmatter",
            )
        if walk.markdown_files + walk.txt_files:
            add(
                "plain_markdown",
                md_family,
                40,
                f"{walk.markdown_files} markdown + {walk.txt_files} text files",
            )
        return tuple(signals)

    def _scores(self, signals: tuple[SourceSignalRecord, ...]) -> dict[SourceType, int]:
        scores: dict[SourceType, int] = {}
        for signal in signals:
            if signal.source_type in {SourceType.EMPTY, SourceType.MIXED}:
                continue
            scores[signal.source_type] = min(100, scores.get(signal.source_type, 0) + signal.weight)
        return scores
