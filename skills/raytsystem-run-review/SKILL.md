---
name: raytsystem-run-review
description: Independently review an raytsystem run, diff, contract, test result, or milestone checkpoint and return structured findings. Use for REVIEW, architecture/contracts/data-integrity/test critique, gate verification, or pre-promotion review; remain read-only and separate from the writer context.
---

# raytsystem Run Review

## Inputs and outputs

- Accept one bounded target, exact file/source references, rubric, and stop condition.
- Return `PASS` or sorted Critical/High/Medium findings with file:line, impact, evidence, and minimal fix.

## Write scope

- Keep the reviewer read-only.
- Never edit files, acquire writer leases, promote, create Git refs, or perform external actions.
- Let the main agent resolve contradictions and own all writes.

## Preflight

1. Run `uv run raytsystem agent preflight --skill raytsystem-run-review --write --json` locally.
2. Run `agent subagent-check` before sending any excerpt to a reviewer surface.
3. Supply only the minimal non-sensitive target; do not leak the intended answer or suspected fix.

## Workflow

1. Inspect target contracts, implementation, tests, and declared gate evidence.
2. Reproduce suspected failures read-only when safe.
3. Rank findings by concrete impact; omit style-only commentary unless requested.
4. Return summaries with source references, not raw logs.

## Validation

- Verify source-of-truth boundaries, generation binding, idempotency, recovery, skipped gates, and docs/code agreement.
- Require evidence for every finding and distinguish untested risk from confirmed failure.
- Exercise evals `m3-review-golden` and `m3-review-adversarial`.

## Recovery

- If quota/tool access ends, return reviewed scope, unresolved files/questions, and exact next read-only check.
- Do not call partial review success.

## Stop and approval conditions

- Stop before writes, private hosted transfer, secrets, promotion, external mutation, or scope expansion.
- Report `unavailable` when an independent surface cannot safely receive inputs; let the main agent continue sequentially.
