# ADR-029 — Typed workflow DAG engine

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Multi-step processes need durable orchestration with retries, timeouts and human approval gates.
An n8n-style engine with arbitrary node code would reopen the shell/RCE surface ADR-019 closed,
and in-memory orchestration would lose state on crash. Concurrent hardening work is landing in
`raytsystem.workflows`; this ADR documents the module together with those in-flight fixes: retries,
timeouts, approval gates, crash recovery and notification producers for gated transitions.

## Decision

- Register workflows as immutable, manifest-hash-bound revisions of typed nodes and edges. DAG
  validation runs Kahn's topological sort and rejects duplicate edges and cycles before anything
  is stored.
- Allow deterministic command nodes to reference only engine-registered built-in operations;
  constructor-supplied callables are forbidden and operation IDs containing shell metacharacters
  are rejected. Non-deterministic node types pause for an operator instead of executing.
- Persist every run and step as versioned records with hash-chained events in the isolated
  `ops/platform.sqlite` store, using derived idempotency keys for run start and per-step
  execution. Because all state lives in the store with optimistic revisions, `run_ready_steps`
  recomputes readiness from records after a crash and resumes exactly where the last committed
  step left off.
- Enforce per-node timeouts and bounded retries with typed retry policies (linear/exponential
  backoff, recorded delay and attempt), and expire waiting steps deterministically.
- Gate approval nodes on registered immutable `WorkflowApprovalGate`s: granting requires a
  resolved approval bound to a derived run/node target, the run's input hash and the gate's
  required role scope, and gates expire after their configured window; denial fails the run.
  Manual `wake` drives wait nodes, with the same timeout discipline.
- Check `EmergencyService.assert_runtime_allowed()` before starting, stepping or approving, so the
  global stop switch halts orchestration too. Inputs and outputs are canonical-JSON bounded and
  secret-scanned.
- Emit gated transitions through the notification outbox: producers added by the concurrent
  hardening work publish workflow state changes with a policy destination allowlist, while
  `external_notifications_enabled` stays default-off so nothing leaves the machine.
- Fail closed: every operation raises the workflow subsystem error when
  `workflow_engine_enabled` is off.

## Consequences

- A workflow cannot smuggle execution: the only runnable node bodies are engine-shipped pure
  functions, and everything else waits for a human or a future governed runtime.
- Crash recovery is a replay of durable records, not a reconciliation heuristic.
- A real n8n adapter/import, agent-node execution through runtime adapters and scheduled triggers
  are intentionally deferred to separate reviewed milestones.

## Alternatives considered

- Embedding n8n or executing user-supplied node code: rejected as an arbitrary-code surface.
- In-memory orchestration with periodic checkpoints: rejected; every transition must be durable.
- Approval gates as boolean flags on the run: rejected; approvals must resolve to recorded,
  hash-bound authority.
