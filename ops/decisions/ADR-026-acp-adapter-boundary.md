# ADR-026 — ACP adapter boundary

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Agent Client Protocol sessions stream messages, tool calls, permission requests, terminal output
and file changes from a runtime into the control plane. Persisting those payloads verbatim would
copy prompts, secrets and terminal scrollback into operational storage, and accepting privileged
events on the runtime's word would bypass policy. Concurrent hardening work is landing in
`raytsystem.protocols.acp`; this ADR documents the module together with those in-flight fixes.

## Decision

- Bind every ACP session at initialization to an ACTIVE `ExecutionSession` in `ops/control.sqlite`
  with a matching adapter and workspace; an ACP session can never exist without a governed runtime
  session behind it.
- Negotiate capabilities as the intersection of the request with a fixed supported set (messages,
  streaming events, tool calls, permission requests, cancellation, terminal output, file changes,
  session resume). Events whose type requires a capability that was never negotiated are rejected.
- Store sessions, capability sets and per-session sequence state as versioned records with
  hash-chained events in the isolated `ops/platform.sqlite` store. Event payloads are never
  persisted: each event is bounded (256 KiB), reduced to `payload_sha256`, secret-scanned and
  always marked redacted before it is recorded.
- Enforce strict per-session monotonic sequencing with optimistic revisions, so replayed, dropped
  or reordered events are integrity errors instead of silent state drift.
- Treat privileged events as authority checks: `tool_call`, `permission_request` and `file_change`
  require a resolved policy decision bound to the event payload hash; `tool_call` and
  `file_change` additionally require a resolved approval with scope `acp_privileged_event`; a
  `permission_request` may never arrive with a pre-granted approval.
- Issue resume as a rotating bearer secret: only its SHA-256 is stored, comparison is
  constant-time, resume requires the negotiated capability, and every successful resume rotates
  the token. Terminal transitions (cancelled/closed/failed) are one-way.
- Fail closed: every operation raises the ACP adapter error when `acp_adapter_enabled` is off
  (default off in the repository configuration).

## Consequences

- The control plane gets an auditable, hash-chained protocol timeline without ever holding the
  streamed content itself.
- A compromised runtime cannot mint authority through the event channel; it can only reference
  approvals and policy decisions that already exist.
- Deep runtime integration — a native ACP client/transport driving Zed-style agents — is
  intentionally deferred; the adapter governs and records the protocol boundary only.

## Alternatives considered

- Persisting event payloads for debugging: rejected; digests preserve ordering and identity
  without disclosure.
- Long-lived static resume tokens: rejected in favor of hashed, rotating tokens.
- Accepting privileged events with inline "approved" flags: rejected; only resolver-verified
  records count.
