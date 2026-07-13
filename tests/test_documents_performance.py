from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from raytsystem.documents.index import DocumentIndex

_RUN = os.environ.get("RAYTSYSTEM_RUN_DOCUMENT_BENCHMARKS") == "1"
_SIZE = int(os.environ.get("RAYTSYSTEM_DOCUMENT_BENCHMARK_SIZE", "100"))
_CYRILLIC = "кириллический"


@pytest.mark.skipif(not _RUN, reason="opt-in large-vault benchmark")
def test_document_index_large_vault_benchmark(tmp_path: Path) -> None:
    if _SIZE not in {100, 10_000, 100_000}:
        raise AssertionError("RAYTSYSTEM_DOCUMENT_BENCHMARK_SIZE must be 100, 10000, or 100000")
    root = _benchmark_workspace(tmp_path, size=_SIZE)
    index = DocumentIndex(root)

    started = time.perf_counter()
    status = index.rebuild()
    rebuild_seconds = time.perf_counter() - started
    assert status["file_count"] == _SIZE

    started = time.perf_counter()
    search = index.search(_CYRILLIC, limit=50)
    search_seconds = time.perf_counter() - started
    assert search["items"]

    hub = next(item for item in index.search('"backlink hub"')["items"])
    started = time.perf_counter()
    backlinks = index.links(
        hub["document_id"],
        backlinks=True,
        limit=min(2_000, max(1, _SIZE - 2)),
    )
    backlinks_seconds = time.perf_counter() - started
    assert len(backlinks["items"]) == min(2_000, max(1, _SIZE - 2))

    print(
        json.dumps(
            {
                "documents": _SIZE,
                "rebuild_seconds": round(rebuild_seconds, 3),
                "search_seconds": round(search_seconds, 3),
                "backlinks_seconds": round(backlinks_seconds, 3),
                "index_bytes": index.path.stat().st_size,
                "large_file_bytes": 5 * 1024 * 1024,
                "max_backlinks_exercised": len(backlinks["items"]),
                "deep_tree_depth": 48,
                "cyrillic_and_long_names": True,
            },
            sort_keys=True,
        )
    )


def _benchmark_workspace(tmp_path: Path, *, size: int) -> Path:
    (tmp_path / "config").mkdir()
    notes = tmp_path / "knowledge" / "manual"
    notes.mkdir(parents=True)
    (tmp_path / "config" / "raytsystem.toml").write_text(
        f"""
[documents]
max_files = {size}
max_file_bytes = 5242880
max_total_bytes = 2147483648
search_page_size = 50
search_timeout_ms = 2000

[[documents.roots]]
id = "manual"
path = "knowledge/manual"
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    deep = notes
    for index in range(48):
        deep /= f"d{index:02d}"
    deep.mkdir(parents=True)

    targets = max(1, size - 2)
    link_count = min(5_000, targets)
    hub_links = " ".join(f"[[Doc-{index:06d}]]" for index in range(link_count))
    (notes / "Hub.md").write_text(
        f"# Backlink hub\n\nbacklink hub {hub_links}\n",
        encoding="utf-8",
    )
    large = f"# Five MiB\n\n{_CYRILLIC}\n".encode()
    large += b"x" * (5 * 1024 * 1024 - len(large))
    long_name = "Очень-длинное-имя-" + "x" * 120 + ".md"
    (deep / long_name).write_bytes(large)
    for index in range(targets):
        (notes / f"Doc-{index:06d}.md").write_text(
            f"# Doc {index}\n\n{_CYRILLIC} benchmark {index} [[Hub]]\n",
            encoding="utf-8",
        )
    return tmp_path
