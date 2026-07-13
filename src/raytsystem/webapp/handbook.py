"""Read-only in-app knowledge base ("База знаний").

Serves the public documentation that lives under ``website/docs`` as a bounded,
path-contained, read-only surface so the raytsystem control plane can present it as
a native section instead of a separate site. It never writes, never follows
symlinks, and returns cleaned Markdown (frontmatter, MDX imports and Docusaurus
admonition syntax removed) that a small in-app renderer can display safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raytsystem.security.paths import PathPolicyError, read_regular_file

# The handbook content ships with raytsystem itself, so it is read relative to the
# installed package (repo root), not the user's project root.
_DOCS_ROOT = Path(__file__).resolve().parents[3] / "website" / "docs"

_MAX_FILES = 500
_MAX_FILE_BYTES = 262_144
_MAX_TITLE = 200
_ALLOWED_STATUS = {"stable", "experimental", "disabled", "draft"}
_SLUG_RE = re.compile(r"^/[A-Za-z0-9/_-]*$")
_ADMONITION_OPEN = re.compile(r"^:::(note|info|tip|warning|danger|caution)\s*(.*)$")
_MDX_IMPORT = re.compile(r"^import\s+.+\s+from\s+.+;?\s*$")
_JSX_TAG = re.compile(r"^\s*<[A-Za-z][^>]*/?>\s*$")


class HandbookError(RuntimeError):
    """Raised when a handbook article cannot be resolved or read safely."""


@dataclass(frozen=True)
class _Doc:
    slug: str
    title: str
    status: str
    generated: bool
    section: str
    relative: str


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    data: dict[str, str] = {}
    for line in block.splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        data[key.strip()] = value
    return data, body


def _clean_body(body: str) -> str:
    """Strip MDX imports and convert admonitions to blockquotes for plain rendering."""
    out: list[str] = []
    in_admonition = False
    for line in body.splitlines():
        if _MDX_IMPORT.match(line):
            continue
        if line.strip().startswith("<GeneratedNotice"):
            out.append("> ⚙ **Сгенерировано автоматически — не редактируйте вручную.**")
            continue
        if _JSX_TAG.match(line):
            continue
        opener = _ADMONITION_OPEN.match(line.strip())
        if opener:
            in_admonition = True
            title = opener.group(2).strip() or opener.group(1).upper()
            out.append(f"> **{title}**")
            continue
        if line.strip() == ":::" and in_admonition:
            in_admonition = False
            out.append("")
            continue
        if in_admonition:
            out.append(f"> {line}" if line.strip() else ">")
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


class HandbookService:
    def __init__(self, docs_root: Path | None = None) -> None:
        self.root = (docs_root or _DOCS_ROOT).resolve()

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def _category_labels(self) -> dict[str, tuple[str, int]]:
        labels: dict[str, tuple[str, int]] = {}
        if not self.available:
            return labels
        import json

        for path in sorted(self.root.glob("*/_category_.json")):
            if path.is_symlink():
                continue
            section = path.parent.name
            try:
                data = read_regular_file(
                    self.root, path.relative_to(self.root), max_bytes=16_384
                ).data
                parsed = json.loads(data.decode("utf-8"))
            except (PathPolicyError, OSError, ValueError):
                continue
            label = str(parsed.get("label", section))[:_MAX_TITLE]
            position = (
                int(parsed.get("position", 999))
                if isinstance(parsed.get("position"), (int, float))
                else 999
            )
            labels[section] = (label, position)
        return labels

    def _scan(self) -> list[_Doc]:
        docs: list[_Doc] = []
        if not self.available:
            return docs
        count = 0
        for path in sorted(self.root.rglob("*")):
            if path.suffix not in (".md", ".mdx") or path.is_symlink():
                continue
            count += 1
            if count > _MAX_FILES:
                break
            relative = path.relative_to(self.root).as_posix()
            try:
                data = read_regular_file(self.root, relative, max_bytes=_MAX_FILE_BYTES).data
            except (PathPolicyError, OSError):
                continue
            fm, _ = _parse_frontmatter(data.decode("utf-8", errors="replace"))
            parts = path.relative_to(self.root).with_suffix("").parts
            slug = fm.get("slug") or "/" + "/".join(p for p in parts if p != "index")
            if not slug.startswith("/"):
                slug = "/" + slug
            if slug == "":
                slug = "/"
            status = fm.get("status", "")
            docs.append(
                _Doc(
                    slug=slug,
                    title=(fm.get("title") or parts[-1])[:_MAX_TITLE],
                    status=status if status in _ALLOWED_STATUS else "",
                    generated=str(fm.get("generated", "")).lower() == "true",
                    section=parts[0] if len(parts) > 1 else "",
                    relative=relative,
                )
            )
        return docs

    def tree(self) -> dict[str, Any]:
        docs = self._scan()
        labels = self._category_labels()
        sections: dict[str, dict[str, Any]] = {}
        root_articles: list[dict[str, Any]] = []
        for doc in docs:
            entry = {
                "slug": doc.slug,
                "title": doc.title,
                "status": doc.status,
                "generated": doc.generated,
            }
            if not doc.section:
                root_articles.append(entry)
                continue
            bucket = sections.setdefault(
                doc.section,
                {
                    "id": doc.section,
                    "label": labels.get(doc.section, (doc.section, 999))[0],
                    "position": labels.get(doc.section, (doc.section, 999))[1],
                    "articles": [],
                },
            )
            bucket["articles"].append(entry)
        ordered = sorted(sections.values(), key=lambda s: (s["position"], s["label"]))
        for section in ordered:
            section["articles"].sort(key=lambda a: a["title"])
        return {
            "available": self.available,
            "root_articles": sorted(root_articles, key=lambda a: a["slug"] != "/"),
            "sections": ordered,
            "article_count": len(docs),
        }

    def article(self, slug: str) -> dict[str, Any]:
        if not isinstance(slug, str) or not _SLUG_RE.match(slug) or ".." in slug:
            raise HandbookError("Invalid handbook slug")
        docs = {doc.slug: doc for doc in self._scan()}
        doc = docs.get(slug)
        if doc is None:
            raise HandbookError("Handbook article not found")
        try:
            data = read_regular_file(self.root, doc.relative, max_bytes=_MAX_FILE_BYTES).data
        except (PathPolicyError, OSError) as error:
            raise HandbookError("Handbook article is unavailable") from error
        _, body = _parse_frontmatter(data.decode("utf-8", errors="replace"))
        return {
            "slug": doc.slug,
            "title": doc.title,
            "status": doc.status,
            "generated": doc.generated,
            "section": doc.section,
            "markdown": _clean_body(body),
        }
