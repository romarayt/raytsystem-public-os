from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.catalog import CatalogError, CatalogService
from raytsystem.contracts import RuntimeAdapterState, Sensitivity


def _write_catalog_baseline(root: Path, *, context_path: str = "AGENTS.md") -> None:
    (root / "config").mkdir(exist_ok=True)
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
    (root / "packs" / "core").mkdir(parents=True)
    (root / "packs" / "core" / "pack.yaml").write_text(
        f"""pack_id: pack_core
name: Core
version: \"1.0.0\"
description: Core test pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: []
skill_ids: [safe-skill]
context_paths: [{context_path}]
optional: false
""",
        encoding="utf-8",
    )
    (root / "skills" / "safe-skill").mkdir(parents=True)
    (root / "skills" / "safe-skill" / "SKILL.md").write_text(
        """---
name: safe-skill
description: A safe inert skill.
---

# Safe skill
""",
        encoding="utf-8",
    )


def test_repository_catalog_is_universal_and_inert() -> None:
    root = Path(__file__).parents[1]

    snapshot = CatalogService(root).load()

    assert {pack.pack_id for pack in snapshot.packs} == {
        "pack_core",
        "pack_local",
        "pack_starter",
    }
    assert "raytsystem-youtube" not in {skill.skill_id for skill in snapshot.skills}
    assert len(snapshot.agents) == 5
    assert all(not agent.enabled for agent in snapshot.agents)
    assert all(adapter.state is RuntimeAdapterState.DISABLED for adapter in snapshot.adapters)


def test_catalog_load_is_read_only(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    snapshot = CatalogService(tmp_path).load()

    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert before == after
    assert snapshot.skills[0].skill_id == "safe-skill"
    assert snapshot.skill_bodies["safe-skill"].startswith("---")


def test_catalog_rejects_context_outside_allowlisted_roots(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path, context_path=".env")

    with pytest.raises(CatalogError, match="Pack manifest is invalid"):
        CatalogService(tmp_path).load()


def test_catalog_rejects_symlinked_skill_directory(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    target = tmp_path / "real-skill"
    target.mkdir()
    (tmp_path / "skills" / "linked-skill").symlink_to(target, target_is_directory=True)

    with pytest.raises(CatalogError, match="symlinked"):
        CatalogService(tmp_path).load()


def test_restricted_skill_body_is_never_disclosed(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    planted = "ghp_" + "x" * 36
    skill_path = tmp_path / "skills" / "safe-skill" / "SKILL.md"
    skill_path.write_text(
        f"""---
name: safe-skill
description: Restricted test skill.
---

Token: {planted}
""",
        encoding="utf-8",
    )

    snapshot = CatalogService(tmp_path).load()
    skill = snapshot.skills[0]

    assert skill.sensitivity is Sensitivity.RESTRICTED
    assert not skill.enabled
    assert skill.skill_id not in snapshot.skill_bodies
    assert skill.name == "safe-skill"
    assert skill.description == "Content withheld by the sensitivity policy."
    assert planted not in str(snapshot.to_dict())


def test_unknown_pack_cannot_self_assert_official_trust(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    pack = tmp_path / "packs" / "community"
    pack.mkdir()
    (pack / "pack.yaml").write_text(
        """pack_id: pack_community
name: Community
version: "1.0.0"
description: Unattested community pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: []
skill_ids: []
optional: true
""",
        encoding="utf-8",
    )

    snapshot = CatalogService(tmp_path).load()

    community = next(item for item in snapshot.packs if item.pack_id == "pack_community")
    assert community.trust_class.value == "user"


def test_skill_cannot_be_owned_by_multiple_packs(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    pack = tmp_path / "packs" / "duplicate"
    pack.mkdir()
    (pack / "pack.yaml").write_text(
        """pack_id: pack_duplicate
name: Duplicate
version: "1.0.0"
description: Invalid duplicate ownership.
license_expression: Apache-2.0
trust_class: user
agent_ids: []
skill_ids: [safe-skill]
optional: true
""",
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="multiple packs"):
        CatalogService(tmp_path).load()


@pytest.mark.parametrize("target", ["pack", "adapter", "agent"])
def test_catalog_definition_metadata_secrets_fail_closed(
    tmp_path: Path,
    target: str,
) -> None:
    _write_catalog_baseline(tmp_path)
    planted = "sk-proj-" + "s" * 32
    if target == "pack":
        path = tmp_path / "packs" / "core" / "pack.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("Core test pack.", planted),
            encoding="utf-8",
        )
    elif target == "adapter":
        path = tmp_path / "config" / "runtime-adapters.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("Catalog only", planted),
            encoding="utf-8",
        )
    else:
        pack = tmp_path / "packs" / "agentpack"
        (pack / "agents").mkdir(parents=True)
        (pack / "pack.yaml").write_text(
            """pack_id: pack_agent_test
name: Agent test
version: "1.0.0"
description: Agent metadata scan fixture.
license_expression: Apache-2.0
trust_class: user
agent_ids: [agent_secret]
skill_ids: []
optional: true
""",
            encoding="utf-8",
        )
        (pack / "agents" / "agent_secret.yaml").write_text(
            f"""agent_id: agent_secret
name: Agent
role: reviewer
description: {planted}
version: "1.0.0"
pack_id: pack_agent_test
runtime_adapter_id: adapter_disabled
accent: "#A99CF8"
enabled: false
""",
            encoding="utf-8",
        )

    with pytest.raises(CatalogError, match="restricted"):
        CatalogService(tmp_path).load()


@pytest.mark.parametrize(
    "extension",
    [
        "extensions:\n  loop: &loop [*loop]\n",
        "optional: true\n",
        "extensions:\n  deep: " + "[" * 40 + "0" + "]" * 40 + "\n",
    ],
)
def test_catalog_yaml_rejects_aliases_duplicate_keys_and_deep_trees(
    tmp_path: Path,
    extension: str,
) -> None:
    _write_catalog_baseline(tmp_path)
    path = tmp_path / "packs" / "core" / "pack.yaml"
    path.write_text(path.read_text(encoding="utf-8") + extension, encoding="utf-8")

    with pytest.raises(CatalogError):
        CatalogService(tmp_path).load()


def test_secret_shaped_skill_path_is_never_exported(tmp_path: Path) -> None:
    _write_catalog_baseline(tmp_path)
    secret_id = "sk-proj-" + "q" * 32
    secret_path = tmp_path / "skills" / secret_id
    secret_path.mkdir()
    (secret_path / "SKILL.md").write_text(
        """---
name: withheld
description: Safe body under an unsafe path.
---

# Withheld
""",
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="path cannot be disclosed") as captured:
        CatalogService(tmp_path).load()

    assert secret_id not in str(captured.value)
