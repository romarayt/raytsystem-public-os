# ADR-033 — Installation architecture and the bootstrap installer

Status: partially-implemented (read-only engine core)
Date: 2026-07-12

## Context

raytsystem must become publicly distributable and installable into a user's *existing* repository —
Obsidian vault, plain Markdown, a Graphify-like export, a software repo, or a mixed workspace —
without destroying, copying, or reinterpreting the user's data. The existing `raytsystem init`
(ADR-032, `TemplateService`) scaffolds a fixed template file set in place and is non-destructive
only by *refusal*: any pre-existing file that differs becomes a conflict and aborts the whole run.
It has no notion of a foreign target, no source discovery, no backup-before-apply, no plan-hash
confirmation binding, and no uninstall/rollback. A safe installer needs all of these while reusing —
not reinventing — the mature hash-bound primitives already in the codebase.

## Decision

### Conceptual separation

- **Engine** — the versioned open-source `raytsystem` package (pinned dependency or vendored copy).
- **Workspace** — per-repo configuration the installer creates (`AGENTS.md`, `CLAUDE.md`, `WORK.md`,
  `config/`, `.raytsystem/`).
- **Source Repository** — the user's data. Owner of truth; never copied or overwritten.
- **Managed State** — append-only raytsystem state (`ledger/`, `_raw/`, `ops/`), guard-protected.
- **Derived Projections** — rebuildable indexes/graphs under `.raytsystem/` (gitignored).

Two modes: **managed** (default; engine external, workspace-only) and **vendored** (engine copied
into a dedicated in-repo directory; explicit opt-in). No mandatory symlinks; cross-platform
(Windows/macOS/Linux) via structurally-relative paths.

### `bootstrap` composes existing primitives

`bootstrap` is a **new** root CLI command (`--target` names a foreign repo), leaving `init`/
`migrate`/`upgrade` untouched. Its phases map to existing services:

| Phase | Reuse |
|---|---|
| preflight → discovery → classification → mapping → plan (read-only) | `RootClassifier`, `CheckpointGuard.classify_path`, `TemplateService.plan` (`init_plan_id` + `manifest_sha256` fingerprint) |
| backup | `BackupService.create(PRIVATE)` (the pattern `upgrade` already uses) |
| apply | `TemplateService.initialize` + merge strategies + `--confirm FINGERPRINT` binding modeled on `MigrationService._verify_plan` |
| import proposals | `IngestPipeline.ingest(..., prepare_only=True)` — staging only, default-deny promotion |
| validation → index → graph → UI smoke | `doctor`, `CatalogService.load`, `ProjectionService.rebuild`, `CodeGraphProjection.rebuild`, `SnapshotProvider` |

Because `TemplateService` writes files but does not create the `ledger/generations` state the read
model/UI require, the apply path must additionally drive a genesis generation before
`rebuild-index`/`graph rebuild`/snapshot check. That post-init sequence is named explicitly in the
plan (`post_init_steps`).

### Contracts and safety invariants

- New installer contracts (`InstallationRecord`, `SourceMap`, `SourceRoot`, `SourceClassification`,
  `PreflightReport`, `BootstrapPlan`) are `VersionedModel` subclasses. Every path field is a
  `RelativePath`, which **structurally** rejects absolute paths on every OS — this, not a runtime
  scrub, is what keeps host absolute paths out of persisted and exported installer JSON. The plan's
  only human-facing name is the target **basename**.
- These contracts are deliberately **not** in the frozen public `SCHEMA_MODELS` registry (`v1.4.0`
  binds existing generations); folding them in belongs to a deliberate schema-version bump.
- Source repository is read-only during discovery; `_raw/`, `ledger/`, generations and generated
  knowledge are never edited; imported content is inert data; real-corpus promotion stays default
  deny; uninstall removes only installer-created/merged paths and never user data; rollback restores
  the pre-bootstrap backup; re-runs are idempotent.

### This build (read-only engine core)

Shipped: `src/raytsystem/contracts/installation.py`, `src/raytsystem/bootstrap/` (`classify.py`,
`service.py`, `cli.py`), CLI `raytsystem bootstrap --target … --dry-run` and `raytsystem onboarding
prompt --agent {codex,claude}`. The `--apply` write path is **gated** (exits non-zero with a clear
message) pending the next phase (backup + merge + fingerprint-bound write + uninstall/rollback).

## Consequences

- A user (or an assistant driving the CLI) can preview a complete, deterministic, fingerprinted
  integration plan that provably writes nothing to the target.
- The installer inherits the existing hash-binding, conflict-refusal and redaction guarantees rather
  than duplicating them.
- Follow-up work: apply/uninstall/rollback with backup wiring; source adapters (Obsidian canvas,
  embeds, Graphify import mapping contract, per-root sensitivity); personal context profile; web
  onboarding wizard; the public knowledge-base install section; and the synthetic fixture matrix.
- Supersedes nothing; extends ADR-032 (workspace templates and migrations) and depends on ADR-031
  (backup/export/restore).
