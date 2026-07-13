# ADR-011 — Generation-bound retrieval, projections and draft SAVE

Status: accepted
Date: 2026-07-11

## Context

M2 adds search, graph and Markdown views without allowing any derived representation to become a
second source of truth. A query may race with `ledger/CURRENT`, a SQLite file may be stale or
tampered, and free-form model text could otherwise bypass claim-level evidence. SAVE also needs a
useful workflow boundary before a future human-approved canonical promotion exists.

The M2 AnswerProposal and QueryCitation contracts add required generation binding. Their schema
changes must not overwrite the registry referenced by existing M0/M1 generations.

## Decision

- `ActiveCorpus` is the single read-only resolver for the generation manifest, active typed ledger
  objects, reachable Source/SourceRevision/Normalization/Segment evidence and stable run metadata.
  It verifies canonical bytes, content addresses, locators, excerpts and exact raw hashes before a
  factual consumer receives a record.
- SQLite FTS5, NetworkX graph JSON, `knowledge/index.md`, `knowledge/hot.md` and generated record
  pages are one rebuildable projection bundle. `knowledge/.projection.json` binds the bundle to the
  generation manifest, canonical projection inputs, logical SQLite row digest and every generated
  file hash, and is installed last.
- SQLite file bytes are not a reproducibility oracle. The deterministic `documents` rows are
  canonical-JSON hashed, verified again before search, and compared with the projection marker.
  Rebuild uses a same-directory temporary database, integrity check, fsync and atomic replacement.
- Search accepts a bounded literal token grammar only, uses parameterized SQL, a read-only immutable
  connection and a progress deadline. QMD remains a contract-only unavailable adapter until a
  separately approved model artifact and M5b benchmark exist; FTS5 is always functional without a
  model or API key.
- Every hit carries generation and canonical object hashes. QUERY captures one `ActiveCorpus`,
  rehydrates hits from its typed records, verifies object hashes and the complete evidence chain,
  and rechecks `ledger/CURRENT` before returning. A generation race triggers one forced rebuild and
  retry, then fails closed.
- The deterministic kernel creates citation IDs, answer spans and VERIFIED state. Facts and
  inferences require verified citations, gaps cannot cite, and `rendered_answer` is derived exactly
  from typed sections. Indexed source text is always inert data and is escaped before rendering.
- LINT is deterministic and read-only. It checks canonical/evidence/raw integrity, projection
  freshness, generated views, local links, aliases/IDs, secrets and operation divergence. Semantic
  mode reports review findings only and never changes canonical knowledge.
- SAVE writes a hash-closed typed EvidencePack/ProposalRequest/ProposalResponse/Artifact bundle to
  `ops/staging/` and an escaped DRAFT preview to `artifacts/drafts/`. Its operation key binds the
  active generation, evidence, schemas, component and config. It never writes ledger, events,
  generated knowledge, Git refs or outbox and performs no external side effect.
- Generated paths and SQLite families reject symlink, non-regular and hardlinked targets. Query,
  projection and SAVE errors redact implementation paths at their public boundary.
- NetworkX 3.6.1 is used only as an in-memory deterministic graph builder. The persisted format is
  sorted JSON; pickle/gpickle is forbidden.
- Contract schema registry `v1.0.0` remains unchanged for historical M0/M1 generations. M2 exports
  registry `v1.1.0`; new generations bind that hash while existing generations keep resolving to
  their original immutable registry.

## Consequences

- Deleting `.raytsystem/index.sqlite`, graph JSON or generated Markdown cannot delete knowledge; one
  local command rebuilds equivalent logical results from canonical state.
- A corrupt or forged cache can reduce availability briefly but cannot introduce a factual answer.
- Query and LINT are read-only. SAVE is useful for review and handoff but cannot silently promote or
  publish content.
- Search quality beyond labeled synthetic cases and any QMD/model comparison remains explicitly
  pending M5b pilot data and approval.
