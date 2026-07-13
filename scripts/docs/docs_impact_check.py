#!/usr/bin/env python3
"""Fail a change set that touches a public surface without touching the docs.

The public knowledge base is part of the product. When a change affects a
user-visible surface — the web UI, a CLI command, an API/schema/public
contract, configuration or feature flags, workflow/tasking/agents/skills/packs,
security/approvals, install/migration/backup/restore, or observable behavior —
the documentation must change in the same change set.

Escape hatch: a purely internal change may set ``docs-not-needed`` with a
concrete, verifiable justification, either via a top-level ``.docs-not-needed``
file with non-empty content or the ``DOCS_NOT_NEEDED`` environment variable.
This check never generates or commits documentation itself.

Usage:
    python3 scripts/docs/docs_impact_check.py [--base <git-ref>]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# A change to any of these is a public surface that requires documentation.
SURFACE_PATTERNS = [
    re.compile(r"^web/src/(?!.*test).*\.(tsx?|css)$"),
    re.compile(r"^src/raytsystem/cli\.py$"),
    re.compile(r"^src/raytsystem/platform_cli\.py$"),
    re.compile(r"^src/raytsystem/contracts/.*\.py$"),
    re.compile(r"^src/raytsystem/webapp/(app|dto|execution_views|feature_routes|feature_dto)\.py$"),
    re.compile(r"^src/raytsystem/(execution|packages|emergency|secrets|backup|protocols|tooling)/"),
    re.compile(r"^src/raytsystem/(migrations|authority|features|universe|tasking|querying)\.py$"),
    re.compile(r"^src/raytsystem/codegraph/(querying|projection|contracts)\.py$"),
    re.compile(r"^config/.*\.(toml|yaml)$"),
    re.compile(r"^config/schemas/"),
    re.compile(r"^packs/"),
    re.compile(r"^skills/"),
]

# Changes limited to these never require a documentation change on their own.
NON_SURFACE_PATTERNS = [
    re.compile(r"^tests?/"),
    re.compile(r".*/test/"),
    re.compile(r".*\.test\.(tsx?|py)$"),
    re.compile(r"^website/"),
    re.compile(r"^scripts/docs/"),
    re.compile(r"^\.github/"),
    re.compile(r"^ops/"),
    re.compile(r"^docs/"),
    re.compile(r".*\.md$"),
    re.compile(r"^uv\.lock$"),
    re.compile(r"^web/(package|package-lock)\.json$"),
]

DOCS_CHANGE_PATTERNS = [
    re.compile(r"^website/docs/"),
]


def _git(args: list[str]) -> list[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def changed_files(base: str | None) -> list[str]:
    files: set[str] = set()
    if base:
        merge_base = _git(["merge-base", base, "HEAD"])
        ref = merge_base[0] if merge_base else base
        files.update(_git(["diff", "--name-only", ref, "HEAD"]))
    # Always include the working tree so the check is useful locally.
    files.update(_git(["diff", "--name-only"]))
    files.update(_git(["diff", "--name-only", "--cached"]))
    files.update(_git(["ls-files", "--others", "--exclude-standard"]))
    return sorted(files)


def _matches(path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(path) for p in patterns)


def docs_not_needed_reason() -> str:
    env = os.environ.get("DOCS_NOT_NEEDED", "").strip()
    if env:
        return env
    marker = REPO_ROOT / ".docs-not-needed"
    if marker.is_file():
        return marker.read_text("utf-8").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("GITHUB_BASE_REF") or None,
        help="git ref to diff against (default: $GITHUB_BASE_REF, else working tree only)",
    )
    args = parser.parse_args()

    files = changed_files(args.base)
    if not files:
        print("No changed files detected; nothing to check.")
        return 0

    surface = [
        f for f in files if _matches(f, SURFACE_PATTERNS) and not _matches(f, NON_SURFACE_PATTERNS)
    ]
    docs_changed = [f for f in files if _matches(f, DOCS_CHANGE_PATTERNS)]

    print(
        f"Changed files: {len(files)} · public-surface: {len(surface)} · docs: {len(docs_changed)}"
    )

    if not surface:
        print("No public surface changed; documentation update not required.")
        return 0

    if docs_changed:
        print("Public surface changed and documentation was updated in the same change set.")
        return 0

    reason = docs_not_needed_reason()
    if reason:
        print("Public surface changed without docs, but docs-not-needed is set:")
        print(f"  reason: {reason}")
        print(
            "Reminder: docs-not-needed is invalid if UI, CLI, API, schema, config, feature flag,\n"
            "permissions, approvals, workflow, user data, install or observable behavior changed."
        )
        return 0

    print("\nPublic surface changed but no documentation was updated.", file=sys.stderr)
    print("Affected surface files:", file=sys.stderr)
    for f in surface[:40]:
        print(f"  - {f}", file=sys.stderr)
    print(
        "\nUpdate website/docs/** in this change set, or set a verifiable docs-not-needed reason\n"
        "(top-level .docs-not-needed file or DOCS_NOT_NEEDED env var).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
