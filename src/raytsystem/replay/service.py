from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from raytsystem.contracts import (
    ExecutionRecord,
    RecordedSideEffectResult,
    ReplayPlan,
    RunComparison,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.evaluation import EvalResult, EvalRun
from raytsystem.contracts.observability import ReplayMode
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    initialize_platform_store,
    open_platform_store_read_only,
)

# Fork fields must exist on the ExecutionRecord contract; the intersection drops any drift.
_ALLOWED_FORK_FIELDS = frozenset(
    {
        "runtime_id",
        "model",
        "instruction_hashes",
        "skill_hashes",
        "toolset_sha256",
        "token_budget",
        "cost_budget",
        "graph_snapshot_id",
    }
) & frozenset(ExecutionRecord.model_fields)
_RECORDED_BINDING_KEY = "recorded_result_sha256_by_side_effect"


class ReplayError(RuntimeError):
    """A replay would use mutable state, stale authority, or repeat a side effect."""


@dataclass(frozen=True)
class _RunObservations:
    trace: dict[str, Any] | None
    tool_names: tuple[str, ...] | None
    tool_call_count: int | None
    error_codes: frozenset[str] | None
    changed_paths: tuple[str, ...] | None
    eval_scores: dict[str, Decimal] | None
    assertion_results: dict[str, bool] | None
    artifact_hashes: dict[str, str] | None


