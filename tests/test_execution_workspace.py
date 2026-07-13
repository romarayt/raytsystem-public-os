from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import pytest

from raytsystem.catalog import CatalogService
from raytsystem.codegraph.projection import CodeGraphProjection
from raytsystem.execution.config import ExecutionConfig, FeatureFlags
from raytsystem.execution.workspace import (
    WorkspaceDriftError,
    WorkspaceGraphError,
    WorkspaceManager,
    WorkspaceSecurityError,
)
from raytsystem.skill_authoring import SkillAuthoringService
from raytsystem.tasking import TaskService

NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)
FIXTURE_COMMIT = "a" * 40
PRIVATE_CORPUS_MARKER = "PRIVATE_CORPUS_BODY_MUST_NOT_ENTER_CONTEXT"


def _configure_catalog(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        f"# Fixture instructions\n\n{PRIVATE_CORPUS_MARKER}\n",
        encoding="utf-8",
    )
    (root / "config" / "runtime-adapters.yaml").write_text(
        """version: "1.0.0"
adapters:
  - adapter_id: adapter_fake
    name: Deterministic fixture adapter
    version: "1.0.0"
    state: available
    isolation_mode: managed_worktree
    capabilities: [code_edit]
""",
        encoding="utf-8",
    )
    pack = root / "packs" / "fixture"
    (pack / "agents").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        """pack_id: pack_fixture
name: Fixture pack
version: "1.0.0"
description: Local workspace-manager fixture.
license_expression: Apache-2.0
trust_class: user
agent_ids: [agent_builder]
skill_ids: []
context_paths: [AGENTS.md]
optional: false
""",
        encoding="utf-8",
    )
    (pack / "agents" / "agent_builder.yaml").write_text(
        """agent_id: agent_builder
name: Builder
role: software_engineer
description: Builds an approved task in a managed workspace.
version: "1.0.0"
pack_id: pack_fixture
runtime_adapter_id: adapter_fake
skill_ids: []
context_paths: [AGENTS.md]
capabilities: [code_edit]
requested_filesystem_mode: staging_only
approved_data_classes: [public, internal]
accent: "#123456"
enabled: true
""",
        encoding="utf-8",
    )


def _create_task(root: Path) -> str:
    created = TaskService(root).create_task(
        title="Implement managed workspace context",
        description="Bind the task to the current code graph and policy.",
        actor="user:test",
        idempotency_key="workspace-fixture-task",
        expected_generation_id=None,
        now=NOW,
    )
    return created.task.task_id


def _build_graph(root: Path) -> None:
    config = root / "config" / "raytsystem.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + """

[code_graph]
path = ".raytsystem/graph"
roots = ["src", "tests"]
files = ["pyproject.toml"]
max_files = 100
max_file_bytes = 1048576
max_total_bytes = 8388608
max_nodes = 5000
max_edges = 20000
universe_max_nodes = 1000
universe_max_edges = 5000
query_max_nodes = 40
query_max_edges = 100
query_max_bytes = 24000
parser_timeout_seconds = 10
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "workspace-fixture"\ndependencies = ["pydantic>=2"]\n',
        encoding="utf-8",
    )
    (root / "src" / "store.py").write_text(
        "def persist(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    (root / "src" / "service.py").write_text(
        "from store import persist\n\ndef save(value: str) -> str:\n    return persist(value)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "from service import save\n\ndef test_save() -> None:\n    assert save('x') == 'x'\n",
        encoding="utf-8",
    )
    CodeGraphProjection(root).rebuild()


def _manager(root: Path, *, graph_enabled: bool = True) -> WorkspaceManager:
    flags = FeatureFlags(
        code_graph_enabled=graph_enabled,
        graph_first_query_enabled=graph_enabled,
        runtime_execution_enabled=True,
    )
    return WorkspaceManager(root, config=ExecutionConfig(features=flags))


def _prepared_project(root: Path) -> str:
    _configure_catalog(root)
    task_id = _create_task(root)
    _build_graph(root)
    return task_id


def _configure_local_skill(root: Path) -> tuple[str, str]:
    target = root / "skills" / "local-runtime-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        """---
name: local-runtime-skill
description: Local runtime fence fixture.
version: "1.0.0"
permissions:
  - filesystem_read
