from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest

from raytsystem.catalog import CatalogService
from raytsystem.contracts import sha256_hex
from raytsystem.platform_store import (
    PlatformStoreError,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.skill_authoring import (
    PINNED_SKILL_POLICY_UNKNOWN,
    SkillAuthoringError,
    SkillAuthoringService,
    SkillConflictError,
    SkillIdempotencyError,
    SkillPathError,
    SkillPersistenceError,
    SkillReadOnlyError,
    SkillValidationError,
)


def _skill_content(
    skill_id: str,
    *,
    description: str = "A local test skill.",
    version: str = "1.0.0",
    test_status: str = "pending",
    body: str = "# Local skill\n",
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


def _write_workspace(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
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
    core = root / "packs" / "core"
    (core / "agents").mkdir(parents=True)
    (core / "pack.yaml").write_text(
        """pack_id: pack_core
name: Core
version: "1.0.0"
description: Official test pack.
license_expression: Apache-2.0
trust_class: official
agent_ids: [agent_builder]
skill_ids: [official-skill]
context_paths: []
optional: false
""",
        encoding="utf-8",
    )
    (core / "agents" / "agent_builder.yaml").write_text(
        """agent_id: agent_builder
name: Builder
role: implementation
description: Implements bounded changes.
version: "1.0.0"
pack_id: pack_core
runtime_adapter_id: adapter_disabled
skill_ids: [local-skill]
context_paths: []
capabilities: [implementation]
requested_filesystem_mode: staging_only
approved_data_classes: [internal]
accent: "#A99CF8"
enabled: false
""",
        encoding="utf-8",
    )
    pinned = root / "packs" / "pinned"
    pinned.mkdir(parents=True)
    (pinned / "pack.yaml").write_text(
        """pack_id: pack_pinned
name: Pinned
version: "1.0.0"
description: Installed pinned test pack.
license_expression: Apache-2.0
trust_class: user
agent_ids: []
skill_ids: [pinned-skill]
context_paths: []
optional: true
""",
        encoding="utf-8",
    )
    skills = root / "skills"
    for skill_id in ("local-skill", "official-skill", "pinned-skill", "restricted-skill"):
        (skills / skill_id).mkdir(parents=True)
    (skills / "local-skill" / "SKILL.md").write_text(
        _skill_content("local-skill"), encoding="utf-8"
    )
    # Bundled source intentionally uses the legacy minimal frontmatter. Forking must upgrade the
    # local copy to the complete authoring contract without touching this source.
    (skills / "official-skill" / "SKILL.md").write_text(
        """---
name: official-skill
description: An official bundled skill.
---
# Official skill
""",
        encoding="utf-8",
    )
    (skills / "pinned-skill" / "SKILL.md").write_text(
        _skill_content("pinned-skill", description="A pinned skill."), encoding="utf-8"
    )
    planted = "ghp_" + "x" * 36
    (skills / "restricted-skill" / "SKILL.md").write_text(
        _skill_content(
            "restricted-skill",
            description="A quarantined skill.",
            body=f"Token: {planted}\n",
        ),
        encoding="utf-8",
    )
    (root / "unrelated.txt").write_text("do not change\n", encoding="utf-8")
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return _write_workspace(tmp_path)


@pytest.fixture
def service(workspace: Path) -> SkillAuthoringService:
    return SkillAuthoringService(workspace, pinned_skill_ids={"pinned-skill"})


def _snapshot_pair(root: Path, skill_id: str) -> tuple[str, str]:
    snapshot = CatalogService(root).load()
    skill = snapshot.skill(skill_id)
    assert skill is not None
    return snapshot.catalog_sha256, skill.source_sha256


def _create_pending_fork(
    service: SkillAuthoringService,
    *,
    target_id: str,
    idempotency_key: str,
) -> tuple[Any, dict[str, Any]]:
    context = service._context("official-skill")
    request = {
        "operation": "fork",
        "source_skill_id": "official-skill",
        "new_skill_id": target_id,
        "expected_catalog_sha256": context.snapshot.catalog_sha256,
        "expected_source_sha256": context.definition.source_sha256,
        "actor_id": "user_local_test",
    }
    proposed = service._fork_content(context, target_id)
    intent = service._new_recovery_intent(
        operation="fork",
        source_skill_id="official-skill",
        target_skill_id=target_id,
        scope="skill_authoring_fork",
        idempotency_key=idempotency_key,
        request=request,
        original_source_sha256=context.definition.source_sha256,
        proposed_source_sha256=sha256_hex(proposed.data),
    )
    return service._create_skill_file(target_id, proposed.data, intent=intent), request


def _create_pending_save(
    service: SkillAuthoringService,
    *,
    idempotency_key: str,
) -> tuple[Any, dict[str, Any], bytes, bytes]:
    context = service._context("local-skill")
    proposed = service._validate_content(
        "local-skill",
        _skill_content(
            "local-skill",
            description="Synthetic pre-rebrand RecoveryV2 save.",
            version="2.0.0",
        ),
    )
    request = {
        "operation": "save",
        "skill_id": "local-skill",
        "proposed_source_sha256": sha256_hex(proposed.data),
        "expected_catalog_sha256": context.snapshot.catalog_sha256,
        "expected_source_sha256": context.definition.source_sha256,
        "actor_id": "user_local_test",
    }
    intent = service._new_recovery_intent(
        operation="save",
        source_skill_id="local-skill",
        target_skill_id="local-skill",
        scope="skill_authoring_save",
        idempotency_key=idempotency_key,
        request=request,
        original_source_sha256=context.definition.source_sha256,
        proposed_source_sha256=sha256_hex(proposed.data),
    )
    applied = service._atomic_replace(
        "local-skill",
        proposed.data,
        expected_source_sha256=context.definition.source_sha256,
        intent=intent,
    )
    return applied, request, context.data, proposed.data


def test_edit_policy_is_computed_from_origin_trust_pin_and_sensitivity(
    service: SkillAuthoringService,
) -> None:
    local = service.edit_policy("local-skill")
    official = service.edit_policy("official-skill")
    pinned = service.edit_policy("pinned-skill")
    restricted = service.edit_policy("restricted-skill")

    assert local == {
        "skill_id": "local-skill",
        "source_path": "skills/local-skill/SKILL.md",
        "pack_id": "pack_local",
        "trust_class": "user",
        "sensitivity": "internal",
        "editable": True,
        "read_only_reason": None,
        "forkable": True,
    }
    assert official["editable"] is False
    assert official["read_only_reason"] == "official_skill"
    assert official["forkable"] is True
    assert pinned["editable"] is False
    assert pinned["read_only_reason"] == "installed_pinned_pack"
    assert pinned["forkable"] is True
    assert restricted["editable"] is False
    assert restricted["read_only_reason"] == "sensitivity_restricted"
    assert restricted["forkable"] is False
    json.dumps([local, official, pinned, restricted])

    local_definition = CatalogService(service.root).load().skill("local-skill")
    assert local_definition is not None
    pinned_local = SkillAuthoringService.policy_for_definition(
        local_definition,
        pinned_skill_ids={"local-skill"},
    )
    assert pinned_local["editable"] is False
    assert pinned_local["read_only_reason"] == "installed_pinned_pack"

    unresolved = SkillAuthoringService.policy_for_definition(
        local_definition,
        pinned_skill_ids={PINNED_SKILL_POLICY_UNKNOWN},
    )
    assert unresolved["editable"] is False
    assert unresolved["forkable"] is False
    assert unresolved["read_only_reason"] == "installed_pack_state_unavailable"

    non_authorable = local_definition.model_copy(
        update={
            "skill_id": "local.skill",
            "source_path": "skills/local.skill/SKILL.md",
        }
    )
    rejected = SkillAuthoringService.policy_for_definition(non_authorable)
    assert rejected["editable"] is False
    assert rejected["forkable"] is False
    assert rejected["read_only_reason"] == "non_authorable_skill_id"


def test_recovery_v2_names_remain_deterministic_across_product_rebrand() -> None:
    transaction_id = "a" * 48

    assert SkillAuthoringService._save_recovery_names(transaction_id) == (
        f".agentos-save-{transaction_id}.proposed",
        f".agentos-save-{transaction_id}.original",
        f".agentos-save-{transaction_id}.displaced",
    )
    assert SkillAuthoringService._fork_marker_name(transaction_id) == (
        f".agentos-fork-{transaction_id}.marker"
    )
    assert SkillAuthoringService._fork_marker_data(transaction_id) == (
        f"agentos-skill-authoring:{transaction_id}\n".encode("ascii")
    )


@pytest.mark.parametrize("operation", ["save", "fork"])
@pytest.mark.parametrize("committed", [False, True])
def test_current_startup_recovers_synthetic_pre_rebrand_v2_state(
    workspace: Path,
    service: SkillAuthoringService,
    operation: str,
    committed: bool,
) -> None:
    idempotency_key = f"legacy-v2-{operation}-{'committed' if committed else 'pending'}"
    if operation == "save":
        intent, request, original, proposed = _create_pending_save(
            service,
            idempotency_key=idempotency_key,
        )
        target = workspace / "skills" / "local-skill" / "SKILL.md"
        assert target.read_bytes() == proposed
        assert len(list(target.parent.glob(".agentos-save-*"))) == 1
        assert not list(target.parent.glob(".raytsystem-save-*"))
    else:
        target_id = f"legacy-v2-fork-{'committed' if committed else 'pending'}"
        intent, request = _create_pending_fork(
            service,
            target_id=target_id,
            idempotency_key=idempotency_key,
        )
        target = workspace / "skills" / target_id / "SKILL.md"
        original = b""
        proposed = target.read_bytes()
        marker = target.parent / f".agentos-fork-{intent.transaction_id}.marker"
        assert marker.read_bytes() == (
            f"agentos-skill-authoring:{intent.transaction_id}\n".encode("ascii")
        )
        assert not list(target.parent.glob(".raytsystem-fork-*"))

    state = workspace / "ops" / "skill-authoring-recovery"
    journal = state / f"txn-{intent.transaction_id}.json"
    assert json.loads(journal.read_text(encoding="utf-8"))["schema_name"] == (
        "SkillAuthoringRecoveryV2"
    )
    if committed:
        with initialize_platform_store(workspace) as store:
            store.idempotent_receipt(
                scope=f"skill_authoring_{operation}",
                idempotency_key=idempotency_key,
                request=request,
                receipt={
                    "operation": operation,
                    "skill_id": intent.target_skill_id,
                    "source_sha256": intent.proposed_source_sha256,
                },
            )

    from raytsystem.webapp import create_app

    app = create_app(workspace)
    assert app.state.skill_authoring is not None
    assert not list(state.glob("txn-*.json*"))
    if operation == "save":
        assert target.read_bytes() == (proposed if committed else original)
        assert not list(target.parent.glob(".agentos-save-*"))
    elif committed:
        assert target.read_bytes() == proposed
        assert not list(target.parent.glob(".agentos-fork-*"))
    else:
        assert not target.parent.exists()
    snapshot = CatalogService(workspace).load()
    if operation == "fork":
        assert (snapshot.skill(intent.target_skill_id) is not None) is committed
    else:
        recovered = snapshot.skill("local-skill")
        assert recovered is not None
        assert recovered.source_sha256 == sha256_hex(proposed if committed else original)


def test_preview_save_returns_normalized_pending_diff_validation_and_agents(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    proposed = _skill_content(
        "local-skill",
        description="Edited locally.",
        version="1.1.0",
        test_status="pass",
        body="# Edited\n",
    )
    before = (workspace / "skills" / "local-skill" / "SKILL.md").read_bytes()

    preview = service.preview_save(
        "local-skill",
        content=proposed,
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
    )

    assert preview["operation"] == "skill_save_preview"
    assert preview["validation"]["valid"] is True
    assert preview["validation"]["requested_test_status"] == "pass"
    assert preview["validation"]["effective_test_status"] == "pending"
    assert preview["validation"]["warnings"][0]["code"] == "test_status_reset"
    assert "test_status: pending" in preview["normalized_content"]
    assert "test_status: pass" not in preview["normalized_content"]
    assert "skills/local-skill/SKILL.md" in preview["diff"]
    assert preview["affected_agents"] == [{"agent_id": "agent_builder", "name": "Builder"}]
    assert (workspace / "skills" / "local-skill" / "SKILL.md").read_bytes() == before
    assert not (workspace / "ops" / "platform.sqlite").exists()
    json.dumps(preview)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            """---
name: local-skill
description: Missing fields.
---
# Body
""",
            "required",
        ),
        (_skill_content("wrong-name"), "directory_name_mismatch"),
        (
            """---
name: local-skill
description: Bad permission.
version: "1.0.0"
permissions: ["UPPER CASE"]
test_status: pending
---
# Body
""",
            "invalid_permission",
        ),
        (_skill_content("local-skill", test_status="verified"), "invalid_status"),
    ],
)
def test_preview_rejects_invalid_required_frontmatter(
    workspace: Path,
    service: SkillAuthoringService,
    content: str,
    code: str,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")

    with pytest.raises(SkillValidationError) as captured:
        service.preview_save(
            "local-skill",
            content=content,
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )

    assert code in {item["code"] for item in captured.value.details["errors"]}
    json.dumps(captured.value.to_dict())


def test_preview_rejects_duplicate_yaml_keys_aliases_and_restricted_content(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    duplicate = _skill_content("local-skill").replace(
        "description: A local test skill.\n",
        "description: First.\ndescription: Second.\n",
    )
    alias = _skill_content("local-skill").replace(
        "permissions:\n  - filesystem_read",
        "permissions: &permissions [filesystem_read]\nextensions: *permissions",
    )
    secret = _skill_content(
        "local-skill",
        body="Token: ghp_" + "z" * 36 + "\n",
    )

    for content, expected in (
        (duplicate, "invalid_frontmatter"),
        (alias, "invalid_frontmatter"),
        (secret, "restricted_content"),
    ):
        with pytest.raises(SkillValidationError) as captured:
            service.preview_save(
                "local-skill",
                content=content,
                expected_catalog_sha256=catalog_sha,
                expected_source_sha256=source_sha,
            )
        assert captured.value.details["errors"][0]["code"] == expected


def test_preview_rejects_oversize_invalid_utf8_and_bad_skill_id(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    oversized = _skill_content(
        "local-skill",
        body="x" * service.max_content_bytes,
    )
    with pytest.raises(SkillValidationError) as size_error:
        service.preview_save(
            "local-skill",
            content=oversized,
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )
    assert size_error.value.details["errors"][0]["code"] == "content_too_large"

    with pytest.raises(SkillValidationError) as encoding_error:
        service.preview_save(
            "local-skill",
            content=_skill_content("local-skill", body="\ud800"),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )
    assert encoding_error.value.details["errors"][0]["code"] == "invalid_utf8"

    with pytest.raises(SkillPathError):
        service.edit_policy("../escape")
    with pytest.raises(SkillPathError):
        service.edit_policy("a/b")


def test_source_symlink_and_hardlink_are_rejected(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    replacement = workspace / "replacement.md"
    replacement.write_text(_skill_content("local-skill"), encoding="utf-8")
    target.unlink()
    target.symlink_to(replacement)

    with pytest.raises(SkillPathError):
        service.edit_policy("local-skill")

    target.unlink()
    os.link(replacement, target)
    with pytest.raises(SkillPathError):
        service.edit_policy("local-skill")


def test_stale_source_conflict_contains_both_versions_and_diff(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    target.write_text(
        _skill_content("local-skill", description="Changed elsewhere."),
        encoding="utf-8",
    )
    proposed = _skill_content("local-skill", description="My editor version.")

    with pytest.raises(SkillConflictError) as captured:
        service.preview_save(
            "local-skill",
            content=proposed,
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )

    details = captured.value.details
    assert details["kind"] == "source_sha256"
    assert "Changed elsewhere." in details["current_content"]
    assert "My editor version." in details["proposed_content"]
    assert details["diff"]
    assert details["content_withheld"] is False
    json.dumps(captured.value.to_dict())


def test_write_race_scans_exact_concurrent_bytes_and_withholds_secret(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    secret = "ghp_" + "s" * 36
    concurrent = _skill_content(
        "local-skill",
        description="Concurrent restricted bytes.",
        body=f"Token: {secret}\n",
    ).encode()
    original_replace = service._atomic_replace

    def race_then_replace(
        skill_id: str,
        data: bytes,
        *,
        expected_source_sha256: str,
        intent: Any,
    ) -> Any:
        target.write_bytes(concurrent)
        return original_replace(
            skill_id,
            data,
            expected_source_sha256=expected_source_sha256,
            intent=intent,
        )

    monkeypatch.setattr(service, "_atomic_replace", race_then_replace)
    with pytest.raises(SkillConflictError) as captured:
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="My safe editor version."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="race-secret-save",
            actor_id="user_local_test",
        )

    rendered = json.dumps(captured.value.to_dict())
    assert captured.value.details["content_withheld"] is True
    assert captured.value.details["current_source_sha256"] == sha256_hex(concurrent)
    assert "current_content" not in captured.value.details
    assert "proposed_content" not in captured.value.details
    assert secret not in rendered
    assert target.read_bytes() == concurrent


def test_final_install_boundary_never_overwrites_concurrent_replacement(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    concurrent = _skill_content(
        "local-skill",
        description="Won the actual final install boundary.",
    ).encode()
    original_link = service._link_no_replace
    injected = False

    def race_at_no_replace(directory_fd: int, source_name: str, target_name: str) -> None:
        nonlocal injected
        if not injected and source_name.endswith(".proposed") and target_name == "SKILL.md":
            injected = True
            target.write_bytes(concurrent)
        original_link(directory_fd, source_name, target_name)

    monkeypatch.setattr(service, "_link_no_replace", race_at_no_replace)
    with pytest.raises(SkillConflictError) as captured:
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="Must not win the race."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="final-boundary-race",
            actor_id="user_local_test",
        )

    assert injected is True
    assert captured.value.details["kind"] == "source_sha256"
    assert target.read_bytes() == concurrent
    assert not list(target.parent.glob(".agentos-save-*"))
    state = workspace / "ops" / "skill-authoring-recovery"
    assert not list(state.glob("txn-*.json*"))


def test_stale_catalog_hash_is_a_typed_conflict_even_when_source_is_unchanged(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    added = workspace / "skills" / "another-skill"
    added.mkdir()
    (added / "SKILL.md").write_text(_skill_content("another-skill"), encoding="utf-8")

    with pytest.raises(SkillConflictError) as captured:
        service.preview_save(
            "local-skill",
            content=_skill_content("local-skill", description="Still mine."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )

    assert captured.value.details["kind"] == "catalog_sha256"
    assert captured.value.details["current_source_sha256"] == source_sha


def test_save_is_atomic_idempotent_audited_and_never_trusts_frontmatter_pass(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    unrelated = workspace / "unrelated.txt"
    unrelated_before = (unrelated.read_bytes(), unrelated.stat().st_mtime_ns)
    content = _skill_content(
        "local-skill",
        description="Saved safely.",
        version="2.0.0",
        test_status="pass",
        body="# Saved\n",
    )

    saved = service.save(
        "local-skill",
        content=content,
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="save-local-1",
        actor_id="user_local_test",
    )
    replay = service.save(
        "local-skill",
        content=content,
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="save-local-1",
        actor_id="user_local_test",
    )

    target = workspace / "skills" / "local-skill" / "SKILL.md"
    written = target.read_bytes()
    assert saved == replay
    assert saved["source_sha256"] == sha256_hex(written)
    assert saved["source_sha256"] != source_sha
    assert saved["catalog_sha256"] != catalog_sha
    assert saved["test_status"] == "pending"
    assert b"test_status: pending" in written
    assert b"test_status: pass" not in written
    assert saved["affected_agents"] == [{"agent_id": "agent_builder", "name": "Builder"}]
    assert saved["cache_invalidation"] == {
        "scope": "related_skill_queries",
        "skill_ids": ["local-skill"],
    }
    assert (unrelated.read_bytes(), unrelated.stat().st_mtime_ns) == unrelated_before

    snapshot = CatalogService(workspace).load()
    skill = snapshot.skill("local-skill")
    assert skill is not None
    assert skill.source_sha256 == saved["source_sha256"]
    assert skill.test_status == "pending"
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        record = store.head("skill_authoring_revision", "local-skill")
        assert record is not None and record.revision == 1
        assert record.state == "pending"
        assert record.payload["test_status"] == "pending"
        events = store.list_events("skill_authoring_local-skill")
        assert len(events) == 1
        assert events[0]["event_type"] == "skill_saved"
        assert store.verify_event_stream("skill_authoring_local-skill")
    json.dumps(saved)


def test_save_rechecks_active_package_pins_inside_write_transaction(
    workspace: Path,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    service = SkillAuthoringService(workspace, pinned_skill_ids=())
    assert service.edit_policy("local-skill")["editable"] is True
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    before = target.read_bytes()

    with initialize_platform_store(workspace) as store:
        store.append_record(
            kind="package_manifest",
            record_id="pkgrev_late_pin",
            payload={"skill_ids": ["local-skill"]},
            state="active",
            expected_revision=None,
        )
        store.append_record(
            kind="package_active",
            record_id="package_late_pin",
            payload={"revision_id": "pkgrev_late_pin"},
            state="active",
            expected_revision=None,
        )

    with pytest.raises(SkillReadOnlyError) as captured:
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="Must remain unchanged."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="late-pin-save",
            actor_id="user_local_test",
        )

    assert captured.value.code == "skill_read_only"
    assert captured.value.details["read_only_reason"] == "installed_pinned_pack"
    assert target.read_bytes() == before
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        assert store.head("skill_authoring_revision", "local-skill") is None


def test_idempotency_key_reuse_with_another_payload_is_rejected(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    first = _skill_content("local-skill", description="First request.")
    service.save(
        "local-skill",
        content=first,
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="same-key",
        actor_id="user_local_test",
    )

    with pytest.raises(SkillIdempotencyError):
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="Different request."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="same-key",
            actor_id="user_local_test",
        )


def test_revisions_and_audit_events_form_verifiable_chains(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    service.save(
        "local-skill",
        content=_skill_content("local-skill", description="Revision one."),
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="revision-1",
        actor_id="user_local_test",
    )
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        first = store.head("skill_authoring_revision", "local-skill")
        assert first is not None

    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    service.save(
        "local-skill",
        content=_skill_content("local-skill", description="Revision two."),
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="revision-2",
        actor_id="user_local_test",
    )

    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        second = store.head("skill_authoring_revision", "local-skill")
        assert second is not None and second.revision == 2
        assert second.payload["previous_revision_sha256"] == first.payload_sha256
        events = store.list_events("skill_authoring_local-skill")
        assert [event["sequence"] for event in events] == [1, 2]
        assert events[1]["previous_event_sha256"] is not None
        assert store.verify_event_stream("skill_authoring_local-skill")


def test_failed_store_update_restores_original_file(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    original = target.read_bytes()

    def fail_record(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise PlatformStoreError("injected record failure")

    monkeypatch.setattr(service, "_record_change", fail_record)
    with pytest.raises(SkillPersistenceError):
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="Must roll back."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="rollback-save",
            actor_id="user_local_test",
        )

    assert target.read_bytes() == original


def test_save_recovery_never_overwrites_unproven_third_party_version(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    third_party = _skill_content(
        "local-skill",
        description="Third-party version after filesystem commit.",
    ).encode()

    def race_then_fail(*_args: object, **_kwargs: object) -> dict[str, Any]:
        target.write_bytes(third_party)
        raise PlatformStoreError("injected record failure after third-party edit")

    monkeypatch.setattr(service, "_record_change", race_then_fail)
    with pytest.raises(SkillPersistenceError) as captured:
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="raytsystem proposal."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="third-party-save-recovery",
            actor_id="user_local_test",
        )

    assert captured.value.details["manual_recovery_required"] is True
    assert target.read_bytes() == third_party
    assert list((workspace / "ops" / "skill-authoring-recovery").glob("txn-*.json"))


def test_save_crash_restart_recovers_filesystem_before_uncommitted_store_write(
    workspace: Path,
) -> None:
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    original = target.read_bytes()
    script = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        from raytsystem.catalog import CatalogService
        from raytsystem.skill_authoring import SkillAuthoringService

        root = Path(sys.argv[1])
        snapshot = CatalogService(root).load()
        source = snapshot.skill("local-skill")
        assert source is not None
        service = SkillAuthoringService(root, pinned_skill_ids={"pinned-skill"})
        service._load_updated = lambda *_args, **_kwargs: os._exit(73)
        content = '''---
        name: local-skill
        description: Crash-window save.
        version: "2.0.0"
        permissions:
          - filesystem_read
        test_status: pending
        ---
        # Crash window
        '''.replace("        ", "")
        service.save(
            "local-skill",
            content=content,
            expected_catalog_sha256=snapshot.catalog_sha256,
            expected_source_sha256=source.source_sha256,
            idempotency_key="crash-save-recovery",
            actor_id="user_local_test",
        )
        """
    )
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(workspace)],
        cwd=Path(__file__).parents[1],
        check=False,
    )

    assert crashed.returncode == 73
    assert target.read_bytes() != original
    state = workspace / "ops" / "skill-authoring-recovery"
    assert len(list(state.glob("txn-*.json"))) == 1

    restarted = SkillAuthoringService(workspace, pinned_skill_ids={"pinned-skill"})
    assert restarted.recover_pending() == {"recovered": 1}
    assert target.read_bytes() == original
    assert not list(state.glob("txn-*.json*"))
    assert not list(target.parent.glob(".agentos-save-*"))
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        assert store.head("skill_authoring_revision", "local-skill") is None


@pytest.mark.skipif(os.name == "nt", reason="fault-injection harness relies on POSIX process exit semantics")
def test_startup_recovers_crash_after_original_is_renamed_to_displaced(
    workspace: Path,
) -> None:
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    original = target.read_bytes()
    snapshot = CatalogService(workspace).load()
    source = snapshot.skill("local-skill")
    assert source is not None
    script = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        import raytsystem.skill_authoring as authoring_module
        from raytsystem.skill_authoring import SkillAuthoringService

        root = Path(sys.argv[1])
        original_rename = authoring_module.os.rename

        def crash_after_displacing_original(source, target, *args, **kwargs):
            original_rename(source, target, *args, **kwargs)
            if source == "SKILL.md" and str(target).endswith(".displaced"):
                os._exit(75)

        authoring_module.os.rename = crash_after_displacing_original
        service = SkillAuthoringService(root, pinned_skill_ids={"pinned-skill"})
        service.save(
            "local-skill",
            content='''---
        name: local-skill
        description: Crash immediately after displacing the original.
        version: "2.0.0"
        permissions:
          - filesystem_read
        test_status: pending
        ---
        # Must roll back
        '''.replace("        ", ""),
            expected_catalog_sha256=sys.argv[2],
            expected_source_sha256=sys.argv[3],
            idempotency_key="crash-after-displaced-rename",
            actor_id="user_local_test",
        )
        """
    )
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(workspace),
            snapshot.catalog_sha256,
            source.source_sha256,
        ],
        cwd=Path(__file__).parents[1],
        check=False,
    )

    assert crashed.returncode == 75
    assert not target.exists()
    assert len(list(target.parent.glob(".agentos-save-*.displaced"))) == 1
    assert len(list((workspace / "ops" / "skill-authoring-recovery").glob("txn-*.json"))) == 1

    from raytsystem.webapp import create_app

    app = create_app(workspace)
    assert app.state.skill_authoring is not None
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".agentos-save-*"))
    assert not list((workspace / "ops" / "skill-authoring-recovery").glob("txn-*.json*"))
    recovered = CatalogService(workspace).load()
    recovered_skill = recovered.skill("local-skill")
    assert recovered.catalog_sha256 == snapshot.catalog_sha256
    assert recovered_skill is not None
    assert recovered_skill.source_sha256 == source.source_sha256


def test_non_target_catalog_race_rejects_and_rolls_back_only_target(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    target = workspace / "skills" / "local-skill" / "SKILL.md"
    unrelated = workspace / "skills" / "pinned-skill" / "SKILL.md"
    target_before = target.read_bytes()
    concurrent = _skill_content(
        "pinned-skill",
        description="Concurrent unrelated catalog edit.",
    ).encode()
    original_load_updated = service._load_updated

    def load_after_unrelated_race(skill_id: str, expected_data: bytes) -> Any:
        unrelated.write_bytes(concurrent)
        return original_load_updated(skill_id, expected_data)

    monkeypatch.setattr(service, "_load_updated", load_after_unrelated_race)
    with pytest.raises(SkillConflictError) as captured:
        service.save(
            "local-skill",
            content=_skill_content("local-skill", description="Must be rolled back."),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="catalog-race-save",
            actor_id="user_local_test",
        )

    assert captured.value.details["kind"] == "non_target_catalog_changed"
    assert captured.value.details["content_withheld"] is True
    assert target.read_bytes() == target_before
    assert unrelated.read_bytes() == concurrent
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        assert store.head("skill_authoring_revision", "local-skill") is None


def test_read_only_skills_cannot_be_saved_and_restricted_skill_cannot_be_forked(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    with pytest.raises(SkillReadOnlyError) as official_error:
        service.preview_save(
            "official-skill",
            content=_skill_content("official-skill"),
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )
    assert official_error.value.details["forkable"] is True

    catalog_sha, source_sha = _snapshot_pair(workspace, "restricted-skill")
    with pytest.raises(SkillReadOnlyError) as restricted_error:
        service.preview_fork(
            "restricted-skill",
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )
    assert restricted_error.value.details["forkable"] is False


def test_fork_preview_proposes_unique_local_id_and_complete_pending_frontmatter(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    source = (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes()

    preview = service.preview_fork(
        "official-skill",
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
    )

    assert preview["new_skill_id"] == "official-skill-local"
    assert preview["destination"] == "skills/official-skill-local/SKILL.md"
    assert preview["source_unchanged"] is True
    assert preview["validation"]["effective_test_status"] == "pending"
    assert "version: unversioned" in preview["diff"]
    assert "permissions: []" in preview["diff"]
    assert "test_status: pending" in preview["diff"]
    assert (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes() == source
    assert not (workspace / "skills" / "official-skill-local").exists()
    json.dumps(preview)


def test_create_fork_does_not_mutate_source_and_returns_editable_local_skill(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    source_path = workspace / "skills" / "official-skill" / "SKILL.md"
    source_before = source_path.read_bytes()

    created = service.create_fork(
        "official-skill",
        new_skill_id="official-skill-local",
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="fork-official-1",
        actor_id="user_local_test",
    )
    replay = service.create_fork(
        "official-skill",
        new_skill_id="official-skill-local",
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
        idempotency_key="fork-official-1",
        actor_id="user_local_test",
    )

    assert created == replay
    assert source_path.read_bytes() == source_before
    target = workspace / "skills" / "official-skill-local" / "SKILL.md"
    assert target.is_file() and not target.is_symlink()
    assert created["source_sha256"] == sha256_hex(target.read_bytes())
    assert created["source_skill_id"] == "official-skill"
    assert created["test_status"] == "pending"
    snapshot = CatalogService(workspace).load()
    copied = snapshot.skill("official-skill-local")
    assert copied is not None
    assert copied.pack_id == "pack_local"
    assert copied.trust_class.value == "user"
    assert copied.test_status == "pending"
    assert service.edit_policy("official-skill-local")["editable"] is True
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        assert store.verify_event_stream("skill_authoring_official-skill-local")
        events = store.list_events("skill_authoring_official-skill-local")
        assert len(events) == 1 and events[0]["event_type"] == "skill_forked"


def test_fork_rejects_existing_destination_without_changing_either_skill(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    source = (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes()
    local = (workspace / "skills" / "local-skill" / "SKILL.md").read_bytes()

    with pytest.raises(SkillConflictError) as captured:
        service.preview_fork(
            "official-skill",
            new_skill_id="local-skill",
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
        )

    assert captured.value.details["kind"] == "destination_exists"
    assert (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes() == source
    assert (workspace / "skills" / "local-skill" / "SKILL.md").read_bytes() == local


def test_failed_fork_store_update_removes_only_the_new_destination(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    source = (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes()

    def fail_record(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise PlatformStoreError("injected record failure")

    monkeypatch.setattr(service, "_record_change", fail_record)
    with pytest.raises(SkillPersistenceError):
        service.create_fork(
            "official-skill",
            new_skill_id="fork-rollback",
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="rollback-fork",
            actor_id="user_local_test",
        )

    assert not (workspace / "skills" / "fork-rollback").exists()
    assert (workspace / "skills" / "official-skill" / "SKILL.md").read_bytes() == source
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "do not change\n"


def test_fork_recovery_never_deletes_destination_with_unproven_extra_content(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    target = workspace / "skills" / "fork-third-party"

    def race_then_fail(*_args: object, **_kwargs: object) -> dict[str, Any]:
        (target / "third-party-note.txt").write_text("preserve me\n", encoding="utf-8")
        raise PlatformStoreError("injected record failure after third-party fork edit")

    monkeypatch.setattr(service, "_record_change", race_then_fail)
    with pytest.raises(SkillPersistenceError) as captured:
        service.create_fork(
            "official-skill",
            new_skill_id="fork-third-party",
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="third-party-fork-recovery",
            actor_id="user_local_test",
        )

    assert captured.value.details["manual_recovery_required"] is True
    assert (target / "third-party-note.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert (target / "SKILL.md").is_file()
    assert list((workspace / "ops" / "skill-authoring-recovery").glob("txn-*.json"))


def test_fork_recovery_preserves_same_bytes_replaced_with_a_new_inode(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    target = workspace / "skills" / "fork-replaced-inode"
    _create_pending_fork(
        service,
        target_id="fork-replaced-inode",
        idempotency_key="fork-replaced-inode",
    )
    skill = target / "SKILL.md"
    original = skill.stat()
    replacement = target / ".third-party-replacement"
    replacement.write_bytes(skill.read_bytes())
    os.replace(replacement, skill)
    assert (skill.stat().st_dev, skill.stat().st_ino) != (
        original.st_dev,
        original.st_ino,
    )

    with pytest.raises(SkillPersistenceError) as captured:
        service.recover_pending()

    assert captured.value.details["manual_recovery_required"] is True
    assert skill.is_file()
    assert list(target.glob(".agentos-fork-*.marker"))
    assert list((workspace / "ops" / "skill-authoring-recovery").glob("txn-*.json"))


def test_recovery_receipt_must_match_exact_idempotency_identity(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    target_id = "fork-receipt-identity"
    intent, request = _create_pending_fork(
        service,
        target_id=target_id,
        idempotency_key="current-transaction-key",
    )
    with initialize_platform_store(workspace) as store:
        store.idempotent_receipt(
            scope="skill_authoring_fork",
            idempotency_key="older-identical-request-key",
            request=request,
            receipt={
                "operation": "fork",
                "skill_id": target_id,
                "source_sha256": intent.proposed_source_sha256,
            },
        )

    assert service.recover_pending() == {"recovered": 1}
    assert not (workspace / "skills" / target_id).exists()


def test_recovery_falls_back_to_valid_final_when_next_is_torn(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    target_id = "fork-torn-next"
    intent, _request = _create_pending_fork(
        service,
        target_id=target_id,
        idempotency_key="fork-torn-next",
    )
    state = workspace / "ops" / "skill-authoring-recovery"
    next_path = state / f"txn-{intent.transaction_id}.json.next"
    next_path.write_bytes(b'{"schema_name":"SkillAuthoringRecoveryV2"')

    assert service.recover_pending() == {"recovered": 1}
    assert not (workspace / "skills" / target_id).exists()
    assert not list(state.glob("txn-*.json*"))


def test_catalog_read_guard_never_exposes_an_in_progress_namespace_transition(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "local-skill")
    signal = workspace / "writer-at-filesystem-boundary"
    script = textwrap.dedent(
        """
        import sys
        import time
        from pathlib import Path

        from raytsystem.skill_authoring import SkillAuthoringService, SkillPersistenceError

        root = Path(sys.argv[1])
        service = SkillAuthoringService(root, pinned_skill_ids={"pinned-skill"})
        signal = root / "writer-at-filesystem-boundary"

        def pause_then_fail(*_args, **_kwargs):
            signal.write_text("ready", encoding="utf-8")
            time.sleep(0.5)
            raise SkillPersistenceError("injected failure after filesystem transition")

        service._load_updated = pause_then_fail
        try:
            service.save(
                "local-skill",
                content='''---
        name: local-skill
        description: Must never be visible to a concurrent reader.
        version: "2.0.0"
        permissions:
          - filesystem_read
        test_status: pending
        ---
        # Uncommitted
        '''.replace("        ", ""),
                expected_catalog_sha256=sys.argv[2],
                expected_source_sha256=sys.argv[3],
                idempotency_key="concurrent-reader-fence",
                actor_id="user_local_test",
            )
        except SkillPersistenceError:
            pass
        else:
            raise AssertionError("injected failure did not run")
        """
    )
    writer = subprocess.Popen(
        [sys.executable, "-c", script, str(workspace), catalog_sha, source_sha],
        cwd=Path(__file__).parents[1],
    )
    deadline = monotonic() + 5
    while not signal.exists():
        assert monotonic() < deadline, "writer did not reach the namespace transition"
        sleep(0.01)

    started = monotonic()
    with service.catalog_read_guard():
        observed = CatalogService(workspace).load().skill("local-skill")
    elapsed = monotonic() - started
    writer.wait(timeout=5)

    assert writer.returncode == 0
    assert elapsed >= 0.25
    assert observed is not None and observed.source_sha256 == source_sha


def test_fork_crash_restart_removes_only_proven_uncommitted_destination(
    workspace: Path,
) -> None:
    source = workspace / "skills" / "official-skill" / "SKILL.md"
    source_before = source.read_bytes()
    target = workspace / "skills" / "fork-crash-recovery"
    script = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        from raytsystem.catalog import CatalogService
        from raytsystem.skill_authoring import SkillAuthoringService

        root = Path(sys.argv[1])
        snapshot = CatalogService(root).load()
        source = snapshot.skill("official-skill")
        assert source is not None
        service = SkillAuthoringService(root, pinned_skill_ids={"pinned-skill"})
        service._load_updated = lambda *_args, **_kwargs: os._exit(74)
        service.create_fork(
            "official-skill",
            new_skill_id="fork-crash-recovery",
            expected_catalog_sha256=snapshot.catalog_sha256,
            expected_source_sha256=source.source_sha256,
            idempotency_key="crash-fork-recovery",
            actor_id="user_local_test",
        )
        """
    )
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(workspace)],
        cwd=Path(__file__).parents[1],
        check=False,
    )

    assert crashed.returncode == 74
    assert target.is_dir()
    assert len(list(target.glob(".agentos-fork-*.marker"))) == 1
    state = workspace / "ops" / "skill-authoring-recovery"
    assert len(list(state.glob("txn-*.json"))) == 1

    restarted = SkillAuthoringService(workspace, pinned_skill_ids={"pinned-skill"})
    assert restarted.recover_pending() == {"recovered": 1}
    assert not target.exists()
    assert source.read_bytes() == source_before
    assert not list(state.glob("txn-*.json*"))
    store = open_platform_store_read_only(workspace)
    assert store is not None
    with store:
        assert store.head("skill_authoring_revision", "fork-crash-recovery") is None


def test_non_target_catalog_race_cleans_up_only_new_fork(
    workspace: Path,
    service: SkillAuthoringService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    source = workspace / "skills" / "official-skill" / "SKILL.md"
    unrelated = workspace / "skills" / "pinned-skill" / "SKILL.md"
    source_before = source.read_bytes()
    concurrent = _skill_content(
        "pinned-skill",
        description="Concurrent change during fork.",
    ).encode()
    original_load_updated = service._load_updated

    def load_after_unrelated_race(skill_id: str, expected_data: bytes) -> Any:
        unrelated.write_bytes(concurrent)
        return original_load_updated(skill_id, expected_data)

    monkeypatch.setattr(service, "_load_updated", load_after_unrelated_race)
    with pytest.raises(SkillConflictError) as captured:
        service.create_fork(
            "official-skill",
            new_skill_id="fork-catalog-race",
            expected_catalog_sha256=catalog_sha,
            expected_source_sha256=source_sha,
            idempotency_key="fork-catalog-race",
            actor_id="user_local_test",
        )

    assert captured.value.details["kind"] == "non_target_catalog_changed"
    assert source.read_bytes() == source_before
    assert unrelated.read_bytes() == concurrent
    assert not (workspace / "skills" / "fork-catalog-race").exists()


def test_public_errors_and_successes_are_json_ready_and_redacted(
    workspace: Path,
    service: SkillAuthoringService,
) -> None:
    catalog_sha, source_sha = _snapshot_pair(workspace, "official-skill")
    result = service.preview_fork(
        "official-skill",
        expected_catalog_sha256=catalog_sha,
        expected_source_sha256=source_sha,
    )
    json.dumps(result)

    with pytest.raises(SkillAuthoringError) as captured:
        service.edit_policy("../absolute-or-traversal")
    rendered = json.dumps(captured.value.to_dict())
    assert str(workspace) not in rendered