class ReplayService:
    def __init__(self, root: Path, *, features: FeatureConfig | None = None) -> None:
        self.root = root.resolve()
        self.features = features or load_feature_config(self.root)

    def record_execution(self, record: ExecutionRecord) -> ExecutionRecord:
        self._require_enabled()
        if not record.verify_id():
            raise ReplayError("Execution record ID is not hash-bound")
        with initialize_platform_store(self.root) as store:
            existing = store.head("execution_record", record.run_id)
            rendered_hash = sha256_hex(canonical_json_bytes(record.model_dump(mode="json")))
            if existing is not None:
                if existing.payload_sha256 != rendered_hash:
                    raise ReplayError("Execution record for this run is immutable")
                return record
            store.append_record(
                kind="execution_record",
                record_id=record.run_id,
                payload=record.model_dump(mode="json"),
                state="recorded",
                expected_revision=None,
            )
            store.append_event(
                stream_id=record.run_id,
                aggregate_id=record.run_id,
                event_type="execution_recorded",
                actor_id="raytsystem_kernel",
                payload_schema="execution_record_v1",
                payload={
                    "run_id": record.run_id,
                    "execution_record_id": record.execution_record_id,
                    "trace_id": record.trace_id,
                },
            )
        return record

    def record_side_effect_result(
        self, result: RecordedSideEffectResult
    ) -> RecordedSideEffectResult:
        self._require_enabled()
        if not result.verify_id():
            raise ReplayError("Recorded side-effect result ID is not hash-bound")
        original, _original_hash = self._load_execution(result.original_run_id)
        if result.side_effect_id not in original.side_effect_ids:
            raise ReplayError("Recorded result is not an original side effect")
        record_id = f"{result.original_run_id}:{result.side_effect_id}"
        rendered_hash = sha256_hex(canonical_json_bytes(result.model_dump(mode="json")))
        with initialize_platform_store(self.root) as store:
            existing = store.head("recorded_side_effect", record_id)
            if existing is not None:
                if existing.payload_sha256 != rendered_hash:
                    raise ReplayError("Recorded side-effect result is immutable")
                return result
            store.append_record(
                kind="recorded_side_effect",
                record_id=record_id,
                payload=result.model_dump(mode="json"),
                state="recorded",
                expected_revision=None,
            )
            store.append_event(
                stream_id=result.original_run_id,
                aggregate_id=result.recorded_result_id,
                event_type="side_effect_result_recorded",
                actor_id="raytsystem_replay",
                payload_schema="recorded_side_effect_result_v1",
                payload={
                    "side_effect_id": result.side_effect_id,
                    "recorded_result_id": result.recorded_result_id,
                    "result_sha256": result.result_sha256,
                },
            )
        return result

    def plan_replay(
        self,
        original_run_id: str,
        *,
        new_run_id: str,
        recorded_side_effect_ids: tuple[str, ...] = (),
    ) -> ReplayPlan:
        self._require_enabled()
        original, original_hash = self._load_execution(original_run_id)
        recorded = set(recorded_side_effect_ids)
        side_effects = set(original.side_effect_ids)
        if not recorded.issubset(side_effects):
            raise ReplayError("Recorded side-effect results do not belong to the original run")
        recorded_hashes = self._verify_recorded_results(original.run_id, tuple(sorted(recorded)))
        blocked = tuple(sorted(side_effects - recorded))
        return self._make_plan(
            mode=ReplayMode.REPLAY,
            original=original,
            original_hash=original_hash,
            new_run_id=new_run_id,
            recorded=tuple(sorted(recorded)),
            blocked=blocked,
            differences={},
            recorded_hashes=recorded_hashes,
        )

    def plan_fork(
        self,
        original_run_id: str,
        *,
        new_run_id: str,
        changes: dict[str, Any],
        recorded_side_effect_ids: tuple[str, ...] = (),
    ) -> ReplayPlan:
        self._require_enabled()
        unknown = set(changes) - _ALLOWED_FORK_FIELDS
        if unknown:
            raise ReplayError("Fork attempts to change unsupported fields")
        original, original_hash = self._load_execution(original_run_id)
        original_payload = original.model_dump(mode="json")
        try:
            modified_payload = ExecutionRecord.model_validate(
                original.model_copy(update=changes).model_dump(mode="python")
            ).model_dump(mode="json")
        except ValidationError as error:
            raise ReplayError("Fork changes are invalid for the execution record") from error
        differences: dict[str, Any] = {
            key: {
                "field": key,
                "original": original_payload.get(key),
                "modified": modified_payload.get(key),
            }
            for key in sorted(changes)
            if original_payload.get(key) != modified_payload.get(key)
        }
        recorded = set(recorded_side_effect_ids)
        side_effects = set(original.side_effect_ids)
        if not recorded.issubset(side_effects):
            raise ReplayError("Recorded side-effect results do not belong to the original run")
        recorded_hashes = self._verify_recorded_results(original.run_id, tuple(sorted(recorded)))
        return self._make_plan(
            mode=ReplayMode.FORK,
            original=original,
            original_hash=original_hash,
            new_run_id=new_run_id,
            recorded=tuple(sorted(recorded)),
            blocked=tuple(sorted(side_effects - recorded)),
            differences=differences,
            recorded_hashes=recorded_hashes,
        )

    def stage(self, plan: ReplayPlan, *, actor_id: str = "raytsystem_replay") -> dict[str, Any]:
        self._require_enabled()
        plan = self._validated_plan(plan)
        original, original_hash = self._load_execution(plan.original_run_id)
        if original_hash != plan.original_execution_record_sha256:
            raise ReplayError("Original execution record changed")
        self._assert_plan_authority(plan, original)
        state = "blocked_side_effects" if plan.blocked_side_effect_tool_ids else "ready"
        if original.approval_ids and plan.required_approval_ids:
            state = "approval_required"
        with initialize_platform_store(self.root) as store:
            if store.head("execution_record", plan.new_run_id) is not None:
                raise ReplayError("Replay run ID already exists")
            existing = store.head("replay_plan", plan.replay_plan_id)
            if existing is None:
                store.append_record(
                    kind="replay_plan",
                    record_id=plan.replay_plan_id,
                    payload=plan.model_dump(mode="json"),
                    state=state,
                    expected_revision=None,
                )
                store.append_event(
                    stream_id=plan.replay_plan_id,
                    aggregate_id=plan.replay_plan_id,
                    event_type="replay_staged",
                    actor_id=actor_id,
                    payload_schema="replay_plan_v1",
                    payload={
                        "original_run_id": plan.original_run_id,
                        "new_run_id": plan.new_run_id,
                        "mode": plan.mode.value,
                        "state": state,
                        "old_approvals_transferred": False,
                    },
                )
            return {
                "replay_plan_id": plan.replay_plan_id,
                "state": state,
                "new_run_id": plan.new_run_id,
                "original_run_id": plan.original_run_id,
                "blocked_side_effects": list(plan.blocked_side_effect_tool_ids),
                "old_approvals_transferred": False,
                "snapshot_id": store.snapshot_id(),
            }

    def materialize_execution_record(self, plan: ReplayPlan) -> ExecutionRecord:
        """Create an immutable, side-effect-free record for the future runtime adapter."""

        self._require_enabled()
        plan = self._validated_plan(plan)
        if plan.blocked_side_effect_tool_ids or plan.required_approval_ids:
            raise ReplayError("Replay cannot materialize with blocked effects or approvals")
        original, original_hash = self._load_execution(plan.original_run_id)
        if original_hash != plan.original_execution_record_sha256:
            raise ReplayError("Original execution record changed")
        self._assert_plan_authority(plan, original)
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise ReplayError("Staged replay plan store is unavailable")
        with store:
            staged = store.head("replay_plan", plan.replay_plan_id)
            if (
                staged is None
                or staged.state != "ready"
                or staged.payload_sha256
                != sha256_hex(canonical_json_bytes(plan.model_dump(mode="json")))
            ):
                raise ReplayError("Replay plan is not an immutable ready staged plan")
        payload = original.model_dump(mode="python")
        payload.update(
            {
                "execution_record_id": "xrec_pending",
                "run_id": plan.new_run_id,
                "approval_ids": (),
                "side_effect_ids": (),
                "trace_id": None,
                "result_sha256": None,
                "manifest_path": None,
                "created_at": datetime.now(UTC),
                # The origin stamp replaces the original extensions so stale eval or
                # approval linkage can never carry over into the new run.
                "extensions": {
                    "replay_origin": {
                        "original_run_id": plan.original_run_id,
                        "original_execution_record_sha256": (plan.original_execution_record_sha256),
                        "replay_plan_id": plan.replay_plan_id,
                        "mode": plan.mode.value,
                    }
                },
            }
        )
        for key, difference in plan.differences.items():
            if key not in _ALLOWED_FORK_FIELDS:
                raise ReplayError("Fork attempts to change unsupported fields")
            if (
                not isinstance(difference, dict)
                or difference.get("field") != key
                or "modified" not in difference
            ):
                raise ReplayError("Fork differences are malformed")
            payload[key] = difference["modified"]
        try:
            draft = ExecutionRecord.model_validate(payload)
            record = draft.model_copy(
                update={"execution_record_id": derive_id("xrec", draft.identity_payload())}
            )
        except ValidationError as error:
            raise ReplayError("Fork differences produce an invalid execution record") from error
        if not record.verify_id():
            raise ReplayError("Materialized execution record is not hash-bound")
        return record

    def compare(self, left_run_id: str, right_run_id: str) -> RunComparison:
        self._require_enabled()
        left, left_hash = self._load_execution(left_run_id)
        right, right_hash = self._load_execution(right_run_id)
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise ReplayError("Run comparison store is unavailable")
        with store:
            left_view = _observe_run(store, left)
            right_view = _observe_run(store, right)
        sources: dict[str, str] = {}
        unavailable: list[str] = []
        extensions: dict[str, Any] = {
            "left_execution_record_sha256": left_hash,
            "right_execution_record_sha256": right_hash,
            "left_result_sha256": left.result_sha256,
            "right_result_sha256": right.result_sha256,
            "left_approval_ids": list(left.approval_ids),
            "right_approval_ids": list(right.approval_ids),
        }
        token_delta = 0
        if left_view.trace is not None and right_view.trace is not None:
            token_delta = _trace_tokens(right_view.trace) - _trace_tokens(left_view.trace)
            sources["token_delta"] = "trace_usage"
        elif left.token_budget is not None and right.token_budget is not None:
            token_delta = right.token_budget - left.token_budget
            sources["token_delta"] = "token_budget"
        else:
            unavailable.append("token_delta")
        cost_delta = Decimal("0")
        if left_view.trace is not None and right_view.trace is not None:
            cost_delta = _trace_cost(right_view.trace) - _trace_cost(left_view.trace)
            sources["cost_delta"] = "trace_cost"
        elif left.cost_budget is not None and right.cost_budget is not None:
            cost_delta = right.cost_budget - left.cost_budget
            sources["cost_delta"] = "cost_budget"
        else:
            unavailable.append("cost_delta")
        latency_delta_ms = 0
        left_ms = None if left_view.trace is None else _trace_duration_ms(left_view.trace)
        right_ms = None if right_view.trace is None else _trace_duration_ms(right_view.trace)
        if left_ms is not None and right_ms is not None:
            latency_delta_ms = right_ms - left_ms
            sources["latency_delta_ms"] = "trace_duration"
        else:
            unavailable.append("latency_delta_ms")
        tool_changes: list[str] = []
        if left.toolset_sha256 != right.toolset_sha256:
            tool_changes.append("toolset_changed")
        if (
            left_view.tool_names is not None
            and right_view.tool_names is not None
            and left_view.tool_call_count is not None
            and right_view.tool_call_count is not None
        ):
            tool_changes.extend(sorted(set(left_view.tool_names) ^ set(right_view.tool_names)))
            extensions["tool_call_count_delta"] = (
                right_view.tool_call_count - left_view.tool_call_count
            )
            sources["tool_call_changes"] = "trace_spans"
        else:
            unavailable.append("tool_call_count_delta")
        failure_changes: tuple[str, ...] = ()
        if left_view.error_codes is not None and right_view.error_codes is not None:
            failure_changes = tuple(sorted(left_view.error_codes ^ right_view.error_codes))
            sources["failure_changes"] = "trace_spans"
        else:
            unavailable.append("failure_changes")
        file_changes: tuple[str, ...] = ()
        if left_view.changed_paths is not None and right_view.changed_paths is not None:
            file_changes = tuple(
                sorted(set(left_view.changed_paths) ^ set(right_view.changed_paths))
            )
            sources["file_changes"] = "execution_record_extensions"
        else:
            unavailable.append("file_changes")
        eval_score_deltas: dict[str, Decimal] = {}
        assertion_changes: dict[str, Literal["added", "removed", "changed"]] = {}
        artifact_changes: tuple[str, ...] = ()
        if (
            left_view.eval_scores is not None
            and right_view.eval_scores is not None
            and left_view.assertion_results is not None
            and right_view.assertion_results is not None
            and left_view.artifact_hashes is not None
            and right_view.artifact_hashes is not None
        ):
            shared = sorted(set(left_view.eval_scores) & set(right_view.eval_scores))
            eval_score_deltas = {
                name: right_view.eval_scores[name] - left_view.eval_scores[name] for name in shared
            }
            added_scores = sorted(set(right_view.eval_scores) - set(left_view.eval_scores))
            removed_scores = sorted(set(left_view.eval_scores) - set(right_view.eval_scores))
            if added_scores or removed_scores:
                extensions["eval_score_changes"] = {
                    "added": added_scores,
                    "removed": removed_scores,
                }
            for assertion_id in sorted(
                set(left_view.assertion_results) | set(right_view.assertion_results)
            ):
                in_left = assertion_id in left_view.assertion_results
                in_right = assertion_id in right_view.assertion_results
                if in_left and not in_right:
                    assertion_changes[assertion_id] = "removed"
                elif in_right and not in_left:
                    assertion_changes[assertion_id] = "added"
                elif (
                    left_view.assertion_results[assertion_id]
                    != right_view.assertion_results[assertion_id]
                ):
                    assertion_changes[assertion_id] = "changed"
            artifact_changes = tuple(
                sorted(
                    key
                    for key in set(left_view.artifact_hashes) | set(right_view.artifact_hashes)
                    if left_view.artifact_hashes.get(key) != right_view.artifact_hashes.get(key)
                )
            )
            sources["eval_score_deltas"] = "eval_results"
        else:
            unavailable.extend(("eval_score_deltas", "assertion_changes", "artifact_changes"))
        # Observation-level test results are never persisted, so test_changes stays explicit.
        unavailable.append("test_changes")
        extensions["dimension_sources"] = sources
        extensions["unavailable_dimensions"] = sorted(unavailable)
        try:
            return RunComparison(
                comparison_id=derive_id(
                    "rcmp",
                    {
                        "left_run_id": left_run_id,
                        "right_run_id": right_run_id,
                        "left_record_sha256": left_hash,
                        "right_record_sha256": right_hash,
                    },
                ),
                left_run_id=left_run_id,
                right_run_id=right_run_id,
                eval_score_deltas=eval_score_deltas,
                assertion_changes=assertion_changes,
                token_delta=token_delta,
                cost_delta=cost_delta,
                latency_delta_ms=latency_delta_ms,
                tool_call_changes=tuple(tool_changes),
                file_changes=file_changes,
                test_changes=(),
                artifact_changes=artifact_changes,
                approval_changes=tuple(sorted(set(left.approval_ids) ^ set(right.approval_ids))),
                failure_changes=failure_changes,
                result_changed=left.result_sha256 != right.result_sha256,
                extensions=extensions,
                created_at=datetime.now(UTC),
            )
        except ValidationError as error:
            raise ReplayError("Run comparison is invalid") from error

    def list_plans(self, *, limit: int = 100) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {"snapshot_id": "pview_unavailable", "state": "unavailable", "plans": []}
        with store:
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready" if self.features.enabled("replay_enabled") else "disabled",
                "plans": [
                    record.payload for record in store.list_heads("replay_plan", limit=limit)
                ],
            }

    def _make_plan(
        self,
        *,
        mode: ReplayMode,
        original: ExecutionRecord,
        original_hash: str,
        new_run_id: str,
        recorded: tuple[str, ...],
        blocked: tuple[str, ...],
        differences: dict[str, Any],
        recorded_hashes: dict[str, str],
    ) -> ReplayPlan:
        if new_run_id == original.run_id:
            raise ReplayError("Replay requires a new run ID")
        extensions: dict[str, Any] = (
            {_RECORDED_BINDING_KEY: dict(sorted(recorded_hashes.items()))}
            if recorded_hashes
            else {}
        )
        draft = ReplayPlan.model_construct(
            replay_plan_id="rplan_pending",
            mode=mode,
            original_run_id=original.run_id,
            original_execution_record_sha256=original_hash,
            new_run_id=new_run_id,
            recorded_result_tool_ids=recorded,
            blocked_side_effect_tool_ids=blocked,
            required_approval_ids=_fresh_approval_placeholders(original, new_run_id),
            differences=differences,
            extensions=extensions,
            plan_sha256="0" * 64,
            created_at=datetime.now(UTC),
        )
        plan_hash = sha256_hex(
            canonical_json_bytes(
                draft.model_dump(mode="python", exclude={"plan_sha256", "replay_plan_id"})
            )
        )
        values = draft.model_dump(mode="python")
        values["plan_sha256"] = plan_hash
        values["replay_plan_id"] = derive_id(
            "rplan",
            {
                "original_run_id": original.run_id,
                "new_run_id": new_run_id,
                "mode": mode.value,
                "plan_sha256": plan_hash,
            },
        )
        try:
            return ReplayPlan.model_validate(values)
        except ValidationError as error:
            raise ReplayError("Replay plan is invalid") from error

    def _load_execution(self, run_id: str) -> tuple[ExecutionRecord, str]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise ReplayError("Execution record store is unavailable")
        with store:
            record = store.head("execution_record", run_id)
            if record is None:
                raise ReplayError("Execution record does not exist")
            try:
                execution = ExecutionRecord.model_validate(record.payload)
            except ValidationError as error:
                raise ReplayError("Execution record is invalid") from error
            if not execution.verify_id():
                raise ReplayError("Execution record was forged or corrupted")
            return execution, record.payload_sha256

    @staticmethod
    def _validated_plan(plan: ReplayPlan) -> ReplayPlan:
        try:
            validated = ReplayPlan.model_validate(plan.model_dump(mode="python"))
        except ValidationError as error:
            raise ReplayError("Replay plan is forged or corrupted") from error
        expected_id = derive_id(
            "rplan",
            {
                "original_run_id": validated.original_run_id,
                "new_run_id": validated.new_run_id,
                "mode": validated.mode.value,
                "plan_sha256": validated.plan_sha256,
            },
        )
        if validated.replay_plan_id != expected_id:
            raise ReplayError("Replay plan identity is invalid")
        return validated

    def _assert_plan_authority(self, plan: ReplayPlan, original: ExecutionRecord) -> None:
        recorded = tuple(sorted(plan.recorded_result_tool_ids))
        recorded_hashes = self._verify_recorded_results(original.run_id, recorded)
        expected_blocked = tuple(sorted(set(original.side_effect_ids) - set(recorded)))
        expected_approvals = _fresh_approval_placeholders(original, plan.new_run_id)
        expected_extensions: dict[str, Any] = (
            {_RECORDED_BINDING_KEY: dict(sorted(recorded_hashes.items()))}
            if recorded_hashes
            else {}
        )
        if (
            plan.blocked_side_effect_tool_ids != expected_blocked
            or plan.required_approval_ids != expected_approvals
            or plan.extensions != expected_extensions
        ):
            raise ReplayError("Replay plan attempts to bypass side effects or fresh approvals")

    def _verify_recorded_results(
        self, original_run_id: str, side_effect_ids: tuple[str, ...]
    ) -> dict[str, str]:
        if not side_effect_ids:
            return {}
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise ReplayError("Recorded side-effect result store is unavailable")
        hashes: dict[str, str] = {}
        with store:
            for side_effect_id in side_effect_ids:
                record = store.head("recorded_side_effect", f"{original_run_id}:{side_effect_id}")
                if record is None:
                    raise ReplayError("Recorded side-effect evidence is missing")
                try:
                    result = RecordedSideEffectResult.model_validate(record.payload)
                except ValidationError as error:
                    raise ReplayError("Recorded side-effect evidence is invalid") from error
                if (
                    not result.verify_id()
                    or result.original_run_id != original_run_id
                    or result.side_effect_id != side_effect_id
                ):
                    raise ReplayError("Recorded side-effect evidence was forged")
                hashes[side_effect_id] = result.result_sha256
        return hashes

    def _require_enabled(self) -> None:
        if not self.features.enabled("replay_enabled"):
            raise ReplayError("Replay is disabled")


