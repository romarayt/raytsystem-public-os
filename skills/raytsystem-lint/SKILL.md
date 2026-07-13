---
name: raytsystem-lint
description: Run deterministic integrity, provenance, projection, link, alias, operation, and secret checks over raytsystem. Use for LINT, health checks, pre-commit verification, stale projection diagnosis, broken evidence, or semantic review; never auto-fix canonical knowledge.
---

# raytsystem LINT

## Inputs and outputs

- Accept deterministic mode by default or explicit semantic-review mode.
- Return a sorted machine-readable `LintReport` with stable codes and a report hash.

## Write scope

- Keep LINT read-only.
- Permit a separate explicit `rebuild-index` only for derived artifacts.
- Never edit canonical records, generated pages, events, run history, or manual notes.

## Preflight

1. Run `uv run raytsystem agent preflight --skill raytsystem-lint --write --json`.
2. Record the active generation and Git dirty state.
3. Read all inspected files through no-follow, hardlink-safe paths.

## Workflow

1. Run `uv run raytsystem lint --json`.
2. Use `uv run raytsystem lint --semantic --json` only to create review findings, never fixes.
3. Group findings by stable code and point to workspace-relative subjects only.

## Validation

- Check raw hashes, citation closure, schema/canonical bytes, projection marker/files/index, local links, IDs/aliases/slugs, secrets, and operation uniqueness.
- Require nonzero exit for any critical/high/error finding.
- Exercise evals `m3-lint-golden` and `m3-lint-adversarial`.

## Recovery

- Repair code or rebuild derived projections through their owning command, then re-run LINT.
- Preserve corrupted inputs and failed staging for diagnosis; never hide a finding by deletion.

## Stop and approval conditions

- Stop before canonical repair, destructive migration, secret disclosure, or external action.
- Escalate a semantic conflict as a proposal/human review, not an automatic write.
