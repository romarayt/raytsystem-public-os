from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from raytsystem.contracts import LedgerGeneration, canonical_json_bytes
from raytsystem.platform_store import initialize_platform_store
from raytsystem.skill_authoring import SkillAuthoringService
from raytsystem.webapp import create_app

ORIGIN = "http://testserver"


def _skill_content(
    skill_id: str,
    *,
    description: str,
    version: str = "1.0.0",
    test_status: str = "pending",
    body: str = "# Skill\n",
) -> str:
    return f"""---
name: {skill_id}
description: {description}
version: "{version}"
permissions:
  - filesystem_read
test_status: {test_status}
---
{body}"""


def _write_skill_web_fixture(root: Path) -> Path:
    generation_path = root / "ledger" / "generations" / "genesis.json"
    generation = LedgerGeneration.model_validate_json(generation_path.read_bytes())
    generation_path.write_bytes(canonical_json_bytes(generation))
    (root / "config" / "runtime-adapters.yaml").write_text(
        """version: "1.0.0"
adapters:
  - adapter_id: adapter_disabled
    name: Catalog only
    version: "1.0.0"
    state: disabled
    isolation_mode: none
    reason: Execution is unavailable.
""",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Safe routing\n", encoding="utf-8")
    core = root / "packs" / "core"
    (core / "agents").mkdir(parents=True)
    (core / "pack.yaml").write_text(
        """pack_id: pack_core
name: Core
version: "1.0.0"
description: Official HTTP authoring test pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: [agent_builder]
skill_ids: [official-skill]
context_paths: [AGENTS.md]
optional: false
""",
        encoding="utf-8",
    )
    (core / "agents" / "agent_builder.yaml").write_text(
        """agent_id: agent_builder
name: Builder
role: builder
description: Builds local implementation proposals.
version: "1.0.0"
pack_id: pack_core
runtime_adapter_id: adapter_disabled
skill_ids: [local-skill, official-skill, restricted-skill]
context_paths: [AGENTS.md]
capabilities: [implementation]
requested_filesystem_mode: staging_only
approved_data_classes: [internal]
accent: "#FF8A5B"
enabled: false
""",
        encoding="utf-8",
    )
    skills = root / "skills"
    for skill_id in ("local-skill", "official-skill", "restricted-skill"):
        (skills / skill_id).mkdir(parents=True)
    (skills / "local-skill" / "SKILL.md").write_text(
        _skill_content("local-skill", description="Editable local skill."),
        encoding="utf-8",
    )
    (skills / "official-skill" / "SKILL.md").write_text(
        """---
name: official-skill
description: Official bundled skill.
---
# Official skill
""",
        encoding="utf-8",
    )
    planted = "ghp_" + "r" * 36
    (skills / "restricted-skill" / "SKILL.md").write_text(
        _skill_content(
            "restricted-skill",
            description="Restricted skill.",
            body=f"Token: {planted}\n",
        ),
        encoding="utf-8",
    )
    (root / "unrelated.txt").write_text("preserve me\n", encoding="utf-8")
    static = root / "skill-test-static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        '<!doctype html><html><head><meta name="raytsystem-csp-nonce" '
        'content="__RAYTSYSTEM_CSP_NONCE__"></head><body>'
        '<div id="root">raytsystem</div></body></html>',
        encoding="utf-8",
    )
    (static / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        encoding="utf-8",
    )
    return static


@pytest.fixture
def skill_web_client(project_root: Path) -> Iterator[tuple[TestClient, str, Path]]:
    static = _write_skill_web_fixture(project_root)
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        shell = client.get("/")
        assert shell.status_code == 200, shell.text
        session = client.get("/api/v1/session")
        assert session.status_code == 200
        yield client, str(session.json()["csrf_token"]), project_root


def _headers(csrf: str, key: str) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf,
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }


