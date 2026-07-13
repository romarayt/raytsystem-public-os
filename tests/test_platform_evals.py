"""Deterministic evaluation laboratory: assertions, baselines, comparisons, safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import EvalBaseline, canonical_json_bytes, sha256_hex
from raytsystem.contracts.evaluation import (
    EvalAssertion,
    EvalAssertionType,
    EvalCase,
    EvalJudge,
    EvalSuite,
)
from raytsystem.evals import EvalError, EvalObservation, EvalService
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only

pytestmark = pytest.mark.filterwarnings("error")


def _assertion(
    assertion_id: str,
    assertion_type: EvalAssertionType,
    *,
    target: str = "result_text",
    expected: object = None,
) -> EvalAssertion:
    return EvalAssertion(
        assertion_id=assertion_id,
        assertion_type=assertion_type,
        target=target,
        expected=expected,
    )


def _case(assertions: tuple[EvalAssertion, ...], **overrides: object) -> EvalCase:
    payload: dict[str, object] = {
        "case_id": "case_platform_eval",
        "name": "Platform eval case",
        "task_fixture": "evals/platform/fixture.json",
        "repository_snapshot_sha256": "0" * 64,
        "agent_configuration_sha256": "1" * 64,
        "runtime_id": "runtime_deterministic",
        "instruction_hashes": {},
        "skill_hashes": {},
        "assertions": assertions,
    }
    payload.update(overrides)
    return EvalCase.model_validate(payload)


def _suite(case: EvalCase, *, enabled: bool = True) -> EvalSuite:
    return EvalSuite(
        suite_id="suite_platform_eval",
        name="Platform eval suite",
        version="1.0.0",
        dataset_id="dataset_platform_eval",
        target_ids=("target_platform",),
        case_ids=(case.case_id,),
        manifest_sha256="2" * 64,
        enabled=enabled,
    )


def _run(service: EvalService, suite: EvalSuite, case: EvalCase, observation: EvalObservation):
    return service.run_case(
        suite,
        case,
        observation,
        workspace_id="workspace_test",
        target_id="target_platform",
    )


def test_deterministic_assertion_matrix(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    (root / "artifact.txt").write_bytes(b"artifact-bytes")
    assertions = (
        _assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="hello"),
        _assertion("a_contains", EvalAssertionType.CONTAINS, expected="ell"),
        _assertion("a_regex", EvalAssertionType.REGEX, expected=r"^hel+o$"),
        _assertion(
            "a_schema",
            EvalAssertionType.JSON_SCHEMA,
            target="json_value",
            expected={"type": "object", "required": ["ok"], "properties": {"ok": {"const": True}}},
        ),
        _assertion("a_file", EvalAssertionType.FILE_EXISTS, target="artifact.txt"),
        _assertion(
            "a_hash",
            EvalAssertionType.FILE_HASH,
            target="artifact.txt",
            expected=sha256_hex(b"artifact-bytes"),
        ),
        _assertion(
            "a_artifact", EvalAssertionType.ARTIFACT_TYPE, target="report", expected="markdown"
        ),
        _assertion("a_test", EvalAssertionType.TEST_RESULT, target="pytest", expected=True),
        _assertion("a_exit", EvalAssertionType.COMMAND_EXIT_STATUS, target="lint", expected=0),
        _assertion("a_citation", EvalAssertionType.CITATION_EXISTS, target="cit_1"),
        _assertion("a_source", EvalAssertionType.SOURCE_LOCATION_EXISTS, target="loc_1"),
        _assertion(
            "a_transition", EvalAssertionType.TASK_TRANSITION, target="task_1", expected="done"
        ),
        _assertion("a_approval", EvalAssertionType.APPROVAL_COMPLIANCE, target="apr_1"),
        _assertion("a_forbidden", EvalAssertionType.FORBIDDEN_ACTION_ABSENT, target="publish"),
        _assertion("a_budget", EvalAssertionType.BUDGET_NOT_EXCEEDED, target="budget"),
        _assertion("a_secret", EvalAssertionType.NO_SECRET_LEAK, target="all"),
        _assertion("a_protected", EvalAssertionType.NO_PROTECTED_PATH_MODIFICATION, target="paths"),
    )
    case = _case(assertions, token_budget=100)
    observation = EvalObservation(
        text="hello",
        json_value={"ok": True},
        artifact_types={"report": "markdown"},
        test_results={"pytest": True},
        command_exit_statuses={"lint": 0},
        citation_ids=frozenset({"cit_1"}),
        source_location_ids=frozenset({"loc_1"}),
        task_transitions={"task_1": "done"},
        approval_compliance={"apr_1": True},
        actions=frozenset({"read"}),
        tokens_used=10,
        changed_paths=("notes/output.md",),
    )
    run, result = _run(EvalService(root), _suite(case), case, observation)
    assert result.passed and run.state.value == "passed"
    assert set(result.assertion_results) == {item.assertion_id for item in assertions}
    assert result.scores[0].deterministic is True


def test_rerun_is_idempotent_and_identity_bound(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    first_run, first_result = _run(service, suite, case, EvalObservation(text="ok"))
    second_run, second_result = _run(service, suite, case, EvalObservation(text="ok"))
    assert first_run.eval_run_id == second_run.eval_run_id
    assert first_result.result_id == second_result.result_id
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.head("eval_run", first_run.eval_run_id) is not None
        assert len(store.list_heads("eval_run", limit=10)) == 1
        assert store.verify_event_stream(first_run.eval_run_id)


def test_disabled_feature_and_disabled_suite_fail_closed(tmp_path: Path) -> None:
    disabled_root = make_platform_workspace(
        tmp_path / "disabled",
        flag_overrides={"evals_enabled": False, "promptfoo_adapter_enabled": False},
    )
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    with pytest.raises(EvalError, match="disabled"):
        _run(EvalService(disabled_root), _suite(case), case, EvalObservation(text="ok"))
    enabled_root = make_platform_workspace(tmp_path / "enabled")
    with pytest.raises(EvalError, match="Disabled eval suites"):
        _run(
            EvalService(enabled_root),
            _suite(case, enabled=False),
            case,
            EvalObservation(text="ok"),
        )


def test_llm_judge_stays_separate_from_deterministic_assertions() -> None:
    with pytest.raises(ValueError, match="EvalJudge"):
        EvalAssertion(
            assertion_id="a_judge",
            assertion_type=EvalAssertionType.CONTAINS,
            target="result_text",
            expected="x",
            deterministic=False,
        )
    judge = EvalJudge(
        judge_id="judge_local",
        provider="provider_local",
        model="judge-model",
        rubric_sha256="3" * 64,
    )
    assert judge.optional is True and judge.enabled is False
    with pytest.raises(ValueError, match="optional"):
        EvalJudge(
            judge_id="judge_forced",
            provider="provider_local",
            model="judge-model",
            rubric_sha256="3" * 64,
            optional=False,
        )


def test_malicious_eval_fixture_configuration_is_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    malicious = EvalAssertion(
        assertion_id="a_malicious",
        assertion_type=EvalAssertionType.CONTAINS,
        target="result_text",
        expected="x",
        configuration={"assert": [{"type": "javascript", "value": "process.exit(0)"}]},
    )
    case = _case((malicious,))
    with pytest.raises(EvalError, match="execute code"):
        _run(EvalService(root), _suite(case), case, EvalObservation(text="x"))


def test_unsafe_regex_is_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_redos", EvalAssertionType.REGEX, expected="(a+)+$"),))
    with pytest.raises(EvalError, match="regex"):
        _run(EvalService(root), _suite(case), case, EvalObservation(text="aaaa"))


def test_secret_leak_and_protected_path_assertions_fail(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case(
        (
            _assertion("a_secret", EvalAssertionType.NO_SECRET_LEAK, target="all"),
            _assertion(
                "a_protected", EvalAssertionType.NO_PROTECTED_PATH_MODIFICATION, target="paths"
            ),
        )
    )
    observation = EvalObservation(
        text="key AKIA" + "A" * 16,
        changed_paths=("_raw/source.bin",),
    )
    _, result = _run(EvalService(root), _suite(case), case, observation)
    assert result.passed is False
    assert set(result.failed_assertion_ids) == {"a_secret", "a_protected"}


def test_traversal_changed_path_fails_protected_assertion(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case(
        (
            _assertion(
                "a_protected", EvalAssertionType.NO_PROTECTED_PATH_MODIFICATION, target="paths"
            ),
        )
    )
    observation = EvalObservation(changed_paths=("../outside.txt",))
    _, result = _run(EvalService(root), _suite(case), case, observation)
    assert result.passed is False


def _accepted_baseline(root: Path, service: EvalService, suite: EvalSuite, run_id: str):
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        run_record = store.head("eval_run", run_id)
        assert run_record is not None
        result_hashes = {
            str(result_id): store.head("eval_result", str(result_id)).payload_sha256  # type: ignore[union-attr]
            for result_id in run_record.payload["result_ids"]
        }
    aggregate = sha256_hex(canonical_json_bytes(result_hashes))
    approval = store_approval(
        root,
        action="accept_eval_baseline",
        target_id=run_id,
        artifact_sha256=aggregate,
        scope=("eval_baseline",),
    )
    return service.create_baseline(
        suite, run_id, accepted_by="user_local_test", approval_id=approval.approval_id
    )


def test_baseline_requires_exact_manual_approval(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    run, _ = _run(service, suite, case, EvalObservation(text="ok"))
    with pytest.raises(EvalError, match="approval"):
        service.create_baseline(
            suite, run.eval_run_id, accepted_by="user_local_test", approval_id=""
        )
    wrong_scope = store_approval(
        root,
        action="accept_eval_baseline",
        target_id=run.eval_run_id,
        artifact_sha256="4" * 64,
        scope=("eval_baseline",),
    )
    with pytest.raises(EvalError, match="authority"):
        service.create_baseline(
            suite,
            run.eval_run_id,
            accepted_by="user_local_test",
            approval_id=wrong_scope.approval_id,
        )
    baseline = _accepted_baseline(root, service, suite, run.eval_run_id)
    assert baseline.verify_id() and baseline.verify_aggregate()


def test_forged_baseline_is_rejected_on_compare(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    run, _ = _run(service, suite, case, EvalObservation(text="ok"))
    baseline = _accepted_baseline(root, service, suite, run.eval_run_id)
    tampered = dict(baseline.model_dump(mode="json"))
    tampered["accepted_by"] = "user_attacker"
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="eval_baseline",
            record_id=baseline.baseline_id,
            payload=tampered,
            state="accepted",
            expected_revision=1,
        )
    with pytest.raises(EvalError, match="forged or corrupted"):
        service.compare_with_baseline(baseline.baseline_id, run.eval_run_id)


def test_baseline_comparison_detects_regression(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    good_run, _ = _run(service, suite, case, EvalObservation(text="ok"))
    baseline = _accepted_baseline(root, service, suite, good_run.eval_run_id)
    clean = service.compare_with_baseline(baseline.baseline_id, good_run.eval_run_id)
    assert clean.regression is False and clean.added_failures == ()
    bad_run, bad_result = _run(service, suite, case, EvalObservation(text="broken"))
    assert bad_result.passed is False
    regression = service.compare_with_baseline(baseline.baseline_id, bad_run.eval_run_id)
    assert regression.regression is True
    assert regression.added_failures == ("a_exact",)
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        heads = store.list_heads("eval_comparison", state="regression", limit=10)
        assert len(heads) == 1


def test_baseline_cannot_change_automatically(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    run, _ = _run(service, suite, case, EvalObservation(text="ok"))
    baseline = _accepted_baseline(root, service, suite, run.eval_run_id)
    second = service.create_baseline(
        suite,
        run.eval_run_id,
        accepted_by="user_local_test",
        approval_id=baseline.approval_id,
    )
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        original = store.head("eval_baseline", baseline.baseline_id)
        assert original is not None and original.revision == 1
        restored = EvalBaseline.model_validate(original.payload)
    assert restored.verify_id() and restored.verify_aggregate()
    assert second.verify_id() and second.verify_aggregate()
    validated = EvalBaseline.model_validate(baseline.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        validated.aggregate_sha256 = "5" * 64  # type: ignore[misc]
    validated.result_hashes["forged"] = "5" * 64
    assert validated.verify_aggregate() is False


def test_eval_snapshot_lists_runs_without_raw_observations(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    case = _case((_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),))
    suite = _suite(case)
    service = EvalService(root)
    _run(service, suite, case, EvalObservation(text="ok, but with a private token value"))
    snapshot = service.list_runs()
    assert snapshot["state"] == "ready"
    assert snapshot["runs"] and snapshot["cases"] and snapshot["scores"]
    assert snapshot["scores"][0]["judge_kind"] == "deterministic"
    rendered = canonical_json_bytes(snapshot).decode("utf-8")
    assert "private token value" not in rendered
