"""Platform web API: honest section states, fail-closed gating, write-free reads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from platform_helpers import DEFAULT_FLAGS, make_platform_workspace
from raytsystem.contracts.evaluation import (
    EvalAssertion,
    EvalAssertionType,
    EvalCase,
    EvalSuite,
)
from raytsystem.contracts.workflows import NotificationType
from raytsystem.emergency import EmergencyService
from raytsystem.evals import EvalObservation, EvalService
from raytsystem.notifications import NotificationService
from raytsystem.platform_store import initialize_platform_store, open_platform_store_read_only
from raytsystem.policy_simulator import PolicySimulator
from raytsystem.webapp import create_app

ORIGIN = "http://testserver"
SECTIONS = (
    "evals",
    "traces",
    "replays",
    "policies",
    "tools",
    "protocols",
    "packages",
    "workflows",
    "notifications",
    "backups",
)
SECTION_DISABLING_FLAGS: dict[str, bool] = {
    "evals_enabled": False,
    "promptfoo_adapter_enabled": False,
    "telemetry_enabled": False,
    "otel_export_enabled": False,
    "replay_enabled": False,
    "policy_simulator_enabled": False,
    "emergency_controls_enabled": False,
    "mcp_governance_enabled": False,
    "external_mcp_execution_enabled": False,
    "pack_lifecycle_enabled": False,
    "workflow_engine_enabled": False,
    "notifications_enabled": False,
    "external_notifications_enabled": False,
    "backup_enabled": False,
}


def _static_dir(root: Path) -> Path:
    static = root / "test-static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text(
        '<!doctype html><html><head><meta name="raytsystem-csp-nonce" '
        'content="__RAYTSYSTEM_CSP_NONCE__"></head><body>'
        '<div id="root">raytsystem</div></body></html>',
        encoding="utf-8",
    )
    return static


@contextmanager
def _web_session(root: Path) -> Iterator[tuple[TestClient, str]]:
    app = create_app(
        root,
        allowed_hosts=frozenset({"testserver"}),
        allowed_origins=frozenset({ORIGIN}),
        static_dir=_static_dir(root),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        session = client.get("/api/v1/session")
        assert session.status_code == 200
        yield client, str(session.json()["csrf_token"])


def _write_headers(csrf: str, key: str) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf,
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }


def _init_store(root: Path) -> None:
    initialize_platform_store(root).close()


def _store_fingerprint(root: Path) -> tuple[str, int]:
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        return store.snapshot_id(), store.event_count()


def _plant_eval_run(root: Path) -> None:
    assertion = EvalAssertion(
        assertion_id="a_exact",
        assertion_type=EvalAssertionType.EXACT_MATCH,
        target="result_text",
        expected="ok",
    )
    case = EvalCase.model_validate(
        {
            "case_id": "case_web_eval",
            "name": "Web eval case",
            "task_fixture": "evals/web/fixture.json",
            "repository_snapshot_sha256": "0" * 64,
            "agent_configuration_sha256": "1" * 64,
            "runtime_id": "runtime_deterministic",
            "instruction_hashes": {},
            "skill_hashes": {},
            "assertions": (assertion,),
        }
    )
    suite = EvalSuite(
        suite_id="suite_web_eval",
        name="Web eval suite",
        version="1.0.0",
        dataset_id="dataset_web_eval",
        target_ids=("target_web",),
        case_ids=(case.case_id,),
        manifest_sha256="2" * 64,
    )
    EvalService(root).run_case(
        suite,
        case,
        EvalObservation(text="ok, but with a private token value"),
        workspace_id="workspace_web",
        target_id="target_web",
    )


def _plan_payload(root: Path) -> dict[str, Any]:
    return {
        "plan_id": "plan_web_sim",
        "employee_id": "employee_web",
        "task_id": "task_web",
        "task_revision": 1,
        "runtime_id": "adapter_fake",
        "workspace_mode": "none",
        "network_access": "none",
        "policy_sha256": PolicySimulator(root).policy_sha256,
    }


def test_features_endpoint_shape_with_uninitialized_store(project_root: Path) -> None:
    root = make_platform_workspace(project_root)

    with _web_session(root) as (client, _csrf):
        response = client.get("/api/v1/features")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "unavailable"
    assert body["platform_store"] == "uninitialized"
    assert body["snapshot_id"] == "pview_unavailable"
    assert body["a2a_state"] == "disabled"
    assert set(body["active_feature_flags"]) == set(DEFAULT_FLAGS)
    assert body["active_feature_flags"]["a2a_network_exposure_enabled"] is False
    assert len(body["feature_config_sha256"]) == 64
    for counter in (
        "event_backlog",
        "notification_backlog",
        "outbox_backlog",
        "eval_regression_count",
    ):
        assert body[counter] == 0


def test_sections_report_honest_states_with_default_flags(project_root: Path) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)
    _plant_eval_run(root)
    expected = {
        "evals": "ready",
        "traces": "ready",
        "replays": "ready",
        "policies": "ready",
        "tools": "catalog_only",
        "protocols": "disabled",
        "packages": "ready",
        "workflows": "ready",
        "notifications": "ready",
        "backups": "ready",
    }

    with _web_session(root) as (client, _csrf):
        for section in SECTIONS:
            response = client.get(f"/api/v1/systems/{section}", params={"limit": 5})
            assert response.status_code == 200, section
            body = response.json()
            assert body["state"] == expected[section], section
            assert "snapshot_id" in body, section
            for value in body.values():
                if isinstance(value, list):
                    assert len(value) <= 200, section
            assert "private token value" not in response.text, section


def test_protocols_section_is_ready_when_any_protocol_flag_is_on(project_root: Path) -> None:
    root = make_platform_workspace(project_root, flag_overrides={"acp_adapter_enabled": True})
    _init_store(root)

    with _web_session(root) as (client, _csrf):
        response = client.get("/api/v1/systems/protocols")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert body["acp"]["state"] == "ready"
    assert body["a2a"]["state"] == "disabled"


def test_every_section_reports_disabled_when_its_flag_is_off(project_root: Path) -> None:
    root = make_platform_workspace(project_root, flag_overrides=SECTION_DISABLING_FLAGS)
    _init_store(root)

    with _web_session(root) as (client, _csrf):
        for section in SECTIONS:
            response = client.get(f"/api/v1/systems/{section}")
            assert response.status_code == 200, section
            assert response.json()["state"] == "disabled", section


def test_unavailable_store_wins_over_disabled_flags(project_root: Path) -> None:
    root = make_platform_workspace(project_root, flag_overrides=SECTION_DISABLING_FLAGS)

    with _web_session(root) as (client, _csrf):
        for section in SECTIONS:
            response = client.get(f"/api/v1/systems/{section}")
            assert response.status_code == 200, section
            state = response.json()["state"]
            if section == "policies":
                assert state == "disabled"
            else:
                assert state == "unavailable", section


def test_unknown_section_returns_404(project_root: Path) -> None:
    root = make_platform_workspace(project_root)

    with _web_session(root) as (client, _csrf):
        response = client.get("/api/v1/systems/shell")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "section_not_found"


def test_get_endpoints_perform_no_platform_writes(project_root: Path) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)
    before = _store_fingerprint(root)

    with _web_session(root) as (client, _csrf):
        assert client.get("/api/v1/features").status_code == 200
        for section in SECTIONS:
            assert client.get(f"/api/v1/systems/{section}").status_code == 200
        assert client.get("/api/v1/traces/trace_missing").status_code == 404

    assert _store_fingerprint(root) == before


def test_policy_simulation_happy_path_and_stale_policy_rejection(project_root: Path) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)
    plan = _plan_payload(root)

    with _web_session(root) as (client, csrf):
        simulated = client.post(
            "/api/v1/policy-simulations",
            json={"plan": plan},
            headers=_write_headers(csrf, "policy-sim-00001"),
        )
        rejected = client.post(
            "/api/v1/policy-simulations",
            json={"plan": plan | {"policy_sha256": "f" * 64}},
            headers=_write_headers(csrf, "policy-sim-00002"),
        )

    assert simulated.status_code == 200
    body = simulated.json()
    assert body["plan_id"] == "plan_web_sim"
    assert body["outcome"] in {"allowed", "approval_required", "blocked"}
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "policy_simulation_rejected"


def test_emergency_command_requires_fresh_snapshot_and_replays_idempotently(
    project_root: Path,
) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)

    def payload(expected_snapshot_id: str) -> dict[str, Any]:
        return {
            "actions": ["pause_all_employees"],
            "reason": "Web emergency drill",
            "expected_snapshot_id": expected_snapshot_id,
        }

    with _web_session(root) as (client, csrf):
        stale = client.post(
            "/api/v1/emergency-commands",
            json=payload("pview_stale"),
            headers=_write_headers(csrf, "emergency-cmd-0001"),
        )
        current = str(EmergencyService(root).snapshot()["snapshot_id"])
        first = client.post(
            "/api/v1/emergency-commands",
            json=payload(current),
            headers=_write_headers(csrf, "emergency-cmd-0002"),
        )
        after = str(EmergencyService(root).snapshot()["snapshot_id"])
        replayed = client.post(
            "/api/v1/emergency-commands",
            json=payload(after),
            headers=_write_headers(csrf, "emergency-cmd-0002"),
        )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "snapshot_stale"
    assert first.status_code == 200
    body = first.json()
    assert body["state"] == "active"
    assert "pause_all_employees" in body["expected_effect"]
    assert replayed.status_code == 200
    assert replayed.json() == body


def test_notification_transition_is_gated_when_notifications_disabled(
    project_root: Path,
) -> None:
    root = make_platform_workspace(
        project_root,
        flag_overrides={"notifications_enabled": False, "external_notifications_enabled": False},
    )
    _init_store(root)
    snapshot_id = str(NotificationService(root).snapshot()["snapshot_id"])

    with _web_session(root) as (client, csrf):
        response = client.post(
            "/api/v1/notifications/notice_gated/transitions",
            json={"state": "read", "expected_snapshot_id": snapshot_id},
            headers=_write_headers(csrf, "notification-cmd-0001"),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "notification_rejected"


def test_notification_transition_happy_path(project_root: Path) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)
    notice = NotificationService(root).emit(
        NotificationType.RUN_FAILED,
        severity="high",
        related_object_id="xrun_web",
        actor_id="user_local_test",
        payload={"title": "Run failed", "message": "The web run failed."},
    )
    snapshot_id = str(NotificationService(root).snapshot()["snapshot_id"])

    with _web_session(root) as (client, csrf):
        response = client.post(
            f"/api/v1/notifications/{notice.notification_id}/transitions",
            json={"state": "read", "expected_snapshot_id": snapshot_id},
            headers=_write_headers(csrf, "notification-cmd-0002"),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "read"


def test_replay_plan_is_rejected_for_unknown_run(project_root: Path) -> None:
    root = make_platform_workspace(project_root)
    _init_store(root)

    with _web_session(root) as (client, csrf):
        response = client.post(
            "/api/v1/replay-plans",
            json={"original_run_id": "xrun_missing", "new_run_id": "xrun_fresh"},
            headers=_write_headers(csrf, "replay-cmd-0001"),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "replay_rejected"
