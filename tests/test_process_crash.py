from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from raytsystem.ingestion import IngestPipeline


@pytest.mark.parametrize(
    ("checkpoint", "pointer_changed"),
    [
        ("after_generation_publish", False),
        ("after_current_swap", True),
    ],
)
@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork and SIGKILL semantics")
def test_sigkill_resume_converges_to_one_generation(
    project_root: Path,
    checkpoint: str,
    pointer_changed: bool,
) -> None:
    config = project_root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text().replace("lease_ttl_seconds = 60", "lease_ttl_seconds = 1"),
        encoding="utf-8",
    )
    source = project_root / "inbox" / "crash.md"
    source.write_text("Crash-safe evidence.\n", encoding="utf-8")
    code = (
        "from pathlib import Path; "
        "from raytsystem.ingestion import IngestPipeline; "
        f"IngestPipeline(Path({str(project_root)!r}), hard_fail_at={checkpoint!r})"
        f".ingest(Path({str(source)!r}), fixture=True)"
    )
    environment = dict(os.environ)
    environment["RAYTSYSTEM_ENABLE_TEST_HARD_FAULTS"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )

    assert completed.returncode == -9
    current_after_crash = (project_root / "ledger" / "CURRENT").read_text().strip()
    assert (current_after_crash != "genesis") is pointer_changed
    time.sleep(1.05)

    resumed = IngestPipeline(project_root).ingest(source, fixture=True)

    assert resumed.status == "succeeded"
    assert len(list((project_root / "ledger" / "generations").glob("*.json"))) == 2
    assert len(list((project_root / "ops" / "events").glob("evt_*.json"))) == 1
