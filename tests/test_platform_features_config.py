"""Fail-closed feature configuration loading and honest platform status."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from platform_helpers import make_platform_workspace, platform_config_payload
from raytsystem.contracts.governance import EmergencyAction
from raytsystem.contracts.workflows import NotificationType
from raytsystem.emergency import EmergencyService
from raytsystem.features import FEATURE_NAMES, FeatureConfigError, load_feature_config
from raytsystem.notifications import NotificationService
from raytsystem.platform_store import initialize_platform_store
from raytsystem.system_status import platform_status

pytestmark = pytest.mark.filterwarnings("error")

DEPENDENCY_PAIRS = (
    ("promptfoo_adapter_enabled", "evals_enabled"),
    ("promptfoo_remote_generation_enabled", "promptfoo_adapter_enabled"),
    ("otel_export_enabled", "telemetry_enabled"),
    ("external_mcp_execution_enabled", "mcp_governance_enabled"),
    ("a2a_network_exposure_enabled", "a2a_gateway_enabled"),
    ("external_notifications_enabled", "notifications_enabled"),
    ("external_kms_enabled", "restricted_encryption_enabled"),
)


def _write_config(root: Path, payload: object) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "platform.yaml").write_text(
        payload if isinstance(payload, str) else yaml.safe_dump(payload, sort_keys=True),
        encoding="utf-8",
    )
    return root


def test_missing_config_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FeatureConfigError, match="unavailable"):
        load_feature_config(tmp_path)


def test_malformed_yaml_fails_closed(tmp_path: Path) -> None:
    _write_config(tmp_path, "features: [unclosed\n  broken: {")
    with pytest.raises(FeatureConfigError, match="invalid"):
        load_feature_config(tmp_path)


def test_non_mapping_config_fails_closed(tmp_path: Path) -> None:
    _write_config(tmp_path, "- just\n- a list\n")
    with pytest.raises(FeatureConfigError, match="mapping"):
        load_feature_config(tmp_path)


def test_missing_flag_is_rejected(tmp_path: Path) -> None:
    payload = platform_config_payload()
    del payload["features"]["backup_enabled"]
    _write_config(tmp_path, payload)
    with pytest.raises(FeatureConfigError, match="incomplete or unknown"):
        load_feature_config(tmp_path)


def test_unknown_flag_is_rejected(tmp_path: Path) -> None:
    payload = platform_config_payload()
    payload["features"]["surprise_feature_enabled"] = True
    _write_config(tmp_path, payload)
    with pytest.raises(FeatureConfigError, match="incomplete or unknown"):
        load_feature_config(tmp_path)


def test_non_boolean_flag_is_rejected(tmp_path: Path) -> None:
    payload = platform_config_payload()
    payload["features"]["evals_enabled"] = 1
    _write_config(tmp_path, payload)
    with pytest.raises(FeatureConfigError, match="booleans"):
        load_feature_config(tmp_path)


def test_missing_version_is_rejected(tmp_path: Path) -> None:
    payload = platform_config_payload()
    del payload["version"]
    _write_config(tmp_path, payload)
    with pytest.raises(FeatureConfigError, match="version"):
        load_feature_config(tmp_path)


@pytest.mark.parametrize(("child", "parent"), DEPENDENCY_PAIRS)
def test_dependency_violations_fail_closed(tmp_path: Path, child: str, parent: str) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={child: True, parent: False})
    with pytest.raises(FeatureConfigError, match=f"{child} requires {parent}"):
        load_feature_config(root)


def test_store_path_must_remain_fixed(tmp_path: Path) -> None:
    payload = platform_config_payload()
    payload["store"] = "ops/elsewhere.sqlite"
    _write_config(tmp_path, payload)
    with pytest.raises(FeatureConfigError, match="fixed"):
        load_feature_config(tmp_path)


def test_config_sha256_is_stable_and_flag_sensitive(tmp_path: Path) -> None:
    first = load_feature_config(make_platform_workspace(tmp_path / "one"))
    again = load_feature_config(tmp_path / "one")
    second = load_feature_config(make_platform_workspace(tmp_path / "two"))
    changed = load_feature_config(
        make_platform_workspace(tmp_path / "three", flag_overrides={"backup_enabled": False})
    )
    assert first.config_sha256 == again.config_sha256 == second.config_sha256
    assert changed.config_sha256 != first.config_sha256
    assert set(first.flags) == set(FEATURE_NAMES)
    with pytest.raises(KeyError):
        first.enabled("unknown_feature")


def test_status_reports_unconfigured_without_platform_config(tmp_path: Path) -> None:
    status = platform_status(tmp_path)
    assert status["state"] == "unconfigured"
    assert status["active_feature_flags"] == {}
    assert status["platform_store"] == "unavailable"


def test_status_reports_error_for_broken_config(tmp_path: Path) -> None:
    payload = platform_config_payload()
    payload["store"] = "ops/elsewhere.sqlite"
    _write_config(tmp_path, payload)
    status = platform_status(tmp_path)
    assert status["state"] == "error"
    assert status["platform_store"] == "unavailable"


def test_status_with_uninitialized_store_stays_honest(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    status = platform_status(root)
    assert status["state"] == "unavailable"
    assert status["platform_store"] == "uninitialized"
    assert status["snapshot_id"] == "pview_unavailable"
    assert status["event_backlog"] == 0
    assert status["notification_backlog"] == 0
    assert status["outbox_backlog"] == 0
    assert status["eval_regression_count"] == 0
    assert status["emergency_state"]["state"] == "unavailable"
    assert status["a2a_state"] == "disabled"
    assert status["a2a_network_exposure"] is False
    assert status["encryption_provider"]["state"] == "unavailable"
    assert "restricted_encryption_disabled" in status["encryption_provider"]["reason_codes"]


def test_status_counts_backlogs_from_initialized_store(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    NotificationService(root).emit(
        NotificationType.RUN_FAILED,
        severity="high",
        related_object_id="xrun_status",
        actor_id="user_local_test",
        payload={"title": "Run failed", "message": "The status run failed."},
    )
    with initialize_platform_store(root) as store:
        store.append_record(
            kind="notification_outbox",
            record_id="outbox_status",
            payload={"outbox_id": "outbox_status"},
            state="draft",
            expected_revision=None,
        )
        store.append_record(
            kind="eval_comparison",
            record_id="ecmp_status",
            payload={"comparison_id": "ecmp_status", "regression": True},
            state="regression",
            expected_revision=None,
        )
        event_count = store.event_count()
    status = platform_status(root)
    assert status["state"] == "ready"
    assert status["platform_store"] == "ready"
    assert status["snapshot_id"].startswith("pview_")
    assert status["event_backlog"] == event_count
    assert status["notification_backlog"] == 1
    assert status["outbox_backlog"] == 1
    assert status["eval_regression_count"] == 1
    assert status["trace_storage_size"] > 0
    assert status["emergency_state"]["state"] == "ready"


def test_status_surfaces_active_emergency(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    initialize_platform_store(root).close()
    EmergencyService(root).activate(
        (EmergencyAction.PAUSE_ALL_EMPLOYEES,),
        reason="Status drill stop",
        actor_id="user_local_test",
        idempotency_key="status-emergency-0001",
    )
    status = platform_status(root)
    assert status["emergency_state"]["state"] == "blocked"
    assert "pause_all_employees" in status["emergency_state"]["active_actions"]
    assert status["emergency_state"]["revision"] == 1
