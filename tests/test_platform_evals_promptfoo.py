"""Promptfoo adapter boundary (§22) and regression-rejection governance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import EvalBaseline, canonical_json_bytes, sha256_hex
from raytsystem.contracts.evaluation import EvalAssertion, EvalAssertionType, EvalCase, EvalSuite
from raytsystem.evals import EvalError, EvalObservation, EvalService
from raytsystem.evals.promptfoo import PromptfooAdapter, PromptfooConfigError
from raytsystem.features import load_feature_config
from raytsystem.platform_store import open_platform_store_read_only

pytestmark = pytest.mark.filterwarnings("error")

_CODE_EXECUTING_CONFIGS: tuple[dict[str, Any], ...] = (
    {"prompts": ["p"], "tests": [{"assert": [{"type": "javascript", "value": "return true"}]}]},
    {"prompts": ["p"], "tests": [{"assert": [{"type": "python", "value": "output == 'x'"}]}]},
    {"prompts": ["p"], "tests": [{"assert": [{"type": "javascript:file://check.js"}]}]},
    {"prompts": ["p"], "providers": ["exec: python attack.py"]},
    {"prompts": ["p"], "providers": [{"id": "shell:sh -c id"}]},
    {"prompts": ["p"], "extensions": ["file://hooks.js:setup"]},
    {"prompts": ["p"], "extensions": ["file://hooks.py:setup"]},
    {"prompts": ["p"], "defaultTest": {"options": {"transform": "output.slice(0)"}}},
)
_REMOTE_CONFIGS: tuple[dict[str, Any], ...] = (
    {"prompts": ["p"], "sharing": True},
    {"prompts": ["p"], "remoteGeneration": "on"},
    {"prompts": ["p"], "cloud": {"apiHost": "https://cloud.example"}},
    {"prompts": ["p"], "telemetry": {"enabled": True}},
)


def _adapter(root: Path) -> PromptfooAdapter:
    return PromptfooAdapter(root, features=load_feature_config(root))


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


def _case() -> EvalCase:
    return EvalCase.model_validate(
        {
            "case_id": "case_platform_eval",
            "name": "Platform eval case",
            "task_fixture": "evals/platform/fixture.json",
            "repository_snapshot_sha256": "0" * 64,
            "agent_configuration_sha256": "1" * 64,
            "runtime_id": "runtime_deterministic",
            "instruction_hashes": {},
            "skill_hashes": {},
            "assertions": (_assertion("a_exact", EvalAssertionType.EXACT_MATCH, expected="ok"),),
        }
    )


def _suite(case: EvalCase) -> EvalSuite:
    return EvalSuite(
        suite_id="suite_platform_eval",
        name="Platform eval suite",
        version="1.0.0",
        dataset_id="dataset_platform_eval",
        target_ids=("target_platform",),
        case_ids=(case.case_id,),
        manifest_sha256="2" * 64,
    )


def _run(service: EvalService, suite: EvalSuite, case: EvalCase, observation: EvalObservation):
    return service.run_case(
        suite,
        case,
        observation,
        workspace_id="workspace_test",
        target_id="target_platform",
    )


def _accepted_baseline(
    root: Path, service: EvalService, suite: EvalSuite, run_id: str
) -> EvalBaseline:
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


def _regression_setup(root: Path) -> tuple[EvalService, EvalBaseline, str, str]:
    case = _case()
    suite = _suite(case)
    service = EvalService(root)
    good_run, _ = _run(service, suite, case, EvalObservation(text="ok"))
    baseline = _accepted_baseline(root, service, suite, good_run.eval_run_id)
    bad_run, bad_result = _run(service, suite, case, EvalObservation(text="broken"))
    assert bad_result.passed is False
    return service, baseline, good_run.eval_run_id, bad_run.eval_run_id


@pytest.mark.parametrize("config", _CODE_EXECUTING_CONFIGS)
def test_promptfoo_code_executing_config_is_rejected(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"promptfoo_adapter_enabled": True})
    with pytest.raises(PromptfooConfigError, match="forbidden"):
        _adapter(root).validate_config(config, trusted=True)


@pytest.mark.parametrize("config", _REMOTE_CONFIGS)
def test_promptfoo_remote_features_rejected_without_remote_flag(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"promptfoo_adapter_enabled": True})
    with pytest.raises(PromptfooConfigError, match="Remote"):
        _adapter(root).validate_config(config, trusted=True)


def test_promptfoo_remote_features_pass_only_with_remote_flag(tmp_path: Path) -> None:
    root = make_platform_workspace(
        tmp_path,
        flag_overrides={
            "promptfoo_adapter_enabled": True,
            "promptfoo_remote_generation_enabled": True,
        },
    )
    summary = _adapter(root).validate_config({"prompts": ["p"], "sharing": True}, trusted=True)
    assert summary["cloud_sharing"] is True
    assert summary["remote_generation"] is False and summary["telemetry"] is False


def test_promptfoo_adapter_is_unusable_when_flag_is_off(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    with pytest.raises(PromptfooConfigError, match="disabled"):
        _adapter(root).validate_config({"prompts": ["p"]}, trusted=True)


def test_promptfoo_untrusted_config_and_unapproved_provider_are_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"promptfoo_adapter_enabled": True})
    adapter = _adapter(root)
    with pytest.raises(PromptfooConfigError, match="Untrusted"):
        adapter.validate_config({"prompts": ["p"]}, trusted=False)
    with pytest.raises(PromptfooConfigError, match="not approved"):
        adapter.validate_config({"prompts": ["p"], "providers": ["local-echo"]}, trusted=True)
    summary = adapter.validate_config(
        {"prompts": ["p"], "providers": ["local-echo"]},
        trusted=True,
        approved_provider_destinations=frozenset({"local-echo"}),
    )
    assert summary["provider_destinations"] == ["local-echo"]
    assert summary["custom_code"] is False


def test_reject_regression_records_finding_and_event_idempotently(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, baseline, _, bad_run_id = _regression_setup(root)
    comparison = service.compare_with_baseline(baseline.baseline_id, bad_run_id)
    assert comparison.regression is True
    finding = service.reject_regression(
        comparison.comparison_id, actor_id="user_local_test", reason="Candidate regresses a_exact"
    )
    assert finding.severity == "high" and finding.code == "regression_rejected"
    assert finding.eval_run_id == comparison.candidate_eval_run_id
    repeat = service.reject_regression(
        comparison.comparison_id, actor_id="user_local_test", reason="Candidate regresses a_exact"
    )
    assert repeat.finding_id == finding.finding_id
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        record = store.head("eval_finding", finding.finding_id)
        assert record is not None and record.revision == 1
        assert len(store.list_heads("eval_finding", limit=10)) == 1
        events = store.list_events(comparison.comparison_id, limit=10)
        rejected = [item for item in events if item["event_type"] == "eval_regression_rejected"]
        assert len(rejected) == 1
        assert rejected[0]["payload"]["finding_id"] == finding.finding_id
        assert store.verify_event_stream(comparison.comparison_id)


def test_reject_regression_refuses_clean_or_missing_comparisons(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, baseline, good_run_id, _ = _regression_setup(root)
    clean = service.compare_with_baseline(baseline.baseline_id, good_run_id)
    assert clean.regression is False
    with pytest.raises(EvalError, match="regression"):
        service.reject_regression(
            clean.comparison_id, actor_id="user_local_test", reason="not a regression"
        )
    with pytest.raises(EvalError, match="missing"):
        service.reject_regression(
            "ecmp_missing", actor_id="user_local_test", reason="nothing there"
        )
    with pytest.raises(EvalError, match="actor"):
        service.reject_regression(clean.comparison_id, actor_id="", reason="x")
    with pytest.raises(EvalError, match="actor"):
        service.reject_regression(clean.comparison_id, actor_id="user_local_test", reason="")


def test_compare_and_reject_fail_closed_when_evals_disabled(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service, baseline, _, bad_run_id = _regression_setup(root)
    comparison = service.compare_with_baseline(baseline.baseline_id, bad_run_id)
    make_platform_workspace(
        root, flag_overrides={"evals_enabled": False, "promptfoo_adapter_enabled": False}
    )
    disabled = EvalService(root)
    with pytest.raises(EvalError, match="disabled"):
        disabled.compare_with_baseline(baseline.baseline_id, bad_run_id)
    with pytest.raises(EvalError, match="disabled"):
        disabled.reject_regression(
            comparison.comparison_id, actor_id="user_local_test", reason="blocked"
        )
