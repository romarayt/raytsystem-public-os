from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raytsystem.security.paths import read_regular_file


@dataclass(frozen=True)
class CapturedInput:
    relative_path: str
    data: bytes


class LocalFileFetcher:
    name = "local_file"
    version = "1.0.0"

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes

    def fetch(self, source: str | Path) -> CapturedInput:
        result = read_regular_file(self.root, source, max_bytes=self.max_bytes)
        return CapturedInput(relative_path=result.relative_path, data=result.data)


class RemoteFetcherUnavailable(RuntimeError):
    """Remote capture remains disabled until peer-pinned SSRF controls are enabled."""


class DisabledRemoteFetcher:
    """Fail-closed network Fetcher placeholder for the local-only M5a runtime."""

    name = "remote_fetch_disabled"
    version = "1.0.0"
    capabilities: tuple[str, ...] = ()

    def fetch(self, source: str) -> CapturedInput:
        del source
        raise RemoteFetcherUnavailable(
            "Remote capture is unavailable until the peer-pinned SSRF adapter is approved"
        )
