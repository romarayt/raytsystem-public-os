from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from raytsystem.documents.index import DocumentIndex
from raytsystem.webapp import create_app
from raytsystem.webapp.document_routes import create_document_router


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge" / "manual").mkdir(parents=True)
    (tmp_path / "config" / "raytsystem.toml").write_text(
        """
[documents]
search_page_size = 50

[[documents.roots]]
id = "manual"
path = "knowledge/manual"
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "knowledge" / "manual" / "Note.md").write_text(
        "# Note\n",
        encoding="utf-8",
    )
    DocumentIndex(tmp_path).rebuild()
    return tmp_path


def _client(root: Path) -> TestClient:
    app = FastAPI()

    def session() -> object:
        return object()

    app.include_router(create_document_router(root, require_session=session))
    return TestClient(app)


def test_document_routes_exact_mutation_contract(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = _client(root)
    listed = client.get("/api/v1/documents?limit=50")
    assert listed.status_code == 200
    envelope = listed.json()
    note = envelope["items"][0]
    snapshot = envelope["snapshot_id"]

    updated = client.post(
        f"/api/v1/documents/{note['document_id']}/update",
        headers={"Idempotency-Key": "route-update"},
        json={
            "content": "# Updated\n",
            "expected_sha256": note["content_sha256"],
            "expected_snapshot_id": snapshot,
            "format": "markdown",
        },
    )
    assert updated.status_code == 200, updated.text
    written = updated.json()

    renamed = client.post(
        f"/api/v1/documents/{note['document_id']}/rename",
        headers={"Idempotency-Key": "route-rename"},
        json={
            "name": "Renamed.md",
            "expected_sha256": written["content_sha256"],
            "expected_snapshot_id": written["snapshot_id"],
        },
    )
    assert renamed.status_code == 200, renamed.text

    folder = client.post(
        "/api/v1/documents/folders",
        headers={"Idempotency-Key": "route-folder"},
        json={
            "root_id": "manual",
            "folder": "projects",
            "expected_snapshot_id": renamed.json()["snapshot_id"],
        },
    )
    assert folder.status_code == 200, folder.text
    folders = client.get("/api/v1/documents/folders?root_id=manual")
    assert folders.status_code == 200
    assert folders.json()["items"][0]["path"] == "knowledge/manual/projects"


def test_refresh_rejects_paths_and_accepts_ids(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = _client(root)
    envelope = client.get("/api/v1/documents").json()
    document = envelope["items"][0]
    bad = client.post(
        "/api/v1/documents/index/refresh",
        headers={"Idempotency-Key": "bad-refresh"},
        json={"expected_snapshot_id": envelope["snapshot_id"], "paths": ["config/raytsystem.toml"]},
    )
    assert bad.status_code == 422
    good = client.post(
        "/api/v1/documents/index/refresh",
        headers={"Idempotency-Key": "good-refresh"},
        json={
            "expected_snapshot_id": envelope["snapshot_id"],
            "document_id": document["document_id"],
        },
    )
    assert good.status_code == 200, good.text
    replay = client.post(
        "/api/v1/documents/index/refresh",
        headers={"Idempotency-Key": "good-refresh"},
        json={
            "expected_snapshot_id": envelope["snapshot_id"],
            "document_id": document["document_id"],
        },
    )
    assert replay.status_code == 200
    assert replay.json() == good.json()
    rebound = client.post(
        "/api/v1/documents/index/refresh",
        headers={"Idempotency-Key": "good-refresh"},
        json={
            "expected_snapshot_id": good.json()["snapshot_id"],
            "document_id": document["document_id"],
        },
    )
    assert rebound.status_code == 409
    assert rebound.json()["error"]["code"] == "document_idempotency_conflict"


def test_documents_filter_by_repeatable_opaque_ids(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "knowledge" / "manual" / "Second.md").write_text("# Second\n", encoding="utf-8")
    DocumentIndex(root).rebuild()
    client = _client(root)
    items = client.get("/api/v1/documents?limit=50").json()["items"]
    selected_id = next(item["document_id"] for item in items if item["filename"] == "Second.md")
    response = client.get(
        "/api/v1/documents",
        params=[("document_ids", selected_id), ("limit", "50")],
    )
    assert response.status_code == 200
    assert [item["document_id"] for item in response.json()["items"]] == [selected_id]


def test_missing_index_returns_typed_initializing_error(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / ".raytsystem" / "documents.sqlite").unlink()
    response = _client_without_index(root).get("/api/v1/documents")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "document_index_initializing"


def test_invalid_config_degrades_only_documents(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text("[documents\n", encoding="utf-8")
    client = _client_without_index(tmp_path)
    status = client.get("/api/v1/documents/index")
    assert status.status_code == 200
    assert status.json()["state"] == "error"
    assert client.get("/api/v1/documents").status_code == 503


def test_corrupted_index_can_be_explicitly_rebuilt_without_snapshot(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / ".raytsystem" / "documents.sqlite").write_bytes(b"not sqlite")
    client = _client(root)
    assert client.get("/api/v1/documents/index").json()["state"] == "error"
    rebuilt = client.post(
        "/api/v1/documents/index/rebuild",
        headers={"Idempotency-Key": "recover-index"},
        json={"expected_snapshot_id": None},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["state"] == "current"


def test_create_rejects_deep_properties_before_yaml_dump(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    client = _client(root)
    snapshot = client.get("/api/v1/documents").json()["snapshot_id"]
    nested: dict[str, object] = {"leaf": "value"}
    for _ in range(40):
        nested = {"nested": nested}
    response = client.post(
        "/api/v1/documents",
        headers={"Idempotency-Key": "deep-properties"},
        json={
            "root_id": "manual",
            "folder": "",
            "name": "Deep.md",
            "template": "note",
            "properties": nested,
            "tags": [],
            "expected_snapshot_id": snapshot,
        },
    )
    assert response.status_code == 403


def _client_without_index(root: Path) -> TestClient:
    app = FastAPI()

    def session() -> object:
        return object()

    app.include_router(create_document_router(root, require_session=session))
    return TestClient(app)


def test_full_app_lifespan_keeps_invalid_documents_fail_isolated(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text("[documents\n", encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<html><head><meta name="raytsystem-csp-nonce" '
        'content="__RAYTSYSTEM_CSP_NONCE__"></head><body>raytsystem</body></html>',
        encoding="utf-8",
    )
    app = create_app(
        tmp_path,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({"http://testserver"}),
        static_dir=static,
    )
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/").status_code == 200
        response = client.get("/api/v1/documents/index")
        assert response.status_code == 200
        assert response.json()["state"] == "error"
