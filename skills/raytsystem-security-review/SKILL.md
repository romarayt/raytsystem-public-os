---
name: raytsystem-security-review
description: Audit raytsystem changes for prompt injection, provenance bypass, path/symlink/hardlink escape, secret leakage, stale fencing, partial promotion, unsafe parsing, and unapproved side effects. Use for SECURITY REVIEW, adversarial testing, recovery review, or approval-boundary validation; remain independent and read-only.
---

# raytsystem Security Review

## Inputs and outputs

- Accept one bounded change/run plus threat model and declared permissions.
- Return confirmed or unproven Critical/High findings with repro, violated invariant, and minimal regression test/fix.

## Write scope

- Keep review read-only; run only non-mutating diagnostics or isolated synthetic tests.
- Never read secrets without scoped necessity, disclose values, promote, publish, or modify external systems.

## Preflight

1. Run `uv run raytsystem agent preflight --skill raytsystem-security-review --write --json` locally.
2. Run `agent subagent-check` before delegation; hosted review receives only safe excerpts.
3. Snapshot declared canonical/external state and identify every write/egress boundary.

## Workflow

1. Trace untrusted input through Fetcher/Extractor/proposal/validation/promotion/query/save paths.
2. Test raw/hash/citation closure, generation races, lease fencing, WAL/pointer crash windows, and idempotency.
3. Test SQL/FTS injection, resource limits, archive/parser containment, symlink/hardlink/no-follow paths, and secret redaction.
4. Verify zero unapproved process/network/outbox/Git/external action.

## Validation

- Require a regression test for every confirmed Critical/High issue.
- Re-run scoped and full gates after fixes; never accept skipped required tests.
- Exercise evals `m3-security-review-golden` and `m3-security-review-adversarial`.

## Recovery

- Preserve failed staging and machine-readable reports; retry only classified transient failures.
- On quota/tool loss, list exact reviewed surfaces and remaining adversarial cases.

## Stop and approval conditions

- Stop on secret/PII exposure, unsafe external destination, real promotion, destructive action, or missing authority.
- Do not downgrade a confirmed issue because exploitation is inconvenient; fail closed and request the narrow required approval.
