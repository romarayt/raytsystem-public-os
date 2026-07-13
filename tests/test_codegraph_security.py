from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from raytsystem.codegraph.detect import DetectedFile, detect_files, load_code_graph_config
from raytsystem.codegraph.extract import extract_file, extract_file_isolated
from raytsystem.codegraph.security import CodeGraphSecurityError, safe_code_read, sanitize_label
from raytsystem.contracts import canonical_json_bytes, sha256_hex


def _configure(root: Path) -> None:
    config = root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + """

[code_graph]
path = ".raytsystem/graph"
roots = ["src"]
files = ["pyproject.toml"]
max_files = 100
max_file_bytes = 1048576
max_total_bytes = 8388608
max_nodes = 5000
max_edges = 20000
universe_max_nodes = 1000
universe_max_edges = 5000
query_max_nodes = 40
query_max_edges = 100
query_max_bytes = 24000
parser_timeout_seconds = 10
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\ndependencies = []\n',
        encoding="utf-8",
    )


def test_code_graph_rejects_traversal_symlink_and_hardlink(project_root: Path) -> None:
    _configure(project_root)
    outside = project_root.parent / "outside.py"
    outside.write_text("SECRET = 'outside'\n", encoding="utf-8")
    os.symlink(outside, project_root / "src" / "linked.py")
    original = project_root / "src" / "original.py"
    original.write_text("def safe(): pass\n", encoding="utf-8")
    os.link(original, project_root / "src" / "hardlinked.py")

    with pytest.raises(CodeGraphSecurityError, match="escapes"):
        safe_code_read(project_root, "../outside.py", max_bytes=1024)
    with pytest.raises(CodeGraphSecurityError, match="no-follow"):
        safe_code_read(project_root, "src/hardlinked.py", max_bytes=1024)
    with pytest.raises(CodeGraphSecurityError, match="no-follow"):
        detect_files(project_root, load_code_graph_config(project_root))


def test_secret_filenames_are_excluded_and_labels_are_escaped(project_root: Path) -> None:
    _configure(project_root)
    (project_root / "src" / ".env.production").write_text("TOKEN=value\n", encoding="utf-8")
    (project_root / "src" / "safe.py").write_text("def safe(): pass\n", encoding="utf-8")

    detected = detect_files(project_root, load_code_graph_config(project_root))

    assert {item.path for item in detected} == {"pyproject.toml", "src/safe.py"}
    assert sanitize_label('<script>alert("x")</script>') == (
        "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
    )
    assert len(sanitize_label("x" * 1000)) == 256


def test_secret_shaped_comment_is_redacted_from_extracted_graph() -> None:
    token = "ghp_" + "a" * 36
    data = f"# SECURITY: rotate {token}\ndef safe():\n    pass\n".encode()
    file = DetectedFile(
        path="src/safe.py",
        data=data,
        content_sha256=sha256_hex(data),
        size_bytes=len(data),
        mtime_ns=1,
        language="python",
    )

    extraction = extract_file(file)
    rendered = canonical_json_bytes(extraction.to_dict())

    assert token.encode() not in rendered
    assert b"redacted sensitive value" in rendered


def test_isolated_parser_timeout_fails_closed(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"def safe():\n    pass\n"
    file = DetectedFile(
        path="src/safe.py",
        data=data,
        content_sha256=sha256_hex(data),
        size_bytes=len(data),
        mtime_ns=1,
        language="python",
    )

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr("raytsystem.codegraph.extract.subprocess.run", timeout)
    with pytest.raises(CodeGraphSecurityError, match="timeout"):
        extract_file_isolated(
            project_root,
            file,
            timeout_seconds=1,
            max_nodes=100,
            max_edges=200,
        )