test_status: pending
---
# Confirmed runtime skill
""",
        encoding="utf-8",
    )
    snapshot = CatalogService(root).load()
    skill = snapshot.skill("local-runtime-skill")
    assert skill is not None
    return snapshot.catalog_sha256, skill.source_sha256


def test_workspace_is_deterministic_idempotent_bounded_and_detects_drift(
    project_root: Path,
) -> None:
    task_id = _prepared_project(project_root)
    manager = _manager(project_root)

    first = manager.prepare(
        task_id,
        "agent_builder",
        fixture_mode=True,
        fixture_git_commit=FIXTURE_COMMIT,
    )
    workspace_root = project_root / first.workspace.relative_root
    manifest_path = workspace_root / "manifest.json"
    context_path = workspace_root / "context" / "bundle.json"
    first_manifest = manifest_path.read_bytes()
    first_context = context_path.read_bytes()
    second = manager.prepare(
        task_id,
        "agent_builder",
        fixture_mode=True,
        fixture_git_commit=FIXTURE_COMMIT,
    )

    assert not first.no_op
    assert second.no_op
    assert second.workspace == first.workspace
    assert second.graph_scope == first.graph_scope

    assert manifest_path.read_bytes() == first_manifest
    assert context_path.read_bytes() == first_context
    assert first.workspace.relative_root.startswith(".raytsystem/workspaces/workspace_")
    assert first.workspace.repo_path == f"{first.workspace.relative_root}/repo"
    assert not Path(first.workspace.repo_path).is_absolute()
    assert not any((workspace_root / "repo").iterdir())

    manifest = json.loads(first_manifest)
    context = json.loads(first_context)
    graph_result = context["graph"]["result"]
    graph_policy = context["policy"]["graph"]
    assert manifest["paths"] == {
        "artifacts": "artifacts",
        "agent_context": "context/AGENT.md",
        "context": "context",
        "context_bundle": "context/bundle.json",
        "graph_context": "context/GRAPH_CONTEXT.json",
        "graph_report": "context/GRAPH_REPORT.md",
        "logs": "logs",
        "policy_context": "context/POLICY.md",
        "repo": "repo",
        "sources": "context/SOURCES.md",
        "task_context": "context/TASK.md",
    }
    assert set(manifest["context_files"]) == {
        "AGENT.md",
        "GRAPH_CONTEXT.json",
        "GRAPH_REPORT.md",
        "POLICY.md",
        "SOURCES.md",
        "TASK.md",
        "bundle.json",
    }
    assert all((workspace_root / "context" / name).is_file() for name in manifest["context_files"])
    assert manifest["bindings"]["task_revision"] == 1
    assert manifest["bindings"]["employee_configuration_revision"]
    assert manifest["bindings"]["git_commit"] == FIXTURE_COMMIT
    assert manifest["bindings"]["graph_snapshot_id"] == context["graph"]["snapshot"]["snapshot_id"]
    assert len(graph_result["nodes"]) <= graph_policy["max_nodes"]
    assert len(graph_result["edges"]) <= graph_policy["max_edges"]
    assert (
        sum(
            (workspace_root / "context" / name).stat().st_size for name in manifest["context_files"]
        )
        <= manager.config.max_context_bytes
    )
    assert PRIVATE_CORPUS_MARKER.encode() not in first_context
    assert not (project_root / "graphify-out").exists()

    manifest_path.write_bytes(b"{}")
    with pytest.raises(WorkspaceDriftError, match="drifted"):
        manager.prepare(
            task_id,
            "agent_builder",
            fixture_mode=True,
            fixture_git_commit=FIXTURE_COMMIT,
        )


def test_workspace_prepare_waits_for_skill_authoring_and_uses_confirmed_catalog(
    project_root: Path,
) -> None:
    task_id = _prepared_project(project_root)
    catalog_sha256, source_sha256 = _configure_local_skill(project_root)
    authoring = SkillAuthoringService(project_root)
    flags = FeatureFlags(
        code_graph_enabled=False,
        graph_first_query_enabled=False,
        runtime_execution_enabled=True,
    )
    manager = WorkspaceManager(
        project_root,
        config=ExecutionConfig(features=flags),
        catalog_read_guard=authoring.catalog_read_guard,
    )
    signal = project_root / "runtime-writer-at-filesystem-boundary"
    script = """
import sys
import time
from pathlib import Path

from raytsystem.skill_authoring import SkillAuthoringService, SkillPersistenceError

root = Path(sys.argv[1])
service = SkillAuthoringService(root)

def pause_then_fail(*_args, **_kwargs):
    (root / "runtime-writer-at-filesystem-boundary").write_text("ready", encoding="utf-8")
    time.sleep(0.5)
    raise SkillPersistenceError("injected failure after namespace transition")

service._load_updated = pause_then_fail
try:
    service.save(
        "local-runtime-skill",
        content='''---
name: local-runtime-skill
description: Uncommitted runtime skill.
version: "2.0.0"
permissions:
  - filesystem_read
test_status: pending
---
# Uncommitted runtime skill
''',
        expected_catalog_sha256=sys.argv[2],
        expected_source_sha256=sys.argv[3],
        idempotency_key="runtime-catalog-reader-fence",
        actor_id="user_local_test",
    )
except SkillPersistenceError:
    pass
else:
    raise AssertionError("injected failure did not run")
