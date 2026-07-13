# ADR-024 — Emergency controls and circuit breakers

Status: implemented behind feature flags
Date: 2026-07-12

## Context

An agentic platform needs a stop switch that is easier to activate than to lift, plus automatic
tripwires for runaway or hostile behavior. Stopping must not require an approval; resuming must.
Concurrent hardening work is landing in `raytsystem.emergency`; this ADR documents the module
together with those in-flight fixes: per-action gates, bounded breaker auto-recovery and
revocation semantics for runtime sessions.

## Decision

- Keep one global emergency record (`emergency_global`) in the isolated `ops/platform.sqlite`
  store with optimistic revisions, hash-chained events and idempotent activate/recover receipts,
  so retries are safe and concurrent writers conflict loudly instead of losing actions.
- Activation needs only local emergency authority and a reason; it unions the requested typed
  `EmergencyAction`s into the active set and preserves per-action activation timestamps. Recovery
  requires a fresh exact approval (`recover_emergency`, scope `emergency_recovery`) hash-bound to
  the recovered actions and reason, and can lift a subset while the rest stays active.
- Enforce per-action gates at the call sites that matter: runtime start, network adapters,
  provider egress, task checkout and runtime session grants each consult the emergency state. If
  the store exists but is unreadable, every gate fails closed. `revoke_runtime_sessions` blocks new
  session grants; concurrent hardening extends the same revocation semantics to stored approvals.
- Drive circuit breakers from the thresholds in `config/platform.yaml`. Security triggers
  (`protected_path`, `forbidden_egress`, `policy_violations`, `failed_approvals`) get zero
  automatic recovery; other triggers get exactly one bounded HALF_OPEN probe before staying open.
  Opening a breaker automatically activates `disable_runtime_execution`.
- Require a fresh approval (`close_circuit_breaker`, scope `emergency_recovery`) to close a
  breaker manually, and refuse runtime recovery while any security breaker is open.
- Fail closed: every operation raises the emergency subsystem error when
  `emergency_controls_enabled` is off; read snapshots degrade to an `unavailable` state that the
  runtime gate treats as blocking.

## Consequences

- Stopping is one local command; resuming demands recorded human authority, so the asymmetry
  favors safety.
- Automatic recovery cannot flap: one probe per open, and security trips never self-heal.
- Workflow and runtime subsystems inherit the stop switch by calling the gates rather than
  re-reading raw records.
- Scoped (per-employee or per-adapter) emergency states and time-boxed auto-expiry remain
  deferred; the current state is global by design.

## Alternatives considered

- Approval-gated activation: rejected; an emergency stop must never wait on an approver.
- Unbounded half-open retries: rejected because a flapping breaker is a disabled breaker.
- Storing emergency state in process memory or config files: rejected; it must survive crashes
  and be readable by every gate.
