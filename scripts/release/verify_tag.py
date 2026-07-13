#!/usr/bin/env python3
"""Verify that a release tag is exactly v<project.version>."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_tag.py vX.Y.Z", file=sys.stderr)
        return 2
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    expected = f"v{version}"
    if sys.argv[1] != expected:
        print(f"release tag mismatch: expected {expected}, received {sys.argv[1]}", file=sys.stderr)
        return 1
    print(f"release tag matches project version: {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