def _fresh_approval_placeholders(original: ExecutionRecord, new_run_id: str) -> tuple[str, ...]:
    # Placeholders are derived per position, so original approval IDs never enter a plan.
    return tuple(
        derive_id(
            "aprq",
            {
                "original_run_id": original.run_id,
                "new_run_id": new_run_id,
                "sequence": index,
            },
        )
        for index in range(len(original.approval_ids))
    )


def _observe_run(store: PlatformStore, record: ExecutionRecord) -> _RunObservations:
    trace: dict[str, Any] | None = None
    tool_names: tuple[str, ...] | None = None
    tool_call_count: int | None = None
    error_codes: frozenset[str] | None = None
    if record.trace_id is not None:
        head = store.head("trace", record.trace_id)
        if head is not None:
            trace = head.payload
            spans = _trace_spans(store, record.trace_id)
            tool_calls = [span for span in spans if span.get("span_kind") == "tool"]
            tool_names = tuple(
                sorted({str(span["tool_name"]) for span in tool_calls if span.get("tool_name")})
            )
            tool_call_count = len(tool_calls)
            error_codes = frozenset(
                str(span["error_code"]) for span in spans if span.get("error_code")
            )
    evals = _eval_observations(store, record)
    return _RunObservations(
        trace=trace,
        tool_names=tool_names,
        tool_call_count=tool_call_count,
        error_codes=error_codes,
        changed_paths=_record_changed_paths(record),
        eval_scores=None if evals is None else evals[0],
        assertion_results=None if evals is None else evals[1],
        artifact_hashes=None if evals is None else evals[2],
    )


