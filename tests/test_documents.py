from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

import raytsystem.documents.history as document_history_module
import raytsystem.documents.service as document_service_module
from raytsystem.contracts import sha256_hex
from raytsystem.documents.config import load_document_config
from raytsystem.documents.contracts import (
    DocumentConfigError,
    DocumentConflict,
    DocumentIndexError,
    DocumentNotFound,
    DocumentPolicyError,
    DocumentRestricted,
)
from raytsystem.documents.history import DocumentHistory
from raytsystem.documents.index import DocumentIndex
from raytsystem.documents.markdown import extract_markdown_metadata, parse_frontmatter
from raytsystem.documents.service import DocumentService
from raytsystem.webapp.document_dto import DocumentCreateRequest


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge" / "manual" / "nested").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text(
        """
[documents]
index_db = ".raytsystem/documents.sqlite"
max_files = 100000
max_file_bytes = 5242880
max_total_bytes = 536870912
search_page_size = 50
search_timeout_ms = 500
allow_maintainer_docs_write = true

[[documents.roots]]
id = "manual"
path = "knowledge/manual"
mode = "read_write"
kind = "notes"

[[documents.roots]]
id = "docs"
path = "docs"
mode = "read_write"
kind = "documentation"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _seed(root: Path) -> None:
    (root / "knowledge" / "manual" / "Alpha.md").write_text(
        "---\ntitle: Альфа\ntags: [тест]\naliases: [First]\ndate: 2026-07-12\n---\n"
        "# Альфа\n\nКириллический контент. [[Beta|Бета]]\n",  # noqa: RUF001
        encoding="utf-8",
    )
    (root / "knowledge" / "manual" / "Beta.md").write_text(
        "# Beta\n\nBack to [[Альфа]].\n",
        encoding="utf-8",
    )


def _git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("git is unavailable")
    completed = subprocess.run(
        (
            executable,
            "-C",
            str(root),
            "-c",
            "user.name=raytsystem Test",
            "-c",
            "user.email=raytsystem@example.invalid",
            *arguments,
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_config_rejects_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text(
        """
[documents]
[[documents.roots]]
id = "everything"
path = "."
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DocumentConfigError):
        load_document_config(tmp_path)


