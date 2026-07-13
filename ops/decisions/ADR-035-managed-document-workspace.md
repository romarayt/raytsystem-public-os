# ADR-035 — Managed document workspace

Status: accepted for experimental delivery

Date: 2026-07-12

## Context

raytsystem needs an in-app replacement for the main local-note workflows commonly handled in
Obsidian: discovering workspace Markdown, searching it, reading it as a document, editing allowed
files, following links, inspecting history and focusing the same object in Knowledge Universe.

A generic file manager would violate the existing control-plane boundary. The browser must not be
able to name an arbitrary absolute path, generated knowledge must not become human-editable, and a
Markdown editor must not silently rewrite constructs that it does not understand. The existing
canonical knowledge FTS5 index, catalog instruction API and public Handbook each have a different
authority and cannot be expanded into a document editor without coupling unrelated state planes.

## Decision

### Separate product and state plane

Add a product surface named **Documents** with its own service, contracts, snapshot and disposable
index. The three user-facing concepts remain distinct:

- **Handbook / База знаний** is the shipped public documentation under `website/docs`; the
  Handbook surface remains read-only, while an explicitly authorized maintainer Documents root may
  expose the same source files for editing without merging the two products;
- **Documents / Документы** are explicitly allowlisted files in the current workspace;
- **Universe / Вселенная** is a bounded projection that may display document nodes and their
  verified relationships, but is not the document source of truth.

Document bytes on disk remain authoritative for documents. They do not become canonical claims by
being indexed, rendered, edited or shown in the graph. A manual note enters canonical knowledge
only through the existing INGEST validation and promotion boundary.

### Policy-defined roots

Load document roots from the typed workspace configuration. Every root has a stable root ID,
workspace-relative POSIX path, mode and kind. Supported modes are `read_write`, `read_only`,
`protected_read_only` and `hidden`.

Configuration can narrow authority but cannot weaken the compiled policy floor. The generic editor
never writes `_raw/`, `normalized/`, `ledger/`, generated `knowledge/`, run/task/audit ledgers,
secrets, `.git/`, `.raytsystem/` managed state, graph/index snapshots, package lock state, machine
credentials or repo-local skills. `knowledge/manual/` is the explicit editable exception inside the
otherwise protected knowledge tree. Maintainer documentation and additional Markdown roots become
writable only through an explicit root policy.

The repository baseline uses roots `manual` (`knowledge/manual`), `maintainer-docs` (`docs`) and
`public-docs` (`website/docs`). The latter two configured `read_write` modes are effective only when
the maintainer-controlled config also declares `allow_maintainer_docs_write = true` and the compiled
and sensitivity policies pass; listing those roots alone grants no write authority and never makes
the Handbook surface editable.

The server resolves roots and paths. Browser requests use document, root and folder IDs plus a
validated basename; they never carry an absolute path or an arbitrary destination path. Symlinks,
unsafe hardlinks and ambiguous overlapping roots fail closed.

### Projection and durable metadata

Persist searchable document metadata and FTS5 rows in `.raytsystem/documents.sqlite`. This database is
a disposable projection and is rebuilt from allowed files plus the small durable metadata/history
plane. It is never a source of document content or canonical knowledge.

Stable first-seen observations, document identity transitions, idempotency receipts and hash-only
audit metadata live in `ops/platform.sqlite`; private Markdown is not stored in its audit payloads.
Content-bearing local revisions, when policy permits them, are immutable content-addressed objects
with manifests under protected `ops/document-revisions/`. Restricted content receives metadata-only
history and no revision object until an encrypted revision policy is separately enabled and
qualified. These durable stores survive an index rebuild and cannot exist only in
`documents.sqlite`.

Only private and workspace-transfer backups include document roots and `ops/document-revisions/`.
Public and diagnostic exports do not disclose private document content. Disposable indexes are
excluded from backup and rebuilt after restore.

### Typed read and write services

Expose bounded, paginated read operations for status, listing, tree expansion, search, recent/new/
modified views, document detail, links, backlinks and history. A detail is bound to the document
snapshot and content SHA-256 observed by the server. API payloads contain only relative paths.

All mutations go through a dedicated `DocumentService`: create document, create folder, update,
rename and move. Deletion is not part of this release. Every mutation requires the existing local
session, exact Origin, CSRF token and idempotency key plus expected document snapshot and content
hash where applicable. It rechecks root mode, sensitivity, path containment, link safety and the
current on-disk identity immediately before a no-follow atomic create/replace/rename. A successful
mutation appends an audit event and refreshes only the affected projection rows.

The document SHA-256 and stable document identity are the single-document CAS authority. A changed
file, changed identity or unavailable snapshot returns a typed conflict with the safely available
base/disk/draft data. Drift of the *global* index snapshot caused only by another document may be
rebased by the operations that explicitly permit scoped rebase (content update, the shared
move/rename path and restore); the response then sets `snapshot_rebased = true`. Create and folder
creation still require their prepared global snapshot because destination availability is part of
their authority. raytsystem never overwrites a changed disk version and never invokes an automatic LLM
merge.

### Markdown qualification

Markdown remains the source format. Reading uses a parsed, allowlisted React component tree and
never `dangerouslySetInnerHTML`. URLs, images, HTML, SVG, embeds and data/remote resources are
sanitized or represented as inert placeholders.

