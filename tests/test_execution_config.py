from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.execution.config import (
    ExecutionConfigError,
    FeatureFlags,
    load_execution_config,
)


def _write(root: Path, body: str) -> None:
    (root / "config").mkdir(parents=True)
    (root / "config" / "raytsystem.toml").write_text(body, encoding="utf-8")


def test_safe_execution_defaults_keep_real_runtimes_off(tmp_path: Path) -> None:
    _write(tmp_path, 'control_db = "ops/control.sqlite"\n')

    config = load_execution_config(tmp_path)

    assert config.features.code_graph_enabled
    assert config.features.graph_first_query_enabled
    assert not config.features.runtime_execution_enabled
    assert not config.features.adapter_enabled("adapter_codex_local")
    assert config.workspaces_root == ".raytsystem/workspaces"


def test_runtime_and_provider_flags_are_both_required(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
control_db = "ops/control.sqlite"

[features]
runtime_execution_enabled = true
codex_local_enabled = true
""",
    )

    config = load_execution_config(tmp_path)

    assert config.features.adapter_enabled("adapter_codex_local")
    assert not config.features.adapter_enabled("adapter_claude_code")


def test_provider_flag_without_runtime_is_rejected() -> None:
    with pytest.raises(ExecutionConfigError, match="require runtime execution"):
        FeatureFlags(codex_local_enabled=True)


def test_unknown_feature_flag_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "[features]\nunsafe_bypass = true\n")

    with pytest.raises(ExecutionConfigError, match="unknown keys"):
        load_execution_config(tmp_path)


def test_config_symlink_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    target = tmp_path / "actual.toml"
    target.write_text("", encoding="utf-8")
    (tmp_path / "config" / "raytsystem.toml").symlink_to(target)

    with pytest.raises(ExecutionConfigError, match="unavailable or invalid"):
        load_execution_config(tmp_path)


def test_workspace_path_cannot_escape_project(tmp_path: Path) -> None:
    _write(tmp_path, '[execution]\nworkspaces_root = "../elsewhere"\n')

    with pytest.raises(ExecutionConfigError, match="stay inside"):
        load_execution_config(tmp_path)
