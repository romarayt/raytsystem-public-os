# ADR-032 — Workspace templates and journaled migrations

Status: implemented behind explicit confirmation gates
Date: 2026-07-12

## Context

New workspaces need a safe starting shape, and existing workspaces need schema upgrades that
cannot brick them. Template initialization that overwrites files, or a migration that mutates
configuration without a verified backup, would both be one-way doors. Concurrent hardening work is
landing in `raytsystem.templates` and `raytsystem.migrations`; this ADR documents the modules together
with those in-flight fixes, including the journaled migration registry.

## Decision

- Ship three typed workspace templates (software, content and research) as deterministic,
  manifest-hash-bound file sets: starter pack, disabled-by-default agents, workflow and task
  samples, `config/raytsystem.toml`, `config/policies.yaml` and a generated `config/platform.yaml`
  whose external and exposure flags are all off.
- Plan before writing: the init plan lists files to create, detects byte-identical files as
  no-ops, reports conflicts, and flags existing repositories. Initialization refuses conflicts
  outright, requires explicit confirmation inside an existing repository, and treats a target that
  appeared after planning as a race error. All writes are atomic.
- Parse the workspace schema version from `config/raytsystem.toml` and plan migrations only toward
  this build's `SCHEMA_VERSION`, within the same major version, refusing workspaces newer than the
  build. Plans are hash- and ID-verified so a forged or stale plan cannot apply.
- Apply only with `confirm=True` and a verified backup: the referenced backup must exist in the
  isolated `ops/platform.sqlite` store in state `created` (ADR-031). The configuration is
  rewritten atomically and restored byte-for-byte if journaling fails.
- Journal every application as a registry: a `migration` record keyed by migration ID with the
  before/after hashes bound into `migration_sha256`, plus a hash-chained event on the
  `workspace_migrations` stream. Re-applying an applied migration idempotently returns the
  existing record and cross-checks it against the live configuration. The platform store itself
  keeps a separate `migration_journal` for its own schema versioning.
- Fail closed on ambiguity: missing schema versions, stale plans, cross-major targets, unbacked
  applications and applied-record/configuration disagreements are all hard errors.

## Consequences

- `raytsystem init` cannot destroy work: the worst case is a refused plan listing conflicts.
- Every schema change is reconstructible from the journal — what ran, from and to which version,
  against which backup, with which hashes.
- The retired YouTube production vertical is absent from runtime and catalog. Published
  `v1.0.0`–`v1.4.0` registries remain byte-identical compatibility artifacts; legacy contract
  readers stay available so old ledger and backup identities remain verifiable.
- Cross-major migrations and content-transforming migration steps are intentionally deferred; the
  current registry contains only the version-stamp migration, and wider transformations require a
  separate compatibility release.

## Alternatives considered

- Overwrite-with-backup initialization: rejected; refusal plus explicit confirmation is simpler
  and reversible.
- Auto-migrating on startup: rejected; migration is a confirmed, backup-gated operator action.
- Recording migrations only in the config file: rejected; the journaled registry survives config
  rewrites and supports idempotent re-application.
