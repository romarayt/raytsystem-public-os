# ADR-025 — MCP governance and catalog-only default

Status: implemented behind feature and exposure gates
Date: 2026-07-12

## Context

MCP servers are third-party code with self-declared tools, schemas and prompts. Trusting a
manifest at discovery time would let a hostile package name a dangerous executable, smuggle
`$ref`-laden schemas or ship pre-enabled tools. Concurrent hardening work is landing in
`raytsystem.tooling`; this ADR documents the module together with those in-flight fixes.

## Decision

- Run a monotonic revision state machine — DISCOVERED → VALIDATED → APPROVED → ENABLED, with
  QUARANTINED, BLOCKED, DEGRADED and DISABLED — stored as versioned records with hash-chained
  events in the isolated `ops/platform.sqlite` store. Canonical knowledge is never written.
- Quarantine at discovery on any of: package hash mismatch against observed bytes, unsafe stdio
  executable (absolute paths, traversal, non-allowlisted characters), tool/resource/prompt
  manifest mismatches, schema hash mismatches, unsupported or unsafe schema keywords (`$ref`,
  `$dynamicRef`, content encodings, unbounded depth/size), and any initial policy other than
  catalog-only. Quarantine reasons are recorded on the server record.
- Require an exact resolved approval (`approve_mcp_catalog`, scope `mcp_catalog`) hash-bound to
  the pinned definition before a validated revision can be approved; enablement re-verifies the
  pinned definition hash and the recorded approval. Every transition requires a reason.
- Verify policies end to end: `McpPolicy` is hash-verified, must cover exactly the revision's tool
  set, and while `external_mcp_execution_enabled` is off (default) every tool policy must stay
  `catalog_only` and the policy may request no network allowlist, roots or secrets.
- Gate invocation behind `external_mcp_execution_enabled`, an ENABLED server revision, an ENABLED
  per-tool policy, pinned definition and schema hashes, per-tool input/output byte limits and
  timeouts. Output is untrusted: it is truncated, secret-scanned and replaced with `[REDACTED]` on
  scanner hits, and every invocation is recorded with input/output hashes and enforced limits.
- Report health as `disabled` with `external_mcp_execution_disabled` while execution stays off, so
  the catalog never implies runtime capability.
- Fail closed: every operation raises the MCP governance error when `mcp_governance_enabled` is
  off; execution additionally requires the separate exposure flag, which is default-off.

## Consequences

- Installing or approving an MCP server grants zero runtime authority; catalog-only is the resting
  state of the whole subsystem.
- A tampered definition, schema or policy is detected by hash pinning at use time, not only at
  discovery time.
- Actual external server transport (stdio/HTTP process management) is intentionally deferred: the
  invoke path accepts an injected runner and no transport ships while the exposure flag is off.

## Alternatives considered

- Trusting server-declared manifests: rejected; every hash is recomputed from observed bytes.
- Auto-enabling tools after approval: rejected; per-tool policy plus the exposure flag keep
  execution a separate decision.
- Full JSON Schema support: rejected; the bounded keyword subset removes reference and content
  smuggling classes outright.
