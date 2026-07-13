from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

from raytsystem.bootstrap.cli import register_bootstrap_commands
from raytsystem.bootstrap.service import BootstrapService
from raytsystem.contracts.installation import SourceRoot, SourceType


def _make_repo(root: Path, files: dict[str, str], *, dirs: tuple[str, ...] = ()) -> Path:
    for name in dirs:
        (root / name).mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _obsidian_vault(root: Path) -> Path:
    return _make_repo(
        root,
        {
            ".obsidian/app.json": "{}",
            "notes/index.md": "# Index\nSee [[topic-a]] and [[topic-b]].\n",
            "notes/topic-a.md": "---\nid: a\n---\nLinks to [[index]].\n",
            "notes/board.canvas": '{"nodes":[],"edges":[]}',
        },
    )


def _software_repo(root: Path) -> Path:
    return _make_repo(
        root,
        {
            "pyproject.toml": "[project]\nname='x'\n",
            "src/app.py": "def main() -> None:\n    return None\n",
            "src/util.py": "X = 1\n",
            "README.md": "# X\n",
        },
        dirs=(".git",),
    )


def _markdown_kb(root: Path) -> Path:
    return _make_repo(
        root,
        {
            "docs/one.md": "# One\nplain prose, no links.\n",
            "docs/two.md": "# Two\nmore prose.\n",
        },
    )


def _mixed_repo(root: Path) -> Path:
    return _make_repo(
        root,
        {
            ".obsidian/app.json": "{}",
            "vault/a.md": "# A\n[[b]] [[c]]\n",
            "vault/b.md": "# B\n[[a]]\n",
            "src/main.py": "def run() -> int:\n    return 0\n",
            "src/lib.py": "Y = 2\n",
            "pyproject.toml": "[project]\nname='m'\n",
        },
        dirs=(".git",),
    )


def test_classifies_obsidian(tmp_path: Path) -> None:
    classification = BootstrapService(_obsidian_vault(tmp_path)).classify()
    assert classification.primary_type is SourceType.OBSIDIAN
    assert classification.verify_id()
    assert any(s.kind == "obsidian_vault" for s in classification.signals)


def test_classifies_software(tmp_path: Path) -> None:
    classification = BootstrapService(_software_repo(tmp_path)).classify()
    assert classification.primary_type is SourceType.SOFTWARE


def test_classifies_markdown(tmp_path: Path) -> None:
    classification = BootstrapService(_markdown_kb(tmp_path)).classify()
    assert classification.primary_type is SourceType.MARKDOWN


def test_classifies_mixed(tmp_path: Path) -> None:
    classification = BootstrapService(_mixed_repo(tmp_path)).classify()
    assert classification.primary_type is SourceType.MIXED
    assert classification.is_mixed


