# ADR-013 — Typed YouTube DAG and master projection

Status: superseded by removal of the active domain vertical
Date: 2026-07-11

This ADR is retained only as immutable decision history. The runtime, CLI, pack, skill and active
tests it described are no longer shipped. Published schemas remain inert compatibility artifacts
for historical ledger and backup verification.

## Context

The local `/yt` donor procedure intentionally produces one human document. That is a useful
operator interface, but a mutable Markdown monolith cannot prove stage-level idempotency, resume,
fact-bound invalidation or coherent recovery after a crash. M4 also needs to preserve the domain
logic in the deep-research, title, thumbnail and scriptwriter donors without copying their live
runtime trees, obsolete Notion/Desktop mutations or unlicensed code into the core.

## Decision

- Keep `master.md` as the only human-facing YouTube document and generate it deterministically from
  typed machine artifacts. It is a projection, never workflow state.
- Store every stage output as an immutable content-addressed JSON object. Atomically replace one
  small stage record only after the object hash and schema validate. A project manifest points to
  stage-record hashes and records `checkpointed` or `draft_complete`; these draft pointers never
  affect `ledger/CURRENT`.
- Use the fixed DAG: brief and verdict; exact-span research; sibling title/thumbnail branches;
  evidence-bound script; presentation; packaging; warmup; master projection; deterministic package
  validation.
  Titles and thumbnails have identical direct dependencies and may be computed in parallel, while
  the single project writer serializes their durable records.
- Bind each stage fingerprint to the exact runtime component manifest, full schema registry,
  Python/Pydantic/Unicode runtime, scanner, renderer, canonicalizer, policy, skill, adapter,
  direct input and direct dependency output hashes. Reuse only a manifest-closed exact match and
  rescan its bytes under the current policy. A research/evidence change invalidates research
  descendants while preserving brief and verdict.
- Preserve donor domain invariants: exactly 10 ranked titles, 10 orthogonal thumbnail concepts,
  title/thumbnail promise compatibility, fact IDs in every factual script/deck line, numeric
  consistency, presentation graphics plus animation, one warmup draft and one master document.
- Strengthen provenance beyond donor URL-only citations: every synthetic supported fact resolves
  to exact workspace bytes, a byte span, source SHA-256 and quote SHA-256. Unsupported or disputed
  facts cannot enter factual output.
- Restrict the v1 executor to `romarayt` and `neuropros` typed synthetic fixtures. Use an offline
  deterministic adapter; no model, network, upload, Notion, Telegram, Desktop or outbox adapter is
  present. Every output remains `draft`, and independent human review remains mandatory after
  deterministic package validation passes.
- Snapshot all 18 approved donor roots by per-file SHA-256 and tree hash in
  `ops/donors/youtube-assets.json`. Select the Codex `/yt` copy as canonical because its only
  substantive delta from the Claude copy is runtime-safe Codex routing. Reimplement behavior
  clean-room; copy no donor runtime code or binary asset.
- Add schema registry v1.2.0 for typed YouTube inputs, stages, records and manifest. Preserve prior
  registries unchanged. Add no third-party runtime dependency.

## Consequences

- Operators still review and copy one coherent `master.md`, while the system can resume after any
  stage without trusting that Markdown.
- Identical complete builds are filesystem no-ops. Stale descendants are visible and recomputed;
  immutable orphan objects after a crash are harmless and diagnosable.
- Synthetic gates prove contract and recovery behavior, not real title/thumbnail/channel quality.
  Real quality, user review and pilot promotion remain M5b work requiring separate inputs and
  approval.
- The donor single-document rule is preserved at the human interface but intentionally not used as
  canonical machine state. This is the only material adaptation of that rule.
