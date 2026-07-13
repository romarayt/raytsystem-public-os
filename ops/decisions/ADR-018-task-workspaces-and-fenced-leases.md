# ADR-018 — Managed task workspaces and fenced leases

Status: implemented
Date: 2026-07-12

## Context

CLI agents require a working directory, but accepting a path from a task or browser would expose
the host. Concurrent or crashed workers also need stronger ownership than a mutable checkout flag.

## Decision

- raytsystem derives every workspace identity and creates it only below
  `.raytsystem/workspaces/<workspace-id>/`. HTTP never accepts a `cwd` or filesystem path.
- The production default is a detached Git worktree at an exact commit. Context, artifacts and logs
  are siblings inside the managed workspace; no cleanup or deletion is automatic.
- The immutable workspace manifest binds task generation/revision, catalog and employee revision,
  Git commit, graph snapshot/fingerprint, graph scope and context hash. Reusing an ID requires exact
  byte equality; drift fails closed.
- Path creation rejects symlink components. Immutable reads reject symlinks and hardlinks; Git
  metadata is checked immediately before use. External roots require a separate typed approval and
  are not enabled in this delivery.
- Checkout uses the existing `ControlDB` lease table with TTL, renewal, control epoch and monotonic
  fencing token. The typed task lease also binds employee, run, task generation and revision.
- One task partition can have one live owner. Retry by the same run is idempotent; a released or
  expired lease gives the next owner a higher fence. Stale fences cannot commit operational state.
- Heartbeats renew leases while work is live. Pause/cancel/recovery release or revoke them
  explicitly; they never delete a workspace.

## Consequences

- Browser input cannot redirect a runtime to another directory.
- A process that outlives its lease may still exist briefly, but its stale fence cannot authorize
  persistence; cancellation terminates the isolated process group.
- Worktrees consume disk until an explicit future retention policy is approved.
- A task/status transition creates a new immutable binding and therefore may require a fresh
  workspace/context snapshot.

## Alternatives considered

- Reusing the repository root for writes: rejected because workers would collide with user edits.
- A lock without TTL/fencing: rejected because crash recovery could admit stale writers.
- Automatic workspace deletion: rejected because retention and user-data authority are undecided.

