from __future__ import annotations

import json
import re
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi.testclient import TestClient

from raytsystem.codegraph.projection import CodeGraphProjection
from raytsystem.contracts import sha256_hex
from raytsystem.ingestion import IngestPipeline
from raytsystem.skill_authoring import SkillAuthoringService
from raytsystem.webapp import create_app

ORIGIN = "http://testserver"


def _write_web_fixture(root: Path) -> Path:
    (root / "config" / "runtime-adapters.yaml").write_text(
        """version: \"1.0.0\"
adapters:
  - adapter_id: adapter_disabled
    name: Catalog only
    version: \"1.0.0\"
    state: disabled
    isolation_mode: none
    reason: Execution is unavailable.
""",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Safe routing\n", encoding="utf-8")
    (root / "packs" / "core" / "agents").mkdir(parents=True)
    (root / "packs" / "core" / "pack.yaml").write_text(
        """pack_id: pack_core
name: Core
version: \"1.0.0\"
description: Core web test pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: [agent_builder]
skill_ids: [safe-skill]
context_paths: [AGENTS.md]
optional: false
""",
        encoding="utf-8",
    )
    (root / "packs" / "core" / "agents" / "agent_builder.yaml").write_text(
        """agent_id: agent_builder
name: Builder
role: builder
description: Builds local implementation proposals.
version: "1.0.0"
pack_id: pack_core
runtime_adapter_id: adapter_disabled
skill_ids: [safe-skill]
context_paths: [AGENTS.md]
capabilities: [implementation]
requested_filesystem_mode: staging_only
approved_data_classes: [internal]
accent: "#FF8A5B"
enabled: false
""",
        encoding="utf-8",
    )
    (root / "skills" / "safe-skill").mkdir(parents=True)
    (root / "skills" / "safe-skill" / "SKILL.md").write_text(
        """---
name: safe-skill
description: Safe skill body for the local catalog.
---

# Safe skill
""",
        encoding="utf-8",
    )
    source = root / "inbox" / "web.md"
    source.write_text("# Web evidence\n\nThe control plane is local.\n", encoding="utf-8")
    IngestPipeline(root).ingest(source, fixture=True)
    static = root / "test-static"
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
def web_client(project_root: Path) -> tuple[TestClient, str, Path]:
    static = _write_web_fixture(project_root)
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        root_response = client.get("/")
        assert root_response.status_code == 200
        session_response = client.get("/api/v1/session")
        assert session_response.status_code == 200
        deadline = monotonic() + 5
        while app.state.document_initialization["state"] in {"pending", "checking"}:
            assert monotonic() < deadline, "Documents startup scan did not settle"
            sleep(0.01)
        yield client, str(session_response.json()["csrf_token"]), project_root


def _write_headers(csrf: str, key: str = "browser-command-0001") -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf,
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }


def _task_payload(expected: str | None = None) -> dict[str, Any]:
    return {
        "title": "Review the local control plane",
        "description": "Verify the immutable task boundary.",
        "priority": "high",
        "expected_generation_id": expected,
    }


def _leave_pending_skill_transition(root: Path, operation: str) -> str:
    service = SkillAuthoringService(root)
    context = service._context("safe-skill")
    if operation == "save":
        proposed = context.data.replace(b"# Safe skill", b"# Uncommitted skill")
        target_id = "safe-skill"
    else:
        target_id = "safe-skill-local"
        proposed = service._fork_content(context, target_id).data
    request = {
        "operation": operation,
        "source_skill_id": "safe-skill",
        "target_skill_id": target_id,
    }
    intent = service._new_recovery_intent(
        operation=operation,
        source_skill_id="safe-skill",
        target_skill_id=target_id,
        scope=f"skill_authoring_{operation}",
        idempotency_key=f"pending-{operation}",
        request=request,
        original_source_sha256=context.definition.source_sha256,
        proposed_source_sha256=sha256_hex(proposed),
    )
    if operation == "save":
        service._atomic_replace(
            target_id,
            proposed,
            expected_source_sha256=context.definition.source_sha256,
            intent=intent,
        )
    else:
        service._create_skill_file(target_id, proposed, intent=intent)
    return context.definition.source_sha256


