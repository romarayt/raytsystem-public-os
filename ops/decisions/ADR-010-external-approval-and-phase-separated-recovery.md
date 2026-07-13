# ADR-010 — External approval authority and phase-separated recovery

Status: accepted
Date: 2026-07-11

## Context

A JSON file created inside the agent-writable workspace can prove candidate/hash consistency, but
it cannot prove that a human or an authority outside the agent approved the action. Approval and
fixture policy are also time-dependent before commit, while a crash after the atomic
`ledger/CURRENT` swap must remain recoverable after the approval expires, its incoming transport is
removed, or fixture policy changes.

The same distinction applies to staging and audit side effects: incomplete pre-commit bundles may
be rebuilt, while a committed generation may only be reconciled and must never receive a second
canonical commit.

## Decision

- Real-corpus promotion is default-deny. `ApprovalVerifierUnavailable` rejects workspace-authored
  records; a separately configured `ApprovalVerifier` must authenticate the incoming bytes.
- Approval identity binds decision, action, transaction, exact candidate hash, destination, scope,
  policy version/hash, approver, validity window and conditions. M1 accepts exactly
  `scope=("real_corpus",)` and rejects all non-empty conditions.
- Accepted approvals and verifier metadata (name, version and key ID) are published immutably.
  Candidate hash, current policy and expiry are checked again inside the fenced commit section.
- Fixture authority is derived from immutable Source/SourceRevision/raw evidence plus the current
  trusted fixture manifest and the complete operation fingerprint. Mutable run-manifest booleans
  are audit fields only and cannot grant authority.
- Before `ledger/CURRENT` changes, current approval/fixture policy is authoritative. After
  `CURRENT == WAL.next_generation_id`, recovery validates the commit-time WAL authority hash,
  immutable accepted approval (when applicable), event outbox, generation and result snapshot;
  it does not demand a fresh approval, current fixture manifest, current parser or current schema.
- A fresh approval may replace an expired pre-commit approval for the same exact candidate. The WAL
  authority field may change only in `prepared`/`committing`, and an immutable supersession record
  preserves the old/new authority hashes.
- Staging uses a hash-closed bundle marker written last. A superseded WAL remains recoverable until
  the replacement bundle and WAL are durable. Git checkpoint creation similarly writes a pending
  record before its create-only ref and can reconcile a ref/record crash window.
- The pointer commit renews both fenced leases before releasing the SQLite writer lock. Generated
  views and Git audit checkpoints are materialized only while holding the renewed global/source
  leases and while `ledger/CURRENT` still names the generation.
- Identical preparation is serialized by worker-token operation/source leases. A separate control
  connection renews those leases during expensive extraction and validation; expiry is checked in
  the renewal predicate and all preparation fences are verified again before promotion.
- A child promotion is blocked until the active generation's parent transaction, run, operation,
  event outbox, run manifest and required Git checkpoint are terminal. A semantic no-op never
  rematerializes an already reconciled generation and therefore cannot rewind derived views.
- When the same proposition is proposed with new evidence, canonical preparation and stale-parent
  rebase both form a deterministic union with existing source spans. Old evidence remains
  addressable and the prior immutable object is never rewritten.
- Immutable file publication treats its same-directory temporary hardlink as part of the crash
  protocol: the temp name is removed before directory fsync, and retry removes only owned
  same-inode remnants before verifying the canonical object has a single link.
- Historical normalization snapshots are validated through their immutable manifest, raw hash,
  segment identities, locators and excerpt closure. Only the current run's normalization is replayed
  with the current parser, so a parser version upgrade cannot strand old evidence. Historical PDF
  snapshots additionally retain their recorded containment requirement; an unavailable/weaker OS
  sandbox still fails closed without parsing the old bytes.
- Secret classification covers exact bytes, decoded proposal/final Claim bytes and workspace-relative
  paths. The final candidate is rescanned on every validation, including immediately under the
  promotion fence, so a scanner upgrade cannot rely on an older staging decision.
- A cached successful operation is terminal only when its WAL, run and manifest have converged.
  Identical ingest retries route through committed recovery. Git checkpoint records, commit objects
  and refs are verified together; a missing ref is recreated create-only from a verified record,
  while the SQLite coordination database remains rebuildable rather than checkpoint authority.

## Consequences

- `raytsystem promote RUN --approval ...` is an interface, not an implicit trust mechanism. It remains
  intentionally unavailable with the default verifier until M5b selects and approves an external
  verifier/destination. Synthetic fixture promotion remains autonomous in its exact namespace.
- Expired or removed incoming approvals cannot strand an already committed generation; missing or
  changed accepted audit evidence does fail closed.
- Policy/parser upgrades can reject an uncommitted run and require re-preparation, but they cannot
  reinterpret or recommit historical canonical state.
- SQLite and generated indexes remain rebuildable coordination/projection layers; the observable
  canonical commit point remains the immutable generation named by `ledger/CURRENT`.
