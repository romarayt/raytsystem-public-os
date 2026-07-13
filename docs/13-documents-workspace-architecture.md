# Documents workspace architecture

Status: experimental implementation contract; M1–M5 qualification is required before release

Decision: `ops/decisions/ADR-035-managed-document-workspace.md`

## Product boundary

The Documents module is a managed view of allowlisted files in one pinned raytsystem workspace. It is
not a filesystem explorer and it does not merge the following products:

```text
Handbook       shipped public raytsystem documentation; read-only
Documents      policy-allowed workspace files and notes
Universe       bounded graph projection across typed state planes
Knowledge      evidence-backed canonical ledger and generated views
```

Document bytes remain the source of truth for Documents. Index rows, rendered React nodes, backlinks
and graph nodes are projections. Editing a manual note does not promote a claim.

The Handbook route remains a read-only product even though a maintainer workspace may expose
`website/docs` through the separately authorized `public-docs` Documents root.

## Configuration and policy resolution

Document roots extend the typed `config/raytsystem.toml` model. The concrete schema is versioned with
the other public contracts. A representative configuration is:

```toml
[documents]
index_db = ".raytsystem/documents.sqlite"
max_files = 100000
max_file_bytes = 5242880
max_total_bytes = 536870912
search_page_size = 50
allow_maintainer_docs_write = true

[[documents.roots]]
id = "manual"
path = "knowledge/manual"
mode = "read_write"
kind = "notes"

[[documents.roots]]
id = "maintainer-docs"
path = "docs"
mode = "read_write"
kind = "documentation"

[[documents.roots]]
id = "public-docs"
path = "website/docs"
mode = "read_write"
kind = "documentation"
```

The explicit maintainer flag is required before `docs` or `website/docs` can be writable; merely
declaring a root does not enable that authority. Config loading validates relative POSIX paths, unique
root IDs, known modes/kinds, bounded limits and deterministic overlap resolution. The most specific
configured root may narrow a parent rule. It may not escape the compiled protection floor.

Policy order:

1. reject malformed, absolute, traversal or secret-bearing paths;
2. reject symlinked roots/components/files and unsafe hardlinks;
3. apply hard hidden/protected zones;
4. resolve the most-specific configured document root;
5. apply the sensitivity decision, which may only reduce disclosure/write authority;
6. apply operation-specific checks such as extension, MIME, size and destination collision.

Hard non-generic-editor zones include `.git`, `.raytsystem`, `_raw`, `normalized`, `ledger`, generated
knowledge except `knowledge/manual`, operational ledgers/manifests/events, secrets, machine
credentials, package locks and repo-local skills. Generated knowledge is intentionally absent from
the configured roots: compiled policy hides or protects it without relying on a root entry. A
protected file has no Edit action.

## State planes

| Plane | Role | Recovery |
|---|---|---|
| Allowed workspace file | Authoritative document bytes | Git, backup or local revision restore |
| `.raytsystem/documents.sqlite` | Disposable metadata, FTS5 and links/backlinks projection | Rebuild from files and durable metadata |
| `ops/platform.sqlite` | First-seen, identity transitions, idempotency receipts and hash-only audit provenance | Existing protected private/transfer store policy |
| `ops/document-revisions/` | Private immutable content-addressed objects and manifests written through DocumentService | Hash verification and private/transfer backup only |
| Browser session | Open IDs, view preferences and unsaved draft | Ephemeral; never private content in localStorage |
| Universe slice | Bounded document nodes and relationships | Reproject from current document snapshot |

`first_seen_at` is projected into the index but is not owned only by it. Otherwise an explicit
rebuild would make every file appear newly added. The same rule applies to rename continuity and
local revisions.

## Index lifecycle

The index stores document ID, root ID, relative path, filename, extension, byte size, content hash,
title, headings, tags, aliases, bounded frontmatter, outgoing links, backlink rows, `mtime`,
first-seen, Git status, root policy, sensitivity and last-indexed time.

Restricted content and secret-bearing filenames are never put in FTS. Depending on policy, the
corresponding file is hidden or represented by redacted metadata only. Restricted content also has
metadata-only history: no revision bytes are written until an encrypted revision policy is enabled
and qualified. Absolute paths never enter the database or API.

Lifecycle operations:

- background initialization at web-app startup or an explicit protected rebuild, never as a GET
  side effect;
- explicit refresh of at most 256 requested document IDs, with a safe rebuild fallback when the
  projection is missing or structurally stale;
- single-file refresh after an audited mutation;
- explicit rebuild through a temporary same-directory SQLite file, integrity check, fsync and
  atomic replacement;
- explicit refresh and post-mutation single-file refresh in the first delivery; filesystem watching
  and a background polling loop are deferred;
- stable identity transition for rename/move performed through `DocumentService`; correlation of a
  rename performed externally is deferred and such a change can appear as remove+add after refresh;
- safe replacement of a missing/stale/error projection by rebuild from source files; a separate
  retained quarantine copy of the corrupt SQLite file is not part of this delivery.

Scanning ignores VCS internals, dependencies, virtual environments, build outputs, caches, managed
raytsystem state and configured exclusions. Directory enumeration is lazy in the API even when metadata
is already indexed.

