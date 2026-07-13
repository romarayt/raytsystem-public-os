# ADR-023 — Policy simulation against the real execution policy

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Operators need to answer "what would this execution be allowed to do?" before granting anything. A
simulator with its own rule copy would drift from the runtime gate and produce false confidence.
Concurrent hardening work is landing in `raytsystem.policy_simulator`; this ADR documents the module
together with those in-flight fixes: the simulator delegates to the real execution policy engine
and echoes the full plan facts into its decision.

## Decision

- Bind every `ExecutionPlan` to `policy_sha256`, the hash of `config/policies.yaml` plus
  `config/platform.yaml`. A plan built against a stale policy is rejected before evaluation.
- Map the dry-run plan onto the exact `ExecutionPolicyRequest` the runtime preflight evaluates and
  run the real `evaluate_execution_policy`. Unresolvable dry-run facts fail closed: sensitivity is
  pinned to RESTRICTED, so simulation always assumes the widest provider-egress approval scope.
- Merge active emergency actions read from the isolated `ops/platform.sqlite` store. If the store
  exists but cannot be read, the simulation carries `emergency_state_unavailable` and blocks.
- Evaluate the shared pure engine (`raytsystem.policy_simulator.engine.evaluate_execution`) over the
  merged facts: protected roots (`_raw`, `ledger/*`, `knowledge/*`), workspace write modes,
  staging-only bounds, network/provider/secret approval kinds, side-effect approval kinds
  (send/publish/upload/delete/pay/git_push/pull_request), read-only tool allowlist and MCP feature
  gates. Secrets require `restricted_encryption_enabled`; MCP tools require governance and
  external-execution flags.
- Echo the full plan into the `PolicySimulation`: roots, budgets, scopes, allowed and blocked
  tools, requested secrets, side effects, missing approvals, sorted reason codes and an outcome of
  `allowed`, `approval_required` or `blocked`. Every simulation is `dry_run` and grants nothing.
- Make the runtime gate the same code path: `authorize_execution` refuses caller-claimed approval
  kinds and calls the identical pure policy, so simulation and enforcement cannot diverge.
- Fail closed: simulation raises the simulator subsystem error when `policy_simulator_enabled` is
  off or the execution configuration is unavailable.

## Consequences

- A simulated `allowed` means the runtime preflight would also allow it under the same policy
  bytes; there is no second rule set to drift.
- Simulations are safe to expose to operators and agents because they are pure reads plus a
  read-only emergency lookup; nothing is persisted or granted.
- Resolving real task sensitivity, live budget usage and resolved approval records during dry runs
  is intentionally deferred; the simulator over-approximates required authority instead.

## Alternatives considered

- A standalone simulator rule table: rejected because divergence from the runtime gate is the
  exact failure this subsystem exists to prevent.
- Letting callers pass "granted" approval kinds into runtime authorization: rejected; only
  resolved approval records may satisfy the gate.
- Skipping emergency state during simulation: rejected; an operator would see `allowed` while the
  runtime is stopped.
