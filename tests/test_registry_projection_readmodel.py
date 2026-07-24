from __future__ import annotations

import json
from pathlib import Path

from platform_helpers import make_platform_workspace
from raytsystem.webapp.registry_projection_readmodel import RegistryProjectionReadModel


def test_registry_projection_is_disabled_by_default(project_root: Path) -> None:
    root = make_platform_workspace(project_root)

    payload = RegistryProjectionReadModel(root).snapshot()

    assert payload["protocol"] == "raytsystem-registry-projection"
    assert payload["enabled"] is False
    assert payload["state"] == "disabled"
    assert payload["catalog_sha256"] is None
    assert payload["matched_agents"] == []
    assert payload["project_skills"] == []
    assert payload["warnings"] == []
    assert payload["side_effects"] == {
        "write": False,
        "repair": False,
        "sync": False,
        "reindex": False,
        "external_send": False,
        "execution": False,
    }


def test_enabled_registry_projection_reports_missing_evidence_without_writing(
    project_root: Path,
) -> None:
    root = make_platform_workspace(
        project_root,
        flag_overrides={"registry_projection_enabled": True},
    )
    before = {path.relative_to(root).as_posix() for path in root.rglob("*")}

    payload = RegistryProjectionReadModel(root).snapshot()

    after = {path.relative_to(root).as_posix() for path in root.rglob("*")}
    assert after == before
    assert payload["enabled"] is True
    assert payload["state"] == "not_configured"
    assert payload["warnings"] == [
        {
            "code": "registry_projection_evidence_missing",
            "paths": [
                ".raytsystem/registry-projection/jarvis-registry-snapshot.json",
                ".raytsystem/registry-projection/jarvis-registry-manifest.json",
            ],
        }
    ]


def test_enabled_registry_projection_projects_valid_evidence(project_root: Path) -> None:
    root = make_platform_workspace(
        project_root,
        flag_overrides={"registry_projection_enabled": True},
    )
    evidence_dir = root / ".raytsystem" / "registry-projection"
    evidence_dir.mkdir(parents=True)
    snapshot = {
        "snapshot_hash": "a" * 64,
        "warnings": ["source is read-only"],
        "agents": [
            {
                "agent_id": "agent_orchestrator",
                "evidence": "tested",
                "evidence_refs": ["docs/orchestrator/runs/route.md"],
            }
        ],
        "skills": [
            {
                "skill_id": "orchestrator-route",
                "name": "Orchestrator Route",
                "description": "Build a bounded route.",
                "evidence": "tested",
                "source_hash": "b" * 64,
                "evidence_refs": ["docs/orchestrator/runs/route.md"],
            }
        ],
    }
    (evidence_dir / "jarvis-registry-snapshot.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    (evidence_dir / "jarvis-registry-manifest.json").write_text(
        json.dumps({"snapshot_hash": "a" * 64}), encoding="utf-8"
    )

    payload = RegistryProjectionReadModel(root).snapshot()

    assert payload["enabled"] is True
    assert payload["state"] == "ready"
    assert payload["catalog_sha256"] == "a" * 64
    assert payload["matched_agents"] == [
        {
            "agent_id": "agent_orchestrator",
            "status": "tested",
            "source_hash": None,
            "evidence_refs": ["docs/orchestrator/runs/route.md"],
        }
    ]
    assert payload["project_skills"][0]["skill_id"] == "orchestrator-route"
    assert payload["project_skills"][0]["test_status"] == "tested"
    assert payload["warnings"][0]["code"] == "registry_projection_source_warning"


def test_enabled_registry_projection_accepts_utf8_bom_manifest(project_root: Path) -> None:
    root = make_platform_workspace(
        project_root,
        flag_overrides={"registry_projection_enabled": True},
    )
    evidence_dir = root / ".raytsystem" / "registry-projection"
    evidence_dir.mkdir(parents=True)
    snapshot = {
        "snapshot_hash": "c" * 64,
        "warnings": [],
        "agents": [],
        "skills": [],
    }
    (evidence_dir / "jarvis-registry-snapshot.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    manifest = json.dumps({"snapshot_hash": "c" * 64}).encode("utf-8")
    (evidence_dir / "jarvis-registry-manifest.json").write_bytes(b"\xef\xbb\xbf" + manifest)

    payload = RegistryProjectionReadModel(root).snapshot()

    assert payload["enabled"] is True
    assert payload["state"] == "ready"
    assert payload["catalog_sha256"] == "c" * 64


def test_enabled_registry_projection_degrades_on_invalid_evidence(project_root: Path) -> None:
    root = make_platform_workspace(
        project_root,
        flag_overrides={"registry_projection_enabled": True},
    )
    evidence_dir = root / ".raytsystem" / "registry-projection"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "jarvis-registry-snapshot.json").write_text("{", encoding="utf-8")
    (evidence_dir / "jarvis-registry-manifest.json").write_text("{}", encoding="utf-8")

    payload = RegistryProjectionReadModel(root).snapshot()

    assert payload["enabled"] is True
    assert payload["state"] == "degraded"
    assert payload["matched_agents"] == []
    assert payload["project_skills"] == []
    assert payload["warnings"][0]["code"] == "registry_projection_evidence_invalid"
