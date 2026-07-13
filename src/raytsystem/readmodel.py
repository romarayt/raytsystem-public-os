from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.contracts import RunState, RunSummary, sha256_hex
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.storage import IntegrityError


class ReadModelError(IntegrityError):
    """A committed object cannot be represented by the public read model."""


def load_run_summaries(
    root: Path,
    manifests: tuple[dict[str, Any], ...],
) -> tuple[RunSummary, ...]:
    summaries: list[RunSummary] = []
    for manifest in manifests:
        run_id = manifest.get("run_id")
        operation_type = manifest.get("operation_type")
        state = manifest.get("state")
        created_at = manifest.get("created_at")
        updated_at = manifest.get("updated_at")
        generation_id = manifest.get("generation_id")
        semantic_noop = manifest.get("semantic_noop", False)
        if (
            not isinstance(run_id, str)
            or not isinstance(operation_type, str)
            or not isinstance(state, str)
            or not isinstance(created_at, str)
            or not isinstance(updated_at, str)
            or (generation_id is not None and not isinstance(generation_id, str))
            or not isinstance(semantic_noop, bool)
        ):
            raise ReadModelError("Committed run manifest is missing public summary fields")
        try:
            normalized_state = RunState(state)
        except ValueError as error:
            raise ReadModelError("Committed run state is invalid") from error
        relative = f"ops/runs/{run_id}/manifest.json"
        try:
            manifest_bytes = read_regular_file(root, relative, max_bytes=4 * 1024 * 1024).data
        except (OSError, PathPolicyError) as error:
            raise ReadModelError("Committed run manifest is missing or unsafe") from error
        try:
            summary = RunSummary(
                run_id=run_id,
                operation_type=operation_type,
                state=normalized_state.value,
                generation_id=generation_id,
                semantic_noop=semantic_noop,
                created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
                updated_at=datetime.fromisoformat(updated_at.replace("Z", "+00:00")),
                manifest_sha256=sha256_hex(manifest_bytes),
            )
        except (ValidationError, ValueError) as error:
            raise ReadModelError("Committed run summary is invalid") from error
        summaries.append(summary)
    return tuple(sorted(summaries, key=lambda item: (item.updated_at, item.run_id), reverse=True))
