from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raytsystem.webapp import create_app

ORIGIN = "http://testserver"


def _static(root: Path) -> Path:
    static = root / "onboarding-static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        '<!doctype html><html><head><meta name="raytsystem-csp-nonce" '
        'content="__RAYTSYSTEM_CSP_NONCE__"></head><body>'
        '<div id="root">raytsystem</div></body></html>',
        encoding="utf-8",
    )
    (static / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', "utf-8")
    return static


def _software_target(root: Path) -> Path:
    target = root / "external_repo"
    (target / "src").mkdir(parents=True)
    (target / ".git").mkdir()
    (target / "src" / "app.py").write_text("def main() -> None:\n    return None\n", "utf-8")
    (target / "pyproject.toml").write_text("[project]\nname='ext'\n", "utf-8")
    (target / "README.md").write_text("# External\nkeep me\n", "utf-8")
    return target


@pytest.fixture
def client(project_root: Path) -> Iterator[tuple[TestClient, str]]:
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=_static(project_root),
    )
    with TestClient(app, base_url=ORIGIN) as raw:
        assert raw.get("/").status_code == 200
        csrf = str(raw.get("/api/v1/session").json()["csrf_token"])
        yield raw, csrf


def _headers(csrf: str, *, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf, "Content-Type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_onboarding_plan_rejects_missing_session(project_root: Path) -> None:
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=_static(project_root),
    )
    with TestClient(app, base_url=ORIGIN) as raw:
        response = raw.get("/api/v1/onboarding/plan", params={"target": str(project_root)})
    assert response.status_code in {401, 403}


def test_onboarding_plan_previews_read_only(
    client: tuple[TestClient, str], project_root: Path
) -> None:
    raw, _csrf = client
    target = _software_target(project_root)
    before = {p.name for p in target.iterdir()}
    response = raw.get("/api/v1/onboarding/plan", params={"target": str(target)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "bootstrap"
    assert body["dry_run"] is True
    assert body["classification"]["primary_type"] == "software"
    assert str(target) not in response.text  # no absolute path leaks to the client
    assert {p.name for p in target.iterdir()} == before  # wrote nothing


def test_onboarding_apply_then_uninstall(
    client: tuple[TestClient, str], project_root: Path
) -> None:
    raw, csrf = client
    target = _software_target(project_root)
    plan = raw.get("/api/v1/onboarding/plan", params={"target": str(target)}).json()
    apply = raw.post(
        "/api/v1/onboarding/apply",
        json={"target": str(target), "confirm": plan["fingerprint"]},
        headers=_headers(csrf, key="onboard-apply-1"),
    )
    assert apply.status_code == 200, apply.text
    assert apply.json()["status"] == "installed"
    assert (target / "config" / "raytsystem.toml").is_file()
    assert (target / "ledger" / "CURRENT").read_text().strip() == "genesis"

    uninstall = raw.post(
        "/api/v1/onboarding/uninstall",
        json={"target": str(target)},
        headers=_headers(csrf, key="onboard-uninstall-1"),
    )
    assert uninstall.status_code == 200, uninstall.text
    assert uninstall.json()["status"] == "uninstalled"
    assert not (target / "config").exists()
    assert (target / "src" / "app.py").read_text() == "def main() -> None:\n    return None\n"


def test_onboarding_apply_wrong_fingerprint(
    client: tuple[TestClient, str], project_root: Path
) -> None:
    raw, csrf = client
    target = _software_target(project_root)
    response = raw.post(
        "/api/v1/onboarding/apply",
        json={"target": str(target), "confirm": "bootstrap_wrong"},
        headers=_headers(csrf, key="onboard-bad-1"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bootstrap_failed"


def test_onboarding_prompt(client: tuple[TestClient, str], project_root: Path) -> None:
    raw, _csrf = client
    target = _software_target(project_root)
    response = raw.get(
        "/api/v1/onboarding/prompt", params={"target": str(target), "agent": "claude"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_template"] == "software"
    assert "<TARGET_REPOSITORY_PATH>" in body["prompt"]
