from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raytsystem.catalog import CatalogService, CatalogSnapshot
from raytsystem.codegraph.contracts import CodeGraphSnapshot, CodeGraphState, CodeGraphStatus
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.contracts import GraphSnapshot, RunSummary
from raytsystem.corpus import ActiveCorpus
from raytsystem.readmodel import load_run_summaries
from raytsystem.storage import IntegrityError, read_current_generation
from raytsystem.tasking import TaskBoardSnapshot, TaskService
from raytsystem.universe import UniverseService


class SnapshotError(IntegrityError):
    """A cross-plane read snapshot could not be captured consistently."""


@dataclass(frozen=True)
class ReadSnapshot:
    corpus: ActiveCorpus
    tasks: TaskBoardSnapshot
    catalog: CatalogSnapshot
    runs: tuple[RunSummary, ...]
    code: CodeGraphSnapshot | None
    code_object_sha256: str | None
    code_status: CodeGraphStatus
    graph: GraphSnapshot
    loaded_at: datetime


class SnapshotProvider:
    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: float = 0.75,
        catalog_read_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cached: ReadSnapshot | None = None
        self._cached_at = 0.0
        self._catalog_read_guard = catalog_read_guard or nullcontext

    def get(self) -> ReadSnapshot:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at <= self.ttl_seconds:
                return self._cached
            with self._catalog_read_guard():
                snapshot = self._load_consistent()
            self._cached = snapshot
            self._cached_at = time.monotonic()
            return snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    def _load_consistent(self) -> ReadSnapshot:
        for _ in range(3):
            corpus = ActiveCorpus.load(self.root, verify_evidence=True)
            tasks = TaskService(self.root).snapshot()
            catalog = CatalogService(self.root).load()
            runs = load_run_summaries(self.root, corpus.run_manifests)
            code, code_sha256, code_status = self._load_code_plane()
            if read_current_generation(self.root) != corpus.generation.generation_id:
                continue
            if TaskService(self.root).snapshot().generation_id != tasks.generation_id:
                continue
            if CatalogService(self.root).load().catalog_sha256 != catalog.catalog_sha256:
                continue
            _, code_sha256_after, _ = self._load_code_plane()
            if code_sha256_after != code_sha256:
                continue
            graph = UniverseService(self.root).snapshot(
                corpus=corpus,
                tasks=tasks,
                catalog=catalog,
                runs=runs,
                code_snapshot=code,
                code_snapshot_sha256=code_sha256,
                code_graph_state=code_status.state.value,
            )
            return ReadSnapshot(
                corpus=corpus,
                tasks=tasks,
                catalog=catalog,
                runs=runs,
                code=code,
                code_object_sha256=code_sha256,
                code_status=code_status,
                graph=graph,
                loaded_at=datetime.now(UTC),
            )
        raise SnapshotError("State changed repeatedly while capturing the read snapshot")

    def _load_code_plane(
        self,
    ) -> tuple[CodeGraphSnapshot | None, str | None, CodeGraphStatus]:
        projection = CodeGraphProjection(self.root)
        status = projection.status(verify_hashes=False)
        if status.snapshot_id is None:
            return None, None, status
        try:
            snapshot, object_sha256 = projection.current_snapshot_with_sha256()
        except CodeGraphUnavailable:
            return (
                None,
                None,
                CodeGraphStatus(
                    state=CodeGraphState.ERROR,
                    reason="integrity_failed",
                ),
            )
        if snapshot.snapshot_id != status.snapshot_id:
            raise SnapshotError("Code graph pointer changed during snapshot capture")
        return snapshot, object_sha256, status
