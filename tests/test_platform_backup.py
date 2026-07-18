"""Verifiable backup bundles: kind surfaces, disclosure gates, safe restore."""

from __future__ import annotations

from contextlib import closing

import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import raytsystem.backup.service as backup_service_module
from platform_helpers import make_platform_workspace
from raytsystem.backup import BackupError, BackupService
from raytsystem.contracts import sha256_hex
from raytsystem.contracts.lifecycle import BackupKind, EncryptedBlob
from raytsystem.platform_store import initialize_platform_store

pytestmark = pytest.mark.filterwarnings("error")


def _seed_workspace(root: Path) -> Path:
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (root / "website" / "docs").mkdir(parents=True)
    (root / "website" / "docs" / "documents.md").write_text("documents\n", encoding="utf-8")
    (root / "knowledge" / "manual").mkdir(parents=True)
    (root / "knowledge" / "manual" / "note.md").write_text("note\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "_raw").mkdir()
    (root / "_raw" / "source.bin").write_bytes(b"raw-bytes")
    (root / "normalized").mkdir()
    (root / "normalized" / "item.json").write_text('{"ok": true}\n', encoding="utf-8")
    (root / "ledger").mkdir()
    (root / "ledger" / "CURRENT").write_text("genesis\n", encoding="utf-8")
    (root / "ops" / "runs").mkdir(parents=True)
    (root / "ops" / "runs" / "run.json").write_text('{"run": 1}\n', encoding="utf-8")
    (root / "ops" / "events").mkdir(parents=True)
    (root / "ops" / "events" / "event.json").write_text('{"event": 1}\n', encoding="utf-8")
    (root / "ops" / "document-revisions").mkdir(parents=True)
    (root / "ops" / "document-revisions" / "revision.json").write_text(
        '{"content_sha256": "test"}\n', encoding="utf-8"
    )
    return root


def _crafted_bundle(path: Path, member_name: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META/manifest.json", "{}")
        archive.writestr("META/redaction-report.json", "{}")
        archive.writestr(member_name, "evil")
    return path


def _encrypted_blob_json() -> str:
    blob = EncryptedBlob(
        blob_id="blob_test",
        key_provider_id="provider_local",
        key_id="key_local",
        algorithm="aes-256-gcm",
        encrypted_data_key="ZGF0YS1rZXk=",
        nonce="bm9uY2U=",
        ciphertext="Y2lwaGVy",
        authentication_tag="dGFn",
        plaintext_sha256="0" * 64,
        associated_data_sha256="1" * 64,
        created_at=datetime.now(UTC),
    )
    return blob.model_dump_json()


@pytest.mark.parametrize("member_name", ["../evil.txt", "sub/../../evil.txt", "/abs/evil.txt"])
def test_backup_path_traversal_member_is_rejected_before_write(
    tmp_path: Path, member_name: str
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    bundle = _crafted_bundle(root / "bundles" / "crafted.zip", member_name)
    destination = root / "restored" / "target"
    with pytest.raises(BackupError):
        service.restore_plan(bundle, destination)
    with pytest.raises(BackupError):
        service.restore(bundle, destination)
    assert not destination.exists()
    assert not (root / "restored").exists()


def test_restore_refuses_non_empty_destination(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    bundle = root / "bundles" / "private.zip"
    service.create(bundle)
    destination = root / "restored" / "occupied"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("keep\n", encoding="utf-8")
    plan = service.restore_plan(bundle, destination)
    assert plan.conflicts == ("keep.txt",)
    with pytest.raises(BackupError, match="empty destination"):
        service.restore(bundle, destination)
    assert [path.name for path in destination.iterdir()] == ["keep.txt"]
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_backup_create_enforces_member_and_aggregate_limits_while_collecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    monkeypatch.setattr(backup_service_module, "_MAX_BUNDLE_MEMBERS", 4)
    with pytest.raises(BackupError, match="too many source members"):
        service.create(root / "bundles" / "too-many.zip")
    assert not (root / "bundles" / "too-many.zip").exists()

    monkeypatch.setattr(backup_service_module, "_MAX_BUNDLE_MEMBERS", 150_000)
    monkeypatch.setattr(backup_service_module, "_MAX_BUNDLE_BYTES", 8)
    with pytest.raises(BackupError, match="source bytes"):
        service.create(root / "bundles" / "too-large.zip")
    assert not (root / "bundles" / "too-large.zip").exists()


def test_private_backup_rechecks_limits_after_platform_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    with initialize_platform_store(root):
        pass
    service = BackupService(root)

    def collect_one(
        _roots: tuple[str, ...],
        *,
        spool: Path,
        redact_secrets: bool,
        redact_paths: bool,
    ) -> tuple[dict[str, Path], dict[str, list[str]]]:
        assert redact_secrets is False
        assert redact_paths is False
        member = spool / "small"
        member.write_bytes(b"ok")
        return {"AGENTS.md": member}, {"removed": [], "redacted": [], "unresolved": []}

    def oversized_snapshot(path: Path, *, max_bytes: int) -> bool:
        assert max_bytes == 32
        path.write_bytes(b"x" * 64)
        return True

    monkeypatch.setattr(service, "_collect", collect_one)
    monkeypatch.setattr(service, "_platform_snapshot_file", oversized_snapshot)
    monkeypatch.setattr(backup_service_module, "_MAX_MEMBER_BYTES", 32)

    destination = root / "bundles" / "platform-too-large.zip"
    with pytest.raises(BackupError, match="member size"):
        service.create(destination)

    assert not destination.exists()


def test_private_backup_rejects_symlinked_platform_store_before_publication(
    tmp_path: Path,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    outside = tmp_path / "outside.sqlite"
    with closing(sqlite3.connect(outside)) as connection, connection:
        connection.execute("CREATE TABLE private(value TEXT)")
        connection.execute("INSERT INTO private VALUES ('outside-secret')")
    platform = root / "ops" / "platform.sqlite"
    platform.unlink(missing_ok=True)
    platform.symlink_to(outside)

    destination = root / "bundles" / "unsafe-platform.zip"
    with pytest.raises(BackupError, match="unsafe or unavailable"):
        BackupService(root).create(destination)

    assert not destination.exists()


def test_backup_publish_is_no_replace_and_preserves_concurrent_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    destination = root / "bundles" / "concurrent.zip"
    original_link = os.link

    def racing_link(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(b"concurrent-owner")
        raise FileExistsError

    monkeypatch.setattr(backup_service_module.os, "link", racing_link)
    with pytest.raises(BackupError, match="already exists"):
        BackupService(root).create(destination, kind=BackupKind.PUBLIC)
    monkeypatch.setattr(backup_service_module.os, "link", original_link)

    assert destination.read_bytes() == b"concurrent-owner"


def test_backup_audit_failure_removes_only_its_published_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    destination = root / "bundles" / "audit-failed.zip"

    def unavailable_store(_root: Path) -> None:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(backup_service_module, "initialize_platform_store", unavailable_store)
    with pytest.raises(RuntimeError, match="store unavailable"):
        BackupService(root).create(destination, kind=BackupKind.PUBLIC)

    assert not destination.exists()


def test_export_absolute_path_leak_is_redacted_in_public_only(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    abs_root = str(service.root)
    (root / "docs" / "note.md").write_text(f"workspace at {abs_root}/docs\n", encoding="utf-8")
    (root / "normalized" / "note.json").write_text(
        json.dumps({"path": f"{abs_root}/normalized"}), encoding="utf-8"
    )
    public_bundle = root / "bundles" / "public.zip"
    manifest = service.create(public_bundle, kind=BackupKind.PUBLIC)
    assert abs_root.encode("utf-8") not in public_bundle.read_bytes()
    with zipfile.ZipFile(public_bundle) as archive:
        for name in archive.namelist():
            assert abs_root.encode("utf-8") not in archive.read(name)
        redaction = json.loads(archive.read("META/redaction-report.json"))
    assert "docs/note.md" in manifest.file_hashes
    assert "docs/note.md" in redaction["redacted"]
    assert redaction["unresolved"] == []
    assert service.leak_scan(public_bundle)["passed"] is True
    private_bundle = root / "bundles" / "private.zip"
    service.create(private_bundle)
    with zipfile.ZipFile(private_bundle) as archive:
        private_note = archive.read("normalized/note.json")
        # JSON escapes backslashes, so Windows paths appear as C:\\... inside
        json_escaped_root = json.dumps(abs_root)[1:-1].encode("utf-8")
        assert abs_root.encode("utf-8") in private_note or json_escaped_root in private_note


def test_public_export_removes_secret_files_and_keeps_notices(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    (root / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (root / "NOTICE").write_text("Third-party notices\n", encoding="utf-8")
    (root / "docs" / "creds.md").write_text("token AKIA" + "A" * 16 + "\n", encoding="utf-8")
    service = BackupService(root)
    bundle = root / "bundles" / "public.zip"
    manifest = service.create(bundle, kind=BackupKind.PUBLIC)
    assert "docs/creds.md" not in manifest.file_hashes
    assert "LICENSE" in manifest.file_hashes
    assert "NOTICE" in manifest.file_hashes
    with zipfile.ZipFile(bundle) as archive:
        redaction = json.loads(archive.read("META/redaction-report.json"))
    assert "docs/creds.md" in redaction["removed"]


def test_round_trip_restore_is_hash_identical_and_doctor_is_honest(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    with initialize_platform_store(root):
        pass
    service = BackupService(root)
    bundle = root / "bundles" / "private.zip"
    manifest = service.create(bundle)
    assert service.verify(bundle).manifest_sha256 == manifest.manifest_sha256
    destination = root / "restored" / "workspace"
    report = service.restore(bundle, destination)
    assert report.state == "restored"
    assert report.projection_rebuild_required is True
    assert report.doctor_passed is False
    for relative, digest in manifest.file_hashes.items():
        assert sha256_hex((destination / relative).read_bytes()) == digest
        if relative != "ops/platform.sqlite":
            assert (destination / relative).read_bytes() == (root / relative).read_bytes()
    passing = service.restore(
        bundle,
        root / "restored" / "doctored",
        doctor=lambda path: (path / "config" / "platform.yaml").is_file(),
    )
    assert passing.doctor_passed is True

    def broken_doctor(_: Path) -> bool:
        raise RuntimeError("doctor crashed")

    failing = service.restore(bundle, root / "restored" / "undoctored", doctor=broken_doctor)
    assert failing.state == "restored"
    assert failing.doctor_passed is False


def test_tampered_member_fails_verify_and_restore(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    bundle = root / "bundles" / "private.zip"
    service.create(bundle)
    tampered = root / "bundles" / "tampered.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "AGENTS.md":
                data = b"tampered\n"
            target.writestr(info.filename, data)
    with pytest.raises(BackupError, match="hash"):
        service.verify(tampered)
    destination = root / "restored" / "tampered"
    with pytest.raises(BackupError, match="hash"):
        service.restore(tampered, destination)
    assert not destination.exists()


def test_verify_normalizes_missing_redaction_metadata_to_backup_error(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    bundle = root / "bundles" / "private.zip"
    service.create(bundle)
    malformed = root / "bundles" / "missing-redaction.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(malformed, "w") as target:
        for info in source.infolist():
            if info.filename != "META/redaction-report.json":
                target.writestr(info, source.read(info.filename))

    with pytest.raises(BackupError, match="member list"):
        service.verify(malformed)


def test_kind_matrix_includes_exactly_documented_roots(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    with initialize_platform_store(root):
        pass
    service = BackupService(root)
    manifests = {
        kind: service.create(root / "bundles" / f"{kind.value}.zip", kind=kind)
        for kind in BackupKind
    }
    common = {
        "AGENTS.md",
        "config/platform.yaml",
        "_raw/source.bin",
        "normalized/item.json",
        "ledger/CURRENT",
        "ops/events/event.json",
        "ops/runs/run.json",
        "ops/document-revisions/revision.json",
        "knowledge/manual/note.md",
        "docs/guide.md",
        "website/docs/documents.md",
    }
    assert set(manifests[BackupKind.PRIVATE].file_hashes) == common | {"ops/platform.sqlite"}
    assert set(manifests[BackupKind.TRANSFER].file_hashes) == common
    assert "ops/platform.sqlite" in manifests[BackupKind.TRANSFER].excluded_paths
    assert set(manifests[BackupKind.DIAGNOSTIC].file_hashes) == {
        "config/platform.yaml",
        "ops/events/event.json",
        "ops/runs/run.json",
        "ops/platform-status.json",
    }
    assert set(manifests[BackupKind.PUBLIC].file_hashes) == {
        "AGENTS.md",
        "config/platform.yaml",
        "docs/guide.md",
        "website/docs/documents.md",
        "src/module.py",
    }
    with zipfile.ZipFile(root / "bundles" / f"{BackupKind.DIAGNOSTIC.value}.zip") as archive:
        status = json.loads(archive.read("ops/platform-status.json"))
    assert status["features"]["flags"]["backup_enabled"] is True
    assert status["platform_store_snapshot_id"].startswith("pview_")


def test_private_backup_includes_configured_document_roots_but_not_projection(
    tmp_path: Path,
) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    (root / "config" / "raytsystem.toml").write_text(
        """schema_version = "1.4.0"
[documents]
index_db = ".raytsystem/documents.sqlite"
[[documents.roots]]
id = "notes"
path = "notes"
mode = "read_write"
kind = "notes"
""",
        encoding="utf-8",
    )
    (root / "notes").mkdir()
    (root / "notes" / "private-note.md").write_text("private\n", encoding="utf-8")
    (root / ".raytsystem").mkdir()
    (root / ".raytsystem" / "documents.sqlite").write_bytes(b"disposable")

    private = BackupService(root).create(root / "bundles" / "documents-private.zip")
    public = BackupService(root).create(
        root / "bundles" / "documents-public.zip", kind=BackupKind.PUBLIC
    )

    assert "notes/private-note.md" in private.file_hashes
    assert "notes/private-note.md" not in public.file_hashes
    assert ".raytsystem/documents.sqlite" not in private.file_hashes
    assert ".raytsystem/documents.sqlite" in private.excluded_paths


def test_transfer_export_strips_absolute_paths_but_keeps_private_roots(tmp_path: Path) -> None:
    root = _seed_workspace(make_platform_workspace(tmp_path))
    service = BackupService(root)
    abs_root = str(service.root)
    (root / "normalized" / "note.json").write_text(
        json.dumps({"path": f"{abs_root}/normalized"}), encoding="utf-8"
    )
    bundle = root / "bundles" / "transfer.zip"
    manifest = service.create(bundle, kind=BackupKind.TRANSFER)
    assert "normalized/note.json" in manifest.file_hashes
    assert "_raw/source.bin" in manifest.file_hashes
    with zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():
            assert abs_root.encode("utf-8") not in archive.read(name)
        redaction = json.loads(archive.read("META/redaction-report.json"))
    assert "normalized/note.json" in redaction["redacted"]


def test_restricted_inclusion_requires_encryption_flag_and_valid_blobs(tmp_path: Path) -> None:
    plain_root = _seed_workspace(make_platform_workspace(tmp_path / "plain"))
    with pytest.raises(BackupError, match="without encryption"):
        BackupService(plain_root).create(
            plain_root / "bundles" / "restricted.zip", include_restricted_encrypted=True
        )
    root = _seed_workspace(
        make_platform_workspace(
            tmp_path / "encrypted", flag_overrides={"restricted_encryption_enabled": True}
        )
    )
    service = BackupService(root)
    with pytest.raises(BackupError, match="no encrypted blobs"):
        service.create(root / "bundles" / "empty.zip", include_restricted_encrypted=True)
    (root / "ops" / "encrypted").mkdir(parents=True)
    (root / "ops" / "encrypted" / "blob.json").write_text(_encrypted_blob_json(), encoding="utf-8")
    manifest = service.create(
        root / "bundles" / "restricted.zip", include_restricted_encrypted=True
    )
    assert manifest.restricted_data_included is True
    assert manifest.encrypted is True
    assert "ops/encrypted/blob.json" in manifest.file_hashes
    (root / "ops" / "encrypted" / "plain.json").write_text('{"plain": true}', encoding="utf-8")
    with pytest.raises(BackupError, match="not encrypted"):
        service.create(root / "bundles" / "plain.zip", include_restricted_encrypted=True)
    for kind in (BackupKind.PUBLIC, BackupKind.DIAGNOSTIC):
        with pytest.raises(BackupError, match="disclosure"):
            service.create(
                root / "bundles" / f"restricted-{kind.value}.zip",
                kind=kind,
                include_restricted_encrypted=True,
            )


def test_backup_disabled_fails_closed_but_pure_verification_stays(tmp_path: Path) -> None:
    enabled_root = _seed_workspace(make_platform_workspace(tmp_path / "enabled"))
    enabled_service = BackupService(enabled_root)
    bundle = enabled_root / "bundles" / "private.zip"
    manifest = enabled_service.create(bundle)
    disabled_root = make_platform_workspace(
        tmp_path / "disabled", flag_overrides={"backup_enabled": False}
    )
    disabled_service = BackupService(disabled_root)
    destination = disabled_root / "restored" / "workspace"
    with pytest.raises(BackupError, match="disabled"):
        disabled_service.create(disabled_root / "bundles" / "private.zip")
    with pytest.raises(BackupError, match="disabled"):
        disabled_service.restore_plan(bundle, destination)
    with pytest.raises(BackupError, match="disabled"):
        disabled_service.restore(bundle, destination)
    assert not destination.exists()
    assert disabled_service.verify(bundle).backup_id == manifest.backup_id
    scan = disabled_service.leak_scan(bundle)
    assert scan["backup_id"] == manifest.backup_id
