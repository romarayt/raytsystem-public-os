from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raytsystem.features import load_feature_config
from raytsystem.security.paths import PathPolicyError, read_regular_file

_SNAPSHOT_PATH = ".raytsystem/registry-projection/jarvis-registry-snapshot.json"
_MANIFEST_PATH = ".raytsystem/registry-projection/jarvis-registry-manifest.json"
_MAX_EVIDENCE_BYTES = 512 * 1024


class RegistryProjectionReadModel:
    """Read-only registry evidence projection for the loopback UI."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def snapshot(self) -> dict[str, Any]:
        features = load_feature_config(self.root)
        base = {
            "protocol": "raytsystem-registry-projection",
            "protocol_version": "0.1",
            "feature": "registry_projection_enabled",
            "feature_config_sha256": features.config_sha256,
            "snapshot_path": _SNAPSHOT_PATH,
            "manifest_path": _MANIFEST_PATH,
            "catalog_sha256": None,
            "matched_agents": [],
            "project_skills": [],
            "warnings": [],
            "side_effects": {
                "write": False,
                "repair": False,
                "sync": False,
                "reindex": False,
                "external_send": False,
                "execution": False,
            },
        }
        if not features.enabled("registry_projection_enabled"):
            return base | {"enabled": False, "state": "disabled"}
        snapshot_path = self.root / _SNAPSHOT_PATH
        manifest_path = self.root / _MANIFEST_PATH
        missing = [
            path
            for path, exists in (
                (_SNAPSHOT_PATH, snapshot_path.is_file()),
                (_MANIFEST_PATH, manifest_path.is_file()),
            )
            if not exists
        ]
        if missing:
            return base | {
                "enabled": True,
                "state": "not_configured",
                "warnings": [
                    {
                        "code": "registry_projection_evidence_missing",
                        "paths": missing,
                    }
                ],
            }
        try:
            snapshot = _load_json(self.root, _SNAPSHOT_PATH)
            manifest = _load_json(self.root, _MANIFEST_PATH)
            return base | _project_snapshot(snapshot, manifest)
        except (PathPolicyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            return base | {
                "enabled": True,
                "state": "degraded",
                "warnings": [
                    {
                        "code": "registry_projection_evidence_invalid",
                        "message": type(error).__name__,
                    }
                ],
            }


def _load_json(root: Path, relative: str) -> dict[str, Any]:
    data = read_regular_file(root, relative, max_bytes=_MAX_EVIDENCE_BYTES).data
    payload = json.loads(data.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("registry projection evidence must be a JSON object")
    return payload


def _project_snapshot(snapshot: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    snapshot_hash = _optional_string(snapshot.get("snapshot_hash"), max_length=128)
    manifest_hash = _optional_string(manifest.get("snapshot_hash"), max_length=128)
    warnings = _warning_items(snapshot.get("warnings"))
    if manifest_hash and snapshot_hash and manifest_hash != snapshot_hash:
        warnings.append({"code": "registry_projection_manifest_hash_mismatch"})
    return {
        "enabled": True,
        "state": "ready",
        "catalog_sha256": snapshot_hash,
        "matched_agents": _agent_items(snapshot.get("agents")),
        "project_skills": _skill_items(snapshot.get("skills")),
        "warnings": warnings[:20],
    }


def _agent_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("agents must be a list")
    items: list[dict[str, Any]] = []
    for raw in value[:500]:
        if not isinstance(raw, dict):
            continue
        agent_id = _required_string(raw.get("agent_id"), max_length=256)
        items.append(
            {
                "agent_id": agent_id,
                "status": _optional_string(raw.get("evidence"), max_length=64) or "observed",
                "source_hash": None,
                "evidence_refs": _string_list(raw.get("evidence_refs")),
            }
        )
    return items


def _skill_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("skills must be a list")
    items: list[dict[str, Any]] = []
    for raw in value[:500]:
        if not isinstance(raw, dict):
            continue
        skill_id = _required_string(raw.get("skill_id"), max_length=256)
        items.append(
            {
                "skill_id": skill_id,
                "name": _optional_string(raw.get("name"), max_length=256),
                "description": _optional_string(raw.get("description"), max_length=512),
                "test_status": _optional_string(raw.get("evidence"), max_length=64) or "observed",
                "source_hash": _optional_string(raw.get("source_hash"), max_length=128),
                "evidence_refs": _string_list(raw.get("evidence_refs")),
            }
        )
    return items


def _warning_items(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"code": "registry_projection_source_warning", "message": item[:256]}
        for item in value[:20]
        if isinstance(item, str)
    ]


def _required_string(value: object, *, max_length: int) -> str:
    text = _optional_string(value, max_length=max_length)
    if text is None:
        raise ValueError("required string is missing")
    return text


def _optional_string(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_length]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()[:512]
        for item in value[:20]
        if isinstance(item, str) and item.strip()
    ]
