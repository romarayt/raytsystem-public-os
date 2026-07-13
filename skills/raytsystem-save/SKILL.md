---
name: raytsystem-save
description: Stage a cited synthesis as a typed raytsystem DRAFT bundle and escaped preview. Use for SAVE, preserving a verified query answer, preparing a knowledge proposal, or creating a reviewable draft; never treat SAVE as canonical promotion or publication.
---

# raytsystem SAVE

## Inputs and outputs

- Accept bounded synthesis text, safe title, and one or more verified active evidence segment IDs.
- Return one idempotent `SaveResult`, typed staging bundle, and escaped DRAFT preview.

## Write scope

- Write only `ops/staging/<run_id>/`, `ops/runs/<run_id>/`, coordination state, and `artifacts/drafts/`.
- Never write `ledger/`, events, generated knowledge, Git refs, outbox, or external systems.

## Preflight

1. Run `uv run raytsystem agent preflight --skill raytsystem-save --write --json`.
2. Obtain evidence IDs from a verified generation-bound QUERY result.
3. Reject secret text, unsafe title/path, stale generation, linked output/control paths, or uncited synthesis.

## Workflow

1. Run `uv run raytsystem save "SYNTHESIS" --title "TITLE" --evidence SEGMENT_ID --json`.
2. Inspect `bundle.json`, typed proposal artifacts, and preview hashes.
3. Leave the result in DRAFT/awaiting-review state.

## Validation

- Bind the operation key to generation, text/title hashes, evidence, schemas, component, and config.
- Verify a repeated/concurrent identical SAVE returns the same artifact and no canonical mutation.
- Exercise evals `m3-save-golden` and `m3-save-adversarial`.

## Recovery

- Re-run the exact SAVE; join the existing operation and verify its hash-closed bundle.
- Keep incomplete staging for diagnosis and fail closed on generation change.

## Stop and approval conditions

- Stop before promotion, send, publish, upload, outbox dispatch, push, PR, or release.
- Require a later explicit reviewed promotion design; M2/M3 SAVE cannot promote by construction.
