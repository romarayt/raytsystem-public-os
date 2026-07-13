# ADR-034 — Unified agent surface and safe local skill authoring

Status: implemented
Date: 2026-07-12

## Context

The catalog plane owns inert `AgentDefinition` and `SkillDefinition` records, while the execution
plane owns mutable employee, session and run state. Exposing those two planes as separate agent
catalogs made one logical agent appear twice and obscured the difference between a definition and
its current runtime state. The Skills catalog already returned the allowlisted `SKILL.md` body, but
the generic Inspector rendered it as raw text and there was no bounded write model.

The product needs one user-facing Agent identity and a useful skill document/editor without
collapsing the backend planes or giving the browser filesystem authority.

## Decision

### One Agent presentation model

- Join `AgentDefinition` and execution state only by the stable `agent_id` / `employee_id` binding.
  Never join by display name, translation or array position.
- Preserve catalog and execution as separate backend planes. The API returns a derived
  `AgentViewModel` with `definition` and nullable `execution` branches.
- Keep a definition-only agent visible as `catalog_only`. Keep an execution-only record visible as
  degraded data instead of silently dropping it. Duplicate or inconsistent execution bindings are
  also explicit degraded states.
- Use one `/agents` list and one specialized detail surface. Runtime-disabled and
  store-uninitialized states are status on that same agent card, never a second card catalog.
- Canonical agent and skill names stay English and verbatim. Russian presentation is limited to
  roles, descriptions, statuses, labels, actions, hints and errors. Canonical-name lookup and
  localized-label/description lookup are separate functions.

### Shared surface tabs

- A route component owns the only page-level `.route`. Nested views return semantic sections.
- Reusable surface tabs own padding, inter-tab gap, content gap, active/hover/focus states,
  separator, horizontal overflow and ARIA tab semantics. Inspector, graph-layout and compact
  switches retain their own contracts.

### Skill reading and authoring

- A skill detail view exposes Overview, Instruction, Permissions, Tools, Tests and History. The
  instruction is rendered from the allowlisted body as inert Markdown using React nodes, never
  executable HTML. Recursive depth, total render nodes and table dimensions are bounded; an
  oversized preview is visibly truncated. Raw mode returns the exact allowed text.
- Tool, workflow and history relationships are shown only when typed metadata exists. The UI does
  not infer authority by parsing prose in `SKILL.md`.
- Editability is a server-computed policy. A `pack_local`, user-trusted, enabled, non-restricted
  skill at exactly `skills/<skill_id>/SKILL.md` may be edited. Official, pinned, generated,
  restricted, historical, external and unverified-origin skills are read-only with a typed reason.
  Forkability is independent: safe read-only sources may be copied, while restricted or
  unverified sources remain non-forkable.
- The browser sends only a typed skill ID and content. The server derives the destination path and
  requires a local session, same-origin CSRF, an idempotency key, expected catalog SHA-256 and
  expected source SHA-256.
- Preview and save validate UTF-8, size, YAML frontmatter, canonical name/directory agreement,
  permissions, test status and sensitivity. Writes use no-follow descriptor checks, single-link
  regular files and guarded no-replace installation with hash/inode witnesses, then re-read
  through `CatalogService`, recompute hashes, reject non-target catalog races, update the catalog
  projection and append a revision plus audit event. A bounded fsync recovery journal uses the
  exact committed scope/idempotency-key/request receipt to finish or roll back during startup;
  catalog UI readers, execution employee projection and workspace preparation share the same
  inter-process fence and cannot expose or consume a transition in progress. Ambiguous
  third-party state is preserved and requires manual recovery.
- Every edited or forked skill is recorded with `test_status: pending` until an independent
  verifier attests it. Saving never runs the skill, tools, workflows or external checks.
- A stale catalog or source hash returns a typed conflict with current/proposed content and diff
  when disclosure policy permits. raytsystem never auto-merges instructions.
- Forking is a preview-and-confirm operation that creates a unique `pack_local`, user-trusted
  skill without modifying the source.

## Consequences

- Users see one Agent while catalog and execution ownership remain independently auditable.
- Local skill maintenance is possible without accepting an arbitrary path or turning imported
  instructions into commands.
- Official and installed content cannot be edited in place; customization creates an explicit
  local lineage.
- A successful save can still have `pending` tests. The operator must run and record the relevant
  verifier before treating the revision as passed.
- Typed tool/workflow relationships and complete historical revisions remain sparse until their
  registries expose those links; the UI reports the absence instead of guessing.
- Filesystem namespaces and SQLite are not joined by a portable 2PC transaction. The durable
  journal bridges normal crash windows without silently overwriting a concurrent version. A tiny
  fork window remains between directory creation and its recovery marker; it may leave an empty,
  unproven directory that recovery deliberately preserves for operator review.

## Alternatives considered

- Keep separate “digital employees” and “catalog definitions” tabs: rejected because a projected
  employee is runtime state of the same Agent, not a second user entity.
- Join by localized name: rejected because names can change and translations are presentation.
- Make every discovered skill editable: rejected because provenance, pinning and sensitivity are
  security boundaries.
- Accept a filesystem path from the browser: rejected because it creates traversal, symlink and
  confused-deputy authority.
- Automatically merge stale Markdown or preserve a submitted `pass`: rejected because both would
  manufacture unreviewed authority.
