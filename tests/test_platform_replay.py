"""Replay/fork/compare laboratory: no side effects, no approval carry-over."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from platform_helpers import make_platform_workspace
from raytsystem.contracts import (
    ExecutionRecord,
    RecordedSideEffectResult,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.evaluation import EvalResult, EvalRun, EvalRunState, EvalScore
from raytsystem.contracts.observability import SpanKind, SpanStatus, TraceRecord, TraceSpan
from raytsystem.platform_store import initialize_platform_store
from raytsystem.replay import ReplayError, ReplayService

pytestmark = pytest.mark.filterwarnings("error")

_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _execution_record(run_id: str, **overrides: Any) -> ExecutionRecord:
    payload: dict[str, Any] = {
        "execution_record_id": "xrec_pending",
        "run_id": run_id,
        "repository_snapshot_sha256": "a" * 64,
        "graph_snapshot_id": "gsnap_original",
        "knowledge_generation_id": "kgen_original",
        "instruction_hashes": {"inst_root": "b" * 64},
        "skill_hashes": {"skill_review": "c" * 64},
        "policy_sha256": "d" * 64,
        "toolset_sha256": "e" * 64,
        "runtime_configuration_sha256": "f" * 64,
        "runtime_id": "runtime_claude",
        "model": "model-alpha",
        "token_budget": 1_000,
        "cost_budget": Decimal("5"),
        "result_sha256": "1" * 64,
        "created_at": _NOW,
    }
    payload.update(overrides)
    draft = ExecutionRecord.model_validate(payload)
    return draft.model_copy(
        update={"execution_record_id": derive_id("xrec", draft.identity_payload())}
    )


def _recorded_result(run_id: str, side_effect_id: str) -> RecordedSideEffectResult:
    draft = RecordedSideEffectResult.model_validate(
        {
            "recorded_result_id": "rside_pending",
            "original_run_id": run_id,
            "side_effect_id": side_effect_id,
            "invocation_sha256": "2" * 64,
            "result_sha256": "3" * 64,
            "recorded_at": _NOW,
        }
    )
    return draft.model_copy(
        update={"recorded_result_id": derive_id("rside", draft.identity_payload())}
    )


def _seed_trace(
    root: Path,
    *,
    trace_id: str,
    run_id: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: str,
    duration_seconds: int,
    tool_spans: tuple[tuple[str, str | None], ...],
) -> None:
    trace = TraceRecord(
        trace_id=trace_id,
        root_run_id=run_id,
        root_span_id=f"span_root_{run_id}",
        created_at=_NOW,
        completed_at=_NOW + timedelta(seconds=duration_seconds),
        status=SpanStatus.OK,
        span_count=len(tool_spans),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=Decimal(estimated_cost),
    )
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="trace",
            record_id=trace_id,
            payload=trace.model_dump(mode="json"),
            state="ok",
            expected_revision=None,
        )
        for index, (tool_name, error_code) in enumerate(tool_spans, start=1):
            span = TraceSpan(
                trace_id=trace_id,
                span_id=f"span_{run_id}_{index}",
                span_kind=SpanKind.TOOL,
                run_id=run_id,
                workspace_id="workspace_test",
                operation_name="tool_invocation",
                started_at=_NOW,
                tool_name=tool_name,
                error_code=error_code,
            )
            store.append_record(
                kind="span",
                record_id=span.span_id,
                payload=span.model_dump(mode="json"),
                state="unset",
                expected_revision=None,
            )


def _seed_eval_run(
    root: Path,
    *,
    eval_run_id: str,
    scores: dict[str, str],
    assertion_results: dict[str, bool],
    artifact_hashes: dict[str, str],
) -> None:
    result_id = f"{eval_run_id}_result"
    failed = tuple(sorted(key for key, ok in assertion_results.items() if not ok))
    result = EvalResult(
        result_id=result_id,
        eval_run_id=eval_run_id,
        case_id="case_replay_compare",
        passed=not failed,
        assertion_results=assertion_results,
        scores=tuple(
            EvalScore(
                score_id=f"{eval_run_id}_{name}",
                name=name,
                value=Decimal(value),
                maximum=Decimal("1"),
                deterministic=True,
            )
            for name, value in sorted(scores.items())
        ),
        failed_assertion_ids=failed,
        artifact_hashes=artifact_hashes,
        duration_ms=10,
        created_at=_NOW,
    )
    run = EvalRun(
        eval_run_id=eval_run_id,
        suite_id="suite_replay_compare",
        suite_sha256="4" * 64,
        target_id="target_replay",
        state=EvalRunState.FAILED if failed else EvalRunState.PASSED,
        workspace_id="workspace_test",
        result_ids=(result_id,),
        started_at=_NOW,
        completed_at=_NOW,
    )
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="eval_result",
            record_id=result_id,
            payload=result.model_dump(mode="json"),
            state="recorded",
            expected_revision=None,
        )
        store.append_record(
            kind="eval_run",
            record_id=eval_run_id,
            payload=run.model_dump(mode="json"),
            state=run.state.value,
            expected_revision=None,
        )


def test_replayed_stale_approval_is_never_carried_over(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    original = _execution_record(
        "run_original",
        approval_ids=("apr_original_publish",),
        side_effect_ids=("side_publish_post",),
    )
    service.record_execution(original)
    plan = service.plan_replay("run_original", new_run_id="run_replayed")
    rendered = plan.model_dump_json()
    assert "apr_original_publish" not in rendered
    assert plan.required_approval_ids
    assert all(placeholder.startswith("aprq_") for placeholder in plan.required_approval_ids)
    assert plan.blocked_side_effect_tool_ids == ("side_publish_post",)
    staged = service.stage(plan)
    assert staged["state"] == "approval_required"
    assert staged["old_approvals_transferred"] is False
    with pytest.raises(ReplayError, match="blocked effects or approvals"):
        service.materialize_execution_record(plan)


def test_replayed_external_side_effect_requires_recorded_result(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    original = _execution_record("run_original", side_effect_ids=("side_send_email",))
    service.record_execution(original)
    unbound = service.plan_replay("run_original", new_run_id="run_replayed_unbound")
    assert unbound.blocked_side_effect_tool_ids == ("side_send_email",)
    assert service.stage(unbound)["state"] == "blocked_side_effects"
    with pytest.raises(ReplayError, match="blocked effects or approvals"):
        service.materialize_execution_record(unbound)
    with pytest.raises(ReplayError, match="evidence is missing"):
        service.plan_replay(
            "run_original",
            new_run_id="run_replayed_forged",
            recorded_side_effect_ids=("side_send_email",),
        )
    recorded = service.record_side_effect_result(
        _recorded_result("run_original", "side_send_email")
    )
    bound = service.plan_replay(
        "run_original",
        new_run_id="run_replayed_bound",
        recorded_side_effect_ids=("side_send_email",),
    )
    assert bound.blocked_side_effect_tool_ids == ()
    assert bound.recorded_result_tool_ids == ("side_send_email",)
    assert bound.extensions["recorded_result_sha256_by_side_effect"] == {
        "side_send_email": recorded.result_sha256
    }


def test_replay_plan_pins_original_hashes_and_links_new_run(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    original = _execution_record("run_original")
    service.record_execution(original)
    plan = service.plan_replay("run_original", new_run_id="run_replayed")
    expected_hash = sha256_hex(canonical_json_bytes(original.model_dump(mode="json")))
    assert plan.original_execution_record_sha256 == expected_hash
    assert plan.original_run_id == "run_original"
    assert plan.new_run_id == "run_replayed"
    assert service.stage(plan)["state"] == "ready"
    record = service.materialize_execution_record(plan)
    assert record.run_id == "run_replayed"
    assert record.verify_id()
    assert record.repository_snapshot_sha256 == original.repository_snapshot_sha256
    assert record.graph_snapshot_id == original.graph_snapshot_id
    assert record.instruction_hashes == original.instruction_hashes
    assert record.skill_hashes == original.skill_hashes
    assert record.policy_sha256 == original.policy_sha256
    assert record.toolset_sha256 == original.toolset_sha256
    assert record.approval_ids == () and record.side_effect_ids == ()
    origin = record.extensions["replay_origin"]
    assert origin["original_run_id"] == "run_original"
    assert origin["original_execution_record_sha256"] == expected_hash
    assert origin["replay_plan_id"] == plan.replay_plan_id
    assert origin["mode"] == "replay"


def test_fork_produces_structured_diffs_and_rejects_unknown_fields(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    original = _execution_record("run_original")
    service.record_execution(original)
    plan = service.plan_fork(
        "run_original",
        new_run_id="run_forked",
        changes={"runtime_id": "runtime_other", "model": "model-beta", "token_budget": 2_000},
    )
    assert plan.differences == {
        "model": {"field": "model", "original": "model-alpha", "modified": "model-beta"},
        "runtime_id": {
            "field": "runtime_id",
            "original": "runtime_claude",
            "modified": "runtime_other",
        },
        "token_budget": {"field": "token_budget", "original": 1_000, "modified": 2_000},
    }
    with pytest.raises(ReplayError, match="unsupported fields"):
        service.plan_fork(
            "run_original",
            new_run_id="run_forked_bad",
            changes={"retry_policy": {"max_attempts": 3}},
        )
    with pytest.raises(ReplayError, match="unsupported fields"):
        service.plan_fork(
            "run_original",
            new_run_id="run_forked_worse",
            changes={"approval_ids": ("apr_smuggled",)},
        )
    assert service.stage(plan)["state"] == "ready"
    record = service.materialize_execution_record(plan)
    assert record.run_id == "run_forked"
    assert record.runtime_id == "runtime_other"
    assert record.model == "model-beta"
    assert record.token_budget == 2_000
    assert record.extensions["replay_origin"]["mode"] == "fork"


def test_compare_populates_every_recorded_dimension(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    left = _execution_record(
        "run_left",
        trace_id="trace_left",
        approval_ids=("apr_left_only",),
        result_sha256="1" * 64,
        extensions={"eval_run_id": "erun_left", "changed_paths": ["src/a.py"]},
    )
    right = _execution_record(
        "run_right",
        trace_id="trace_right",
        toolset_sha256="9" * 64,
        result_sha256="2" * 64,
        extensions={"eval_run_id": "erun_right", "changed_paths": ["src/a.py", "src/b.py"]},
    )
    service.record_execution(left)
    service.record_execution(right)
    _seed_trace(
        root,
        trace_id="trace_left",
        run_id="run_left",
        input_tokens=100,
        output_tokens=50,
        estimated_cost="1.5",
        duration_seconds=60,
        tool_spans=(("tool_read", None), ("tool_grep", None)),
    )
    _seed_trace(
        root,
        trace_id="trace_right",
        run_id="run_right",
        input_tokens=200,
        output_tokens=100,
        estimated_cost="2.5",
        duration_seconds=90,
        tool_spans=(("tool_read", None), ("tool_write", "err_timeout")),
    )
    _seed_eval_run(
        root,
        eval_run_id="erun_left",
        scores={"score_quality": "0.5"},
        assertion_results={"a_exact": True, "a_old": True},
        artifact_hashes={"artifact_report": "5" * 64},
    )
    _seed_eval_run(
        root,
        eval_run_id="erun_right",
        scores={"score_quality": "0.75", "score_new": "1"},
        assertion_results={"a_exact": False, "a_new": True},
        artifact_hashes={"artifact_report": "6" * 64},
    )
    comparison = service.compare("run_left", "run_right")
    assert comparison.token_delta == 150
    assert comparison.cost_delta == Decimal("1")
    assert comparison.latency_delta_ms == 30_000
    assert comparison.tool_call_changes == ("toolset_changed", "tool_grep", "tool_write")
    assert comparison.file_changes == ("src/b.py",)
    assert comparison.failure_changes == ("err_timeout",)
    assert comparison.approval_changes == ("apr_left_only",)
    assert comparison.result_changed is True
    assert comparison.eval_score_deltas == {"score_quality": Decimal("0.25")}
    assert comparison.assertion_changes == {
        "a_exact": "changed",
        "a_new": "added",
        "a_old": "removed",
    }
    assert comparison.artifact_changes == ("artifact_report",)
    assert comparison.extensions["eval_score_changes"] == {
        "added": ["score_new"],
        "removed": [],
    }
    assert comparison.extensions["tool_call_count_delta"] == 0
    assert comparison.extensions["left_result_sha256"] == "1" * 64
    assert comparison.extensions["right_result_sha256"] == "2" * 64
    assert comparison.extensions["unavailable_dimensions"] == ["test_changes"]
    sources = comparison.extensions["dimension_sources"]
    assert sources["token_delta"] == "trace_usage"
    assert sources["cost_delta"] == "trace_cost"
    assert sources["latency_delta_ms"] == "trace_duration"
    assert sources["file_changes"] == "execution_record_extensions"
    assert sources["eval_score_deltas"] == "eval_results"


def test_compare_marks_missing_dimensions_unavailable(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = ReplayService(root)
    service.record_execution(
        _execution_record("run_bare_left", token_budget=None, cost_budget=None)
    )
    service.record_execution(
        _execution_record("run_bare_right", token_budget=None, cost_budget=None)
    )
    comparison = service.compare("run_bare_left", "run_bare_right")
    assert comparison.token_delta == 0
    assert comparison.cost_delta == Decimal("0")
    assert comparison.latency_delta_ms == 0
    assert comparison.file_changes == ()
    assert comparison.eval_score_deltas == {}
    assert comparison.extensions["unavailable_dimensions"] == [
        "artifact_changes",
        "assertion_changes",
        "cost_delta",
        "eval_score_deltas",
        "failure_changes",
        "file_changes",
        "latency_delta_ms",
        "test_changes",
        "token_delta",
        "tool_call_count_delta",
    ]
    service.record_execution(
        _execution_record("run_budget_right", token_budget=1_500, cost_budget=Decimal("7"))
    )
    service.record_execution(_execution_record("run_budget_left"))
    budgeted = service.compare("run_budget_left", "run_budget_right")
    assert budgeted.token_delta == 500
    assert budgeted.cost_delta == Decimal("2")
    assert budgeted.extensions["dimension_sources"]["token_delta"] == "token_budget"
    assert budgeted.extensions["dimension_sources"]["cost_delta"] == "cost_budget"


def test_disabled_feature_fails_closed_everywhere(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    enabled = ReplayService(root)
    enabled.record_execution(_execution_record("run_original"))
    enabled.record_execution(_execution_record("run_other"))
    plan = enabled.plan_replay("run_original", new_run_id="run_replayed")
    enabled.stage(plan)
    make_platform_workspace(tmp_path, flag_overrides={"replay_enabled": False})
    disabled = ReplayService(root)
    with pytest.raises(ReplayError, match="disabled"):
        disabled.plan_replay("run_original", new_run_id="run_replayed_late")
    with pytest.raises(ReplayError, match="disabled"):
        disabled.plan_fork(
            "run_original",
            new_run_id="run_forked_late",
            changes={"model": "model-beta"},
        )
    with pytest.raises(ReplayError, match="disabled"):
        disabled.materialize_execution_record(plan)
    with pytest.raises(ReplayError, match="disabled"):
        disabled.compare("run_original", "run_other")
    assert disabled.list_plans()["state"] == "disabled"