def test_empty_repo_classifies_empty(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    classification = BootstrapService(tmp_path).classify()
    assert classification.primary_type is SourceType.EMPTY


def test_plan_is_read_only_and_fingerprinted(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    before = _tree(repo)
    plan = BootstrapService(repo).plan(template="auto", mode="managed")
    after = _tree(repo)
    assert before == after, "dry-run plan must not create or delete any file"
    assert plan.dry_run is True
    assert plan.template_id == "software"
    assert plan.fingerprint.startswith("bootstrap_")
    assert plan.verify_fingerprint()
    assert plan.source_map.verify_hash()
    assert plan.source_map.verify_id()
    assert "src" in {r.relative_path for r in plan.source_map.roots}


def test_plan_is_deterministic(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    a = BootstrapService(repo).plan(template="software")
    b = BootstrapService(repo).plan(template="software")
    assert a.fingerprint == b.fingerprint
    assert a.classification.classification_id == b.classification.classification_id


def test_plan_leaks_no_absolute_path(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    plan = BootstrapService(repo).plan(template="software")
    dumped = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    assert str(repo) not in dumped
    assert str(tmp_path) not in dumped
    assert plan.target_name == repo.name


def test_source_root_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        SourceRoot(
            source_root_id="srcroot_x",
            relative_path="/etc/passwd",
            source_type=SourceType.MARKDOWN,
            adapter="adapter:markdown",
        )


def test_onboarding_prompt_has_placeholders_and_no_secrets(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    result = BootstrapService(repo).onboarding_prompt(agent="claude")
    prompt = result["prompt"]
    assert "<TARGET_REPOSITORY_PATH>" in prompt
    assert "<RAYTSYSTEM_SOURCE_PATH>" in prompt
    assert "CLAUDE.md" in prompt
    assert str(repo) not in prompt  # never bake the absolute path in
    assert result["suggested_template"] == "software"


def test_onboarding_prompt_rejects_unknown_agent(tmp_path: Path) -> None:
    from raytsystem.bootstrap.service import BootstrapError

    with pytest.raises(BootstrapError):
        BootstrapService(tmp_path).onboarding_prompt(agent="gemini")


def _bootstrap_cli() -> typer.Typer:
    app = typer.Typer()
    register_bootstrap_commands(app)
    return app


def test_cli_dry_run_emits_plan(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    result = CliRunner().invoke(
        _bootstrap_cli(), ["bootstrap", "--target", str(repo), "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["action"] == "bootstrap"
    assert payload["dry_run"] is True


def test_cli_apply_requires_confirm(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    result = CliRunner().invoke(
        _bootstrap_cli(), ["bootstrap", "--target", str(repo), "--apply", "--json"]
    )
    assert result.exit_code != 0
    assert "confirm" in result.output.lower()


def test_apply_installs_functional_workspace(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    service = BootstrapService(repo)
    plan = service.plan(template="software")
    result = service.apply(confirm=plan.fingerprint, template="software")
    assert result["status"] == "installed"
    # A functional workspace: config + genesis generation + ledger pointer exist.
    assert (repo / "config" / "raytsystem.toml").is_file()
    assert (repo / "ledger" / "generations" / "genesis.json").is_file()
    assert (repo / "ledger" / "CURRENT").read_text().strip() == "genesis"
    assert (repo / ".raytsystem" / "installation.json").is_file()
    assert result["index_rebuilt"] is True


def test_apply_rejects_wrong_fingerprint(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    from raytsystem.bootstrap.service import BootstrapError

    with pytest.raises(BootstrapError, match="fingerprint"):
        BootstrapService(repo).apply(confirm="bootstrap_deadbeef", template="software")
    # Nothing was written on refusal.
    assert not (repo / "config").exists()


def test_apply_merges_agents_and_preserves_readme(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    (repo / "README.md").write_text("# User Readme\nkeep me\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# House rules\ndo the thing\n", encoding="utf-8")
    service = BootstrapService(repo)
    plan = service.plan(template="software")
    result = service.apply(confirm=plan.fingerprint, template="software")
    assert "README.md" in result["skipped"]
    assert "AGENTS.md" in result["merged"]
    assert "# User Readme" in (repo / "README.md").read_text()  # user copy untouched
    agents = (repo / "AGENTS.md").read_text()
    assert "House rules" in agents and "RAYTSYSTEM:BEGIN" in agents  # both present


def test_uninstall_reverses_and_preserves_user_data(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    (repo / "AGENTS.md").write_text("# House rules\ndo the thing\n", encoding="utf-8")
    app_before = (repo / "src" / "app.py").read_text()
    service = BootstrapService(repo)
    plan = service.plan(template="software")
    service.apply(confirm=plan.fingerprint, template="software")
    result = service.uninstall()
    assert result["status"] == "uninstalled"
    # User source + files intact; raytsystem artifacts gone; merge block stripped.
    assert (repo / "src" / "app.py").read_text() == app_before
    assert (repo / "AGENTS.md").read_text().strip() == "# House rules\ndo the thing"
    assert not (repo / "config").exists()
    assert not (repo / "ledger").exists()
    assert not (repo / ".raytsystem" / "installation.json").exists()


def test_apply_is_idempotent_reinstall_refused(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    service = BootstrapService(repo)
    plan = service.plan(template="software")
    service.apply(confirm=plan.fingerprint, template="software")
    # Re-planning now reports the workspace as already initialized (a blocker).
    replan = service.plan(template="software")
    assert replan.preflight.already_initialized
    from raytsystem.bootstrap.service import BootstrapError

    with pytest.raises(BootstrapError, match=r"[Pp]reflight"):
        service.apply(confirm=replan.fingerprint, template="software")


def test_cli_onboarding_prompt(tmp_path: Path) -> None:
    repo = _software_repo(tmp_path)
    result = CliRunner().invoke(
        _bootstrap_cli(), ["onboarding", "prompt", "--agent", "codex", "--target", str(repo)]
    )
    assert result.exit_code == 0, result.output
    assert "AGENTS.md" in result.output