def _save_payload(
    item: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    return {
        "content": content,
        "expected_catalog_sha256": item["catalog_sha256"],
        "expected_source_sha256": item["source_sha256"],
    }


def _skills_by_id(client: TestClient) -> tuple[str, dict[str, dict[str, Any]]]:
    response = client.get("/api/v1/skills")
    assert response.status_code == 200, response.text
    payload = response.json()
    catalog_sha256 = str(payload["catalog_sha256"])
    return catalog_sha256, {str(item["skill_id"]): item for item in payload["skills"]}


def test_skill_list_and_details_are_snapshot_bound_without_body_n_plus_one(
    skill_web_client: tuple[TestClient, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _csrf, root = skill_web_client
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and "skill-test-static" not in path.parts
        and "documents.sqlite" not in path.name
    }

    def body_read_is_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("list/detail must use the verified snapshot, not authoring _context")

    monkeypatch.setattr(SkillAuthoringService, "_context", body_read_is_forbidden)
    catalog_sha256, skills = _skills_by_id(client)

    assert sorted(skills) == ["local-skill", "official-skill", "restricted-skill"]
    local = skills["local-skill"]
    official = skills["official-skill"]
    restricted = skills["restricted-skill"]
    assert local["policy"]["editable"] is True
    assert local["policy"]["read_only_reason"] is None
    assert official["policy"]["editable"] is False
    assert official["policy"]["read_only_reason"] == "official_skill"
    assert official["policy"]["forkable"] is True
    assert restricted["policy"]["read_only_reason"] == "sensitivity_restricted"
    assert restricted["policy"]["forkable"] is False
    assert local["related_agents"] == [
        {"agent_id": "agent_builder", "name": "Builder", "role": "builder"}
    ]
    assert all(not Path(item["source_path"]).is_absolute() for item in skills.values())

    restricted_detail = client.get(f"/api/v1/skills/restricted-skill?expected={catalog_sha256}")
    local_detail = client.get(f"/api/v1/skills/local-skill?expected={catalog_sha256}")

    assert restricted_detail.status_code == 200
    restricted_payload = restricted_detail.json()
    assert restricted_payload["content"] is None
    assert restricted_payload["source"]["content_available"] is False
    assert restricted_payload["source"]["content_restricted"] is True
    assert restricted_payload["policy"]["editable"] is False
    assert "ghp_" not in restricted_detail.text
    assert local_detail.status_code == 200
    local_payload = local_detail.json()
    assert local_payload["content"].startswith("---\n")
    assert local_payload["format"] == "text"
    assert local_payload["content_format"] == "markdown"
    assert local_payload["source"]["path"] == "skills/local-skill/SKILL.md"
    assert local_payload["permission_boundary"] == {
        "availability": "catalog_metadata",
        "declared_permission_ids": ["filesystem_read"],
        "filesystem": {"availability": "not_modeled", "items": []},
        "network": {"availability": "not_modeled", "items": []},
        "tools": {"availability": "not_modeled", "items": []},
        "secrets": {"availability": "not_modeled", "items": []},
        "approvals": {"availability": "not_modeled", "items": []},
        "side_effects": {"availability": "not_modeled", "items": []},
        "sensitivity": "internal",
    }
    assert local_payload["workflows"] == {"availability": "not_modeled", "items": []}
    assert local_payload["tools"] == {"availability": "not_modeled", "items": []}
    assert local_payload["tests"]["test_status"] == "pending"
    assert local_payload["history"]["availability"] == "not_initialized"
    assert local_payload["history"]["revisions"] == []
    assert local_payload["history"]["audit_events"] == []
    rendered = json.dumps([skills, restricted_payload, local_payload], sort_keys=True)
    assert str(root) not in rendered
    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and "skill-test-static" not in path.parts
        and "documents.sqlite" not in path.name
    }
    assert before == after


def test_active_package_manifest_pins_exact_skill_ids(
    skill_web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = skill_web_client
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="package_manifest",
            record_id="pkgrev_local_test",
            payload={"skill_ids": ["local-skill"]},
            state="active",
            expected_revision=None,
        )
        store.append_record(
            kind="package_active",
            record_id="package_local_test",
            payload={"revision_id": "pkgrev_local_test"},
            state="active",
            expected_revision=None,
        )

    _catalog_sha, skills = _skills_by_id(client)
    local = skills["local-skill"]
    assert local["policy"]["editable"] is False
    assert local["policy"]["forkable"] is True
    assert local["policy"]["read_only_reason"] == "installed_pinned_pack"


def test_unresolved_active_package_state_fails_skill_edit_policy_closed(
    skill_web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = skill_web_client
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="package_active",
            record_id="package_missing_manifest",
            payload={"revision_id": "pkgrev_missing_manifest"},
            state="active",
            expected_revision=None,
        )

    _catalog_sha, skills = _skills_by_id(client)
    local = skills["local-skill"]
    assert local["policy"]["editable"] is False
    assert local["policy"]["forkable"] is False
    assert local["policy"]["read_only_reason"] == "installed_pack_state_unavailable"


def test_skill_history_reconstructs_paths_and_redacts_raw_actor_tokens(
    skill_web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = skill_web_client
    catalog_sha, _skills = _skills_by_id(client)
    credential = "credential_like_actor_value"
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="skill_authoring_revision",
            record_id="local-skill",
            payload={
                "skill_revision_id": "skillrev_test",
                "operation": "save",
                "source_skill_id": "local-skill",
                "skill_id": "local-skill",
                "source_path": "/private/credential/path/SKILL.md",
            },
            state="pending",
            expected_revision=None,
        )
        store.append_event(
            stream_id="skill_authoring_local-skill",
            aggregate_id="local-skill",
            event_type="skill_saved",
            actor_id=credential,
            payload_schema="skill_authoring_audit_v1",
            payload={"operation": "save"},
        )

    response = client.get(f"/api/v1/skills/local-skill?expected={catalog_sha}")
    assert response.status_code == 200
    history = response.json()["history"]
    assert history["revisions"][0]["source_path"] == "skills/local-skill/SKILL.md"
    assert history["audit_events"][0]["actor_id"] == "redacted"
    assert credential not in response.text
    assert "/private/credential" not in response.text


def test_official_skill_is_read_only_and_request_cannot_supply_a_path(
    skill_web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, root = skill_web_client
    catalog_sha256, skills = _skills_by_id(client)
    official = skills["official-skill"] | {"catalog_sha256": catalog_sha256}
    source = root / "skills" / "official-skill" / "SKILL.md"
    source_before = source.read_bytes()
    payload = _save_payload(
        official,
        _skill_content("official-skill", description="Attempted edit."),
    )

    read_only = client.post(
        "/api/v1/skills/official-skill/save/preview",
        json=payload,
        headers=_headers(csrf, "official-preview-1"),
    )
    arbitrary_path = client.post(
        "/api/v1/skills/local-skill/save/preview",
        json={
            **_save_payload(
                skills["local-skill"] | {"catalog_sha256": catalog_sha256},
                _skill_content("local-skill", description="Typed edit."),
            ),
            "path": "/private/tmp/escape/SKILL.md",
        },
        headers=_headers(csrf, "path-rejected-1"),
    )

    assert read_only.status_code == 403
    assert read_only.json()["error"]["code"] == "skill_read_only"
    assert read_only.json()["error"]["details"]["forkable"] is True
    assert arbitrary_path.status_code == 422
    assert arbitrary_path.json()["error"]["code"] == "request_invalid"
    assert source.read_bytes() == source_before
    assert not (root / "ops" / "platform.sqlite").exists()


def test_local_save_requires_csrf_is_cas_bound_idempotent_and_pending(
    skill_web_client: tuple[TestClient, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf, root = skill_web_client
    catalog_sha256, skills = _skills_by_id(client)
    local = skills["local-skill"] | {"catalog_sha256": catalog_sha256}
    target = root / "skills" / "local-skill" / "SKILL.md"
    original = target.read_bytes()
    unrelated = root / "unrelated.txt"
    unrelated_before = (unrelated.read_bytes(), unrelated.stat().st_mtime_ns)
    proposed = _skill_content(
        "local-skill",
        description="Saved through the typed HTTP boundary.",
        version="2.0.0",
        test_status="pass",
        body="# Edited\n",
    )
    payload = _save_payload(local, proposed)

    preview = client.post(
        "/api/v1/skills/local-skill/save/preview",
        json=payload,
        headers=_headers(csrf, "local-preview-1"),
    )
    assert preview.status_code == 200
    assert preview.json()["validation"]["effective_test_status"] == "pending"
    assert target.read_bytes() == original
    assert not (root / "ops" / "platform.sqlite").exists()

    missing_csrf_headers = _headers(csrf, "local-save-missing-csrf")
    missing_csrf_headers.pop("X-CSRF-Token")
    missing_csrf = client.post(
        "/api/v1/skills/local-skill/save",
        json=payload,
        headers=missing_csrf_headers,
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_rejected"
    assert target.read_bytes() == original

    provider = client.app.state.snapshots
    original_invalidate = provider.invalidate
    invalidations: list[None] = []

    def tracked_invalidate() -> None:
        invalidations.append(None)
        original_invalidate()

    monkeypatch.setattr(provider, "invalidate", tracked_invalidate)
    headers = _headers(csrf, "local-save-idempotent-1")
    saved = client.post(
        "/api/v1/skills/local-skill/save",
        json=payload,
        headers=headers,
    )
    replay = client.post(
        "/api/v1/skills/local-skill/save",
        json=payload,
        headers=headers,
    )

    assert saved.status_code == replay.status_code == 200
    assert saved.json() == replay.json()
    assert invalidations == [None]
    assert saved.json()["test_status"] == "pending"
    assert b"test_status: pending" in target.read_bytes()
    assert b"test_status: pass" not in target.read_bytes()
    assert (unrelated.read_bytes(), unrelated.stat().st_mtime_ns) == unrelated_before

    reused_key = client.post(
        "/api/v1/skills/local-skill/save",
        json={
            **payload,
            "content": _skill_content("local-skill", description="Different request."),
        },
        headers=headers,
    )
    stale = client.post(
        "/api/v1/skills/local-skill/save",
        json=payload,
        headers=_headers(csrf, "local-save-stale-1"),
    )
    assert reused_key.status_code == 409
    assert reused_key.json()["error"]["code"] == "skill_idempotency_conflict"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "skill_edit_conflict"
    assert stale.json()["error"]["details"]["kind"] == "source_sha256"
    assert invalidations == [None]

    detail = client.get(f"/api/v1/skills/local-skill?expected={saved.json()['catalog_sha256']}")
    assert detail.status_code == 200
    history = detail.json()["history"]
    assert history["availability"] == "available"
    assert len(history["revisions"]) == 1
    assert history["revisions"][0]["record_revision"] == 1
    assert len(history["audit_events"]) == 1
    assert history["audit_events"][0]["event_type"] == "skill_saved"
    assert "Saved through the typed HTTP boundary." in detail.json()["content"]
    assert "test_status: pass" not in detail.json()["content"]


def test_official_fork_preview_and_create_leave_source_separate_and_local(
    skill_web_client: tuple[TestClient, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf, root = skill_web_client
    catalog_sha256, skills = _skills_by_id(client)
    official = skills["official-skill"]
    payload = {
        "new_skill_id": "official-skill-local",
        "expected_catalog_sha256": catalog_sha256,
        "expected_source_sha256": official["source_sha256"],
    }
    source = root / "skills" / "official-skill" / "SKILL.md"
    source_before = source.read_bytes()
    unrelated = (root / "unrelated.txt").read_bytes()

    preview = client.post(
        "/api/v1/skills/official-skill/fork/preview",
        json=payload,
        headers=_headers(csrf, "fork-preview-1"),
    )
    assert preview.status_code == 200
    assert preview.json()["destination"] == "skills/official-skill-local/SKILL.md"
    assert preview.json()["validation"]["effective_test_status"] == "pending"
    assert not (root / "skills" / "official-skill-local").exists()
    assert source.read_bytes() == source_before

    provider = client.app.state.snapshots
    original_invalidate = provider.invalidate
    invalidations: list[None] = []

    def tracked_invalidate() -> None:
        invalidations.append(None)
        original_invalidate()

    monkeypatch.setattr(provider, "invalidate", tracked_invalidate)
    headers = _headers(csrf, "fork-create-idempotent-1")
    created = client.post(
        "/api/v1/skills/official-skill/fork",
        json=payload,
        headers=headers,
    )
    replay = client.post(
        "/api/v1/skills/official-skill/fork",
        json=payload,
        headers=headers,
    )

    assert created.status_code == replay.status_code == 200
    assert created.json() == replay.json()
    assert invalidations == [None]
    assert created.json()["source_skill_id"] == "official-skill"
    assert created.json()["skill_id"] == "official-skill-local"
    assert created.json()["test_status"] == "pending"
    target = root / "skills" / "official-skill-local" / "SKILL.md"
    assert target.is_file() and not target.is_symlink()
    assert source.read_bytes() == source_before
    assert (root / "unrelated.txt").read_bytes() == unrelated

    new_catalog, updated_skills = _skills_by_id(client)
    copied = updated_skills["official-skill-local"]
    assert new_catalog == created.json()["catalog_sha256"]
    assert copied["pack_id"] == "pack_local"
    assert copied["trust_class"] == "user"
    assert copied["test_status"] == "pending"
    assert copied["policy"]["editable"] is True
    detail = client.get(f"/api/v1/skills/official-skill-local?expected={new_catalog}")
    assert detail.status_code == 200
    assert len(detail.json()["history"]["audit_events"]) == 1
    assert detail.json()["history"]["audit_events"][0]["event_type"] == "skill_forked"


def test_restricted_skill_mutation_errors_never_disclose_quarantined_content(
    skill_web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, _root = skill_web_client
    catalog_sha256, skills = _skills_by_id(client)
    restricted = skills["restricted-skill"]
    fork = client.post(
        "/api/v1/skills/restricted-skill/fork/preview",
        json={
            "new_skill_id": "restricted-local",
            "expected_catalog_sha256": catalog_sha256,
            "expected_source_sha256": restricted["source_sha256"],
        },
        headers=_headers(csrf, "restricted-fork-1"),
    )

    assert fork.status_code == 403
    assert fork.json()["error"]["code"] == "skill_read_only"
    assert fork.json()["error"]["details"]["forkable"] is False
    assert "ghp_" not in fork.text
