from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from raytsystem.storage import IntegrityError, publish_immutable, read_current_generation


def test_current_pointer_rejects_path_like_identifier(project_root: Path) -> None:
    (project_root / "ledger" / "CURRENT").write_text("../outside\n", encoding="ascii")

    with pytest.raises(IntegrityError, match="Malformed"):
        read_current_generation(project_root)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX process semantics")
def test_sigkill_after_immutable_link_cleanup_leaves_recoverable_single_link(
    project_root: Path,
) -> None:
    target = project_root / "ledger" / "objects" / "crash-safe.json"
    data = b'{"durable":true}'
    child = os.fork()
    if child == 0:
        from raytsystem import storage

        def kill_after_unlink(_path: Path) -> None:
            os.kill(os.getpid(), signal.SIGKILL)

        storage.fsync_directory = kill_after_unlink
        storage.publish_immutable(target, data)
        os._exit(2)

    waited, status = os.waitpid(child, 0)
    assert waited == child
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL
    assert target.read_bytes() == data
    assert target.stat().st_nlink == 1
    assert not list(target.parent.glob(f".{target.name}.*"))
    assert publish_immutable(target, data) is False


def test_retry_cleans_owned_temp_hardlink_from_pre_unlink_crash(
    project_root: Path,
) -> None:
    target = project_root / "ledger" / "objects" / "recover-link.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"same")
    orphan = target.parent / f".{target.name}.orphan"
    os.link(target, orphan)
    assert target.stat().st_nlink == 2

    assert publish_immutable(target, b"same") is False
    assert target.stat().st_nlink == 1
    assert not orphan.exists()
