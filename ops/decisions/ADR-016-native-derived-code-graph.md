# ADR-016 — Native, derived code graph

Status: implemented
Date: 2026-07-12

## Context

raytsystem needs architectural retrieval, impact analysis and source navigation without making a
second graph database or an upstream tool's output directory authoritative. The graph must remain
rebuildable from the checked-out repository, bounded at query time and safe when stale.

Graphify was reviewed as a behavioral reference at public commit
`0efb2a443c85c04f31620fb8f60d138b8483af05` (captured 2026-07-12). Its useful ideas are the
detect → extract → resolve → cluster → query pipeline, explicit edge confidence, incremental
manifests and neighbors/path/impact operations. No Graphify source or `graphify-out/` artifact is
copied into raytsystem.

At that commit the repository declares unreleased `0.9.13`; the latest released tag observed in
the audit was `v0.9.12` (`35665a76ba26da0e1bfcab074fede19c94fc5c89`, 2026-07-10). Graphify is
MIT licensed, Copyright (c) 2026 Safi Shamsi. raytsystem copied no substantial implementation
fragment, but retains this notice because the pipeline/query concepts were an explicit design
reference.

## Decision

- Implement the graph natively in `raytsystem.codegraph` with Tree-sitter extraction for supported
  JavaScript/TypeScript syntax, Python's local AST and deterministic readers for repository
  metadata, SQL, configuration and ADRs. Parser work runs in a resource-limited subprocess; YAML
  keys are scanned structurally without object construction.
- Persist only immutable, content-addressed snapshots and a small atomic `CURRENT` pointer below
  `.raytsystem/graph/`. The graph is a disposable projection, never canonical knowledge.
- Bind every snapshot to file hashes, extractor/config fingerprints and Git metadata. Stable
  logical inputs produce the same node, edge and snapshot identities.
- Label edges `EXTRACTED`, `INFERRED` or `AMBIGUOUS` and preserve source locations and extractor
  provenance.
- Reject missing, stale, building or integrity-failed graphs for graph-first operations. A caller
  may use a documented fallback only when the operation explicitly permits it.
- Bound queries by depth, node count, edge count and serialized bytes. Provide query, explain,
  neighbors, shortest path and reverse-impact operations through typed CLI/HTTP requests.
- Project code nodes into Universe without exposing absolute paths or allowing the client to
  mutate canonical state. The browser may request only typed update/rebuild operations for the
  disposable projection, bound to an expected snapshot and protected by the existing
  session/origin/CSRF/idempotency boundary.
- Keep the checkpoint guard as the first pre-commit hook. raytsystem-owned post-commit and
  post-checkout hooks perform incremental refresh; no upstream installer may rewrite `AGENTS.md`
  or `.codex/hooks.json`.
- Route architecture/dependency/ownership/impact intent to a verified code graph by default. Exact
  factual knowledge queries retain the evidence-backed QUERY path; missing/stale code graph uses
  an explicit safe fallback rather than implicit rebuild or false confidence.

raytsystem intentionally did not adopt Graphify's mutable `graphify-out/graph.json`, `nx.Graph`
relation collapsing, raw query logging, arbitrary MCP project paths, semantic/media egress,
CDN-based HTML export or instruction/hook installers. WAL, fencing, canonical-byte validation,
secret redaction and API path policy remain raytsystem-native.

## Consequences

- Deleting `.raytsystem/graph/` loses no source data; rebuild restores the projection.
- Repository changes make the graph visibly stale instead of silently returning mixed state.
- `CURRENT` is the derived commit point. Immutable snapshot/manifest objects, a fenced WAL and
  read-side abandoned-writer detection provide observable all-or-nothing recovery; none of these
  records can write `ledger/CURRENT`.
- Graph-first context can reduce broad file reads, but measured quality remains a benchmark result,
  not an architectural assumption.
- Tree-sitter grammar packages become pinned runtime dependencies; no graph database or upstream
  Graphify runtime is introduced.

## Alternatives considered

- Committing `graphify-out/`: rejected because it creates a second projection authority.
- Embedding Graphify or a graph database: rejected because it adds a second control/runtime plane.
- Regex-only extraction: rejected because call/import provenance and locations would be too weak.
