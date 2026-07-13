# ADR-022 — Replay, fork and run comparison

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Debugging agent behavior requires re-running a past execution with pinned inputs, but a naive
replay would repeat side effects (send/publish/pay) and inherit stale approvals. Comparison of two
runs must state where each number came from instead of fabricating deltas. Concurrent hardening
work is landing in `raytsystem.replay`; this ADR documents the module together with those in-flight
fixes, including fully populated comparison dimensions.

## Decision

- Persist immutable, hash-bound `ExecutionRecord`s keyed by run ID in the isolated
  `ops/platform.sqlite` store with hash-chained events; a second write with different bytes is an
  integrity error, never an update. Canonical knowledge is never written.
- Record side-effect results as separate immutable records bound to the original run and side
  effect. A replay plan may substitute only verified recorded results; every original side effect
  without a recorded result is listed as blocked.
- Never transfer approvals. Plans carry per-position derived approval placeholders, so original
  approval IDs cannot enter a plan, and staging marks the plan `approval_required` whenever the
  original run held approvals.
- Restrict forks to an allowlisted field set (`runtime_id`, `model`, instruction/skill hashes,
  `toolset_sha256`, budgets, `graph_snapshot_id`) intersected with the contract, and re-validate
  the modified record. Differences are recorded per field with original and modified values.
- Verify plan integrity on every use: plan hash, derived plan ID, expected blocked set, expected
  approval placeholders and recorded-result bindings must all match, or the plan is rejected as an
  attempted side-effect or approval bypass.
- Materialize a new execution record only from an immutable `ready` staged plan with no blocked
  effects or pending approvals. The origin stamp replaces the original extensions so stale eval or
  approval linkage never carries into the new run.
- Populate comparisons dimension by dimension with explicit provenance: token/cost/latency deltas
  from traces (budget fallback where recorded), tool-call and failure changes from trace spans,
  file changes from record extensions, eval score/assertion/artifact changes from linked eval
  results, and approval/result changes from the records. Unavailable dimensions are named in
  `unavailable_dimensions`; `test_changes` is always explicit-unavailable because observation-level
  test results are never persisted.
- Fail closed: every entry point raises the replay subsystem error when `replay_enabled` is off.

## Consequences

- A replay can never silently re-send, re-publish or re-pay: side effects are replayed from
  recorded evidence or blocked, and fresh approvals are mandatory.
- Comparisons are honest; a missing trace or eval linkage shows up as an unavailable dimension
  instead of a zero delta.
- Executing the materialized record is intentionally deferred to the runtime adapter plane; this
  subsystem plans, stages and audits but does not run.

## Alternatives considered

- Mutating the original run with replay state: rejected because it destroys history.
- Carrying original approvals into replays "for convenience": rejected; authority is not portable
  across runs.
- Free-form fork patches: rejected because unvalidated field changes would forge execution
  provenance.