def _eval_observations(
    store: PlatformStore, record: ExecutionRecord
) -> tuple[dict[str, Decimal], dict[str, bool], dict[str, str]] | None:
    linked = record.extensions.get("eval_run_id")
    if linked is None:
        return None
    if not isinstance(linked, str) or not linked:
        raise ReplayError("Execution record eval linkage is invalid")
    head = store.head("eval_run", linked)
    if head is None:
        raise ReplayError("Linked eval run does not exist")
    try:
        run = EvalRun.model_validate(head.payload)
    except ValidationError as error:
        raise ReplayError("Linked eval run is invalid") from error
    scores: dict[str, Decimal] = {}
    assertions: dict[str, bool] = {}
    artifacts: dict[str, str] = {}
    for result_id in run.result_ids:
        result_head = store.head("eval_result", result_id)
        if result_head is None:
            raise ReplayError("Linked eval result is missing")
        try:
            result = EvalResult.model_validate(result_head.payload)
        except ValidationError as error:
            raise ReplayError("Linked eval result is invalid") from error
        for score in result.scores:
            scores[score.name] = scores.get(score.name, Decimal("0")) + score.value
        assertions.update(result.assertion_results)
        artifacts.update(result.artifact_hashes)
    return scores, assertions, artifacts


def _record_changed_paths(record: ExecutionRecord) -> tuple[str, ...] | None:
    raw = record.extensions.get("changed_paths")
    if raw is None:
        return None
    if not isinstance(raw, list | tuple) or not all(isinstance(item, str) for item in raw):
        raise ReplayError("Execution record changed paths are invalid")
    return tuple(raw)


