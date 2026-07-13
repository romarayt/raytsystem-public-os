from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from raytsystem.checkpoint_guard import CheckpointGuard


def test_path_classifier_blocks_canonical_and_generated_state_only() -> None:
    guard = CheckpointGuard(Path(__file__).parents[1])
    blocked = (
        "_raw/manifests/sources.jsonl",
        "normalized/srev/norm/document.txt",
        "ledger/CURRENT",
        "ledger/objects/sha256/aa/value.json",
        "knowledge/index.md",
        "knowledge/claims/clm.md",
        "ops/events/evt_x.json",
        "ops/checkpoints/evt_x.json",
        "ops/runs/run_x/manifest.json",
        "ops/task-ledger/CURRENT",
        "artifacts/outbox/action.json",
    )
    allowed = (
        "knowledge/manual/note.md",
        "src/raytsystem/example.py",
        "tests/test_example.py",
        "docs/example.md",
        "skills/raytsystem-query/SKILL.md",
        "config/schemas/v1.1.0/Claim.schema.json",
        "ops/runs/m3_qualification_example/manifest.json",
    )

    assert all(guard.classify_path(path) == "protected" for path in blocked)
    assert all(guard.classify_path(path) == "ordinary" for path in allowed)


def test_guard_redacts_secret_and_rejects_protected_paths_without_writes() -> None:
    root = Path(__file__).parents[1]
    guard = CheckpointGuard(root)
    secret = ("sk-" + "proj-" + "z" * 32).encode()
    report = guard.check(
        paths=("src/raytsystem/safe.py", "ledger/CURRENT"),
        staged_bytes={"src/raytsystem/safe.py": secret},
        run_lint=False,
    )

    assert not report.ok
    assert {finding.code for finding in report.findings} == {
        "protected_path",
        "staged_secret",
    }
    assert secret.decode() not in json.dumps(report.to_dict())


def test_guard_redacts_sensitive_filenames_from_report_paths() -> None:
    report = CheckpointGuard(Path(__file__).parents[1]).check(
        paths=(".env",),
        staged_bytes={".env": b"harmless-placeholder"},
        run_lint=False,
    )
    rendered = json.dumps(report.to_dict())

    assert not report.ok
    assert ".env" not in rendered
    assert "path_sha256:" in rendered


def test_read_only_guard_preserves_git_index_and_dirty_worktree() -> None:
    root = Path(__file__).parents[1]
    index = root / ".git" / "index"
    index_before = index.read_bytes()
    status_before = subprocess.run(
        ("git", "-C", str(root), "status", "--porcelain=v1", "-z"),
        capture_output=True,
        check=True,
    ).stdout

    report = CheckpointGuard(root).check(paths=(), staged_bytes={}, run_lint=True)

    status_after = subprocess.run(
        ("git", "-C", str(root), "status", "--porcelain=v1", "-z"),
        capture_output=True,
        check=True,
    ).stdout
    assert report.ok
    assert index.read_bytes() == index_before
    assert status_after == status_before


def test_precommit_uses_the_same_cli_guard_as_runtime() -> None:
    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / ".pre-commit-config.yaml").read_text())
    hooks = config["repos"][0]["hooks"]

    assert hooks[0]["id"] == "raytsystem-checkpoint-guard"
    assert hooks[0]["entry"] == "uv run raytsystem guard-checkpoint --json"
    assert hooks[0]["pass_filenames"] is False


def test_guard_detects_staged_canonical_deletion_without_following_renames(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    current = tmp_path / "ledger" / "CURRENT"
    current.parent.mkdir()
    current.write_text("genesis\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "ledger/CURRENT"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test" + "@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ),
        check=True,
    )
    current.unlink()
    subprocess.run(("git", "-C", str(tmp_path), "add", "-u"), check=True)

    report = CheckpointGuard(tmp_path).check(run_lint=False)

    assert not report.ok
    assert report.paths == ("ledger/CURRENT",)
    assert report.findings[0].code == "protected_path"