def test_index_issues_strict_session_and_security_headers(project_root: Path) -> None:
    static = _write_web_fixture(project_root)
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "worker-src 'self' blob:" in response.headers["content-security-policy"]
    nonce_match = re.search(
        r"style-src-elem 'self' 'nonce-([^']+)'",
        response.headers["content-security-policy"],
    )
    assert nonce_match is not None
    assert f'content="{nonce_match.group(1)}"' in response.text
    assert "__RAYTSYSTEM_CSP_NONCE__" not in response.text
    assert "img-src 'self' data:" not in response.headers["content-security-policy"]
    assert "unsafe-eval" not in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("operation", ["save", "fork"])
def test_fresh_app_recovers_uncommitted_skill_before_product_get(
    project_root: Path,
    operation: str,
) -> None:
    static = _write_web_fixture(project_root)
    committed_source_sha256 = _leave_pending_skill_transition(project_root, operation)

    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        catalog = client.get("/api/v1/catalog")
        agents = client.get("/api/v1/agents")

    assert catalog.status_code == 200
    assert agents.status_code == 200
    skills = {skill["skill_id"]: skill for skill in catalog.json()["skills"]}
    assert skills["safe-skill"]["source_sha256"] == committed_source_sha256
    assert "safe-skill-local" not in skills
    assert [agent["agent_id"] for agent in agents.json()["agents"]] == ["agent_builder"]