## Search contract

Document search has its own literal/phrase grammar and parameterized FTS5 queries. Supported fields
are filename, relative path, title, Markdown content, headings, tags, aliases, simple frontmatter
properties and wikilinks. Query filters include `path:`, `type:`, `tag:`, `property:`,
`is:modified`, `is:new`, `is:readonly`, `after:` and `before:`; the HTTP list/search contract also
accepts typed root/folder/kind/mode filters. `is:modified` uses non-clean Git worktree state when Git
is available; without Git it uses persisted hash-drift or mtime-drift index status. `is:new` means
Git added/untracked or a durable `first_seen_at` within the current 30-day window. The Added view
sorts the full permitted set by first-seen rather than applying that window.

Search requests are length/token bounded, cancellable and paginated. Results bind the index snapshot
and content hash and return a bounded highlighted excerpt. They never return the entire corpus.

## Markdown pipeline

### Verified editor spike

The 2026-07-12 Chromium/Vite spike compared the actual browser candidates:

| Surface | Version and status | Observed bundle | Role |
|---|---|---:|---|
| Milkdown | `7.21.2`, MIT, direct `@milkdown/kit`, CommonMark/GFM, no required cloud | 435.70 kB raw / 131.22 kB gzip in the isolated core+CommonMark+GFM spike | Selected visual candidate |
| Tiptap Markdown | `3.27.3`, MIT; Markdown support remains Beta | Not used for the selected build | Rejected for this release |
| CodeMirror 6 | umbrella `6.0.2`, `lang-markdown` `6.5.0`, `lint` `6.9.7` | 607.53 kB raw / 207.79 kB gzip in the isolated basicSetup+Markdown+lint spike | Source mode |
| Handbook renderer | Existing repository implementation | Existing baseline | Safe read mode, not an editor |

Milkdown parse→serialize measured 0.7–5.4 ms after warm-up and 34.9 ms on the first invocation.
CommonMark/GFM meaning survived and image syntax was exact in the exercised cases, but source
formatting was canonicalized.
Unwrapped frontmatter was destroyed; wikilinks, embeds and callouts were escaped without custom
tokens; CRLF became LF and a final newline was added. Raw/unknown constructs remain Source-only.

Consequently, visual editing is a guarded codec rather than direct Milkdown serialization. It must
extract and restore the exact frontmatter, line-ending and final-newline envelope, preserve raytsystem
wikilink/embed/callout tokens, and compare the exact per-document round trip before enabling Save.
Any mismatch blocks visual saving and leaves Source mode available.

The selected packages are pinned in `web/package.json` and resolved in `web/package-lock.json`.
The 2026-07-12 full and production-only `npm audit` runs both reported zero known vulnerabilities
across the 443-package resolved dependency graph. The generated production notice is checked
against the lockfile by `npm --prefix web run licenses:check`.

The 2026-07-13 Vite build confirms separate lazy editor chunks: Visual is 456.48 kB raw / 137.45 kB
gzip; Source is 607.69 / 207.86 kB. These current build artifacts and the isolated spike use
different entry points, so their numbers are intentionally not presented as the same measurement.
The same build reports a 744.00 / 206.64 kB main chunk and emits the Vite `>500 kB` warning;
further application/source-editor splitting remains performance work even though both editors are
loaded lazily from the Documents surface.

### Read mode

The server returns raw Markdown plus typed, bounded metadata. A bounded browser parser directly
constructs allowlisted React elements and never inserts raw HTML. It supports qualified
CommonMark/GFM headings, emphasis, strike,
links, lists, task lists, tables, code, quotes, rules and footnotes plus raytsystem wikilinks and
callouts. Raw HTML is displayed as inert text or an explicit unsupported block. Link/image targets
pass a scheme and document/asset-ID resolver.

### Source mode

CodeMirror 6 edits the exact string loaded from disk. The current `basicSetup` integration provides
Markdown syntax highlighting, line numbers, search/replace, bracket matching and keyboard
navigation; raytsystem diagnostics mark constructs excluded from visual editing. It preserves line
endings and final-newline state unless the user changes them. Drafts stay in memory/session storage
and save only on an explicit action or `Cmd/Ctrl+S`. A separate custom frontmatter/wikilink syntax
theme and a user-facing line-number preference are not claimed.

### Visual mode

Milkdown is enabled only when the server-side syntax gate and the browser codec both permit it.
The browser performs the decisive parse→serialize comparison against the exact original bytes
after creating the actual Milkdown editor.
The corpus covers CommonMark/GFM, frontmatter, wikilinks, embeds, callouts, footnotes, tags,
aliases, raw HTML/unknown constructs, Cyrillic, emoji, CRLF/LF and final newline. Per-document
serializer profiles preserve qualified homogeneous list markers/indentation, thematic breaks and
GFM table padding/alignment.

The visual pipeline preserves the original frontmatter envelope and restores supported raytsystem
extensions. A per-document profile qualifies canonical homogeneous list markers, thematic breaks
and simple table layout in the exercised corpus. Tight/mixed lists, noncanonical nested indentation,
raw HTML and unknown syntax remain Source-only because Milkdown can normalize them. Strict byte
equality blocks Visual Save when the initial editor cycle changes the document. The UI never claims
full Obsidian plugin compatibility.

