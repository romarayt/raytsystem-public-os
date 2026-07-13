"""Shared helpers for the documentation tooling (coverage, impact, generator).

Only depends on the standard library so the checks run in CI without extra
install steps beyond the project environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "website" / "docs"


@dataclass
class Article:
    path: Path
    slug: str
    frontmatter: dict[str, object] = field(default_factory=dict)
    body: str = ""

    @property
    def status(self) -> str:
        value = self.frontmatter.get("status")
        return str(value) if value else ""

    @property
    def feature_flags(self) -> list[str]:
        value = self.frontmatter.get("feature_flags")
        return [str(v) for v in value] if isinstance(value, list) else []

    @property
    def generated(self) -> bool:
        return bool(self.frontmatter.get("generated"))

    @property
    def route_document(self) -> bool:
        """Whether an interface article documents a concrete application route."""
        return self.frontmatter.get("route_document", True) is not False


_FLOW_LIST = re.compile(r"^\[(.*)\]$")


def _parse_scalar(raw: str) -> object:
    text = raw.strip()
    if text in ("true", "false"):
        return text == "true"
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    return text


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the leading YAML-ish frontmatter block.

    Handles the subset the knowledge base uses: scalars, flow lists ``[a, b]``,
    and block lists (``- item`` or ``- key: value`` under a key). This avoids a
    PyYAML dependency while staying strict enough for the checks.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    data: dict[str, object] = {}
    current_list: list[object] | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            item = line.split("- ", 1)[1].strip()
            if current_list is not None:
                if ":" in item and not item.startswith('"'):
                    key, _, value = item.partition(":")
                    current_list.append({key.strip(): _parse_scalar(value)})
                else:
                    current_list.append(_parse_scalar(item))
            continue
        if re.match(r"^[A-Za-z_]", line):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                current_list = []
                data[key] = current_list
            else:
                flow = _FLOW_LIST.match(value)
                if flow:
                    inner = flow.group(1).strip()
                    items = [i.strip() for i in inner.split(",") if i.strip()]
                    data[key] = [_parse_scalar(i) for i in items]
                else:
                    data[key] = _parse_scalar(value)
                current_list = None
    return data, body


def _slug_for(path: Path) -> str:
    rel = path.relative_to(DOCS_DIR).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "index":
        parts = parts[:-1]
    return "/" + "/".join(parts) if parts else "/"


def load_articles() -> list[Article]:
    articles: list[Article] = []
    for path in sorted(DOCS_DIR.rglob("*")):
        if path.suffix not in (".md", ".mdx"):
            continue
        text = path.read_text("utf-8")
        fm, body = parse_frontmatter(text)
        slug = str(fm.get("slug")) if fm.get("slug") else _slug_for(path)
        articles.append(Article(path=path, slug=slug, frontmatter=fm, body=body))
    return articles


def cli_commands() -> list[str]:
    import typer

    from raytsystem.cli import app

    root = typer.main.get_command(app)

    def walk(command: object, path: list[str]) -> list[str]:
        subs = getattr(command, "commands", None)
        if subs:
            out: list[str] = []
            for name, sub in sorted(subs.items()):
                out += walk(sub, [*path, name])
            return out
        return [" ".join(path)]

    return walk(root, [])


WEB_ROUTES = [
    "command-center",
    "handbook",
    "tasks",
    "universe",
    "runs",
    "agents",
    "skills",
    "context",
    "safety",
    "systems",
]


def core_feature_flags() -> dict[str, bool]:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "config" / "raytsystem.toml").read_text("utf-8"))
    return {k: bool(v) for k, v in data.get("features", {}).items()}


def platform_feature_flags() -> dict[str, bool]:
    flags: dict[str, bool] = {}
    in_flags = False
    for line in (REPO_ROOT / "config" / "platform.yaml").read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":") and not line.startswith(" "):
            in_flags = stripped == "features:"
            continue
        if in_flags and ":" in stripped:
            key, _, value = stripped.partition(":")
            token = value.strip().lower()
            if token in ("true", "false"):
                flags[key.strip()] = token == "true"
    return flags


def all_feature_flags() -> dict[str, bool]:
    return {**core_feature_flags(), **platform_feature_flags()}
