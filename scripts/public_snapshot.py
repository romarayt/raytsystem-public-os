#!/usr/bin/env python3
"""Build a clean public raytsystem snapshot without private development history."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PREFIXES = (
    ".git/",
    ".playwright-cli/",
    ".raytsystem/",
    "_raw/",
    "normalized/",
    "ledger/objects/",
    "knowledge/claims/",
    "knowledge/sources/",
    "ops/approvals/",
    "ops/backups/",
    "ops/checkpoints/",
    "ops/encrypted/",
    "ops/events/",
    "ops/runs/",
    "ops/skill-authoring-recovery/",
    "ops/staging/",
    "output/",
    "web/.vite/",
    "web/.vitest-attachments/",
    "web/coverage/",
    "web/node_modules/",
    "website/.docusaurus/",
    "website/build/",
    "website/node_modules/",
)
EXCLUDED_EXACT = {
    ".coverage",
    "docs/03-implementation-plan.md",
    "docs/05-user-pilot-request.md",
    "docs/08-raytsystem-web-implementation-plan.md",
    "docs/15-ui-ux-system-audit.md",
    "docs/GITHUB_SETUP.md",
    "docs/PUBLICATION_READINESS.md",
    "knowledge/.materialized-generation",
    "knowledge/.projection.json",
    "knowledge/graph.json",
    "knowledge/hot.md",
    "knowledge/index.md",
    "knowledge/overview.md",
    "ops/control.sqlite",
    "ops/platform.sqlite",
    "ops/platform.sqlite-shm",
    "ops/platform.sqlite-wal",
}
ALLOWED_LEDGER_FILES = {
    "ledger/CURRENT",
    "ledger/generations/genesis.json",
}
EXCLUDED_SUFFIXES = (
    ".db",
    ".log",
    ".map",
    ".sqlite",
    ".sqlite3",
    ".trace",
    ".zip",
)


def candidate_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted({item.decode("utf-8") for item in result.stdout.split(b"\0") if item})


def is_public_path(relative: str) -> bool:
    if relative.startswith("ledger/"):
        return relative in ALLOWED_LEDGER_FILES
    if relative in EXCLUDED_EXACT or relative.startswith(EXCLUDED_PREFIXES):
        return False
    return not relative.lower().endswith(EXCLUDED_SUFFIXES)


def copy_public_file(relative: str, destination: Path) -> int | None:
    source = ROOT / relative
    if not source.exists():
        return None
    source_stat = source.lstat()
    if stat.S_ISLNK(source_stat.st_mode):
        raise RuntimeError(f"refusing symlink: {relative}")
    if not stat.S_ISREG(source_stat.st_mode):
        raise RuntimeError(f"refusing non-regular file: {relative}")
    if source_stat.st_nlink != 1:
        raise RuntimeError(f"refusing hard-linked file: {relative}")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)
    os.chmod(target, stat.S_IMODE(source_stat.st_mode))
    return source_stat.st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise SystemExit("destination must be outside the source repository")
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit("destination must not exist or must be empty")
    destination.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    excluded: list[str] = []
    byte_count = 0
    for relative in candidate_paths():
        if not is_public_path(relative):
            excluded.append(relative)
            continue
        size = copy_public_file(relative, destination)
        if size is not None:
            copied.append(relative)
            byte_count += size

    # The public repository carries no development corpus. Point its minimal
    # canonical scaffold at the immutable empty generation copied above and
    # serialize that generation exactly as the runtime's canonical JSON does.
    (destination / "ledger" / "CURRENT").write_text("genesis\n", encoding="ascii")
    genesis_path = destination / "ledger" / "generations" / "genesis.json"
    genesis = json.loads(genesis_path.read_text("utf-8"))
    created_at = datetime.fromisoformat(genesis["created_at"].replace("Z", "+00:00"))
    genesis["created_at"] = (
        created_at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    genesis_path.write_bytes(
        json.dumps(
            genesis,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )

    print(
        json.dumps(
            {
                "schema": "PublicSnapshotV1",
                "destination": str(destination),
                "copied_files": len(copied),
                "copied_bytes": byte_count,
                "excluded_files": len(excluded),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
