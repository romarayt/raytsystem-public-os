"""Read-only bootstrap planning + onboarding-prompt generation.

This is the engine core: it runs the installer's read-only phases
(preflight -> discovery/classification -> mapping -> plan) by composing the
existing hash-bound :class:`TemplateService` planner and never mutating the
target. The write path (apply/uninstall/rollback) is intentionally absent from
this build and is gated by the CLI.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from raytsystem.bootstrap.classify import _CODE_SUFFIXES, _SKIP_DIRS, RootClassifier
from raytsystem.contracts.base import (
    Sensitivity,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.installation import (
    BootstrapPlan,
    InstallationMode,
    InstallationRecord,
    PreflightReport,
    SourceClassification,
    SourceMap,
    SourceRoot,
    SourceRootPolicy,
    SourceType,
)
from raytsystem.contracts.operations import LedgerGeneration
from raytsystem.io import write_bytes_atomic, write_text_atomic
from raytsystem.templates.service import TemplateId, TemplateService

# raytsystem-managed data zones. A pre-existing user directory here would collide
# with raytsystem canonical/managed state, so the installer refuses to adopt it.
_MANAGED_DATA_ZONES: tuple[str, ...] = ("_raw", "normalized", "ledger", "inbox", "knowledge")
# Files that legitimately pre-exist in a user repo: never overwrite them.
# `_SKIP_IF_EXISTS` keeps the user's copy verbatim; `_MERGE_IF_EXISTS` appends a
# fenced managed block so re-runs and uninstall are reversible.
_SKIP_IF_EXISTS: frozenset[str] = frozenset({"README.md"})
_MERGE_IF_EXISTS: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md", "WORK.md", ".gitignore"})
_BLOCK_MARKERS_MD = ("<!-- RAYTSYSTEM:BEGIN -->", "<!-- RAYTSYSTEM:END -->")
_BLOCK_MARKERS_HASH = ("# RAYTSYSTEM:BEGIN", "# RAYTSYSTEM:END")

_POST_INIT_STEPS: tuple[str, ...] = (
    "genesis_generation",
    "rebuild_index",
    "graph_rebuild",
    "snapshot_check",
)
_TEMPLATE_BY_SOURCE: dict[SourceType, TemplateId] = {
    SourceType.EMPTY: "software",
    SourceType.SOFTWARE: "software",
    SourceType.MIXED: "software",
    SourceType.OBSIDIAN: "research",
    SourceType.GRAPHIFY: "research",
    SourceType.MARKDOWN: "content",
}
# Top-level names that are raytsystem-managed or vendored and never proposed as a
# user source root.
_NON_SOURCE_DIRS = _SKIP_DIRS | frozenset(
    {
        ".obsidian",
        "config",
        "packs",
        "skills",
        "workflows",
        "tasks",
        "inbox",
        "knowledge",
        "ledger",
        "normalized",
        "_raw",
        "ops",
        "artifacts",
        "evals",
        "benchmarks",
    }
)
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_MAX_PROBE_PER_ROOT = 4_000


class BootstrapError(RuntimeError):
    """The bootstrap request cannot be planned or applied safely."""


class BootstrapService:
    version = "1.0.0"

    def __init__(self, target: Path) -> None:
        self.target = target.resolve()

    # -- read-only phases ---------------------------------------------------

    def preflight(self) -> PreflightReport:
        warnings: list[str] = []
        blockers: list[str] = []
        python_ok = sys.version_info >= (3, 12)
        if not python_ok:
            blockers.append("Python 3.12+ is required")
        target_exists = self.target.is_dir()
        if not target_exists:
            blockers.append("Target directory does not exist")
        writable = target_exists and os.access(self.target, os.W_OK)
        if target_exists and not writable:
            blockers.append("Target directory is not writable")
        is_git_repo = (self.target / ".git").exists()
        git_clean = self._git_clean() if is_git_repo else None
        if git_clean is False:
            warnings.append("Working tree is dirty; commit or stash before --apply")
        if not is_git_repo:
            warnings.append("Target is not a Git repository; version control is the safety net")
        already_initialized = (self.target / "config" / "raytsystem.toml").is_file()
        if already_initialized:
            blockers.append(
                "raytsystem is already initialized here; use `migrate`/`upgrade` instead"
            )
        return PreflightReport(
            python_ok=python_ok,
            os=platform.system() or "unknown",
            target_exists=target_exists,
            writable=writable,
            is_git_repo=is_git_repo,
            git_clean=git_clean,
            already_initialized=already_initialized,
            warnings=tuple(warnings),
            blockers=tuple(blockers),
        )

    def classify(self) -> SourceClassification:
        return RootClassifier(self.target).classify()

    def propose_source_map(
        self, classification: SourceClassification, *, installation_id: str
    ) -> SourceMap:
        roots: list[SourceRoot] = []
        for name in self._candidate_source_dirs():
            kind = self._directory_kind(self.target / name)
            if kind is None:
                continue
            if kind == "code":
                source_type = SourceType.SOFTWARE
                policy = SourceRootPolicy.INDEX_AND_GRAPH
            else:
                source_type = (
                    classification.primary_type
                    if classification.primary_type
                    in {SourceType.OBSIDIAN, SourceType.MARKDOWN, SourceType.GRAPHIFY}
                    else SourceType.MARKDOWN
                )
                policy = SourceRootPolicy.INDEX_ONLY
            roots.append(
                SourceRoot(
                    source_root_id=derive_id(
                        "srcroot", {"relative_path": name, "source_type": source_type.value}
                    ),
                    relative_path=name,
                    source_type=source_type,
                    adapter=f"adapter:{source_type.value}",
                    sensitivity=Sensitivity.INTERNAL,
                    policy=policy,
                    provenance=("detected:bootstrap",),
                )
            )
        created_at = datetime.now(UTC)
        content = {
            "installation_id": installation_id,
            "roots": [r.model_dump(mode="json") for r in roots],
        }
        map_sha256 = sha256_hex(canonical_json_bytes(content))
        return SourceMap(
            source_map_id=derive_id(
                "srcmap", {"installation_id": installation_id, "map_sha256": map_sha256}
            ),
            installation_id=installation_id,
            roots=tuple(roots),
            map_sha256=map_sha256,
            created_at=created_at,
        )

    def plan(
        self,
        *,
        template: str = "auto",
        source_type: str = "auto",
        mode: str = "managed",
        context_language: str = "en",
    ) -> BootstrapPlan:
        install_mode = self._parse_mode(mode)
        preflight = self.preflight()
        classification = self.classify()
        template_id = self._resolve_template(template, source_type, classification)
        installation_id = derive_id(
            "install",
            {"target": self.target.name, "template_id": template_id, "mode": install_mode.value},
        )
        source_map = self.propose_source_map(classification, installation_id=installation_id)

        service = TemplateService()
        init_plan, _files = service.plan(self.target, template_id)

        # Refuse if the target already carries a directory that raytsystem manages
        # as canonical/managed state — the installer must not adopt a user's
        # pre-existing `ledger/`, `_raw/`, etc.
        protected = [zone for zone in _MANAGED_DATA_ZONES if (self.target / zone).exists()]

        fingerprint = derive_id(
            "bootstrap",
            {
                "init_plan_id": init_plan.init_plan_id,
                "manifest_sha256": init_plan.manifest_sha256,
                "mode": install_mode.value,
                "template_id": template_id,
                "context_language": context_language,
            },
        )
        return BootstrapPlan(
            bootstrap_plan_id=derive_id(
                "bplan",
                {
                    "fingerprint": fingerprint,
                    "classification_id": classification.classification_id,
                },
            ),
            target_name=self.target.name,
            mode=install_mode,
            template_id=template_id,
            template_version=init_plan.template_version,
            context_language=context_language,
            preflight=preflight,
            classification=classification,
            source_map=source_map,
            init_plan_id=init_plan.init_plan_id,
            manifest_sha256=init_plan.manifest_sha256,
            files_to_create=init_plan.files_to_create,
            conflicts=init_plan.conflicts,
            existing_repository=init_plan.existing_repository,
            confirmation_required=init_plan.confirmation_required,
            protected_collisions=tuple(protected),
            post_init_steps=_POST_INIT_STEPS,
            fingerprint=fingerprint,
        )

    # -- write path (apply / uninstall) -------------------------------------

    def apply(
        self,
        *,
        confirm: str,
        template: str = "auto",
        source_type: str = "auto",
        mode: str = "managed",
        context_language: str = "en",
    ) -> dict[str, Any]:
        """Apply a previously reviewed plan, bound to its fingerprint.

        Only creates net-new files (``TemplateService`` refuses any conflict),
        seeds a genesis generation so the workspace is functional, records the
        installation, and rebuilds the derived index. Never overwrites user data.
        """

        plan = self.plan(
            template=template,
            source_type=source_type,
            mode=mode,
            context_language=context_language,
        )
        if not plan.preflight.ok:
            raise BootstrapError("Preflight blocked: " + "; ".join(plan.preflight.blockers))
        if confirm != plan.fingerprint:
            raise BootstrapError(
                "Confirmation fingerprint does not match the current plan "
                "(the workspace changed, or the fingerprint is wrong). Re-run --dry-run."
            )
        if plan.protected_collisions:
            raise BootstrapError(
                "Refusing: target already has raytsystem-managed directories: "
                + ", ".join(plan.protected_collisions)
            )

        template_id = cast(TemplateId, plan.template_id)
        _init_plan, files = TemplateService().plan(self.target, template_id)

        # Classify every file read-only first; refuse before writing anything so a
        # partial apply can never leave the target in an indeterminate state.
        to_create, to_merge, to_skip, conflicts = self._classify_files(files)
        if conflicts:
            raise BootstrapError(
                "Refusing: these raytsystem-owned files already exist and differ: "
                + ", ".join(sorted(conflicts))
            )

        self._write_files(files, to_create, to_merge)
        genesis = self._seed_genesis()
        created_files = sorted({*to_create, *genesis, ".raytsystem/source-map.json"})
        record = self._write_installation(
            plan, created_files=created_files, merged_files=sorted(to_merge)
        )
        index_ok = self._rebuild_index()

        return {
            "status": "installed",
            "installation_id": record.installation_id,
            "template_id": plan.template_id,
            "mode": plan.mode.value,
            "target_name": self.target.name,
            "created": [*created_files, ".raytsystem/installation.json"],
            "merged": sorted(to_merge),
            "skipped": sorted(to_skip),
            "source_roots": [r.relative_path for r in plan.source_map.roots],
            "index_rebuilt": index_ok,
            "fingerprint": plan.fingerprint,
            "next": ["raytsystem doctor", "raytsystem ui"],
        }

    def uninstall(self) -> dict[str, Any]:
        """Remove only files raytsystem created; never touch user or source data."""

        record = self._load_installation()
        removed: list[str] = []
        for relative in record.created_files:
            target = self.target.joinpath(*relative.split("/"))
            if target.is_file() and not target.is_symlink():
                target.unlink()
                removed.append(relative)
        unmerged: list[str] = []
        for relative in record.merged_files:
            target = self.target.joinpath(*relative.split("/"))
            if target.is_file() and not target.is_symlink():
                self._strip_managed_block(target, relative)
                unmerged.append(relative)
        installation_json = self.target / ".raytsystem" / "installation.json"
        if installation_json.is_file():
            installation_json.unlink()
            removed.append(".raytsystem/installation.json")
        self._prune_empty_dirs(record.created_files)
        return {
            "status": "uninstalled",
            "installation_id": record.installation_id,
            "removed": sorted(set(removed)),
            "unmerged": sorted(unmerged),
            "note": "User files and source data were not deleted (merge blocks stripped).",
        }

    # -- write-path helpers -------------------------------------------------

    def _seed_genesis(self) -> list[str]:
        generations = self.target / "ledger" / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        genesis = LedgerGeneration(
            generation_id="genesis",
            parent_generation_id=None,
            records={},
            schema_registry_sha256=None,
            created_at=datetime.now(UTC),
        )
        # The corpus loader re-derives canonical bytes and rejects any mismatch,
        # so the genesis file must be written in exactly that canonical form.
        write_bytes_atomic(generations / "genesis.json", canonical_json_bytes(genesis))
        write_bytes_atomic(self.target / "ledger" / "CURRENT", b"genesis\n")
        return ["ledger/generations/genesis.json", "ledger/CURRENT"]

    def _classify_files(
        self, files: dict[str, bytes]
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        to_create: list[str] = []
        to_merge: list[str] = []
        to_skip: list[str] = []
        conflicts: list[str] = []
        for relative, data in sorted(files.items()):
            path = self.target.joinpath(*relative.split("/"))
            if path.is_symlink():
                conflicts.append(relative)
            elif not path.exists():
                to_create.append(relative)
            elif (path.is_file() and path.read_bytes() == data) or relative in _SKIP_IF_EXISTS:
                to_skip.append(relative)
            elif relative in _MERGE_IF_EXISTS:
                to_merge.append(relative)
            else:
                conflicts.append(relative)
        return to_create, to_merge, to_skip, conflicts

    def _write_files(
        self, files: dict[str, bytes], to_create: list[str], to_merge: list[str]
    ) -> None:
        for relative in to_create:
            path = self.target.joinpath(*relative.split("/"))
            mode = 0o755 if relative.endswith("pre-commit") else 0o644
            write_bytes_atomic(path, files[relative], mode=mode)
        for relative in to_merge:
            path = self.target.joinpath(*relative.split("/"))
            self._merge_managed_block(path, relative, files[relative].decode("utf-8"))

    def _markers(self, relative: str) -> tuple[str, str]:
        if relative == ".gitignore" or relative.endswith("/.gitignore"):
            return _BLOCK_MARKERS_HASH
        return _BLOCK_MARKERS_MD

    def _block_pattern(self, relative: str) -> re.Pattern[str]:
        begin, end = self._markers(relative)
        return re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)

    def _merge_managed_block(self, path: Path, relative: str, body: str) -> None:
        begin, end = self._markers(relative)
        block = f"{begin}\n{body.strip()}\n{end}\n"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        pattern = self._block_pattern(relative)
        if pattern.search(existing):
            merged = pattern.sub(lambda _m: block, existing)
        elif existing.strip():
            merged = existing.rstrip() + "\n\n" + block
        else:
            merged = block
        write_text_atomic(path, merged if merged.endswith("\n") else merged + "\n")

    def _strip_managed_block(self, path: Path, relative: str) -> None:
        existing = path.read_text(encoding="utf-8")
        stripped = self._block_pattern(relative).sub("", existing)
        collapsed = re.sub(r"\n{3,}", "\n\n", stripped).strip()
        write_text_atomic(path, collapsed + "\n" if collapsed else "")

    def _write_installation(
        self, plan: BootstrapPlan, *, created_files: list[str], merged_files: list[str]
    ) -> InstallationRecord:
        from raytsystem import __version__

        now = datetime.now(UTC)
        draft = InstallationRecord(
            installation_id=plan.source_map.installation_id,
            raytsystem_version=__version__,
            template_id=plan.template_id,
            template_version=plan.template_version,
            mode=plan.mode,
            source_root_paths=tuple(r.relative_path for r in plan.source_map.roots),
            config_sha256=plan.manifest_sha256,
            created_files=tuple(sorted(set(created_files))),
            merged_files=tuple(sorted(set(merged_files))),
            backup_id=None,
            record_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
        digest = sha256_hex(canonical_json_bytes(draft.content_payload()))
        record = draft.model_copy(update={"record_sha256": digest})
        raytsystem_dir = self.target / ".raytsystem"
        raytsystem_dir.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(
            raytsystem_dir / "installation.json", (record.model_dump_json(indent=2) + "\n").encode()
        )
        write_bytes_atomic(
            raytsystem_dir / "source-map.json",
            (plan.source_map.model_dump_json(indent=2) + "\n").encode(),
        )
        return record

    def _load_installation(self) -> InstallationRecord:
        path = self.target / ".raytsystem" / "installation.json"
        if not path.is_file():
            raise BootstrapError("No raytsystem installation found in this target")
        try:
            return InstallationRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise BootstrapError("installation.json is malformed") from error

    def _rebuild_index(self) -> bool:
        from raytsystem.corpus import CorpusIntegrityError
        from raytsystem.projections import ProjectionError, ProjectionService
        from raytsystem.storage import IntegrityError

        try:
            ProjectionService(self.target).rebuild()
        except (ProjectionError, CorpusIntegrityError, IntegrityError, OSError):
            return False
        return True

    def _prune_empty_dirs(self, created_files: tuple[str, ...]) -> None:
        directories = sorted(
            {
                str(Path(*Path(relative).parts[:depth]))
                for relative in created_files
                for depth in range(1, len(Path(relative).parts))
            },
            key=len,
            reverse=True,
        )
        for relative in directories:
            candidate = self.target.joinpath(*relative.split("/"))
            try:
                if candidate.is_dir() and not any(candidate.iterdir()):
                    candidate.rmdir()
            except OSError:
                continue

    # -- onboarding prompt --------------------------------------------------

    def onboarding_prompt(
        self, *, agent: str, mode: str = "managed", context_language: str = "en"
    ) -> dict[str, str]:
        if agent not in {"codex", "claude"}:
            raise BootstrapError("agent must be 'codex' or 'claude'")
        classification = self.classify()
        template_id = self._resolve_template("auto", "auto", classification)
        entry = "AGENTS.md" if agent == "codex" else "CLAUDE.md"
        prompt = _render_prompt(
            agent=agent,
            entry=entry,
            source_type=classification.primary_type.value,
            template_id=template_id,
            context_language=context_language,
            mode=mode,
        )
        return {
            "agent": agent,
            "entry": entry,
            "source_type": classification.primary_type.value,
            "suggested_template": template_id,
            "prompt": prompt,
        }

    # -- helpers ------------------------------------------------------------

    def _parse_mode(self, mode: str) -> InstallationMode:
        try:
            return InstallationMode(mode)
        except ValueError as error:
            raise BootstrapError("mode must be 'managed' or 'vendored'") from error

    def _resolve_template(
        self, template: str, source_type: str, classification: SourceClassification
    ) -> TemplateId:
        supported = {"software", "content", "research"}
        if template != "auto":
            if template not in supported:
                raise BootstrapError("Unknown raytsystem template")
            return cast(TemplateId, template)
        if source_type != "auto":
            if source_type not in supported:
                raise BootstrapError("Unknown source template")
            return cast(TemplateId, source_type)
        return _TEMPLATE_BY_SOURCE.get(classification.primary_type, "software")

    def _candidate_source_dirs(self) -> list[str]:
        names: list[str] = []
        try:
            entries = list(os.scandir(self.target))
        except OSError:
            return names
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.name in _NON_SOURCE_DIRS:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    names.append(entry.name)
            except OSError:
                continue
        return sorted(names)

    def _directory_kind(self, path: Path) -> str | None:
        code = 0
        markdown = 0
        seen = 0
        for _current, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                seen += 1
                suffix = Path(name).suffix.lower()
                if suffix in _CODE_SUFFIXES:
                    code += 1
                elif suffix in _MARKDOWN_SUFFIXES:
                    markdown += 1
                if seen >= _MAX_PROBE_PER_ROOT:
                    break
            if seen >= _MAX_PROBE_PER_ROOT:
                break
        if code:
            return "code"
        if markdown:
            return "docs"
        return None

    def _git_clean(self) -> bool | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.target), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() == ""


def _render_prompt(
    *, agent: str, entry: str, source_type: str, template_id: str, context_language: str, mode: str
) -> str:
    label = "Codex" if agent == "codex" else "Claude Code"
    context = (
        f"- SOURCE_TYPE: {source_type}\n"
        f"- DESIRED_TEMPLATE: {template_id}\n"
        f"- CONTEXT_LANGUAGE: {context_language}\n"
        f"- INSTALLATION_MODE: {mode}"
    )
    dry_run_cmd = (
        f"raytsystem bootstrap --target <TARGET_REPOSITORY_PATH> --source-type {source_type} "
        f"--template {template_id} --mode {mode} --dry-run --json"
    )
    apply_cmd = (
        f"raytsystem bootstrap --target <TARGET_REPOSITORY_PATH> --template {template_id} "
        f"--mode {mode} --apply --confirm <PLAN_FINGERPRINT> --json"
    )
    if context_language == "ru":
        return _RU_PROMPT.format(
            label=label, entry=entry, context=context, dry_run_cmd=dry_run_cmd, apply_cmd=apply_cmd
        )
    return _EN_PROMPT.format(
        label=label, entry=entry, context=context, dry_run_cmd=dry_run_cmd, apply_cmd=apply_cmd
    )


_EN_PROMPT = """\
Read the raytsystem instructions at:
<RAYTSYSTEM_SOURCE_PATH>

