# ADR-021 — OTel-aligned local trace model

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Runs, tool calls and model calls need queryable spans with token/cost accounting, but a default
OTLP network exporter would create a standing egress channel and prompts/outputs inside span
attributes would leak restricted data into observability storage. Concurrent hardening work is
landing in `raytsystem.telemetry`; this ADR documents the module together with those in-flight fixes,
including hierarchy validation and local-file OTLP export behind approval.

## Decision

- Store `TraceRecord` and `TraceSpan` as versioned records in the isolated `ops/platform.sqlite`
  store with hash-chained audit events per trace stream. Canonical knowledge is never written.
- Support deterministic trace/span identity: callers that supply the derivation inputs get
  hash-derived IDs verified on write, so replays produce the same trace identifiers.
- Validate hierarchy on every write: a root span must belong to the trace root run and match the
  trace's declared root span, non-root span kinds require a parent, and a parent must exist in the
  same trace. Identity fields are immutable and terminal spans can never be reopened or rewritten.
- Sanitize before persistence: policy-bounded attribute count and bytes (`max_span_attributes`,
  `max_span_attribute_bytes`), digest replacement for sensitive attribute keys and scanner hits,
  redaction of provider/model/tool/error fields, and a whole-span secret scan that fails closed.
- Close a trace by aggregating span counts, token usage and estimated cost with optimistic
  revision locking, so summaries are derived from stored spans rather than caller claims.
- Keep OTLP export local-file only and doubly gated: `telemetry_enabled` plus
  `otel_export_enabled` (default off), then an exact resolved approval (`export_traces`, scope
  `otel_export`) bound to the hash of every exported trace/span and the destination path. The
  export writes one canonical JSON OTLP document to a non-existing, non-symlink path, secret-scans
  the rendered document and records the export event. No network exporter exists.
- Fail closed: mutations raise the telemetry subsystem error when `telemetry_enabled` is off;
  read snapshots degrade to a `disabled`/`unavailable` state instead of guessing.

## Consequences

- Traces are trustworthy evidence: hierarchy, redaction status and terminality are enforced at the
  storage boundary, not by caller discipline.
- Cost/token roll-ups cannot drift from the underlying spans.
- Sending traces to a collector requires a deliberate flag change plus a fresh hash-bound approval
  per export; continuous OTLP push, remote collectors and sampling policies remain deferred.

## Alternatives considered

- Wiring the OpenTelemetry SDK with a default OTLP/gRPC exporter: rejected as a standing egress
  channel with library-controlled buffering.
- Storing raw prompts/outputs in span attributes for debuggability: rejected; digests preserve
  correlation without disclosure.
- A separate telemetry database: rejected; the platform store already provides append-only
  revisions and hash-chained events.
