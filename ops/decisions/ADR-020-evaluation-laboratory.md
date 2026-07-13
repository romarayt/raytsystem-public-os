# ADR-020 — Deterministic evaluation laboratory

Status: implemented behind feature flags
Date: 2026-07-12

## Context

raytsystem needs regression-grade evaluation of agent behavior without letting an eval definition
become a code-execution or egress channel, and without letting eval volume churn canonical
knowledge generations. Promptfoo was reviewed as a config format; embedding its runner would import
custom JS/Python assertions, remote generation and telemetry defaults that violate the raytsystem
trust boundary. Concurrent hardening work is landing in `raytsystem.evals`; this ADR documents the
module together with those in-flight fixes.

## Decision

- Score typed `EvalObservation` inputs against a closed set of deterministic assertions: exact
  match, contains, bounded non-backtracking regex, a JSON Schema subset, workspace-bounded file
  existence/hash, artifact type, test result, command exit status, citation/source-location
  existence, task transition, approval compliance, forbidden-action absence, budget bounds,
  secret-leak scan and protected-path non-modification (`_raw/`, `ledger/`, `knowledge/`).
- Reject any assertion configuration that names code or providers (`javascript`, `python`,
  `script`, `command`, `exec`, `shell`, `provider`, `remote`). The eval service never executes
  target code; it only judges evidence handed to it.
- Derive `eval_run` identity from suite hash, case, target and observation hash so re-running the
  same observation is idempotent and identity collisions are detected, not overwritten.
- Persist runs, results, baselines, comparisons and findings as versioned records in the isolated
  `ops/platform.sqlite` store with hash-chained audit events. Canonical knowledge, `_raw/` and
  `ledger/` are never written.
- Require an exact resolved approval (`accept_eval_baseline`, scope `eval_baseline`, bound to the
  aggregate result hash) before a run becomes a baseline. Comparison re-verifies baseline identity,
  aggregate hash and every stored result hash before reporting added/resolved failures; rejecting a
  regression records a high-severity finding with actor and reason.
- Keep the Promptfoo adapter validation-only behind `promptfoo_adapter_enabled` (default off). It
  rejects custom code assertions, executable providers and `file://` code references, requires
  trusted input and an approved provider-destination allowlist, and refuses remote
  generation/sharing/telemetry unless `promptfoo_remote_generation_enabled` is set (default off).
- Fail closed: every entry point raises the eval subsystem error when `evals_enabled` is off.

## Consequences

- Evals are reproducible evidence, not an execution plane: a hostile suite cannot run code, reach
  the network or read outside the workspace root.
- Baselines and regression verdicts are forgery-evident because every link is hash-verified.
- Runtime eval execution (actually driving an employee run to produce the observation) is
  intentionally deferred to the execution plane; Promptfoo execution and remote generation remain
  deferred and default-off.

## Alternatives considered

- Embedding the Promptfoo runner: rejected because its assertion model includes arbitrary code.
- LLM-judge scores as gating signals: rejected for this milestone; only deterministic scores gate.
- Storing eval output in the knowledge ledger: rejected because eval volume would churn
  generations and recovery coupling.