@pytest.mark.parametrize("unsafe_root", ["Knowledge", "_RAW", "Ledger", "Docs"])
def test_config_rejects_casefolded_protected_aliases(
    tmp_path: Path,
    unsafe_root: str,
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text(
        f"""
[documents]
allow_maintainer_docs_write = true
[[documents.roots]]
id = "unsafe"
path = "{unsafe_root}"
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DocumentConfigError):
        load_document_config(tmp_path)


def test_generated_root_id_is_accepted_by_policy_dto_and_create(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text(
        """
[documents]
[[documents.roots]]
path = "notes"
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config = load_document_config(tmp_path)
    root_id = config.roots[0].root_id
    assert root_id.startswith("droot_")
    DocumentCreateRequest(
        root_id=root_id,
        name="Generated.md",
        expected_snapshot_id="docsnap_" + "0" * 64,
    )
    index = DocumentIndex(tmp_path, config=config)
    snapshot = index.rebuild()["snapshot_id"]
    assert isinstance(snapshot, str)
    result = DocumentService(tmp_path, index=index).create(
        root_id=root_id,
        name="Generated.md",
        expected_snapshot_id=snapshot,
        idempotency_key="generated-root-create",
    )
    assert result["document"]["path"] == "notes/Generated.md"


def test_controls_and_bidi_are_rejected_from_config_scan_and_writes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "knowledge" / "manual" / "bad\nname.md").write_text("# hidden\n", encoding="utf-8")
    index = DocumentIndex(root)
    status = index.rebuild()
    assert status["file_count"] == 0
    assert status["error_count"] >= 1
    snapshot = status["snapshot_id"]
    assert isinstance(snapshot, str)
    service = DocumentService(root, index=index)
    with pytest.raises(DocumentPolicyError):
        service.create_folder(
            root_id="manual",
            folder="bad\tfolder",
            expected_snapshot_id=snapshot,
            idempotency_key="control-folder",
        )
    with pytest.raises(DocumentPolicyError):
        service.create(
            root_id="manual",
            folder="safe\u202eunsafe",
            name="Note.md",
            expected_snapshot_id=snapshot,
            idempotency_key="bidi-folder",
        )


def test_markdown_metadata_dates_nonfinite_and_images() -> None:
    text = "---\ndate: 2026-07-12\nscore: .nan\n---\n# Title\n"
    frontmatter, _body, warnings = parse_frontmatter(text)
    assert frontmatter == {}
    assert warnings == ("frontmatter_invalid",)

    metadata = extract_markdown_metadata(
        "---\ndate: 2026-07-12\n---\n# Title\n![Alt](images/p.png)\n",
        path="notes/Test.md",
    )
    assert metadata.properties["date"] == "2026-07-12"
    assert metadata.links[0].link_type == "markdown_image"
    assert metadata.links[0].embed is True


def test_index_search_links_folders_and_cyrillic(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _seed(root)
    index = DocumentIndex(root)
    status = index.rebuild()
    assert status["state"] == "current"
    assert status["file_count"] == 2

    result = index.search("кириллический tag:тест")
    assert [item["title"] for item in result["items"]] == ["Альфа"]
    assert result["items"][0]["can_edit"] is True
    alpha = result["items"][0]

    outgoing = index.links(alpha["document_id"])
    assert outgoing["items"][0]["target_document_id"] is not None
    assert outgoing["items"][0]["label"] == "Бета"
    backlinks = index.links(alpha["document_id"], backlinks=True)
    assert backlinks["items"][0]["source_title"] == "Beta"

    folders = index.folders(root_id="manual")
    assert folders["items"][0]["path"] == "knowledge/manual/nested"
    assert result["folders"][0]["folder_id"].startswith("dfolder_")


def test_search_new_modified_and_document_id_filter(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _seed(root)
    index = DocumentIndex(root)
    index.rebuild()
    items = index.list_documents(sort="name_asc")["items"]
    alpha = items[0]
    beta = items[1]
    assert {item["document_id"] for item in index.search("is:new")["items"]} == {
        alpha["document_id"],
        beta["document_id"],
    }
    (root / alpha["path"]).write_text("# external change\n", encoding="utf-8")
    index.refresh((alpha["path"],))
    modified = index.search("is:modified")["items"]
    assert [item["document_id"] for item in modified] == [alpha["document_id"]]
    assert modified[0]["modified_source"] == "index_hash_or_mtime"
    assert modified[0]["is_new"] is True
    selected = index.list_documents(document_ids=(beta["document_id"],))
    assert [item["document_id"] for item in selected["items"]] == [beta["document_id"]]
    with pytest.raises(DocumentIndexError):
        index.list_documents(document_ids=("doc_invalid",))


def test_snapshot_changes_for_mtime_only_projection_change(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    path = root / "knowledge" / "manual" / "Touch.md"
    path.write_text("# Same\n", encoding="utf-8")
    index = DocumentIndex(root)
    before = index.rebuild()["snapshot_id"]
    metadata = path.stat()
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000))
    after = index.refresh(("knowledge/manual/Touch.md",))["snapshot_id"]
    assert after != before


def test_ambiguous_link_candidates_are_bounded_with_exact_count(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    manual = root / "knowledge" / "manual"
    for number in range(25):
        folder = manual / f"folder-{number:02d}"
        folder.mkdir()
        (folder / "README.md").write_text("# README\n", encoding="utf-8")
    (manual / "Hub.md").write_text("# Hub\n\n[[README]]\n", encoding="utf-8")
    index = DocumentIndex(root)
    index.rebuild()
    hub = next(
        item for item in index.list_documents(limit=50)["items"] if item["filename"] == "Hub.md"
    )
    outgoing = index.links(hub["document_id"])["items"][0]
    assert outgoing["ambiguous"] is True
    assert outgoing["candidate_count"] == 25
    assert len(outgoing["candidates"]) == 20


def test_markdown_relative_links_resolve_per_source_folder(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    manual = root / "knowledge" / "manual"
    for folder_name in ("one", "two"):
        folder = manual / folder_name
        (folder / "images").mkdir(parents=True)
        (folder / "images" / "picture.md").write_text(
            f"# Picture {folder_name}\n", encoding="utf-8"
        )
        (folder / "Note.md").write_text(
            "# Note\n\n[Picture](images/picture.md)\n", encoding="utf-8"
        )
    index = DocumentIndex(root)
    index.rebuild()
    notes = [
        item for item in index.list_documents(limit=50)["items"] if item["filename"] == "Note.md"
    ]
    for note in notes:
        outgoing = index.links(note["document_id"])["items"][0]
        assert outgoing["ambiguous"] is False
        target = index.detail(outgoing["target_document_id"])["document"]
        assert target["path"] == str(PurePosixPath(note["path"]).parent / "images/picture.md")


def test_symlink_is_never_indexed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    os.symlink(outside, root / "knowledge" / "manual" / "linked.md")
    index = DocumentIndex(root)
    status = index.rebuild()
    assert status["file_count"] == 0
    assert status["error_count"] >= 1


def test_incremental_refresh_adds_and_removes_folder_projection(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    index = DocumentIndex(root)
    index.rebuild()
    external = root / "knowledge" / "manual" / "external"
    external.mkdir()
    document = external / "New.md"
    document.write_text("# New\n", encoding="utf-8")
    index.refresh(("knowledge/manual/external/New.md",))
    assert [item["path"] for item in index.folders(root_id="manual")["items"]] == [
        "knowledge/manual/external",
        "knowledge/manual/nested",
    ]
    document.unlink()
    external.rmdir()
    index.refresh(("knowledge/manual/external/New.md",))
    assert [item["path"] for item in index.folders(root_id="manual")["items"]] == [
        "knowledge/manual/nested"
    ]


def test_service_cas_global_snapshot_rebase_and_stable_rename(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _seed(root)
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    items = index.list_documents(sort="name_asc")["items"]
    alpha = next(item for item in items if item["filename"] == "Alpha.md")
    beta = next(item for item in items if item["filename"] == "Beta.md")
    opened_snapshot = index.status()["snapshot_id"]
    assert isinstance(opened_snapshot, str)

    service.update(
        alpha["document_id"],
        content="# Alpha changed\n",
        expected_sha256=alpha["content_sha256"],
        expected_snapshot_id=opened_snapshot,
        idempotency_key="update-alpha-1",
    )
    # A global snapshot changed, but B's opaque mapping and content CAS still match.
    result = service.update(
        beta["document_id"],
        content="# Beta changed\n",
        expected_sha256=beta["content_sha256"],
        expected_snapshot_id=opened_snapshot,
        idempotency_key="update-beta-1",
    )
    assert result["document"]["filename"] == "Beta.md"

    renamed = service.rename(
        beta["document_id"],
        new_name="Renamed.md",
        expected_sha256=result["content_sha256"],
        expected_snapshot_id=result["snapshot_id"],
        idempotency_key="rename-beta-1",
    )
    assert renamed["document"]["document_id"] == beta["document_id"]
    assert renamed["document"]["path"].endswith("Renamed.md")


@pytest.mark.parametrize("operation", ["create", "create_folder"])
def test_stale_create_idempotency_key_cannot_rebind_snapshot(
    tmp_path: Path,
    operation: str,
) -> None:
    root = _workspace(tmp_path)
    (root / "knowledge" / "manual" / "Existing.md").write_text("# Existing\n", encoding="utf-8")
    index = DocumentIndex(root)
    stale_snapshot = index.rebuild()["snapshot_id"]
    assert isinstance(stale_snapshot, str)
    (root / "knowledge" / "manual" / "Existing.md").write_text("# Changed\n", encoding="utf-8")
    current_snapshot = index.refresh(("knowledge/manual/Existing.md",))["snapshot_id"]
    assert isinstance(current_snapshot, str) and current_snapshot != stale_snapshot
    service = DocumentService(root, index=index)
    if operation == "create":
        callback = lambda snapshot: service.create(  # noqa: E731
            root_id="manual",
            name="Bound.md",
            expected_snapshot_id=snapshot,
            idempotency_key="stale-bound-create",
        )
    else:
        callback = lambda snapshot: service.create_folder(  # noqa: E731
            root_id="manual",
            folder="bound-folder",
            expected_snapshot_id=snapshot,
            idempotency_key="stale-bound-folder",
        )
    with pytest.raises(DocumentConflict):
        callback(stale_snapshot)
    with pytest.raises(DocumentConflict):
        callback(current_snapshot)


def test_update_stale_hash_noop_and_external_secret_drift(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "knowledge" / "manual" / "Note.md").write_text("# Note\n", encoding="utf-8")
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    note = index.list_documents()["items"][0]
    snapshot = index.status()["snapshot_id"]
    assert isinstance(snapshot, str)

    with pytest.raises(DocumentConflict):
        service.update(
            note["document_id"],
            content="# Note\n",
            expected_sha256="0" * 64,
            expected_snapshot_id=snapshot,
            idempotency_key="stale-noop",
        )

    secret = b"OPENAI_API_KEY=" + b"sk-" + b"proj-" + b"z" * 32 + b"\n"
    (root / "knowledge" / "manual" / "Note.md").write_bytes(secret)
    with pytest.raises(DocumentRestricted):
        service.update(
            note["document_id"],
            content="# replacement\n",
            expected_sha256=sha256_hex(secret),
            expected_snapshot_id=snapshot,
            idempotency_key="external-secret",
        )
    records = root / "ops" / "document-revisions" / "records"
    assert not records.exists() or not list(records.glob("*.json"))


def test_history_ownership_preview_and_restore(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _seed(root)
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    items = index.list_documents(sort="name_asc")["items"]
    alpha = next(item for item in items if item["filename"] == "Alpha.md")
    beta = next(item for item in items if item["filename"] == "Beta.md")
    snapshot = index.status()["snapshot_id"]
    assert isinstance(snapshot, str)
    changed = service.update(
        alpha["document_id"],
        content="# Changed\n",
        expected_sha256=alpha["content_sha256"],
        expected_snapshot_id=snapshot,
        idempotency_key="history-update",
    )
    revision_id = changed["revision_id"]
    assert isinstance(revision_id, str)
    history = DocumentHistory(root, index=index)
    with pytest.raises(DocumentNotFound):
        history.revision_bytes(
            revision_id,
            document_id=beta["document_id"],
            max_bytes=5 * 1024 * 1024,
        )

    preview = service.restore_preview(
        alpha["document_id"],
        history_id=revision_id,
        expected_sha256=changed["content_sha256"],
        expected_snapshot_id=changed["snapshot_id"],
    )
    restored = service.restore(
        alpha["document_id"],
        history_id=revision_id,
        expected_sha256=changed["content_sha256"],
        expected_snapshot_id=changed["snapshot_id"],
        preview_token=preview["preview_token"],
        confirmed=True,
        idempotency_key="history-restore",
    )
    assert restored["content_sha256"] == alpha["content_sha256"]


def test_git_history_follows_rename_and_reads_historical_path(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    source = root / "knowledge" / "manual" / "Before.md"
    source.write_text("# Before\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "knowledge/manual/Before.md")
    _git(root, "commit", "-m", "initial document")
    initial_commit = _git(root, "rev-parse", "HEAD")
    _git(root, "mv", "knowledge/manual/Before.md", "knowledge/manual/After.md")
    _git(root, "commit", "-m", "rename document")
    index = DocumentIndex(root)
    index.rebuild()
    document = index.list_documents()["items"][0]
    history = DocumentHistory(root, index=index)
    entries = history.list(document["document_id"])["items"]
    initial = next(item for item in entries if item["commit_sha"] == initial_commit)
    assert initial["historical_path"] == "knowledge/manual/Before.md"
    detail = history.detail(document["document_id"], f"git:{initial_commit}")
    assert detail["content"] == "# Before\n"


def test_oversized_git_blob_is_rejected_before_blob_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    source = root / "knowledge" / "manual" / "Large.md"
    source.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
    _git(root, "init")
    _git(root, "add", "knowledge/manual/Large.md")
    _git(root, "commit", "-m", "large version")
    large_commit = _git(root, "rev-parse", "HEAD")
    source.write_text("# Small\n", encoding="utf-8")
    _git(root, "add", "knowledge/manual/Large.md")
    _git(root, "commit", "-m", "small version")
    index = DocumentIndex(root)
    index.rebuild()
    document = index.list_documents()["items"][0]
    observed: list[tuple[str, ...]] = []
    real_run = document_history_module.run_bounded

    def observing_run(arguments: tuple[str, ...], **kwargs: object) -> object:
        observed.append(tuple(arguments))
        return real_run(arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(document_history_module, "run_bounded", observing_run)
    with pytest.raises(DocumentNotFound):
        DocumentHistory(root, index=index).version_bytes(
            document["document_id"],
            f"git:{large_commit}",
            max_bytes=5 * 1024 * 1024,
        )
    assert not any("cat-file" in arguments and "blob" in arguments for arguments in observed)


def test_git_status_disables_repo_fsmonitor_and_lazy_fetch(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _git(root, "init")
    hook = root / "fsmonitor-sentinel.sh"
    sentinel = root / "fsmonitor-called"
    hook.write_text('#!/bin/sh\n: > "${0%/*}/fsmonitor-called"\n', encoding="utf-8")
    hook.chmod(0o700)
    _git(root, "config", "core.fsmonitor", str(hook))
    (root / "knowledge" / "manual" / "Safe.md").write_text("# Safe\n", encoding="utf-8")
    DocumentIndex(root).rebuild()
    assert not sentinel.exists()
    environment = document_history_module.hardened_git_environment()
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull


def test_image_dimension_bomb_is_not_exposed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (50_000).to_bytes(4, "big")
        + (50_000).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\x00\x00\x00\x00"
    )
    (root / "knowledge" / "manual" / "huge.png").write_bytes(png)
    index = DocumentIndex(root)
    index.rebuild()
    item = index.list_documents()["items"][0]
    detail = index.detail(item["document_id"])
    assert detail["format"] == "unsupported"
    assert detail["asset_url"] is None
    with pytest.raises(DocumentNotFound):
        index.asset_bytes(item["document_id"])


def test_create_update_folder_and_move_recover_after_projection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    initial_snapshot = index.status()["snapshot_id"]
    assert isinstance(initial_snapshot, str)

    real_refresh = index.refresh
    failures = 0

    def fail_refresh(paths: tuple[str, ...] = ()) -> dict[str, object]:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise DocumentIndexError("injected refresh failure")
        return real_refresh(paths)

    monkeypatch.setattr(index, "refresh", fail_refresh)
    with pytest.raises(DocumentIndexError):
        service.create(
            root_id="manual",
            name="Empty.md",
            content="",
            expected_snapshot_id=initial_snapshot,
            idempotency_key="recover-create",
        )
    created = service.create(
        root_id="manual",
        name="Empty.md",
        content="",
        expected_snapshot_id=initial_snapshot,
        idempotency_key="recover-create",
    )
    assert created["content_sha256"] == sha256_hex(b"")

    monkeypatch.setattr(index, "refresh", real_refresh)
    current = index.detail(created["document"]["document_id"])
    failures = 0
    monkeypatch.setattr(index, "refresh", fail_refresh)
    with pytest.raises(DocumentIndexError):
        service.update(
            created["document"]["document_id"],
            content="# recovered\n",
            expected_sha256=current["content_sha256"],
            expected_snapshot_id=current["snapshot_id"],
            idempotency_key="recover-update",
        )
    updated = service.update(
        created["document"]["document_id"],
        content="# recovered\n",
        expected_sha256=current["content_sha256"],
        expected_snapshot_id=current["snapshot_id"],
        idempotency_key="recover-update",
    )
    assert updated["snapshot_rebased"] is False

    monkeypatch.setattr(index, "refresh", real_refresh)
    failures = 0
    monkeypatch.setattr(index, "refresh", fail_refresh)
    with pytest.raises(DocumentIndexError):
        service.rename(
            created["document"]["document_id"],
            new_name="Recovered.md",
            expected_sha256=updated["content_sha256"],
            expected_snapshot_id=updated["snapshot_id"],
            idempotency_key="recover-move",
        )
    renamed = service.rename(
        created["document"]["document_id"],
        new_name="Recovered.md",
        expected_sha256=updated["content_sha256"],
        expected_snapshot_id=updated["snapshot_id"],
        idempotency_key="recover-move",
    )
    assert renamed["document"]["document_id"] == created["document"]["document_id"]

    monkeypatch.setattr(index, "refresh", real_refresh)
    real_rebuild = index.rebuild
    rebuild_calls = 0

    def fail_rebuild() -> dict[str, object]:
        nonlocal rebuild_calls
        rebuild_calls += 1
        if rebuild_calls == 1:
            raise DocumentIndexError("injected rebuild failure")
        return real_rebuild()

    monkeypatch.setattr(index, "rebuild", fail_rebuild)
    with pytest.raises(DocumentIndexError):
        service.create_folder(
            root_id="manual",
            folder="recovered-folder",
            expected_snapshot_id=renamed["snapshot_id"],
            idempotency_key="recover-folder",
        )
    folder = service.create_folder(
        root_id="manual",
        folder="recovered-folder",
        expected_snapshot_id=renamed["snapshot_id"],
        idempotency_key="recover-folder",
    )
    assert folder["folder"].endswith("recovered-folder")


def test_atomic_move_detects_replacement_inside_critical_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    source = root / "knowledge" / "manual" / "Race.md"
    source.write_text("# approved\n", encoding="utf-8")
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    item = index.list_documents()["items"][0]
    real_link = os.link

    def racing_link(*args: object, **kwargs: object) -> None:
        source.write_text("# unapproved replacement\n", encoding="utf-8")
        real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(DocumentConflict):
        service._atomic_move(
            "knowledge/manual/Race.md",
            "knowledge/manual/Moved.md",
            expected_sha256=item["content_sha256"],
            document_id=item["document_id"],
            snapshot_id=index.status()["snapshot_id"],
        )
    assert not (root / "knowledge" / "manual" / "Moved.md").exists()


def test_atomic_update_exchange_preserves_concurrent_external_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    source = root / "knowledge" / "manual" / "Race.md"
    source.write_text("# approved\n", encoding="utf-8")
    index = DocumentIndex(root)
    index.rebuild()
    service = DocumentService(root, index=index)
    item = index.list_documents()["items"][0]
    snapshot = index.status()["snapshot_id"]
    assert isinstance(snapshot, str)
    real_exchange = document_service_module._exchange_names
    raced = False

    def racing_exchange(parent_fd: int, left: str, right: str) -> None:
        nonlocal raced
        if not raced:
            raced = True
            external = source.with_name("external.tmp")
            external.write_text("# external concurrent edit\n", encoding="utf-8")
            os.replace(external, source)
        real_exchange(parent_fd, left, right)

    monkeypatch.setattr(document_service_module, "_exchange_names", racing_exchange)
    with pytest.raises(DocumentConflict):
        service.update(
            item["document_id"],
            content="# raytsystem edit\n",
            expected_sha256=item["content_sha256"],
            expected_snapshot_id=snapshot,
            idempotency_key="exchange-race",
        )
    assert source.read_text(encoding="utf-8") == "# external concurrent edit\n"