def _trace_tokens(trace: dict[str, Any]) -> int:
    try:
        return int(trace.get("input_tokens", 0)) + int(trace.get("output_tokens", 0))
    except (TypeError, ValueError) as error:
        raise ReplayError("Trace token usage is invalid") from error


def _trace_cost(trace: dict[str, Any]) -> Decimal:
    value = trace.get("actual_cost")
    if value is None:
        value = trace.get("estimated_cost", "0")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ReplayError("Trace cost is invalid") from error


def _trace_duration_ms(trace: dict[str, Any]) -> int | None:
    created = trace.get("created_at")
    completed = trace.get("completed_at")
    if not isinstance(created, str) or not isinstance(completed, str):
        return None
    try:
        delta = datetime.fromisoformat(completed) - datetime.fromisoformat(created)
    except ValueError as error:
        raise ReplayError("Trace timestamps are invalid") from error
    return int(delta.total_seconds() * 1_000)


def _trace_spans(store: PlatformStore, trace_id: str) -> tuple[dict[str, Any], ...]:
    # Bounded pagination keeps span discovery deterministic without unbounded loads.
    matches: list[dict[str, Any]] = []
    offset = 0
    while offset <= 10_000:
        page = store.list_heads("span", limit=500, offset=offset)
        matches.extend(item.payload for item in page if item.payload.get("trace_id") == trace_id)
        if len(page) < 500:
            break
        offset += len(page)
    return tuple(matches)