CodeMirror 6 is the source editor. Milkdown with GFM and raytsystem extensions is the preferred visual
editor only after an exact corpus round-trip qualification. Frontmatter is preserved as a raw
envelope; supported wikilinks and callouts are restored by raytsystem extensions. Unknown/raw syntax,
complex YAML, HTML fragments or any lossy transform forces Source mode or blocks visual saving with
an explicit warning. Full Obsidian plugin/community-syntax compatibility is not claimed.

The 2026-07-12 browser/Vite spike made the following choice:

| Candidate | Verified evidence | Decision |
|---|---|---|
| Milkdown `7.21.2` | MIT; direct `@milkdown/kit`; Markdown-first ProseMirror/Remark model; GFM and plugin architecture; no required cloud backend | Selected visual candidate behind qualification |
| Tiptap Markdown `3.27.3` | MIT, but its Markdown feature is still Beta | Rejected for this release |
| CodeMirror 6 | umbrella `6.0.2`, `lang-markdown` `6.5.0`, `lint` `6.9.7` | Selected for exact Source mode, not visual editing |
| Existing Handbook renderer | Current safe read baseline | Retained for reading; it is not an editor |

The isolated Chromium/Vite spike measured 435.70 kB raw / 131.22 kB gzip for Milkdown
core+CommonMark+GFM and 607.53 kB raw / 207.79 kB gzip for CodeMirror
basicSetup+Markdown+lint. The repository production build keeps them in separate lazy chunks; the
2026-07-13 Vite build reported 456.48 / 137.45 kB (Visual) and 607.69 / 207.86 kB (Source),
raw / gzip. After warm-up, Milkdown parse→serialize took 0.7–5.4 ms in the exercised
spike fixtures; the first invocation took 34.9 ms.

The spike preserved CommonMark/GFM meaning and kept image syntax exact in the exercised cases, but
it did not produce a generally lossless source round trip: ordinary formatting was canonicalized,
frontmatter was destroyed
without a raw envelope, wikilinks/embeds/callouts were escaped without raytsystem extensions, CRLF was
normalized and a final newline was added. Raw and unknown constructs remain Source-only. Therefore
visual Save requires the frontmatter/line-ending/final-newline envelope, raytsystem token restoration
and an exact per-document round-trip check; otherwise it is blocked.

These measurements qualify the architecture choice, not every Markdown shape or the complete
release. Repository integration subsequently resolved the four exact editor dependencies in
`web/package-lock.json`; full and production-only `npm audit` runs on 2026-07-12 reported zero known
vulnerabilities in the 443-package resolved graph. The production notice is generated from that
lockfile and checked by `npm --prefix web run licenses:check`. Repository tests, browser evidence
and the exact per-document guard remain independent gates and must not be inferred from the spike or
audit.

### History, tabs and graph

History merges bounded Git commits, protected local revision records and the current unsaved diff.
Restore is a separate previewed, expected-hash-bound, audited write and never commits to Git.

Tabs, pins, favorites and recently closed identifiers may be stored as bounded browser preferences.
Private document content is never stored in `localStorage`; drafts are memory/session scoped.

Universe receives visually distinct manual, documentation and protected/generated document nodes
plus document-link edges. Unverified notes never render as canonical claims. The current focused
slice contains the selected document and its resolved outgoing/backlink document neighborhood;
claim/entity association projection remains deferred. Graph focus is bound to the document
projection fingerprint and is capped independently from the full document index.

## Consequences

- Users gain a local document workspace without granting the browser generic filesystem authority.
- Document search and Knowledge QUERY remain separate indexes with separate freshness and recovery.
- Generated raytsystem state stays read-only even when it is viewable.
- Index deletion loses no document bytes, but durable first-seen/history metadata requires the
  protected platform/revision planes and their private/transfer backup policy.
- Visual editing is intentionally narrower than Source mode. A document may be readable and
  source-editable while visual saving is unavailable.
- The measured editor bundles are material and must be lazy-loaded only for the active document.
- The release adds dependency, CSP, backup/restore, schema, documentation and security-review work.
- Collaboration, cloud sync, Canvas, plugin marketplace, deletion and arbitrary binary editing stay
  outside this decision.
- Filesystem watching/background polling and correlation of renames performed outside raytsystem stay
  deferred; the first delivery uses startup initialization, explicit refresh/rebuild and
  post-mutation targeted refresh.
- Attachment selection/upload/copy and a document command palette stay deferred even though safe
  viewing of already indexed local images and wikilink heading focus are available.

## Alternatives considered

- **Generic filesystem browser:** rejected because paths supplied by the browser would become
  ambient authority.
- **Reuse the canonical knowledge FTS5 database:** rejected because workspace files and verified
  claims have different authority, lifecycle and sensitivity.
- **Expand the catalog instruction API:** rejected because instruction documents are a fixed inert
  allowlist and repo-local skills require a specialized editor.
- **Use only Milkdown serialization:** rejected because unknown Markdown and frontmatter could be
  changed silently.
- **Tiptap Markdown for visual editing:** rejected for this release because the verified `3.27.3`
  Markdown feature is still Beta.
- **CodeMirror as the visual editor:** rejected because it is the exact source-editing surface, not
  a visual ProseMirror-style document model.
- **Handbook renderer as an editor:** rejected because it is a safe read baseline without editing
  or Markdown serialization.
- **Store first-seen/history only in the disposable index:** rejected because rebuild would rewrite
  user-visible history.
- **Autosave every keystroke:** rejected for the first release; drafts, explicit save and conflict
  checks provide a safer boundary.
