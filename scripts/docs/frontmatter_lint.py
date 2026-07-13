#!/usr/bin/env python3
"""Lint the knowledge-base frontmatter, links, examples and safety hygiene.

Checks, for every ``website/docs`` page:

  * required frontmatter fields are present and well-formed;
  * ``status`` is one of the allowed values;
  * slugs are unique;
  * every ``feature_flags`` entry is a real flag from the configuration;
  * every ``related_commands`` entry is a real CLI command;
  * ``related_pages`` and inline ``/...`` doc links resolve to a real page;
  * no absolute local filesystem path or obvious secret leaks into a page.

The Docusaurus build additionally enforces MDX compilation, broken links and
missing images; this linter catches contract-level problems earlier and with
clearer messages. Run:

    python3 scripts/docs/frontmatter_lint.py
"""

from __future__ import annotations

import re
import sys

from _docs_common import (
    DOCS_DIR,
    all_feature_flags,
    cli_commands,
    load_articles,
)

REQUIRED_FIELDS = ["title", "description", "audience", "status"]
ALLOWED_STATUS = {"stable", "experimental", "disabled", "draft"}

# Absolute local paths and a few high-signal secret shapes. Deliberately narrow
# to avoid false positives on ordinary prose.
ABS_PATH = re.compile(r"(?<![\w`])/(Users|home|private|var|etc)/[\w./-]+")
SECRET_SHAPES = [
    re.compile(r"\b(sk|pk|ghp|xox[baprs])-[A-Za-z0-9]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]
INLINE_DOC_LINK = re.compile(r"\]\((/[A-Za-z0-9/_-]+)\)")


def main() -> int:
    articles = load_articles()
    slugs = {a.slug for a in articles}
    flags = set(all_feature_flags())
    commands = {"raytsystem " + c for c in cli_commands()}
    problems: list[str] = []
    seen_slugs: dict[str, str] = {}

    for article in articles:
        rel = str(article.path.relative_to(DOCS_DIR.parent.parent))
        fm = article.frontmatter

        if article.generated:
            # Generated pages carry a reduced, tool-owned frontmatter.
            if article.slug in seen_slugs:
                problems.append(f"{rel}: duplicate slug '{article.slug}'")
            seen_slugs[article.slug] = rel
            continue

        for field in REQUIRED_FIELDS:
            if field not in fm or fm[field] in ("", None, []):
                problems.append(f"{rel}: missing required frontmatter field '{field}'")

        status = article.status
        if status and status not in ALLOWED_STATUS:
            problems.append(f"{rel}: invalid status '{status}'")

        if article.slug in seen_slugs:
            problems.append(
                f"{rel}: duplicate slug '{article.slug}' (also {seen_slugs[article.slug]})"
            )
        seen_slugs[article.slug] = rel

        for flag in article.feature_flags:
            if flag not in flags:
                problems.append(f"{rel}: unknown feature flag '{flag}' in feature_flags")

        related_cmds = fm.get("related_commands")
        if isinstance(related_cmds, list):
            for cmd in related_cmds:
                text = str(cmd).strip()
                if not text.startswith("raytsystem "):
                    continue
                # Compare against the leaf command prefix (ignore trailing args).
                if not any(text == c or text.startswith(c + " ") for c in commands):
                    problems.append(f"{rel}: related_commands references unknown '{text}'")

        related_pages = fm.get("related_pages")
        if isinstance(related_pages, list):
            for page in related_pages:
                target = str(page)
                if target not in slugs:
                    problems.append(f"{rel}: related_pages '{target}' does not resolve")

        for match in INLINE_DOC_LINK.finditer(article.body):
            target = match.group(1)
            if target.startswith("/reference/"):
                continue
            if target not in slugs and target.rstrip("/") not in slugs:
                problems.append(f"{rel}: inline link '{target}' does not resolve")

        if ABS_PATH.search(article.body):
            problems.append(f"{rel}: contains an absolute local filesystem path")
        for shape in SECRET_SHAPES:
            if shape.search(article.body):
                problems.append(f"{rel}: contains a possible secret")

    print(f"Linted {len(articles)} documentation pages.")
    if problems:
        print("\nFrontmatter / link / hygiene problems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Frontmatter, links, commands and hygiene are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
