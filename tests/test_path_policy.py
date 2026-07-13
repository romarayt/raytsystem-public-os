from __future__ import annotations

import os
from pathlib import Path

import pytest

from raytsystem.security.paths import PathPolicyError, read_regular_file


def test_read_regular_file_stays_inside_workspace(project_root: Path) -> None:
    source = project_root / "inbox" / "source.md"
    source.write_bytes(b"safe bytes")

    result = read_regular_file(project_root, source, max_bytes=1024)

    assert result.relative_path == "inbox/source.md"
    assert result.data == b"safe bytes"


@pytest.mark.parametrize(
    "candidate",
    ["../outside.md", "/etc/passwd", "C:\\Windows\\system.ini", "bad\x00name"],
)
def test_path_escape_shapes_are_rejected(project_root: Path, candidate: str) -> None:
    with pytest.raises(PathPolicyError):
        read_regular_file(project_root, candidate, max_bytes=1024)


def test_symlink_parent_and_final_component_are_rejected(project_root: Path) -> None:
    outside = project_root.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, project_root / "inbox" / "final-link")
    os.symlink(project_root.parent, project_root / "linked-parent")

    with pytest.raises(PathPolicyError):
        read_regular_file(project_root, "inbox/final-link", max_bytes=1024)
    with pytest.raises(PathPolicyError):
        read_regular_file(project_root, "linked-parent/outside-secret.txt", max_bytes=1024)


def test_non_regular_and_oversized_inputs_are_rejected(project_root: Path) -> None:
    directory = project_root / "inbox" / "directory"
    directory.mkdir()
    oversized = project_root / "inbox" / "large.md"
    oversized.write_bytes(b"x" * 20)

    with pytest.raises(PathPolicyError, match="regular file"):
        read_regular_file(project_root, directory, max_bytes=1024)
    with pytest.raises(PathPolicyError, match="size limit"):
        read_regular_file(project_root, oversized, max_bytes=10)
