from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

import raytsystem.brand_migration as brand_migration_module
from raytsystem.brand_migration import BrandMigrationError, migrate_legacy_workspace


def _legacy_workspace(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "config" / "agentos.toml").write_text('schema_version = "1.4.0"\n')
    (root / ".agentos").mkdir()
    (root / ".agentos" / "installation.json").write_text(
        json.dumps({"agentos_version": "0.1.0", "path": ".agentos"})
    )


def test_brand_migration_backs_up_moves_and_updates_metadata(tmp_path: Path) -> None:
    _legacy_workspace(tmp_path)

    result = migrate_legacy_workspace(tmp_path, confirm=True)

    assert result.migrated is True
    assert not (tmp_path / "config" / "agentos.toml").exists()
    assert not (tmp_path / ".agentos").exists()
    assert (tmp_path / "config" / "raytsystem.toml").is_file()
    metadata = json.loads((tmp_path / ".raytsystem" / "installation.json").read_text())
    assert metadata == {"raytsystem_version": "0.1.0", "path": ".raytsystem"}
    assert result.backup_path is not None
    with zipfile.ZipFile(tmp_path / result.backup_path) as archive:
        assert set(archive.namelist()) == {
            "config/agentos.toml",
            ".agentos/installation.json",
        }

    again = migrate_legacy_workspace(tmp_path, confirm=True)
    assert again.migrated is False


def test_brand_migration_rejects_old_new_conflict_without_writes(tmp_path: Path) -> None:
    _legacy_workspace(tmp_path)
    (tmp_path / ".raytsystem").mkdir()

    with pytest.raises(BrandMigrationError, match="both exist"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / ".agentos" / "installation.json").is_file()
    assert not (tmp_path / "ops").exists()


def test_brand_migration_requires_confirmation_before_backup(tmp_path: Path) -> None:
    _legacy_workspace(tmp_path)

    with pytest.raises(BrandMigrationError, match="explicit confirmation"):
        migrate_legacy_workspace(tmp_path, confirm=False)

    assert not (tmp_path / "ops").exists()


