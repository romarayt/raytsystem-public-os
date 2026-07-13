#!/usr/bin/env python3
"""Fail closed when the tracked public tree contains common private artifacts."""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    ".playwright-cli/",
    "_raw/",
    "normalized/",
    "ledger/objects/",
    "knowledge/claims/",
    "knowledge/sources/",
    "ops/checkpoints/",
    "ops/events/",
    "ops/skill-authoring-recovery/",
    "output/",
    "website/.docusaurus/",
    "website/build/",
    "web/.vitest-attachments/",
    "ops/backups/",
    "ops/encrypted/",
    "ops/runs/",
    "_raw/restricted/",
    "dist/",
)
FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".trace",
    ".zip",
    ".tar",
    ".tgz",
    ".7z",
    ".pem",
    ".p12",
    ".pfx",
    ".map",
)
GENESIS_PROJECTION_FILES = {
    "knowledge/.materialized-generation",
    "knowledge/.projection.json",
    "knowledge/graph.json",
    "knowledge/hot.md",
    "knowledge/index.md",
}
ALLOWED_LEDGER_FILES = {
    "ledger/CURRENT",
    "ledger/generations/genesis.json",
}
TEXT_SUFFIXES = {
    ".cff",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".mdx",
    ".mjs",
    ".py",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "OpenAI-style token": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "Telegram bot token": re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}\b"),
}
ABSOLUTE_USER_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")
README_IMAGE = re.compile(r"!\[[^\]]*\]\((?!https?://)([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
PLACEHOLDERS = (
    "github.com/" + "OWN" + "ER/",
    "OWN" + "ER.github.io",
    "ORG" + "_PLACEHOLDER",
)
PNG_METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}


def png_metadata_chunks(path: Path) -> list[str]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ["invalid PNG signature"]
    found: list[str] = []
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            return ["truncated PNG chunk"]
        if chunk_type in PNG_METADATA_CHUNKS:
            found.append(chunk_type.decode("ascii"))
        offset = end
        if chunk_type == b"IEND":
            break
    return found


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    problems: list[str] = []
    tracked = tracked_files()
    for relative in tracked:
        lower = relative.lower()
        if relative.startswith("ledger/") and relative not in ALLOWED_LEDGER_FILES:
            problems.append(f"non-genesis ledger state: {relative}")
            continue
        if relative.startswith(FORBIDDEN_PREFIXES) or lower.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden tracked artifact: {relative}")
            continue
        if relative == ".env" or (relative.startswith(".env.") and relative != ".env.example"):
            problems.append(f"environment file must not be tracked: {relative}")
            continue
        path = ROOT / relative
        if relative.startswith("assets/github/") and path.suffix.lower() == ".png":
            for chunk in png_metadata_chunks(path):
                problems.append(f"PNG metadata ({chunk}): {relative}")
            if path.stat().st_size > 5_000_000:
                problems.append(f"oversized public image: {relative}")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text("utf-8")
        except UnicodeDecodeError:
            problems.append(f"non-UTF-8 text-like file: {relative}")
            continue
        if ABSOLUTE_USER_PATH.search(text):
            problems.append(f"absolute local user path: {relative}")
        for placeholder in PLACEHOLDERS:
            if placeholder in text:
                problems.append(f"unresolved public placeholder: {relative}")
        for category, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"possible {category}: {relative}")

    current = (ROOT / "ledger" / "CURRENT").read_text("ascii")
    if current != "genesis\n":
        problems.append("public ledger pointer must be genesis")
    genesis = json.loads((ROOT / "ledger" / "generations" / "genesis.json").read_text("utf-8"))
    if genesis.get("generation_id") != "genesis" or genesis.get("records") != {}:
        problems.append("public genesis must contain no corpus records")
    present_projections = GENESIS_PROJECTION_FILES.intersection(tracked)
    if present_projections and present_projections != GENESIS_PROJECTION_FILES:
        problems.append("genesis projections must be tracked as a complete set")
    marker_path = ROOT / "knowledge" / ".projection.json"
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text("utf-8"))
        if marker.get("generation_id") != "genesis":
            problems.append("public projection must be bound to genesis")

    readme = (ROOT / "README.md").read_text("utf-8")
    for match in README_IMAGE.finditer(readme):
        target = match.group(1).split("#", 1)[0]
        if target and not (ROOT / target).is_file():
            problems.append(f"missing README image: {target}")

    if problems:
        for problem in sorted(set(problems)):
            print(f"PUBLIC_HYGIENE: {problem}")
        return 1
    print(f"PUBLIC_HYGIENE: clean ({len(tracked)} tracked files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
