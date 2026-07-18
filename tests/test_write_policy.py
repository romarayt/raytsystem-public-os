from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.ingestion import IngestPipeline, IntegrityError
from raytsystem.io import UnsafeWritePath


def test_absolute_control_db_config_is_rejected(project_root: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-control.sqlite"
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace(
            'control_db = "ops/control.sqlite"',
            f'control_db = "{outside.as_posix()}"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="control DB path"):
        IngestPipeline(project_root)

    assert not outside.exists()


def test_symlinked_normalized_root_cannot_escape_workspace(
    project_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-normalized"
    outside.mkdir(exist_ok=True)
    (project_root / "normalized").symlink_to(outside, target_is_directory=True)
    source = project_root / "inbox" / "source.md"
    source.write_text("# Safe input\n", encoding="utf-8")

    with pytest.raises(UnsafeWritePath):
        IngestPipeline(project_root).ingest(source, fixture=True)

    assert not list(outside.iterdir())


def test_symlinked_ops_root_cannot_redirect_control_db(project_root: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-ops"
    outside.mkdir(exist_ok=True)
    (project_root / "ops").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeWritePath):
        IngestPipeline(project_root)

    assert not (outside / "control.sqlite").exists()
