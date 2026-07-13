from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import (
    EvalBaseline,
    EvalCase,
    EvalComparison,
    EvalFinding,
    EvalResult,
    EvalRun,
    EvalScore,
    EvalSuite,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.base import validate_relative_path
from raytsystem.contracts.evaluation import EvalAssertion, EvalAssertionType, EvalRunState
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner

_FORBIDDEN_CONFIG_KEYS = frozenset(
    {"javascript", "python", "script", "command", "exec", "shell", "provider", "remote"}
)
_PROTECTED_PREFIXES = (
    "_raw/",
    "ledger/objects/",
    "ledger/generations/",
    "knowledge/claims/",
    "knowledge/entities/",
    "knowledge/sources/",
)


class EvalError(RuntimeError):
    """Deterministic evaluation input or baseline violates safety or integrity."""


@dataclass(frozen=True)
class EvalObservation:
    text: str = ""
    json_value: Any = None
    artifact_types: dict[str, str] = field(default_factory=dict)
    test_results: dict[str, bool] = field(default_factory=dict)
    command_exit_statuses: dict[str, int] = field(default_factory=dict)
    citation_ids: frozenset[str] = frozenset()
    source_location_ids: frozenset[str] = frozenset()
    task_transitions: dict[str, str] = field(default_factory=dict)
    approval_compliance: dict[str, bool] = field(default_factory=dict)
    actions: frozenset[str] = frozenset()
    tokens_used: int = 0
    cost: Decimal = Decimal("0")
    changed_paths: tuple[str, ...] = ()
    secret_scan_payloads: tuple[str, ...] = ()
    result_sha256: str | None = None


class EvalService:
    def __init__(
        self,
        root: Path,
        *,
        scanner: SecretScanner | None = None,
        features: FeatureConfig | None = None,
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()
        self.features = features or load_feature_config(self.root)

    def run_case(
        self,
        suite: EvalSuite,
        case: EvalCase,
        observation: EvalObservation,
        *,
        workspace_id: str,
        target_id: str,
    ) -> tuple[EvalRun, EvalResult]:
        self._require_enabled()
        if not suite.enabled:
            raise EvalError("Disabled eval suites cannot run")
        if case.case_id not in suite.case_ids or target_id not in suite.target_ids:
            raise EvalError("Eval case or target is outside the suite")
        started = time.monotonic_ns()
        assertion_results: dict[str, bool] = {}
        for assertion in case.assertions:
            self._validate_assertion(assertion)
            assertion_results[assertion.assertion_id] = self._evaluate(assertion, case, observation)
        failed = tuple(sorted(key for key, passed in assertion_results.items() if not passed))
        passed_count = sum(assertion_results.values())
        total = len(assertion_results)
        eval_run_id = derive_id(
            "erun",
            {
                "suite_id": suite.suite_id,
                "case_id": case.case_id,
                "target_id": target_id,
                "observation_sha256": sha256_hex(
                    canonical_json_bytes(_observation_identity(observation))
                ),
            },
        )
        existing_store = open_platform_store_read_only(self.root)
        if existing_store is not None:
            with existing_store:
                existing_run = existing_store.head("eval_run", eval_run_id)
                if existing_run is not None:
                    if (
                        existing_run.payload.get("suite_id") != suite.suite_id
                        or existing_run.payload.get("suite_sha256") != suite.manifest_sha256
                        or existing_run.payload.get("target_id") != target_id
                        or existing_run.payload.get("workspace_id") != workspace_id
                    ):
                        raise EvalError("Eval run identity collision")
                    result_ids = existing_run.payload.get("result_ids", [])
                    if not isinstance(result_ids, list) or len(result_ids) != 1:
                        raise EvalError("Stored eval run result set is invalid")
                    existing_result = existing_store.head("eval_result", str(result_ids[0]))
                    if existing_result is None:
                        raise EvalError("Stored eval result is missing")
                    return (
                        EvalRun.model_validate(existing_run.payload),
                        EvalResult.model_validate(existing_result.payload),
                    )
        score = EvalScore(
            score_id=derive_id(
                "escore", {"eval_run_id": eval_run_id, "name": "deterministic_pass_rate"}
            ),
            name="deterministic_pass_rate",
            value=Decimal(passed_count) / Decimal(total),
            maximum=Decimal("1"),
            deterministic=True,
        )
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        result = EvalResult(
            result_id=derive_id(
                "eres",
                {
                    "eval_run_id": eval_run_id,
                    "case_id": case.case_id,
                    "assertions": assertion_results,
                    "score": score.model_dump(mode="json"),
                },
            ),
            eval_run_id=eval_run_id,
            case_id=case.case_id,
            passed=not failed,
            assertion_results=assertion_results,
            scores=(score,),
            failed_assertion_ids=failed,
            duration_ms=duration_ms,
            cost=observation.cost,
            created_at=datetime.now(UTC),
        )
        run = EvalRun(
            eval_run_id=eval_run_id,
            suite_id=suite.suite_id,
            suite_sha256=suite.manifest_sha256,
            target_id=target_id,
            state=EvalRunState.PASSED if result.passed else EvalRunState.FAILED,
            workspace_id=workspace_id,
            result_ids=(result.result_id,),
            deterministic_score_ids=(score.score_id,),
            started_at=result.created_at,
            completed_at=result.created_at,
        )
        with initialize_platform_store(self.root) as store:
            if store.head("eval_run", run.eval_run_id) is not None:
                existing = store.head("eval_result", result.result_id)
                if existing is None or existing.payload_sha256 != sha256_hex(
                    canonical_json_bytes(result.model_dump(mode="json"))
                ):
                    raise EvalError("Eval run identity collision")
                return run, result
            store.append_record(
                kind="eval_result",
                record_id=result.result_id,
                payload=result.model_dump(mode="json"),
                state="passed" if result.passed else "failed",
                expected_revision=None,
            )
            store.append_record(
                kind="eval_run",
                record_id=run.eval_run_id,
                payload=run.model_dump(mode="json"),
                state=run.state.value,
                expected_revision=None,
            )
            store.append_event(
                stream_id=run.eval_run_id,
                aggregate_id=run.eval_run_id,
                event_type="eval_completed",
                actor_id="raytsystem_eval_runner",
                payload_schema="eval_run_v1",
                payload={
                    "eval_run_id": run.eval_run_id,
                    "result_id": result.result_id,
                    "passed": result.passed,
                    "failed_assertion_ids": failed,
                },
            )
        return run, result

    def create_baseline(
        self,
        suite: EvalSuite,
        eval_run_id: str,
        *,
        accepted_by: str,
        approval_id: str,
    ) -> EvalBaseline:
        self._require_enabled()
        if not approval_id:
            raise EvalError("Baseline creation requires an explicit approval")
        with initialize_platform_store(self.root) as store:
            run = store.head("eval_run", eval_run_id)
            if run is None or run.payload.get("suite_id") != suite.suite_id:
                raise EvalError("Baseline run does not belong to the suite")
            if run.payload.get("suite_sha256") != suite.manifest_sha256:
                raise EvalError("Baseline run uses a different suite manifest")
            result_hashes: dict[str, str] = {}
            for result_id in run.payload.get("result_ids", []):
                result = store.head("eval_result", str(result_id))
                if result is None:
                    raise EvalError("Baseline result is missing")
                result_hashes[str(result_id)] = result.payload_sha256
            aggregate_sha256 = sha256_hex(canonical_json_bytes(result_hashes))
            try:
                AuthorityResolver(self.root).require_approval(
                    approval_id,
                    action="accept_eval_baseline",
                    target_id=eval_run_id,
                    artifact_sha256=aggregate_sha256,
                    required_scope=frozenset({"eval_baseline"}),
                )
            except AuthorityError as error:
                raise EvalError("Baseline approval authority is invalid") from error
            created_at = datetime.now(UTC)
            draft = EvalBaseline(
                baseline_id="ebase_pending",
                suite_id=suite.suite_id,
                suite_sha256=suite.manifest_sha256,
                eval_run_id=eval_run_id,
                result_hashes=result_hashes,
                aggregate_sha256=aggregate_sha256,
                accepted_by=accepted_by,
                approval_id=approval_id,
                created_at=created_at,
            )
            baseline = draft.model_copy(
                update={"baseline_id": derive_id("ebase", draft.identity_payload())}
            )
            if not baseline.verify_id() or not baseline.verify_aggregate():
                raise EvalError("Baseline identity is invalid")
            existing = store.head("eval_baseline", baseline.baseline_id)
            if existing is not None:
                if existing.payload_sha256 != sha256_hex(
                    canonical_json_bytes(baseline.model_dump(mode="json"))
                ):
                    raise EvalError("Baseline identity collision")
                return baseline
            store.append_record(
                kind="eval_baseline",
                record_id=baseline.baseline_id,
                payload=baseline.model_dump(mode="json"),
                state="accepted",
                expected_revision=None,
            )
            store.append_event(
                stream_id=baseline.baseline_id,
                aggregate_id=baseline.baseline_id,
                event_type="baseline_accepted",
                actor_id=accepted_by,
                payload_schema="eval_baseline_v1",
                payload={
                    "baseline_id": baseline.baseline_id,
                    "eval_run_id": eval_run_id,
                    "approval_id": approval_id,
                },
            )
            return baseline

    def compare_with_baseline(
        self,
        baseline_id: str,
        candidate_eval_run_id: str,
    ) -> EvalComparison:
        self._require_enabled()
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise EvalError("Eval store is unavailable")
        with store:
            baseline_record = store.head("eval_baseline", baseline_id)
            candidate_record = store.head("eval_run", candidate_eval_run_id)
            if baseline_record is None or candidate_record is None:
                raise EvalError("Baseline or candidate eval run is missing")
            try:
                baseline = EvalBaseline.model_validate(baseline_record.payload)
            except Exception as error:
                raise EvalError("Baseline contract is invalid") from error
            if not baseline.verify_id() or not baseline.verify_aggregate():
                raise EvalError("Baseline was forged or corrupted")
            if (
                candidate_record.payload.get("suite_id") != baseline.suite_id
                or candidate_record.payload.get("suite_sha256") != baseline.suite_sha256
            ):
                raise EvalError("Candidate eval run does not match the baseline suite")
            for result_id, expected_hash in baseline.result_hashes.items():
                result = store.head("eval_result", result_id)
                if result is None or result.payload_sha256 != expected_hash:
                    raise EvalError("Baseline result hashes no longer verify")
            baseline_failures = self._failure_set(store, baseline.eval_run_id)
            candidate_failures = self._failure_set(store, candidate_eval_run_id)
            added = tuple(sorted(candidate_failures - baseline_failures))
            resolved = tuple(sorted(baseline_failures - candidate_failures))
            comparison = EvalComparison(
                comparison_id=derive_id(
                    "ecmp",
                    {
                        "baseline_id": baseline_id,
                        "candidate_eval_run_id": candidate_eval_run_id,
                        "added": added,
                        "resolved": resolved,
                    },
                ),
                baseline_id=baseline_id,
                candidate_eval_run_id=candidate_eval_run_id,
                added_failures=added,
                resolved_failures=resolved,
                regression=bool(added),
                created_at=datetime.now(UTC),
            )
        with initialize_platform_store(self.root) as writer:
            existing = writer.head("eval_comparison", comparison.comparison_id)
            if existing is not None:
                return EvalComparison.model_validate(existing.payload)
            writer.append_record(
                kind="eval_comparison",
                record_id=comparison.comparison_id,
                payload=comparison.model_dump(mode="json"),
                state="regression" if comparison.regression else "clean",
                expected_revision=None,
            )
            writer.append_event(
                stream_id=comparison.baseline_id,
                aggregate_id=comparison.comparison_id,
                event_type="eval_baseline_compared",
                actor_id="raytsystem_eval_runner",
                payload_schema="eval_comparison_v1",
                payload={
                    "comparison_id": comparison.comparison_id,
                    "candidate_eval_run_id": candidate_eval_run_id,
                    "regression": comparison.regression,
                },
            )
        if comparison.regression:
            self._notify_regression(comparison)
        return comparison

    def _notify_regression(self, comparison: EvalComparison) -> None:
        # The inbox is a side channel: a disabled or failing inbox must never
        # block the comparison record itself.
        from raytsystem.contracts.workflows import NotificationType
        from raytsystem.notifications import NotificationError, NotificationService

        try:
            NotificationService(self.root, features=self.features).emit(
                NotificationType.EVAL_REGRESSION,
                severity="high",
                related_object_id=comparison.candidate_eval_run_id,
                actor_id="raytsystem_eval_runner",
                dedup_key=f"eval_regression:{comparison.baseline_id}",
                payload={
                    "comparison_id": comparison.comparison_id,
                    "baseline_id": comparison.baseline_id,
                    "added_failures": list(comparison.added_failures),
                },
                related_kind="eval_comparison",
            )
        except NotificationError:
            return

    def reject_regression(
        self,
        comparison_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> EvalFinding:
        self._require_enabled()
        if not actor_id or not reason:
            raise EvalError("Regression rejection requires an actor and a reason")
        with initialize_platform_store(self.root) as store:
            comparison_record = store.head("eval_comparison", comparison_id)
            if comparison_record is None:
                raise EvalError("Eval comparison is missing")
            try:
                comparison = EvalComparison.model_validate(comparison_record.payload)
            except Exception as error:
                raise EvalError("Eval comparison contract is invalid") from error
            if not comparison.regression:
                raise EvalError("Only regression comparisons can be rejected")
            finding_id = derive_id(
                "efind", {"comparison_id": comparison.comparison_id, "code": "regression_rejected"}
            )
            existing = store.head("eval_finding", finding_id)
            if existing is not None:
                return EvalFinding.model_validate(existing.payload)
            finding = EvalFinding(
                finding_id=finding_id,
                eval_run_id=comparison.candidate_eval_run_id,
                severity="high",
                code="regression_rejected",
                message=reason,
                created_at=datetime.now(UTC),
            )
            store.append_record(
                kind="eval_finding",
                record_id=finding.finding_id,
                payload=finding.model_dump(mode="json"),
                state="rejected",
                expected_revision=None,
            )
            store.append_event(
                stream_id=comparison.comparison_id,
                aggregate_id=finding.finding_id,
                event_type="eval_regression_rejected",
                actor_id=actor_id,
                payload_schema="eval_finding_v1",
                payload={
                    "finding_id": finding.finding_id,
                    "comparison_id": comparison.comparison_id,
                    "candidate_eval_run_id": comparison.candidate_eval_run_id,
                    "reason": reason,
                },
            )
        return finding

    def list_runs(self, *, limit: int = 100) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {"snapshot_id": "pview_unavailable", "state": "unavailable", "runs": []}
        with store:
            runs = [record.payload for record in store.list_heads("eval_run", limit=limit)]
            results = [record.payload for record in store.list_heads("eval_result", limit=limit)]
            suites: dict[str, dict[str, Any]] = {}
            for run in runs:
                suite_id = str(run.get("suite_id", "unknown_suite"))
                suite = suites.setdefault(
                    suite_id,
                    {
                        "suite_id": suite_id,
                        "suite_sha256": run.get("suite_sha256"),
                        "run_count": 0,
                    },
                )
                suite["run_count"] = int(suite["run_count"]) + 1
            cases = [
                {
                    "case_id": result.get("case_id"),
                    "eval_run_id": result.get("eval_run_id"),
                    "passed": result.get("passed"),
                    "failed_assertion_ids": result.get("failed_assertion_ids", []),
                    "duration_ms": result.get("duration_ms"),
                    "cost": result.get("cost"),
                }
                for result in results
            ]
            scores = [
                dict(score)
                | {
                    "eval_run_id": result.get("eval_run_id"),
                    "case_id": result.get("case_id"),
                    "judge_kind": "deterministic"
                    if score.get("deterministic") is True
                    else "llm_judge",
                }
                for result in results
                for score in result.get("scores", [])
                if isinstance(score, dict)
            ]
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready" if self.features.enabled("evals_enabled") else "disabled",
                "suites": list(suites.values()),
                "cases": cases,
                "runs": runs,
                "results": results,
                "scores": scores,
                "baselines": [
                    record.payload for record in store.list_heads("eval_baseline", limit=limit)
                ],
                "comparisons": [
                    record.payload for record in store.list_heads("eval_comparison", limit=limit)
                ],
            }

    def _evaluate(
        self,
        assertion: EvalAssertion,
        case: EvalCase,
        observation: EvalObservation,
    ) -> bool:
        kind = assertion.assertion_type
        expected = assertion.expected
        if kind is EvalAssertionType.EXACT_MATCH:
            return observation.text == str(expected)
        if kind is EvalAssertionType.CONTAINS:
            return str(expected) in observation.text
        if kind is EvalAssertionType.REGEX:
            pattern = str(expected)
            if len(pattern) > 512 or _unsafe_regex(pattern):
                raise EvalError("Unsafe or oversized regex assertion")
            return re.search(pattern, observation.text) is not None
        if kind is EvalAssertionType.JSON_SCHEMA:
            return _validate_json_schema_subset(observation.json_value, expected)
        if kind is EvalAssertionType.FILE_EXISTS:
            return self._safe_file(assertion.target) is not None
        if kind is EvalAssertionType.FILE_HASH:
            data = self._safe_file(assertion.target)
            return data is not None and sha256_hex(data) == str(expected)
        if kind is EvalAssertionType.ARTIFACT_TYPE:
            return observation.artifact_types.get(assertion.target) == str(expected)
        if kind is EvalAssertionType.TEST_RESULT:
            return observation.test_results.get(assertion.target) is bool(expected)
        if kind is EvalAssertionType.COMMAND_EXIT_STATUS:
            return observation.command_exit_statuses.get(assertion.target) == int(expected)
        if kind is EvalAssertionType.CITATION_EXISTS:
            return assertion.target in observation.citation_ids
        if kind is EvalAssertionType.SOURCE_LOCATION_EXISTS:
            return assertion.target in observation.source_location_ids
        if kind is EvalAssertionType.TASK_TRANSITION:
            return observation.task_transitions.get(assertion.target) == str(expected)
        if kind is EvalAssertionType.APPROVAL_COMPLIANCE:
            return observation.approval_compliance.get(assertion.target) is True
        if kind is EvalAssertionType.FORBIDDEN_ACTION_ABSENT:
            return assertion.target not in observation.actions
        if kind is EvalAssertionType.BUDGET_NOT_EXCEEDED:
            return (case.token_budget is None or observation.tokens_used <= case.token_budget) and (
                case.cost_budget is None or observation.cost <= case.cost_budget
            )
        if kind is EvalAssertionType.NO_SECRET_LEAK:
            try:
                rendered = canonical_json_bytes(
                    {
                        "text": observation.text,
                        "json_value": observation.json_value,
                        "artifact_types": observation.artifact_types,
                        "test_results": observation.test_results,
                        "command_exit_statuses": observation.command_exit_statuses,
                        "task_transitions": observation.task_transitions,
                        "approval_compliance": observation.approval_compliance,
                        "secret_scan_payloads": observation.secret_scan_payloads,
                    }
                )
            except (TypeError, ValueError) as error:
                raise EvalError("Eval observation is not canonical") from error
            return not self.scanner.scan(rendered).blocks_processing
        if kind is EvalAssertionType.NO_PROTECTED_PATH_MODIFICATION:
            for path in observation.changed_paths:
                try:
                    normalized = validate_relative_path(path)
                except ValueError:
                    return False
                if any(
                    normalized == prefix.removesuffix("/") or normalized.startswith(prefix)
                    for prefix in _PROTECTED_PREFIXES
                ):
                    return False
            return True
        raise EvalError("Unsupported deterministic assertion")

    def _validate_assertion(self, assertion: EvalAssertion) -> None:
        forbidden = {key.replace("_", "").casefold() for key in _FORBIDDEN_CONFIG_KEYS}
        if _contains_forbidden_configuration(assertion.configuration, forbidden=forbidden):
            raise EvalError("Eval assertion attempts to execute code or use a provider")

    def _safe_file(self, relative: str) -> bytes | None:
        try:
            return read_regular_file(self.root, relative, max_bytes=16 * 1024 * 1024).data
        except (OSError, PathPolicyError):
            return None

    @staticmethod
    def _failure_set(store: Any, eval_run_id: str) -> set[str]:
        run = store.head("eval_run", eval_run_id)
        if run is None:
            raise EvalError("Eval run is missing")
        failures: set[str] = set()
        for result_id in run.payload.get("result_ids", []):
            result = store.head("eval_result", str(result_id))
            if result is None:
                raise EvalError("Eval result is missing")
            failures.update(str(value) for value in result.payload.get("failed_assertion_ids", []))
        return failures

    def _require_enabled(self) -> None:
        if not self.features.enabled("evals_enabled"):
            raise EvalError("Deterministic evals are disabled")


def _unsafe_regex(pattern: str) -> bool:
    return bool(re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern) or "(?" in pattern)


def _contains_forbidden_configuration(value: Any, *, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in forbidden:
                return True
            if (
                normalized in {"type", "provider", "method"}
                and isinstance(item, str)
                and item.split(":", maxsplit=1)[0].replace("_", "").casefold() in forbidden
            ):
                return True
            if _contains_forbidden_configuration(item, forbidden=forbidden):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_configuration(item, forbidden=forbidden) for item in value)
    return False


def _validate_json_schema_subset(value: Any, schema: Any) -> bool:
    if not isinstance(schema, dict) or len(canonical_json_bytes(schema)) > 16_384:
        raise EvalError("JSON Schema assertion must be a bounded object")
    if set(schema) - {"type", "required", "properties", "items", "enum", "const"}:
        raise EvalError("JSON Schema assertion uses an unsupported keyword")
    expected_type = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": int | float,
        "boolean": bool,
        "null": type(None),
    }
    if expected_type is not None:
        expected_python = types.get(expected_type)
        if expected_python is None or not isinstance(value, expected_python):
            return False
        if expected_type == "integer" and isinstance(value, bool):
            return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and (not isinstance(schema["enum"], list) or value not in schema["enum"]):
        return False
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise EvalError("JSON Schema required must be a string list")
        if any(item not in value for item in required):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise EvalError("JSON Schema properties must be an object")
        for key, child_schema in properties.items():
            if key in value and not _validate_json_schema_subset(value[key], child_schema):
                return False
    if isinstance(value, list) and "items" in schema:
        return all(_validate_json_schema_subset(item, schema["items"]) for item in value)
    return True


def _observation_identity(observation: EvalObservation) -> dict[str, Any]:
    return {
        "text_sha256": sha256_hex(observation.text.encode("utf-8")),
        "json_sha256": sha256_hex(canonical_json_bytes(observation.json_value)),
        "artifact_types": observation.artifact_types,
        "test_results": observation.test_results,
        "command_exit_statuses": observation.command_exit_statuses,
        "citation_ids": sorted(observation.citation_ids),
        "source_location_ids": sorted(observation.source_location_ids),
        "task_transitions": observation.task_transitions,
        "approval_compliance": observation.approval_compliance,
        "actions": sorted(observation.actions),
        "tokens_used": observation.tokens_used,
        "cost": observation.cost,
        "changed_paths": observation.changed_paths,
        "secret_payload_hashes": [
            sha256_hex(value.encode("utf-8")) for value in observation.secret_scan_payloads
        ],
        "result_sha256": observation.result_sha256,
    }
