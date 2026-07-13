"""Hash-bound pack lifecycle: discover, quarantine, evaluate, approve, activate, rollback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts.evaluation import (
    EvalAssertion,
    EvalAssertionType,
    EvalCase,
    EvalRun,
    EvalSuite,
)
from raytsystem.evals import EvalObservation, EvalService
from raytsystem.packages import PackageLifecycleError, PackageLifecycleService
from raytsystem.platform_store import open_platform_store_read_only

pytestmark = pytest.mark.filterwarnings("error")


def _write_pack(
    root: Path,
    relative: str,
    *,
    package_id: str = "pack_demo",
    version: str = "1.0.0",
    body: str = "print('hello')\n",
    **manifest_overrides: Any,
) -> Path:
    source = root / relative
    (source / "skills").mkdir(parents=True, exist_ok=True)
    (source / "skills" / "hello.py").write_text(body, encoding="utf-8")
    manifest: dict[str, Any] = {
        "package_id": package_id,
        "name": "Demo pack",
        "version": version,
        "publisher": "publisher_local",
        "content_sha256": "0" * 64,
        "license_expression": "MIT",
        "raytsystem_compatibility": ">=1.0.0",
    }
    manifest.update(manifest_overrides)
    (source / "package.yaml").write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    return source


def _approval(root: Path, revision: Any, *, scope: tuple[str, ...]) -> Any:
    return store_approval(
        root,
        action="activate_package",
        target_id=revision.revision_id,
        artifact_sha256=revision.content_sha256,
        scope=scope,
    )


_FULL_SCOPE = ("package_activation", "unsigned_pack")


def _approved_installed(
    service: PackageLifecycleService,
    root: Path,
    source_relative: str,
    *,
    eval_run_ids: tuple[str, ...] = (),
) -> tuple[Any, Any]:
    _, revision = service.inspect(source_relative)
    service.validate(revision.revision_id)
    approval = _approval(root, revision, scope=_FULL_SCOPE)
    service.approve(
        revision.revision_id,
        actor_id="user_local_test",
        approval_id=approval.approval_id,
        eval_run_ids=eval_run_ids,
    )
    service.install(revision.revision_id)
    return revision, approval


def _eval_run(root: Path, suite_id: str, *, passed: bool = True) -> EvalRun:
    case = EvalCase.model_validate(
        {
            "case_id": f"case_{suite_id}",
            "name": "Pack eval case",
            "task_fixture": "evals/packs/fixture.json",
            "repository_snapshot_sha256": "0" * 64,
            "agent_configuration_sha256": "1" * 64,
            "runtime_id": "runtime_deterministic",
            "instruction_hashes": {},
            "skill_hashes": {},
            "assertions": (
                EvalAssertion(
                    assertion_id="a_exact",
                    assertion_type=EvalAssertionType.EXACT_MATCH,
                    target="result_text",
                    expected="ok",
                ),
            ),
        }
    )
    suite = EvalSuite(
        suite_id=suite_id,
        name="Pack eval suite",
        version="1.0.0",
        dataset_id="dataset_pack",
        target_ids=("target_pack",),
        case_ids=(case.case_id,),
        manifest_sha256="2" * 64,
    )
    run, _ = EvalService(root).run_case(
        suite,
        case,
        EvalObservation(text="ok" if passed else "broken"),
        workspace_id="workspace_test",
        target_id="target_pack",
    )
    return run


def _head(root: Path, kind: str, record_id: str) -> Any:
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        return store.head(kind, record_id)


def test_lifecycle_happy_path_pins_hash_and_separates_install_from_activation(
    tmp_path: Path,
) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/demo")
    service = PackageLifecycleService(root)
    discovery = service.discover("packs/demo")
    assert discovery["state"] == "discovered"
    manifest, revision = service.inspect(discovery["discovery_id"])
    assert revision.state.value == "quarantined"
    assert manifest.content_sha256 == revision.content_sha256
    assert _head(root, "package_discovery", discovery["discovery_id"]).state == "inspected"
    validated = service.validate(revision.revision_id)
    assert validated.state.value == "validated"
    assert (
        _head(root, "package_revision", revision.revision_id).payload["signature_verified"] is False
    )
    approval = _approval(root, revision, scope=_FULL_SCOPE)
    approved = service.approve(
        revision.revision_id,
        actor_id="user_local_test",
        approval_id=approval.approval_id,
        eval_run_ids=(),
    )
    assert approved.state.value == "approved"
    installed = service.install(revision.revision_id)
    assert installed.state.value == "installed"
    assert _head(root, "package_active", manifest.package_id) is None
    assert PackageLifecycleService(root).snapshot()["active"] == []
    active = service.activate(
        revision.revision_id, actor_id="user_local_test", approval_id=approval.approval_id
    )
    assert active.state.value == "active"
    active_head = _head(root, "package_active", manifest.package_id)
    assert active_head.payload["revision_id"] == revision.revision_id
    assert active_head.payload["content_sha256"] == revision.content_sha256


def test_unsigned_pack_requires_explicit_unsigned_scope(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/unsigned")
    service = PackageLifecycleService(root)
    _, revision = service.inspect("packs/unsigned")
    service.validate(revision.revision_id)
    narrow = _approval(root, revision, scope=("package_activation",))
    with pytest.raises(PackageLifecycleError, match="authority"):
        service.approve(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=narrow.approval_id,
            eval_run_ids=(),
        )
    explicit = _approval(root, revision, scope=_FULL_SCOPE)
    approved = service.approve(
        revision.revision_id,
        actor_id="user_local_test",
        approval_id=explicit.approval_id,
        eval_run_ids=(),
    )
    assert approved.state.value == "approved"
    assert (
        _head(root, "package_revision", revision.revision_id).payload["signature_verified"] is False
    )


def test_integrity_self_hash_signature_is_not_authenticity(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    source = _write_pack(root, "packs/selfsigned", package_id="pack_selfsigned")
    service = PackageLifecycleService(root)
    manifest, _ = service.inspect("packs/selfsigned")
    raw = yaml.safe_load((source / "package.yaml").read_text(encoding="utf-8"))
    raw["signature"] = f"sha256:{manifest.content_sha256}"
    (source / "package.yaml").write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    _, revision = service.inspect("packs/selfsigned")
    service.validate(revision.revision_id)
    head = _head(root, "package_revision", revision.revision_id)
    assert head.payload["signature_verified"] is False
    narrow = _approval(root, revision, scope=("package_activation",))
    with pytest.raises(PackageLifecycleError, match="authority"):
        service.approve(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=narrow.approval_id,
            eval_run_ids=(),
        )


def test_dependency_confusion_fails_validation(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(
        root,
        "packs/unpinned",
        package_id="pack_unpinned",
        dependencies={"left_pad": "^1.2.3"},
    )
    service = PackageLifecycleService(root)
    _, unpinned = service.inspect("packs/unpinned")
    with pytest.raises(PackageLifecycleError, match="dependency_not_pinned"):
        service.validate(unpinned.revision_id)
    _write_pack(
        root,
        "packs/confused",
        package_id="pack_confused",
        dependencies={"left_pad": "1.2.3"},
    )
    _, confused = service.inspect("packs/confused")
    with pytest.raises(PackageLifecycleError, match="dependency_unresolved"):
        service.validate(confused.revision_id)
    assert _head(root, "package_revision", confused.revision_id).state == "blocked"


def test_malicious_skill_path_is_quarantined(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(
        root,
        "packs/traversal",
        package_id="pack_traversal",
        skill_ids=["skills/../../../etc/passwd"],
    )
    service = PackageLifecycleService(root)
    _, revision = service.inspect("packs/traversal")
    assert revision.state.value == "quarantined"
    with pytest.raises(PackageLifecycleError, match="unsafe_reference_path"):
        service.validate(revision.revision_id)
    assert _head(root, "package_revision", revision.revision_id).state == "blocked"
    _write_pack(
        root,
        "packs/absolute",
        package_id="pack_absolute",
        skill_ids=["/etc/passwd"],
    )
    with pytest.raises(PackageLifecycleError, match="contract validation"):
        service.inspect("packs/absolute")


def test_activation_reverifies_installed_tree_hash(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/demo")
    service = PackageLifecycleService(root)
    revision, approval = _approved_installed(service, root, "packs/demo")
    corrupted = root / ".raytsystem" / "packages" / revision.revision_id / "skills" / "hello.py"
    corrupted.chmod(0o600)
    corrupted.write_text("import os  # tampered\n", encoding="utf-8")
    with pytest.raises(PackageLifecycleError, match="hash does not verify"):
        service.activate(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=approval.approval_id,
        )


def test_rollback_creates_new_active_head_and_preserves_history(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/demo_v1", version="1.0.0")
    service = PackageLifecycleService(root)
    first, first_approval = _approved_installed(service, root, "packs/demo_v1")
    service.activate(
        first.revision_id, actor_id="user_local_test", approval_id=first_approval.approval_id
    )
    _write_pack(root, "packs/demo_v2", version="1.1.0", body="print('hello v2')\n")
    _, second = service.update("packs/demo_v2")
    assert second.previous_revision_id == first.revision_id
    assert second.state.value == "validated"
    second_approval = _approval(root, second, scope=_FULL_SCOPE)
    service.approve(
        second.revision_id,
        actor_id="user_local_test",
        approval_id=second_approval.approval_id,
        eval_run_ids=(),
    )
    service.install(second.revision_id)
    service.activate(
        second.revision_id, actor_id="user_local_test", approval_id=second_approval.approval_id
    )
    assert _head(root, "package_revision", first.revision_id).state == "superseded"
    restored = service.rollback(
        first.package_id,
        first.revision_id,
        actor_id="user_local_test",
        reason="regression detected in v2",
    )
    assert restored.state.value == "active"
    active_head = _head(root, "package_active", first.package_id)
    assert active_head.payload["revision_id"] == first.revision_id
    assert active_head.payload["rolled_back_from"] == second.revision_id
    assert active_head.revision == 3
    assert _head(root, "package_revision", second.revision_id).state == "rolled_back"
    listed = {
        payload["revision_id"] for payload in PackageLifecycleService(root).snapshot()["packages"]
    }
    assert {first.revision_id, second.revision_id} <= listed
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.verify_event_stream(first.package_id)
    with pytest.raises(PackageLifecycleError, match="already at the requested revision"):
        service.rollback(
            first.package_id,
            first.revision_id,
            actor_id="user_local_test",
            reason="duplicate rollback",
        )


def test_approve_rejects_unknown_or_failed_eval_runs(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/evaluated", package_id="pack_evaluated", eval_suite_ids=["suite_pack"])
    service = PackageLifecycleService(root)
    _, revision = service.inspect("packs/evaluated")
    service.validate(revision.revision_id)
    approval = _approval(root, revision, scope=_FULL_SCOPE)
    with pytest.raises(PackageLifecycleError, match="unknown eval run"):
        service.approve(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=approval.approval_id,
            eval_run_ids=("erun_missing",),
        )
    failed = _eval_run(root, "suite_pack", passed=False)
    with pytest.raises(PackageLifecycleError, match="failed eval run"):
        service.approve(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=approval.approval_id,
            eval_run_ids=(failed.eval_run_id,),
        )
    with pytest.raises(PackageLifecycleError, match="required eval suites"):
        service.approve(
            revision.revision_id,
            actor_id="user_local_test",
            approval_id=approval.approval_id,
            eval_run_ids=(),
        )
    passed = _eval_run(root, "suite_pack")
    approved = service.approve(
        revision.revision_id,
        actor_id="user_local_test",
        approval_id=approval.approval_id,
        eval_run_ids=(passed.eval_run_id,),
    )
    assert approved.eval_run_ids == (passed.eval_run_id,)


def test_update_requires_old_and_new_eval_suites(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/suite_v1", package_id="pack_suites", eval_suite_ids=["suite_old"])
    service = PackageLifecycleService(root)
    old_run = _eval_run(root, "suite_old")
    first, first_approval = _approved_installed(
        service, root, "packs/suite_v1", eval_run_ids=(old_run.eval_run_id,)
    )
    service.activate(
        first.revision_id, actor_id="user_local_test", approval_id=first_approval.approval_id
    )
    _write_pack(
        root,
        "packs/suite_v2",
        package_id="pack_suites",
        version="1.1.0",
        eval_suite_ids=["suite_new"],
    )
    _, second = service.update("packs/suite_v2")
    new_run = _eval_run(root, "suite_new")
    approval = _approval(root, second, scope=_FULL_SCOPE)
    with pytest.raises(PackageLifecycleError, match="required eval suites"):
        service.approve(
            second.revision_id,
            actor_id="user_local_test",
            approval_id=approval.approval_id,
            eval_run_ids=(new_run.eval_run_id,),
        )
    approved = service.approve(
        second.revision_id,
        actor_id="user_local_test",
        approval_id=approval.approval_id,
        eval_run_ids=(old_run.eval_run_id, new_run.eval_run_id),
    )
    assert approved.state.value == "approved"


def test_update_requires_an_active_revision(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    _write_pack(root, "packs/orphan", package_id="pack_orphan")
    with pytest.raises(PackageLifecycleError, match="active revision"):
        PackageLifecycleService(root).update("packs/orphan")


def test_pack_lifecycle_disabled_fails_closed(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"pack_lifecycle_enabled": False})
    _write_pack(root, "packs/demo")
    service = PackageLifecycleService(root)
    for attempt in (
        lambda: service.discover("packs/demo"),
        lambda: service.inspect("packs/demo"),
        lambda: service.update("packs/demo"),
        lambda: service.validate("pkgrev_missing"),
        lambda: service.approve(
            "pkgrev_missing", actor_id="user", approval_id="apr_x", eval_run_ids=()
        ),
        lambda: service.install("pkgrev_missing"),
        lambda: service.activate("pkgrev_missing", actor_id="user", approval_id="apr_x"),
        lambda: service.rollback("pack_demo", "pkgrev_missing", actor_id="user", reason="disabled"),
    ):
        with pytest.raises(PackageLifecycleError, match="disabled"):
            attempt()
    assert service.snapshot()["state"] in {"disabled", "unavailable"}