def test_brand_migration_allows_independent_already_current_config(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text('schema_version = "1.4.0"\n')
    (tmp_path / ".agentos").mkdir()
    (tmp_path / ".agentos" / "installation.json").write_text("{}")

    result = migrate_legacy_workspace(tmp_path, confirm=True)

    assert result.changed_paths == (".raytsystem",)
    assert (tmp_path / "config" / "raytsystem.toml").is_file()


def test_brand_migration_rejects_symlinked_legacy_state_before_backup(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agentos.toml").write_text('schema_version = "1.4.0"\n')
    outside = tmp_path.parent / f"{tmp_path.name}-outside-state"
    outside.mkdir()
    marker = outside / "installation.json"
    marker.write_text('{"agentos_version":"external"}')
    (tmp_path / ".agentos").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrandMigrationError, match="must not be a symlink"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / ".agentos").is_symlink()
    assert not (tmp_path / ".raytsystem").exists()
    assert not (tmp_path / "ops").exists()
    assert marker.read_text() == '{"agentos_version":"external"}'


def test_brand_migration_rejects_symlinked_config_component_before_read(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-config"
    outside.mkdir()
    external_config = outside / "agentos.toml"
    external_config.write_text('schema_version = "external"\n')
    (tmp_path / "config").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrandMigrationError, match="must not be a symlink"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / "config").is_symlink()
    assert external_config.read_text() == 'schema_version = "external"\n'
    assert not (tmp_path / "ops").exists()


@pytest.mark.parametrize("component", ["ops", "backups"])
def test_brand_migration_rejects_symlinked_backup_components_without_external_write(
    tmp_path: Path,
    component: str,
) -> None:
    _legacy_workspace(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-{component}"
    outside.mkdir()
    if component == "ops":
        (tmp_path / "ops").symlink_to(outside, target_is_directory=True)
    else:
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "backups").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrandMigrationError, match="Backup directory is unsafe"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert list(outside.iterdir()) == []
    assert (tmp_path / ".agentos" / "installation.json").is_file()
    assert not (tmp_path / ".raytsystem").exists()


def test_brand_migration_rejects_nested_state_symlink_before_backup(tmp_path: Path) -> None:
    _legacy_workspace(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-nested"
    outside.mkdir()
    marker = outside / "manifest.json"
    marker.write_text('{"agentos":"external"}')
    (tmp_path / ".agentos" / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BrandMigrationError, match="must not be a symlink"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert marker.read_text() == '{"agentos":"external"}'
    assert not (tmp_path / "ops").exists()
    assert not (tmp_path / ".raytsystem").exists()


def test_brand_migration_rolls_namespace_back_before_metadata_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_workspace(tmp_path)
    original_config = (tmp_path / "config" / "agentos.toml").read_bytes()
    original_metadata = (tmp_path / ".agentos" / "installation.json").read_bytes()
    real_write = brand_migration_module.write_bytes_atomic

    def fail_only_in_current_namespace(path: Path, data: bytes, *, mode: int = 0o644) -> None:
        if ".raytsystem" in path.parts:
            raise OSError("injected metadata failure")
        real_write(path, data, mode=mode)

    monkeypatch.setattr(
        brand_migration_module,
        "write_bytes_atomic",
        fail_only_in_current_namespace,
    )

    with pytest.raises(BrandMigrationError, match="pre-migration backup"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / "config" / "agentos.toml").read_bytes() == original_config
    assert (tmp_path / ".agentos" / "installation.json").read_bytes() == original_metadata
    assert not (tmp_path / "config" / "raytsystem.toml").exists()
    assert not (tmp_path / ".raytsystem").exists()
    assert len(list((tmp_path / "ops" / "backups").glob("*.zip"))) == 1


def test_brand_migration_rejects_in_place_mutation_after_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_workspace(tmp_path)
    legacy_config = tmp_path / "config" / "agentos.toml"
    real_create_backup = brand_migration_module._create_backup

    def create_backup_then_mutate(
        root: Path,
        files: tuple[brand_migration_module._FileSnapshot, ...],
    ) -> tuple[Path, tuple[brand_migration_module._FileSnapshot, ...]]:
        result = real_create_backup(root, files)
        legacy_config.write_text('schema_version = "9.9.9"\n')
        return result

    monkeypatch.setattr(
        brand_migration_module,
        "_create_backup",
        create_backup_then_mutate,
    )

    with pytest.raises(BrandMigrationError, match=r"changed|differs"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert legacy_config.read_text() == 'schema_version = "9.9.9"\n'
    assert not (tmp_path / "config" / "raytsystem.toml").exists()
    assert (tmp_path / ".agentos" / "installation.json").is_file()
    assert not (tmp_path / ".raytsystem").exists()
    assert len(list((tmp_path / "ops" / "backups").glob("*.zip"))) == 1


def test_brand_migration_does_not_replace_concurrent_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_workspace(tmp_path)
    third_party = b'third_party = "preserve me"\n'
    real_rename = brand_migration_module._rename_no_replace_at
    injected = False

    def inject_destination(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal injected
        if destination_name == "raytsystem.toml" and not injected:
            injected = True
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_fd,
            )
            try:
                os.write(descriptor, third_party)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        real_rename(source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(
        brand_migration_module,
        "_rename_no_replace_at",
        inject_destination,
    )

    with pytest.raises(BrandMigrationError, match="pre-migration backup"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / "config" / "agentos.toml").is_file()
    assert (tmp_path / "config" / "raytsystem.toml").read_bytes() == third_party
    assert (tmp_path / ".agentos" / "installation.json").is_file()
    assert not (tmp_path / ".raytsystem").exists()


def test_brand_migration_does_not_replace_concurrent_rollback_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_workspace(tmp_path)
    original_config = (tmp_path / "config" / "agentos.toml").read_bytes()
    third_party = b'third_party = "rollback source"\n'
    real_rename = brand_migration_module._rename_no_replace_at
    state_failure_injected = False

    def inject_state_destination_and_rollback_source(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal state_failure_injected
        if destination_name == ".raytsystem" and not state_failure_injected:
            state_failure_injected = True
            os.mkdir(destination_name, mode=0o700, dir_fd=destination_fd)
            config_descriptor = os.open(
                tmp_path / "config",
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                descriptor = os.open(
                    "agentos.toml",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=config_descriptor,
                )
                try:
                    os.write(descriptor, third_party)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(config_descriptor)
        real_rename(source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(
        brand_migration_module,
        "_rename_no_replace_at",
        inject_state_destination_and_rollback_source,
    )

    with pytest.raises(BrandMigrationError, match="automatic rollback was incomplete"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert (tmp_path / "config" / "agentos.toml").read_bytes() == third_party
    assert (tmp_path / "config" / "raytsystem.toml").read_bytes() == original_config
    assert (tmp_path / ".agentos" / "installation.json").is_file()
    assert (tmp_path / ".raytsystem").is_dir()


def test_brand_migration_rejects_parent_swap_before_namespace_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_workspace(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-parent-race"
    outside.mkdir()
    real_open_directory = brand_migration_module._open_relative_directory
    swapped = False

    def swap_parent_then_open(root: Path, relative: Path) -> int:
        nonlocal swapped
        if relative == Path("config") and not swapped:
            swapped = True
            (root / "config").rename(root / "config-before-swap")
            (root / "config").symlink_to(outside, target_is_directory=True)
        return real_open_directory(root, relative)

    monkeypatch.setattr(
        brand_migration_module,
        "_open_relative_directory",
        swap_parent_then_open,
    )

    with pytest.raises(BrandMigrationError, match="pre-migration backup"):
        migrate_legacy_workspace(tmp_path, confirm=True)

    assert list(outside.iterdir()) == []
    assert (tmp_path / "config").is_symlink()
    assert (tmp_path / "config-before-swap" / "agentos.toml").is_file()
    assert not (tmp_path / "config-before-swap" / "raytsystem.toml").exists()
    assert (tmp_path / ".agentos" / "installation.json").is_file()
