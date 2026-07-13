from __future__ import annotations

from datetime import UTC, datetime

from raytsystem.contracts.execution import ExecutionSessionStatus, ExecutionUsage
from raytsystem.execution.sessions import (
    SessionCompatibilityInput,
    add_usage,
    create_session,
    resolve_session,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _compatibility(**updates: object) -> SessionCompatibilityInput:
    values: dict[str, object] = {
        "runtime_adapter_id": "adapter_fake",
        "runtime_adapter_sha256": HASH_A,
        "provider": "fake",
        "model": None,
        "task_id": "task_example",
        "employee_id": "employee_example",
        "employee_configuration_revision": HASH_A,
        "workspace_id": "workspace_example",
        "workspace_manifest_sha256": HASH_A,
        "repository_commit": "1" * 40,
        "graph_snapshot_id": "cgraph_example",
        "graph_fingerprint": HASH_A,
        "context_snapshot_sha256": HASH_A,
        "policy_sha256": HASH_A,
        "instruction_bundle_sha256": HASH_A,
    }
    values.update(updates)
    return SessionCompatibilityInput(**values)  # type: ignore[arg-type]


def test_unchanged_session_is_resumable() -> None:
    compatibility = _compatibility()
    session = create_session(compatibility, started_at=NOW)

    resolution = resolve_session(session, compatibility)

    assert resolution.compatible
    assert resolution.reason_code is None


def test_changed_graph_or_policy_fingerprint_starts_new_session() -> None:
    original = _compatibility()
    session = create_session(original, started_at=NOW)
    changed_graph = _compatibility(graph_snapshot_id="cgraph_new")
    changed_policy = _compatibility(policy_sha256=HASH_B)

    graph_resolution = resolve_session(session, changed_graph)
    policy_resolution = resolve_session(session, changed_policy)

    assert not graph_resolution.compatible
    assert graph_resolution.reason_code == "graph_changed"
    assert not policy_resolution.compatible
    assert policy_resolution.reason_code == "compatibility_fingerprint_changed"


def test_completed_session_cannot_resume() -> None:
    compatibility = _compatibility()
    session = create_session(compatibility, started_at=NOW).model_copy(
        update={"status": ExecutionSessionStatus.COMPLETED}
    )

    resolution = resolve_session(session, compatibility)

    assert not resolution.compatible
    assert resolution.reason_code == "session_not_resumable"


def test_usage_totals_are_lossless() -> None:
    combined = add_usage(
        ExecutionUsage(input_tokens=10, output_tokens=4, estimated_cost_micros=20),
        ExecutionUsage(input_tokens=3, cached_tokens=7, actual_cost_micros=15),
    )

    assert combined.input_tokens == 13
    assert combined.output_tokens == 4
    assert combined.cached_tokens == 7
    assert combined.estimated_cost_micros == 20
    assert combined.actual_cost_micros == 15
