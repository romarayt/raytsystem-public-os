from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "raytsystem.toml").write_text(
        "\n".join(
            [
                'schema_version = "1.0.0"',
                'environment = "test"',
                'default_promotion_mode = "manual"',
                'control_db = "ops/control.sqlite"',
                'index_db = ".raytsystem/index.sqlite"',
                "",
                "[fixtures]",
                'root = "inbox"',
                'manifest = "config/fixture-manifest.json"',
                "require_manifest = false",
                "",
                "[git]",
                "checkpoint_on_promotion = false",
                "",
                "[limits]",
                "max_input_bytes = 26214400",
                "lease_ttl_seconds = 60",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "policies.yaml").write_text(
        'version: "1.0.0"\npromotion:\n  fixture: autonomous\n  real: manual_hash_bound\n',
        encoding="utf-8",
    )
    (tmp_path / "ledger" / "generations").mkdir(parents=True)
    genesis = {
        "schema_name": "LedgerGenerationV1",
        "schema_version": "1.0.0",
        "id_scheme_version": "1",
        "extensions": {},
        "generation_id": "genesis",
        "parent_generation_id": None,
        "records": {},
        "schema_registry_sha256": None,
        "created_at": datetime(2026, 7, 10, tzinfo=UTC).isoformat(),
        "promotion_txn_id": None,
        "promotion_event_id": None,
    }
    (tmp_path / "ledger" / "generations" / "genesis.json").write_text(
        json.dumps(genesis, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "ledger" / "CURRENT").write_text("genesis\n", encoding="utf-8")
    (tmp_path / "inbox").mkdir()
    return tmp_path


@pytest.fixture
def platform_root(project_root: Path) -> Path:
    source = Path(__file__).parents[1] / "config" / "platform.yaml"
    shutil.copyfile(source, project_root / "config" / "platform.yaml")
    return project_root
