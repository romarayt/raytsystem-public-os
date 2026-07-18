from __future__ import annotations

import base64
import json
import sys
from typing import Any

try:
    import resource
except ImportError:  # Windows: POSIX rlimits are unavailable; caller enforces timeout/size caps.
    resource = None  # type: ignore[assignment]

from raytsystem.codegraph.detect import DetectedFile
from raytsystem.codegraph.extract import extract_file, validate_file_extraction
from raytsystem.contracts import canonical_json_bytes, sha256_hex


def _positive_int(payload: dict[str, Any], key: str, maximum: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(key)
    return value


def _apply_limits(timeout_seconds: int) -> None:
    if resource is None:
        return
    limits = (
        (resource.RLIMIT_CPU, timeout_seconds + 1),
        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 32),
        (resource.RLIMIT_AS, 1536 * 1024 * 1024),
    )
    for resource_id, maximum in limits:
        try:
            resource.setrlimit(resource_id, (maximum, maximum))
        except (OSError, ValueError):
            continue


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(48 * 1024 * 1024)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload")
        timeout_seconds = _positive_int(payload, "timeout_seconds", 120)
        max_nodes = _positive_int(payload, "max_nodes", 100_000)
        max_edges = _positive_int(payload, "max_edges", 400_000)
        _apply_limits(timeout_seconds)
        data = base64.b64decode(str(payload["data"]), validate=True)
        if sha256_hex(data) != payload.get("content_sha256"):
            raise ValueError("hash")
        file = DetectedFile(
            path=str(payload["path"]),
            data=data,
            content_sha256=str(payload["content_sha256"]),
            size_bytes=_positive_int(payload, "size_bytes", 16 * 1024 * 1024),
            mtime_ns=_positive_int(payload, "mtime_ns", 2**63 - 1),
            language=str(payload["language"]),
        )
        if file.size_bytes != len(data):
            raise ValueError("size")
        extraction = extract_file(file)
        validate_file_extraction(
            extraction,
            file,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        output = canonical_json_bytes(extraction.to_dict())
        if len(output) > 64 * 1024 * 1024:
            raise ValueError("output")
        sys.stdout.buffer.write(output)
        return 0
    except BaseException:
        sys.stderr.write("code_graph_worker_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
