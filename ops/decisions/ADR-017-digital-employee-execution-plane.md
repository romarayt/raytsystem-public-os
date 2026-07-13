# ADR-017 — Digital-employee execution plane

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Agent definitions in the catalog are inert configuration. raytsystem needs an operational employee
identity, assignment, session, run, budget and approval model without replacing its append-only
Task Ledger or allowing a runtime to write canonical knowledge.

Paperclip was reviewed as a behavioral reference at public commit
`e4e12bfb890a0fdf4c7de092362472c50a584533` (2026-07-11). raytsystem adapts durable run state,
workspace/session binding, explicit budgets, comments and adapter diagnostics. It does not import
Paperclip's Postgres task model, automatic instruction inheritance, broad company visibility,
telemetry defaults or local permission-bypass defaults.

## Decision

- Project versioned `AgentDefinition` records into typed `DigitalEmployee` records. Hierarchy is
  optional; no employee is implicitly promoted to CEO.
- Keep `ops/task-ledger/` as the only task journal. Store assignments, workspaces, graph scopes,
  leases, sessions, execution runs, budgets, approvals, comments and transcripts as linked
  operational records in the existing local control database.
- Bind every invocation to exact task generation/revision, employee configuration revision,
  adapter, workspace manifest, graph snapshot, context hash, policy decision and idempotency key.
- Treat runtime output as untrusted evidence. It may update operational run/review state and create
  draft artifacts, but it cannot write `_raw/`, `ledger/`, generated knowledge or promote content.
- Use comments/progress events and child tasks as the auditable coordination channel. Hidden
  inter-agent messages are not part of the design.
- Enforce per-employee concurrency, task leases, budgets, approvals and circuit-breaker limits
  before execution. Manual heartbeat is the first enabled trigger; scheduled heartbeats remain off.
- Default all real runtime and provider flags to false. Catalog activation alone grants no runtime
  authority.

## Consequences

- raytsystem remains the only control plane and Task Ledger remains the only task history.
- Operational state can be rebuilt or migrated without changing canonical knowledge generations.
- An employee's visible state may be disabled even when its agent definition exists, because every
  required feature, adapter and policy gate must also be open.
- Long-running autonomy, automatic publishing/deploying and agent-created authority are explicitly
  outside this delivery.

## Alternatives considered

- Running Paperclip beside raytsystem: rejected because it duplicates tasks, policy and mutable state.
- Mutating immutable task objects with live runtime fields: rejected because it breaks history and
  backward compatibility.
- Treating chat memory as employee state: rejected because it is not durable or auditable.

