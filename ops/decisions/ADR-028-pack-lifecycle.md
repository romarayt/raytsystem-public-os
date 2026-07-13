# ADR-028 — Pack lifecycle with verified eval gating and rollback

Status: implemented behind feature flags
Date: 2026-07-12

## Context

Packs bundle agents, skills, workflows and fixtures — configuration that later becomes behavior.
Installing a directory tree on trust would admit symlink escapes, oversized payloads, unpinned
dependencies and silent post-approval edits. Concurrent hardening work is landing in
`raytsystem.packages`; this ADR documents the module together with those in-flight fixes, including
rollback/supersede and verified eval gating.

## Decision

- Ingest only workspace-relative, symlink-free source directories with bounded file counts and
  bytes, secret-scanning every file. Content identity is the hash of the per-file hash list, with
  `package.yaml` normalized (its own `content_sha256`/`signature` stripped) to avoid a circular
  self-hash while still binding every executable byte.
- Track revisions through a stored lifecycle — discovered, quarantined on ingest, validated,
  approved, installed, active, superseded, rolled back, blocked — as versioned records with
  hash-chained events in the isolated `ops/platform.sqlite` store.
- Validate before approval: the self-hash `signature` marker is checked but never treated as
  authenticity (`signature_verified` stays false), dependencies must be exact versions or commit
  hashes and resolve to active packages, reference paths must not escape the workspace, and
  `self_modify` permission blocks the revision outright.
- Gate approval on verified evals: the referenced eval runs must exist in the platform store,
  have passed, and together cover the manifest's eval suites plus the previous active revision's
  suites, so an update cannot drop coverage. The approval itself must resolve exactly
  (`activate_package`, scope `package_activation`, plus `unsigned_pack` while signatures are
  unverified) and is hash-bound to the content.
- Install by re-hashing the source against the approved content hash, staging under
  `ops/staging/packages/` and atomically renaming into `.raytsystem/packages/<revision>`. Activation
  re-verifies the installed bytes, records the active pointer and supersedes the prior active
  revision with an event.
- Roll back only to a revision that was previously approved and installed for the same package,
  re-verify its installed content hash, require an explicit reason, and record both the
  rolled-back current revision and the restored active pointer.
- Fail closed: every operation raises the package lifecycle error when `pack_lifecycle_enabled`
  is off.

## Consequences

- A pack that changes on disk after approval cannot be installed or activated; hashes are checked
  again at each transition.
- Eval gating turns "the pack still passes its suites" into a hard precondition of activation
  rather than a convention.
- A marketplace, remote pack registries and real cryptographic signature verification are
  intentionally deferred; unsigned packs always demand the wider approval scope.

## Alternatives considered

- Installing directly from the source directory: rejected; staging plus atomic rename keeps
  partially copied packs invisible.
- Trusting the manifest's declared hashes: rejected; every hash is recomputed from observed bytes.
- Allowing rollback to any recorded revision: rejected; only revisions with recorded approval and
  verifiable installed content qualify.