Target repository:
<TARGET_REPOSITORY_PATH>

Installation context ({label}):
{context}

Entry point for this environment: {entry}

Safely integrate raytsystem into this repository.

First run ONLY a read-only preflight and discovery:
  {dry_run_cmd}

Do not modify user data. Determine the workspace type, sources, existing
instructions, conflicts, sensitive data and the required adapters.

Show: (1) what was found; (2) the proposed architecture; (3) which files will be
created; (4) which files need a merge; (5) what stays unchanged; (6) risks;
(7) backup and rollback; (8) verification commands. Then stop and ask for the
plan fingerprint confirmation.

After confirmation, apply the plan with the same fingerprint:
  {apply_cmd}

Do not push, publish, send externally, or promote a real corpus without a
separate hash-bound approval.
"""

_RU_PROMPT = """\
Прочитай инструкции raytsystem по пути:
<RAYTSYSTEM_SOURCE_PATH>

Целевой repository:
<TARGET_REPOSITORY_PATH>

Контекст установки ({label}):
{context}

Точка входа для этой среды: {entry}

Нужно безопасно интегрировать raytsystem в этот repository.

Сначала выполни только read-only preflight и discovery:
  {dry_run_cmd}

Не изменяй пользовательские данные. Определи тип workspace, источники,
существующие инструкции, конфликты, чувствительные данные и необходимые adapters.

Покажи: (1) что найдено; (2) предлагаемую архитектуру; (3) какие файлы будут
созданы; (4) какие файлы требуют merge; (5) что останется неизменным; (6) risks;
(7) backup и rollback; (8) команды проверки. Затем остановись и запроси
подтверждение fingerprint из плана.

После подтверждения примени план тем же fingerprint:
  {apply_cmd}

Не выполняй push, publish, external send или real-corpus promotion без отдельного
hash-bound approval.
"""
