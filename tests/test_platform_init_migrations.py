"""Idempotent workspace init with real template assets and journaled, resumable migrations."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

import raytsystem.migrations as migrations_module
from platform_helpers import make_platform_workspace
from raytsystem.contracts import SCHEMA_VERSION
from raytsystem.contracts.evaluation import EvalCase, EvalSuite
from raytsystem.migrations import Migration, MigrationError, MigrationService
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only
from raytsystem.templates import TemplateError, TemplateService
from raytsystem.templates.service import TemplateId

pytestmark = pytest.mark.filterwarnings("error")

_CONFIG_TEMPLATE = (
    'schema_version = "{version}"\n'
    'environment = "development"\n'
    'default_promotion_mode = "manual"\n'
    'control_db = "ops/control.sqlite"\n'
    'index_db = ".raytsystem/index.sqlite"\n'
)
_TEMPLATE_IDS: tuple[TemplateId, ...] = ("software", "content", "research")


def _migration_workspace(tmp_path: Path, version: str) -> Path:
    root = make_platform_workspace(tmp_path)
    (root / "config" / "raytsystem.toml").write_text(
        _CONFIG_TEMPLATE.format(version=version), encoding="utf-8"
    )
    return root


def _store_backup(root: Path, backup_id: str = "backup_test_0001") -> str:
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="backup",
            record_id=backup_id,
            payload={"backup_id": backup_id, "kind": "private_backup"},
            state="created",
            expected_revision=None,
        )
    return backup_id


def _config_version(root: Path) -> str:
    parsed = tomllib.loads((root / "config" / "raytsystem.toml").read_text(encoding="utf-8"))
    return str(parsed["schema_version"])


def _journal_rows(root: Path) -> dict[str, str]:
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        rows = store.connection.execute(
            "SELECT migration_id, state FROM migration_journal "
            "WHERE migration_id != 'platform_schema_0001' ORDER BY migration_id"
        ).fetchall()
    return {str(row["migration_id"]): str(row["state"]) for row in rows}


def _synthetic_step(
    migration_id: str,
    from_version: str,
    to_version: str,
    calls: list[str],
    *,
    fail_once: set[str] | None = None,
) -> Migration:
    def apply(root: Path) -> dict[str, Any]:
        calls.append(migration_id)
        if fail_once and migration_id in fail_once:
            fail_once.discard(migration_id)
            raise RuntimeError("simulated crash")
        config = root / "config" / "raytsystem.toml"
        data = config.read_text(encoding="utf-8")
        updated = data.replace(
            f'schema_version = "{from_version}"', f'schema_version = "{to_version}"'
        )
        config.write_text(updated, encoding="utf-8")
        return {"changed_files": ["config/raytsystem.toml"], "data_changes": "none"}

    return Migration(
        migration_id=migration_id,
        from_version=from_version,
        to_version=to_version,
        reversible=True,
        apply=apply,
    )


def _two_step_registry(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    fail_once: set[str] | None = None,
) -> tuple[Migration, Migration]:
    first = _synthetic_step("schema_1_2_0_to_1_3_0", "1.2.0", "1.3.0", calls)
    second = _synthetic_step("schema_1_3_0_to_1_4_0", "1.3.0", "1.4.0", calls, fail_once=fail_once)
    monkeypatch.setattr(migrations_module, "MIGRATIONS", (first, second))
    return first, second


def test_stale_workspace_reports_migration_and_resumes_after_journaled_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _migration_workspace(tmp_path, "1.2.0")
    calls: list[str] = []
    first, second = _two_step_registry(monkeypatch, calls)
    service = MigrationService(root)
    status = service.status()
    assert status["migration_required"] is True
    assert status["current_version"] == "1.2.0"
    assert status["target_version"] == "1.4.0"
    assert status["pending_migration_ids"] == [first.migration_id, second.migration_id]
    plan = service.plan()
    assert plan.migration_ids == (first.migration_id, second.migration_id)
    backup_id = _store_backup(root)
    with initialize_platform_store(root) as store, store.transaction():
        store.connection.execute(
            "INSERT INTO migration_journal VALUES (?, ?, ?, ?, 'applied', ?)",
            (first.migration_id, "0" * 64, 1_002_000, 1_003_000, "2026-01-01T00:00:00.000Z"),
        )
    (root / "config" / "raytsystem.toml").write_text(
        _CONFIG_TEMPLATE.format(version="1.3.0"), encoding="utf-8"
    )
    assert service.status()["pending_migration_ids"] == [second.migration_id]
    record = service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    assert record is not None and record.migration_id == second.migration_id
    assert calls == [second.migration_id]
    assert _config_version(root) == "1.4.0"
    assert _journal_rows(root) == {
        first.migration_id: "applied",
        second.migration_id: "applied",
    }


def test_migration_plan_is_deterministic(tmp_path: Path) -> None:
    root = _migration_workspace(tmp_path, "1.3.0")
    service = MigrationService(root)
    first = service.plan()
    second = service.plan()
    assert first.migration_ids == ("schema_1_3_0_to_1_4_0",)
    assert first.plan_sha256 == second.plan_sha256
    assert first.migration_plan_id == second.migration_plan_id
    assert first.backup_required is True and first.reversible is True


def test_apply_journals_persists_report_and_keeps_config_readable(tmp_path: Path) -> None:
    root = _migration_workspace(tmp_path, "1.3.0")
    service = MigrationService(root)
    plan = service.plan()
    backup_id = _store_backup(root)
    with pytest.raises(MigrationError, match="confirmation"):
        service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=False)
    with pytest.raises(MigrationError, match="backup"):
        service.apply(plan, backup_id="", actor_id="user_test", confirm=True)
    record = service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    assert record is not None and record.state.value == "applied"
    assert record.from_version == "1.3.0" and record.to_version == "1.4.0"
    parsed = tomllib.loads((root / "config" / "raytsystem.toml").read_text(encoding="utf-8"))
    assert parsed["schema_version"] == "1.4.0"
    assert parsed["environment"] == "development"
    assert parsed["control_db"] == "ops/control.sqlite"
    assert _journal_rows(root) == {"schema_1_3_0_to_1_4_0": "applied"}
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        row = store.connection.execute(
            "SELECT from_version, to_version FROM migration_journal WHERE migration_id=?",
            ("schema_1_3_0_to_1_4_0",),
        ).fetchone()
        assert (int(row["from_version"]), int(row["to_version"])) == (1_003_000, 1_004_000)
        reports = store.list_heads("migration_report", limit=10)
        assert len(reports) == 1
        assert reports[0].payload_sha256 == record.report_sha256
        assert reports[0].payload["data_changes"] == "none"
        assert store.verify_event_stream("workspace_migrations")


def test_double_apply_is_a_noop(tmp_path: Path) -> None:
    root = _migration_workspace(tmp_path, "1.3.0")
    service = MigrationService(root)
    plan = service.plan()
    backup_id = _store_backup(root)
    first = service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    config_after = (root / "config" / "raytsystem.toml").read_bytes()
    second = service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    assert first is not None and second is not None
    assert first.migration_record_id == second.migration_record_id
    assert (root / "config" / "raytsystem.toml").read_bytes() == config_after
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        head = store.head("migration", "schema_1_3_0_to_1_4_0")
        assert head is not None and head.revision == 1
        assert len(store.list_events("workspace_migrations", limit=10)) == 1
    fresh_plan = service.plan()
    assert fresh_plan.migration_ids == ()
    assert (
        service.apply(fresh_plan, backup_id=backup_id, actor_id="user_test", confirm=True) is None
    )
    assert service.status()["migration_required"] is False


def test_failed_step_is_journaled_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _migration_workspace(tmp_path, "1.2.0")
    calls: list[str] = []
    first, second = _two_step_registry(monkeypatch, calls, fail_once={"schema_1_3_0_to_1_4_0"})
    service = MigrationService(root)
    plan = service.plan()
    backup_id = _store_backup(root)
    with pytest.raises(MigrationError, match="failed"):
        service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    assert _config_version(root) == "1.3.0"
    assert _journal_rows(root) == {
        first.migration_id: "applied",
        second.migration_id: "failed",
    }
    record = service.apply(plan, backup_id=backup_id, actor_id="user_test", confirm=True)
    assert record is not None and record.to_version == "1.4.0"
    assert calls == [first.migration_id, second.migration_id, second.migration_id]
    assert _config_version(root) == "1.4.0"
    assert _journal_rows(root) == {
        first.migration_id: "applied",
        second.migration_id: "applied",
    }


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    plan, _files = TemplateService().plan(target, "software")
    assert plan.dry_run is True
    assert plan.files_to_create and plan.conflicts == ()
    assert not target.exists()


def test_init_apply_creates_declared_template_assets(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    service = TemplateService()
    template = service.template("software")
    result = service.initialize(target, "software")
    assert result["status"] == "initialized"
    assert result["template_version"] == template.version
    assert _config_version(target) == SCHEMA_VERSION
    assert MigrationService(target).status()["migration_required"] is False
    for skill_id in template.skill_ids:
        skill_file = target / "skills" / skill_id / "SKILL.md"
        assert skill_file.is_file()
        assert skill_id.removeprefix("skill_").replace("_", " ") in skill_file.read_text().lower()
    policy_file = target / "config" / "policies" / f"{template.policy_profile_id}.yaml"
    policy = yaml.safe_load(policy_file.read_text(encoding="utf-8"))
    assert policy["policy_profile_id"] == template.policy_profile_id
    assert policy["external_actions_default"] == "approval_required"
    for suite_id in template.eval_suite_ids:
        document = yaml.safe_load(
            (target / "evals" / suite_id / "suite.yaml").read_text(encoding="utf-8")
        )
        suite = EvalSuite.model_validate(document["suite"])
        case = EvalCase.model_validate(document["cases"][0])
        assert suite.suite_id == suite_id
        assert suite.case_ids == (case.case_id,)
        assert case.assertions[0].deterministic is True


def test_reinit_is_a_noop_with_empty_conflicts(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    service = TemplateService()
    first = service.initialize(target, "content")
    assert first["created"]
    plan, _files = service.plan(target, "content")
    assert plan.files_to_create == () and plan.conflicts == ()
    assert plan.confirmation_required is False
    second = service.initialize(target, "content")
    assert second["created"] == [] and second["conflicts"] == []


def test_init_conflict_is_refused_and_reported_exactly(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "README.md").write_text("unrelated existing readme\n", encoding="utf-8")
    service = TemplateService()
    plan, _files = service.plan(target, "software")
    assert plan.conflicts == ("README.md",)
    with pytest.raises(TemplateError, match="conflicts"):
        service.initialize(target, "software", confirm_existing=True)
    assert (target / "README.md").read_text(encoding="utf-8") == "unrelated existing readme\n"


def test_init_into_nonempty_directory_requires_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "notes.txt").write_text("keep me\n", encoding="utf-8")
    service = TemplateService()
    with pytest.raises(TemplateError, match="confirmation"):
        service.initialize(target, "research")
    result = service.initialize(target, "research", confirm_existing=True)
    assert result["status"] == "initialized"
    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me\n"


def test_every_template_plans_successfully(tmp_path: Path) -> None:
    service = TemplateService()
    for template_id in _TEMPLATE_IDS:
        template = service.template(template_id)
        plan, files = service.plan(tmp_path / template_id, template_id)
        assert plan.conflicts == () and plan.confirmation_required is False
        created = set(plan.files_to_create)
        assert created == set(files)
        for skill_id in template.skill_ids:
            assert f"skills/{skill_id}/SKILL.md" in created
        assert f"config/policies/{template.policy_profile_id}.yaml" in created
        for suite_id in template.eval_suite_ids:
            assert f"evals/{suite_id}/suite.yaml" in created