def test_index_fails_closed_when_bundle_lacks_csp_nonce_marker(project_root: Path) -> None:
    static = _write_web_fixture(project_root)
    (static / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ui_nonce_marker_missing"


def test_api_requires_session_and_rejects_untrusted_host(project_root: Path) -> None:
    static = _write_web_fixture(project_root)
    app = create_app(
        project_root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=static,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        no_session = client.get("/api/v1/system")
        hostile_host = client.get("/", headers={"Host": "attacker.invalid"})

    assert no_session.status_code == 401
    assert no_session.json()["error"]["code"] == "session_required"
    assert hostile_host.status_code == 421
    assert hostile_host.json()["error"]["code"] == "host_rejected"


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({"Origin": "http://evil.invalid"}, "origin_rejected"),
        ({"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, "csrf_rejected"),
        (
            {"Origin": ORIGIN, "X-CSRF-Token": "replace", "Idempotency-Key": "tiny"},
            "idempotency_required",
        ),
    ],
)
def test_task_writes_require_origin_csrf_and_idempotency(
    web_client: tuple[TestClient, str, Path],
    headers: dict[str, str],
    code: str,
) -> None:
    client, csrf, _root = web_client
    headers = {key: (csrf if value == "replace" else value) for key, value in headers.items()}
    headers.setdefault("Content-Type", "application/json")

    response = client.post("/api/v1/tasks", json=_task_payload(), headers=headers)

    assert response.status_code in {400, 403}
    assert response.json()["error"]["code"] == code


def test_task_create_is_idempotent_and_never_changes_knowledge(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, root = web_client
    knowledge_before = (root / "ledger" / "CURRENT").read_bytes()
    headers = _write_headers(csrf)

    created = client.post("/api/v1/tasks", json=_task_payload(), headers=headers)
    replayed = client.post("/api/v1/tasks", json=_task_payload(), headers=headers)
    board = client.get("/api/v1/tasks")

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["no_op"] is True
    assert replayed.json()["event_id"] == created.json()["event_id"]
    assert len(board.json()["tasks"]) == 1
    assert (root / "ledger" / "CURRENT").read_bytes() == knowledge_before


def test_task_transition_rejects_stale_generation(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, _root = web_client
    created = client.post(
        "/api/v1/tasks",
        json=_task_payload(),
        headers=_write_headers(csrf, "create-transition-task"),
    ).json()
    task_id = created["task"]["task_id"]
    response = client.post(
        f"/api/v1/tasks/{task_id}/transitions",
        json={"target": "planned", "expected_generation_id": "tgen_" + "0" * 64},
        headers=_write_headers(csrf, "stale-transition-command"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_conflict"


def test_get_routes_are_read_only_and_return_generation_bound_data(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = web_client
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }

    system = client.get("/api/v1/system")
    catalog = client.get("/api/v1/catalog")
    universe = client.get("/api/v1/universe")
    knowledge = client.get("/api/v1/knowledge")

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }
    assert before == after
    assert system.status_code == catalog.status_code == universe.status_code == 200
    assert knowledge.status_code == 200
    assert (
        universe.json()["knowledge_generation_id"]
        == system.json()["fingerprint"]["knowledge_generation_id"]
    )
    assert catalog.json()["catalog_sha256"] == system.json()["fingerprint"]["catalog_sha256"]


def test_execution_get_routes_are_read_only_and_hide_runtime_authority(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = web_client
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }

    features = client.get("/api/v1/execution/features")
    employees = client.get("/api/v1/employees")
    assignments = client.get("/api/v1/execution/assignments")
    health = client.get("/api/v1/execution/runtime-health")

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }
    assert before == after
    assert features.status_code == 200
    assert features.json()["features"]["runtime_execution_enabled"] is False
    assert employees.status_code == 200
    assert employees.json()["state"] == "catalog_only"
    assert assignments.status_code == 200
    assert assignments.json()["assignments"] == []
    assert health.status_code == 200
    rendered = json.dumps(
        [features.json(), employees.json(), assignments.json(), health.json()],
        sort_keys=True,
    )
    for forbidden in ("safe_command", "cwd_token", "provider_session_id", "executable"):
        assert f'"{forbidden}":' not in rendered


def test_unified_agents_return_each_definition_once_with_sanitized_detail(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = web_client
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }

    response = client.get("/api/v1/agents")

    assert response.status_code == 200
    payload = response.json()
    assert [agent["agent_id"] for agent in payload["agents"]] == ["agent_builder"]
    agent = payload["agents"][0]
    assert agent["name"] == "Builder"
    assert agent["definition"]["agent_id"] == "agent_builder"
    assert agent["execution"] is None
    assert agent["readiness"] == "requires_configuration"
    assert agent["unavailable_reason"] == "execution_store_uninitialized"
    detail = client.get(f"/api/v1/agents/agent_builder?expected={payload['catalog_sha256']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["agent"]["agent_id"] == "agent_builder"
    assert detail_payload["skills"][0]["skill_id"] == "safe-skill"
    assert detail_payload["runtime"]["runs"] == []
    assert detail_payload["history"]["audit_events"] == []
    rendered = detail.text
    assert str(root) not in rendered
    for forbidden in ("safe_command", "cwd_token", "provider_session_id", "executable"):
        assert f'"{forbidden}":' not in rendered
    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "test-static" not in path.parts
    }
    assert before == after


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/tasks/task_example/execution/assignments",
            {
                "employee_id": "employee_example",
                "expected_task_generation_id": "tgen_example",
                "command": "rm -rf /",
            },
        ),
        (
            "/api/v1/execution/assignments/assignment_example/workspace",
            {
                "expected_task_generation_id": "tgen_example",
                "cwd": "/private/tmp/escape",
            },
        ),
        (
            "/api/v1/execution/assignments/assignment_example/heartbeat",
            {
                "expected_task_generation_id": "tgen_example",
                "raw_cli_args": ["--dangerously-bypass-approvals-and-sandbox"],
            },
        ),
        (
            "/api/v1/execution/runs/xrun_example/pause",
            {"expected_status": "running", "env": {"PATH": "/attacker"}},
        ),
    ],
)
def test_execution_writes_reject_raw_runtime_authority_before_any_write(
    web_client: tuple[TestClient, str, Path],
    path: str,
    payload: dict[str, Any],
) -> None:
    client, csrf, root = web_client
    before = {
        item.relative_to(root): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file() and "test-static" not in item.parts
    }

    response = client.post(path, json=payload, headers=_write_headers(csrf))

    after = {
        item.relative_to(root): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file() and "test-static" not in item.parts
    }
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_invalid"
    assert before == after


def test_public_details_are_sanitized_but_evidence_remains_verifiable(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, root = web_client
    knowledge = client.get("/api/v1/knowledge").json()
    evidence_id = knowledge["evidence"][0]["evidence_id"]
    generation_id = knowledge["generation_id"]
    detail = client.get(f"/api/v1/knowledge/evidence/{evidence_id}?expected={generation_id}")
    runs = client.get("/api/v1/runs")
    catalog_sha256 = client.get("/api/v1/catalog").json()["catalog_sha256"]
    skill = client.get(f"/api/v1/skills/safe-skill?expected={catalog_sha256}")

    assert detail.status_code == 200
    assert detail.json()["evidence"]["excerpt"]
    rendered = detail.text + runs.text + skill.text
    assert str(root) not in rendered
    assert "raw_path" not in rendered
    assert "input_path" not in runs.text
    assert skill.json()["format"] == "text"


def test_detail_routes_reject_stale_plane_fingerprints(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, _root = web_client
    knowledge = client.get("/api/v1/knowledge").json()
    evidence_id = knowledge["evidence"][0]["evidence_id"]
    stale_knowledge = client.get(f"/api/v1/knowledge/evidence/{evidence_id}?expected=gen_stale")
    stale_catalog = client.get("/api/v1/skills/safe-skill?expected=stale")
    created = client.post(
        "/api/v1/tasks",
        json=_task_payload(),
        headers=_write_headers(csrf, "snapshot-bound-task"),
    ).json()
    stale_task = client.get(f"/api/v1/tasks/{created['task']['task_id']}?expected=tgen_stale")

    assert stale_knowledge.status_code == 409
    assert stale_catalog.status_code == 409
    assert stale_task.status_code == 409
    assert {
        stale_knowledge.json()["error"]["code"],
        stale_catalog.json()["error"]["code"],
        stale_task.json()["error"]["code"],
    } == {"snapshot_stale"}


def test_validation_and_sensitivity_errors_do_not_echo_payload(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, _root = web_client
    planted = "sk-proj-" + "p" * 32
    response = client.post(
        "/api/v1/tasks",
        json={
            "title": "Secret-shaped task",
            "description": planted,
            "unexpected": planted,
            "expected_generation_id": None,
        },
        headers=_write_headers(csrf, "secret-shaped-command"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_invalid"
    assert planted not in response.text


def test_oversized_json_and_arbitrary_paths_are_rejected(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, root = web_client
    oversized = '{"title":"' + "x" * (70 * 1024) + '"}'
    response = client.post(
        "/api/v1/tasks",
        content=oversized,
        headers=_write_headers(csrf, "oversized-command"),
    )
    traversal = client.get("/api/v1/instructions/%2E%2E%2F%2Eenv")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"
    assert traversal.status_code == 404
    assert str(root) not in traversal.text


def test_code_graph_api_is_snapshot_bound_typed_and_canonical_read_only(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, csrf, root = web_client
    source = root / "src" / "sample_service.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text(
        "class SampleService:\n    def execute(self):\n        return 1\n",
        encoding="utf-8",
    )
    CodeGraphProjection(root).rebuild()
    client.app.state.snapshots.invalidate()
    canonical_before = (root / "ledger" / "CURRENT").read_bytes()
    graph_files_before = {
        path.relative_to(root): path.read_bytes()
        for path in (root / ".raytsystem" / "graph").rglob("*")
        if path.is_file()
    }

    status = client.get("/api/v1/code-graph/status")
    universe = client.get("/api/v1/universe")
    snapshot_id = status.json()["snapshot_id"]
    code_node = next(node for node in universe.json()["nodes"] if node["kind"] == "class")
    detail = client.get(f"/api/v1/code-graph/nodes/{code_node['node_id']}?expected={snapshot_id}")
    queried = client.post(
        "/api/v1/code-graph/query",
        json={
            "query": "SampleService architecture",
            "expected_snapshot_id": snapshot_id,
            "depth": 1,
        },
        headers=_write_headers(csrf, "code-graph-query-001"),
    )
    stale = client.post(
        "/api/v1/code-graph/impact",
        json={
            "node_id": code_node["node_id"],
            "expected_snapshot_id": "cgraph_" + "0" * 64,
            "depth": 2,
        },
        headers=_write_headers(csrf, "code-graph-stale-001"),
    )
    traversal = client.post(
        "/api/v1/code-graph/impact",
        json={
            "node_id": "../../.env",
            "expected_snapshot_id": snapshot_id,
            "depth": 2,
        },
        headers=_write_headers(csrf, "code-graph-traversal-001"),
    )
    graph_files_after_reads = {
        path.relative_to(root): path.read_bytes()
        for path in (root / ".raytsystem" / "graph").rglob("*")
        if path.is_file()
    }
    updated = client.post(
        "/api/v1/code-graph/update",
        json={"expected_snapshot_id": snapshot_id},
        headers=_write_headers(csrf, "code-graph-update-001"),
    )

    assert status.status_code == universe.status_code == detail.status_code == 200
    assert queried.status_code == 200
    assert queried.json()["estimated_context_bytes"] <= 24_000
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "snapshot_stale"
    assert traversal.status_code == 422
    assert str(root) not in detail.text
    assert "return 1" not in detail.text
    assert graph_files_before == graph_files_after_reads
    assert updated.status_code == 200
    assert (root / "ledger" / "CURRENT").read_bytes() == canonical_before


def test_all_spa_routes_use_the_same_local_bundle(
    web_client: tuple[TestClient, str, Path],
) -> None:
    client, _csrf, _root = web_client

    for route in (
        "command-center",
        "handbook",
        "documents",
        "tasks",
        "universe",
        "runs",
        "agents",
        "skills",
        "context",
        "safety",
        "systems",
    ):
        response = client.get(f"/{route}")
        assert response.status_code == 200
        assert "raytsystem" in response.text
        nonce_match = re.search(
            r"style-src-elem 'self' 'nonce-([^']+)'",
            response.headers["content-security-policy"],
        )
        assert nonce_match is not None
        assert f'content="{nonce_match.group(1)}"' in response.text
        assert "__RAYTSYSTEM_CSP_NONCE__" not in response.text
    assert client.get("/not-a-route").status_code == 404


def test_distributed_web_bundle_carries_font_license() -> None:
    root = Path(__file__).parents[1]
    static = root / "src" / "raytsystem" / "webapp" / "static"
    font_assets = tuple((static / "assets").glob("*.woff*"))
    license_path = static / "licenses" / "FONTS-OFL-1.1.txt"
    javascript_licenses = static / "licenses" / "THIRD-PARTY-JS-LICENSES.txt"

    assert font_assets
    assert license_path.is_file()
    assert javascript_licenses.is_file()
    license_text = license_path.read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "Bricolage Grotesque Project Authors" in license_text
    assert "Copyright 2019 IBM Corp." in license_text
    assert "Copyright 2017 IBM Corp." in license_text
    javascript_text = javascript_licenses.read_text(encoding="utf-8")
    for package in (
        "react@19.2.7",
        "@tanstack/react-query@5.101.2",
        "graphology@0.26.0",
        "sigma@3.0.3",
        "events@3.3.0",
        "lucide-react@1.24.0",
        "@milkdown/kit@7.21.2",
        "codemirror@6.0.2",
        "@codemirror/lang-markdown@6.5.0",
        "@codemirror/lint@6.9.7",
    ):
        assert package in javascript_text
    assert "Copyright (c) Meta Platforms" in javascript_text
    assert "Copyright (c) 2026 Lucide Icons" in javascript_text