"""
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(project_root),
            catalog_sha256,
            source_sha256,
        ],
        cwd=Path(__file__).parents[1],
    )
    deadline = monotonic() + 5
    while not signal.exists():
        assert monotonic() < deadline, "writer did not reach the namespace transition"
        sleep(0.01)

    started = monotonic()
    prepared = manager.prepare(
        task_id,
        "agent_builder",
        fixture_mode=True,
        fixture_git_commit=FIXTURE_COMMIT,
    )
    elapsed = monotonic() - started
    writer.wait(timeout=5)

    assert writer.returncode == 0
    assert elapsed >= 0.25
    assert prepared.catalog_sha256 == catalog_sha256
    confirmed = CatalogService(project_root).load().skill("local-runtime-skill")
    assert confirmed is not None and confirmed.source_sha256 == source_sha256


def test_workspace_rejects_traversal_and_symlinked_managed_root(
    project_root: Path,
) -> None:
    task_id = _prepared_project(project_root)
    manager = _manager(project_root)

    with pytest.raises(WorkspaceSecurityError, match="path syntax"):
        manager.prepare(
            "../outside",
            "agent_builder",
            fixture_mode=True,
            fixture_git_commit=FIXTURE_COMMIT,
        )

    outside = project_root.parent / "outside-workspaces"
    outside.mkdir()
    (project_root / ".raytsystem" / "workspaces").symlink_to(
        outside,
        target_is_directory=True,
    )
    with pytest.raises(WorkspaceSecurityError, match="unsafe"):
        manager.prepare(
            task_id,
            "agent_builder",
            fixture_mode=True,
            fixture_git_commit=FIXTURE_COMMIT,
        )
    assert list(outside.iterdir()) == []


def test_workspace_rejects_stale_graph_before_creating_managed_root(
    project_root: Path,
) -> None:
    task_id = _prepared_project(project_root)
    (project_root / "src" / "service.py").write_text(
        "def changed() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceGraphError, match="missing, stale, or invalid"):
        _manager(project_root).prepare(
            task_id,
            "agent_builder",
            fixture_mode=True,
            fixture_git_commit=FIXTURE_COMMIT,
        )
    assert not (project_root / ".raytsystem" / "workspaces").exists()
    assert not (project_root / "graphify-out").exists()


def test_graph_disabled_allows_materialization_free_fixture_without_projection(
    project_root: Path,
) -> None:
    _configure_catalog(project_root)
    task_id = _create_task(project_root)

    prepared = _manager(project_root, graph_enabled=False).prepare(
        task_id,
        "agent_builder",
        fixture_mode=True,
        fixture_git_commit=FIXTURE_COMMIT,
    )
    context = json.loads(
        (project_root / prepared.workspace.relative_root / "context" / "bundle.json").read_bytes()
    )

    assert prepared.workspace.graph_snapshot_id == "graph_disabled"
    assert context["graph"]["enabled"] is False
    assert context["graph"]["result"] is None
    assert not (project_root / ".raytsystem" / "graph").exists()


def test_existing_hardlinked_context_is_rejected(project_root: Path) -> None:
    task_id = _prepared_project(project_root)
    manager = _manager(project_root)
    prepared = manager.prepare(
        task_id,
        "agent_builder",
        fixture_mode=True,
        fixture_git_commit=FIXTURE_COMMIT,
    )
    context_path = project_root / prepared.workspace.relative_root / "context" / "bundle.json"
    external = project_root / "context-copy.json"
    external.write_bytes(context_path.read_bytes())
    context_path.unlink()
    os.link(external, context_path)

    with pytest.raises(WorkspaceDriftError, match="missing or unsafe"):
        manager.prepare(
            task_id,
            "agent_builder",
            fixture_mode=True,
            fixture_git_commit=FIXTURE_COMMIT,
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")
def test_production_default_materializes_detached_git_worktree(project_root: Path) -> None:
    task_id = _prepared_project(project_root)
    (project_root / ".gitignore").write_text(".raytsystem/\nops/\n", encoding="utf-8")
    for command in (
        ("git", "init", str(project_root)),
        ("git", "-C", str(project_root), "config", "user.name", "raytsystem Test"),
        ("git", "-C", str(project_root), "config", "user.email", "raytsystem@example.test"),
        ("git", "-C", str(project_root), "add", "."),
        ("git", "-C", str(project_root), "commit", "-m", "fixture"),
    ):
        subprocess.run(command, check=True, capture_output=True)
    commit = subprocess.run(
        ("git", "-C", str(project_root), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    CodeGraphProjection(project_root).rebuild()

    manager = _manager(project_root)
    first = manager.prepare(task_id, "agent_builder")
    second = manager.prepare(task_id, "agent_builder")
    repo = project_root / first.workspace.repo_path

    assert not first.no_op
    assert second.no_op
    assert first.workspace.git_commit == commit
    assert (repo / ".git").is_file()
    assert (repo / "src" / "service.py").is_file()