### Diff mode

Diff compares the opening base and local draft; the conflict presentation adds the current disk
version when it is safely available. Line-level additions and deletions are bounded; large inputs
fall back to a coarse sampled representation instead of rendering an unbounded diff.

## Typed API

The route family is versioned under `/api/v1/documents`. Static operations are registered before a
dynamic document-ID route.

| Operation | Contract |
|---|---|
| List/tree/status | Snapshot-bound, paginated metadata; lazy folder expansion |
| Search/recent | Bounded filters, sort and cursor; no corpus dump |
| Detail | Document ID + snapshot; content, policy and SHA-256 |
| Links/backlinks | Resolved IDs, ambiguity and bounded context |
| History/revision/diff | Git/local sources, hashes and bounded content |
| Index refresh/rebuild | Explicit same-origin mutation; disposable plane only |
| Create/update | Root/folder/document IDs, expected snapshot/hash, idempotency |
| Rename/move/create-folder | Typed IDs and basename; destination must be writable and absent |
| Graph focus | Document ID; returns a snapshot-bound, bounded document-link neighborhood |

The update body carries `document_id`, exact new Markdown, `expected_sha256`,
`expected_snapshot_id` and format. The document hash and stable identity are the single-document
CAS authority. Unrelated global projection drift may be accepted by operations with scoped rebase
and is disclosed as `snapshot_rebased`; changed document bytes or identity return a conflict. A
conflict includes only safely available base/disk/draft data and never merges it.

## Write protocol

1. Authenticate the local session and verify Host/Origin/CSRF/idempotency.
2. Validate request bounds before decoding or logging content.
3. Resolve document/folder/root IDs entirely on the server.
4. Re-evaluate root mode, hard protection and sensitivity.
5. Open parent/file using no-follow descriptor-relative operations.
6. Compare the current bytes, SHA-256, snapshot and stable file identity.
7. Write and fsync a same-directory temporary file or use no-replace create.
8. Recheck the target/destination and atomically replace/rename without overwrite.
9. Append immutable revision provenance, idempotency receipt and hash-only audit event.
10. Refresh the affected index/link rows and publish a new document snapshot.

Prepared operation records and idempotency receipts let a retry with the same key reconcile tested
create/update/move/folder publication failures against the actual disk state. Recovery never guesses
which content should win. The no-follow replace/move paths rebind the opened inode and hash
immediately before publication so an external target swap becomes a conflict.

## Wikilinks and graph

The resolver supports `[[Document]]`, aliases, headings and the image-embed marker. Exact path/title/
alias matches resolve directly; ambiguous matches return candidates. Backlinks store source document,
target document, heading and bounded surrounding context.

Universe already represents claims, entities, sources and evidence as distinct canonical graph
types. The Documents focus slice currently adds distinct manual/documentation/generated document
types and resolved document-link edges only. Projecting typed document→claim/entity associations is
deferred; adding such an edge later still must not promote the note or make its prose verified.

## Performance and accessibility budgets

- `tests/test_documents_performance.py` opt-in profiles all passed in the reviewed 2026-07-13 run:
  rebuild/search were 0.266/0.002 s for 100 files, 3.05/0.031 s for 10,000 and 30.75/0.386 s for
  100,000;
- every profile included one 5 MiB Markdown file, depth 48, Cyrillic and a long filename. The same
  fixture asserted actual backlinks to the Hub (98 for the 100-file case and a bounded 2,000 for
  10,000/100,000); Hub also has outgoing links, but the measured assertion calls
  `links(..., backlinks=True)`;
- only the active document is loaded into an editor;
- editor packages are lazy chunks; the measured Milkdown and CodeMirror payloads are not part of
  every Documents list/search response;
- tree and result lists are lazy/virtualized, search is debounced/cancellable and every endpoint is
  paginated;
- ordinary list/search/detail/write requests do not reread the complete vault; explicit rebuild is
  the intentional full-scan recovery operation;
- tree, tabs, toolbar, conflict dialog and drawers include explicit roles/labels and focus handling
  covered by focused frontend tests;
- keyboard-only, full screen-reader review, 200% zoom, reduced motion, touch targets and
  dark/light/contrast review remain release gates rather than completed certification.

## Experimental release boundary

The bounded M1–M5 delivery includes list/tree/search/recent views, safe Markdown reading,
properties/links/backlinks, source editing with conflicts, qualified visual editing, creation/folder/
rename/move, tabs/favorites, history/diff and graph focus.

It does not include deletion, collaboration, cloud sync, Canvas, plugin marketplace, arbitrary
filesystem access, complete community syntax, or PDF/audio/video editing. Safe local image viewing
is included. The attachment picker, insertion and upload/copy flow remain unavailable until their
MIME/path/no-overwrite tests and UI are present in the same release. A document command palette,
filesystem watcher/polling and external rename correlation are also deferred. Wikilink heading
focus is implemented in Read mode.
