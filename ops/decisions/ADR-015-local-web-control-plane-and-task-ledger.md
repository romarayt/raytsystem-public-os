# ADR-015 — Local web control plane and separate task ledger

Status: accepted
Date: 2026-07-11

## Context

M0–M5a qualified a CLI/Markdown local-first kernel, but the universal raytsystem product requires a
human control plane for work, agents, skills, context, runs and knowledge. Putting task activity in
the knowledge ledger would create incorrect invalidation and recovery coupling. Exposing the CLI
directly through HTTP would introduce path, shell, CSRF and implicit-write hazards.

## Decision

- Add a loopback-only typed web application layer; do not wrap CLI argument parsing in HTTP.
- Pin one workspace root and accept typed IDs, never client-provided roots or filesystem paths.
- Separate read snapshots from command services. GET/HEAD never rebuild projections or write files.
- Store tasks in a separate append-only content-addressed ledger with its own atomic `CURRENT`
  pointer; never change `ledger/CURRENT` for task progress.
- Treat agents, skills, packs and instruction documents as inert catalog data during discovery.
- Build the visual Universe as a disposable generation-bound projection across knowledge, work and
  capability planes.
- Add runtime adapter contracts, but keep execution disabled and omit a generic shell endpoint from
  this milestone.
- Serve a self-contained React bundle from the same process with strict browser security and no
  remote assets, analytics or CORS wildcard.
- License the new universal core under Apache-2.0; reference products are behavioral/design studies
  only.

## Consequences

- The UI can be impressive without becoming a second source of truth.
- Task volume and telemetry do not churn canonical knowledge generations.
- Future runtimes can plug into a stable contract without granting accidental host authority.
- Remote/multi-user operation, uploads, schedules and external execution remain separate reviewed
  milestones.
- The web milestone is independently revertible; historical schemas and M0–M5a evidence remain
  immutable.
