#!/usr/bin/env python3
"""Verify that the public knowledge base covers every real raytsystem surface.

The check maps the live product surfaces — registered CLI commands, web routes,
feature flags and the schema registry — onto documentation identifiers, and
fails when they drift:

  * an undocumented web route (route exists, no interface page);
  * an orphan interface route page (page exists, route removed);
  * a CLI command missing from the generated reference;
  * a disabled-by-default flag not disclosed on the "defaults" page;
  * an article that lists a disabled flag but claims ``status: stable``
    (a feature wrongly marked stable / available by default).

It reads the filesystem and the live contracts, never a hand-maintained list,
so it cannot pass by editing the coverage page alone. Run:

    python3 scripts/docs/coverage_check.py
"""

from __future__ import annotations

import sys

from _docs_common import (
    DOCS_DIR,
    WEB_ROUTES,
    all_feature_flags,
    cli_commands,
    load_articles,
)


def main() -> int:
    problems: list[str] = []
    articles = load_articles()
    by_slug = {a.slug: a for a in articles}

    # 1. Every web route has an interface page; no orphan interface pages.
    for route in WEB_ROUTES:
        if f"/interface/{route}" not in by_slug:
            problems.append(
                f"Route '/{route}' has no documentation page website/docs/interface/{route}.md"
            )
    interface_pages = [
        a.slug.rsplit("/", 1)[-1]
        for a in articles
        if a.slug.startswith("/interface/") and a.slug != "/interface/overview" and a.route_document
    ]
    for page in interface_pages:
        if page not in WEB_ROUTES:
            problems.append(
                f"Interface page '/interface/{page}' documents a route that no longer exists"
            )

    # 2. Every CLI command is present in the generated CLI reference.
    cli_ref = DOCS_DIR / "reference" / "cli.mdx"
    ref_text = cli_ref.read_text("utf-8") if cli_ref.is_file() else ""
    for command in cli_commands():
        anchor = "{#" + command.replace(" ", "-") + "}"
        if anchor not in ref_text:
            problems.append(
                f"CLI command 'raytsystem {command}' is missing from the generated reference "
                f"(run: python3 scripts/docs/gen_reference.py --write)"
            )

    # 3. Disabled-by-default flags must be disclosed on the defaults page.
    flags = all_feature_flags()
    disabled = sorted(k for k, v in flags.items() if not v)
    defaults = by_slug.get("/security/defaults")
    defaults_text = defaults.body if defaults else ""
    for flag in disabled:
        if flag not in defaults_text:
            problems.append(
                f"Disabled-by-default flag '{flag}' is not disclosed on /security/defaults"
            )

    # 4. A feature article whose feature is entirely gated off must not claim
    #    status: stable. This catches a disabled feature presented as available.
    #    Cross-cutting sections (security, troubleshooting, configuration, meta)
    #    legitimately enumerate disabled flags as their subject and are exempt.
    feature_sections = (
        "/interface/",
        "/code-graph/",
        "/tasks/",
        "/workflow/",
        "/knowledge/",
        "/agents/",
        "/research/",
        "/observability/",
    )
    for article in articles:
        if article.status != "stable":
            continue
        if not article.slug.startswith(feature_sections):
            continue
        gating = [f for f in article.feature_flags if f in flags]
        if gating and all(not flags[f] for f in gating):
            problems.append(
                f"{article.path.relative_to(DOCS_DIR.parent.parent)} is status: stable but every "
                f"feature flag it lists is disabled; use status experimental/disabled"
            )

    # 5. Every article carries a status.
    for article in articles:
        if article.generated:
            continue
        if not article.status:
            problems.append(
                f"{article.path.relative_to(DOCS_DIR.parent.parent)} has no 'status' frontmatter"
            )

    total = len(articles)
    print(f"Scanned {total} documentation pages.")
    print(f"Routes: {len(WEB_ROUTES)} · CLI commands: {len(cli_commands())} · flags: {len(flags)}")
    if problems:
        print("\nCoverage problems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Documentation coverage is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
