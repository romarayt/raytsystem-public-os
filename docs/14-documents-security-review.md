# Documents security review

Status: standing release gate for the experimental Documents module

Decision: `ops/decisions/ADR-035-managed-document-workspace.md`

## Review scope

This review covers the document root configuration, filesystem scanner, disposable index, Markdown
reader/editors, local image view, typed HTTP API, DocumentService mutations, revision/history
storage, Git integration and Universe projection. Document content is always untrusted data and
never grants agent authority.

Write routes remain unavailable when a required control below is absent or unqualified.

## Assets and trust boundaries

Protected assets are user document bytes, canonical raytsystem state, Git history, local revisions,
first-seen/identity metadata, secrets and the rest of the host filesystem. The browser, Markdown,
frontmatter, filenames, tags, links, image metadata, Git author text and editor output are untrusted.

Authority comes only from the pinned workspace root, typed server configuration, compiled protected
zones, local session, current document snapshot/hash and operation-specific policy. Neither a
frontmatter field nor content resembling an instruction can broaden access.

## Threat/control matrix

| Threat | Required control | Failure behavior |
|---|---|---|
| Path traversal / absolute path | Relative-path validator; IDs resolved server-side | Typed 404/403 without path echo |
| Symlinked root/component/file | Descriptor-relative `O_NOFOLLOW`/`lstat`; reject link | Skip on scan; deny read/write |
| Unsafe hardlink | Single-link validation for document writes and indexed content | Deny operation |
| TOCTOU target swap | Recheck descriptor identity/hash immediately before atomic replace | Typed conflict; no overwrite |
| Stale or concurrent editor | Stable document ID + SHA-256 CAS + path writer serialization | Conflict for changed file; disclose scoped global-snapshot rebase |
| Duplicate request | Request-hash-bound idempotency receipt | Replay same receipt; reject key reuse |
| Oversized corpus/file/body | Root, file, total, request, result and diff byte budgets | 413/bounded projection error |
| Malformed UTF-8 | Strict decoding for editable text; preserve bytes by refusing edit | Read-only error; no replacement |
| Malicious Markdown/HTML | Bounded parser creates allowlisted React elements; no raw HTML insertion | Render inert unsupported block |
| Dangerous link | Allowlisted schemes; typed local resolver; rel isolation | Inert link or explicit warning |
| Remote/tracking image | Self-hosted typed asset endpoint only by default | Placeholder; no network request |
| SVG/data URL execution | SVG denied unless separately sanitized; data URLs denied | Placeholder / unsupported |
| YAML bomb/duplicate keys | Alias/anchor/merge rejection; depth/node/scalar budgets | Properties read-only; Source mode |
| Lossy visual transform | Server syntax gate plus exact browser parse→serialize guard | Visual Save disabled |
| Secret indexing | Filename/content sensitivity scan before FTS persistence | Hidden/redacted metadata; no FTS row |
| Protected-root write | Compiled policy floor after config resolution | No Edit action and server denial |
| Absolute path leakage | Relative paths only; redacted errors/logs/history | Generic typed error |
| CSRF/DNS rebinding | Loopback, Host/Origin, strict cookie, CSRF, JSON-only writes | Middleware rejection |
| Browser cache/history leak | `Cache-Control: no-store`; no content in URL/localStorage | Request rejected or content omitted |
| Log/trace leak | Hashes/IDs/size/status only; never Markdown or draft | Event rejected/redacted |
| Git argument/path injection | Server-owned validated path, `--` separator, fixed env/timeouts | History unavailable |
| Revision disclosure | Same policy/sensitivity gate as live document | Redacted/denied revision |

## Root policy review

- Configured roots are workspace-relative and checked beneath one pinned root.
- Compiled hidden/protected zones cannot be downgraded by configuration.
- `knowledge/manual` is the only generic-editor exception beneath generated `knowledge`.
- Repo-local skills are not Documents; their specialized editor performs its own validation.
- Files outside a configured root are absent from the API.
- Secret-bearing names and content cannot enter FTS, snippets, breadcrumbs or URL state.
- Missing, overlapping, symlinked or malformed roots fail closed rather than broadening the scan.

## Markdown and editor review

Read mode builds elements from an allowlisted syntax tree and does not use `dangerouslySetInnerHTML`.
Raw HTML, scripts, styles, iframes, object/embed tags, event attributes and executable SVG are not
mounted. `javascript:`, `data:`, credential-bearing and unsupported URL schemes are rejected.

Source mode is the compatibility fallback. Visual mode is not enabled merely because a file ends in
`.md`: the server syntax gate must allow the document and the client qualification must prove that
frontmatter, protected raytsystem tokens, line endings and final newline are preserved byte-for-byte
after the actual initial Milkdown parse→serialize cycle.
Complex/ambiguous YAML stays source-only. Paste is untrusted editor input and the same runtime
token/error checks still apply before Visual Save. Tight/mixed/noncanonical lists, raw HTML and
unknown extensions are Source-only because their serialization cannot be qualified generally.

## Write and recovery review

Atomic replacement protects one file; it does not claim filesystem-wide transactionality. The
observable command result is bound to old/new hashes and an audit/revision record. Prepared
operation records plus the same idempotency key reconcile the tested create/update/move/folder
publication-failure cases; recovery compares disk state and never repeats a write blindly.

The global document-index snapshot is a projection binding, not a substitute for file CAS. Update,
the shared move/rename path and restore may accept unrelated snapshot drift only after the stable
document identity and expected SHA-256 still match; the response exposes `snapshot_rebased`. Create
and folder creation require the prepared snapshot because destination availability is part of their
authority. A changed file or identity remains a conflict.

Create uses no-replace semantics. Rename/move refuse an existing destination and cannot cross into
a weaker or non-writable root. Restore requires preview, current expected hash, explicit confirmation
and a new audit event; it never commits to Git. Delete is unavailable.

## Index and history review

`documents.sqlite` is a derived cache and may be removed. Rebuild reads only allowed files and
durable metadata, uses a safe temporary sibling database and atomically replaces the prior index
after integrity and snapshot construction. Reads use query-only SQLite connections with trusted
schema off; FTS search additionally installs a progress deadline. A retained quarantine copy of a
corrupt index is not created in this delivery.

First-seen, identity transitions, idempotency receipts and hash-only audit metadata are durable in
`ops/platform.sqlite`; they cannot be trusted solely to the disposable DB. Local revision objects
and manifests are immutable, content-addressed and protected under `ops/document-revisions/`. They
are included only in private/workspace-transfer backup classes, excluded from public/diagnostic
exports and never copied to browser persistence. Restricted content gets metadata-only history and
no revision object until an encrypted revision policy is separately enabled and qualified. Git
history requests return bounded author display name, date, commit hash and diff; email and arbitrary
Git formatting are not exposed.

## Browser and CSP review

All assets and editor workers are bundled locally. Milkdown, ProseMirror, CodeMirror and Markdown
extensions must not require a cloud backend, CDN, remote font or analytics endpoint. CSP remains
self-only except for a narrowly reviewed first-party worker mechanism. API and document/asset
responses use `no-store`, `nosniff`, no-referrer and same-origin resource policy.

## Required adversarial evidence

The focused repository tests currently exercise traversal/absolute paths, protected roots,
symlinked files, unsafe image dimensions, bounded YAML/frontmatter, stale hash/snapshot behavior,
scoped snapshot rebase, target replacement inside atomic update/move, secret drift, revision
ownership/sensitivity, idempotent recovery after projection failure, malformed payload contracts,
same-origin/CSRF middleware and inert Markdown/URL rendering. The Milkdown corpus separately proves
exact canonical fixtures and proves that tight/mixed/noncanonical list fixtures are rejected.

This is not the same as completing every release attack simulation. A green release still requires
the full document/security suites, full project `SECURITY_REVIEW`, dependency/notices gates and
manual browser accessibility review together. The opt-in 100/10,000/100,000-file profiles passed in
the reviewed run, but that performance evidence is not a substitute for adversarial testing.

Recorded 2026-07-13 focused gates: 35 Documents backend tests passed (the single opt-in benchmark
test is skipped in the default run); web unit/layout/graph-worker suites passed 153/19/1 tests;
frontend typecheck, lint and build were green; full and production-only npm audits reported 0 known
vulnerabilities in the 443-package resolved graph; the lock-derived production notice check passed.
These focused counts do not replace the repository-wide checkpoint guard.

## Residual risk and deferred surfaces

- A process running as the same OS user can deny service or mutate files outside raytsystem's process;
  the module is a local policy boundary, not an OS sandbox.
- Secret pattern matching is deliberately incomplete; explicit sensitivity policy remains necessary.
- Visual editing cannot preserve every Obsidian/community extension and therefore fails closed to
  Source mode.
- Filesystem watcher/polling and correlation of externally performed renames are deferred. External
  changes require explicit refresh/rebuild before the projection is authoritative again.
- Collaboration, sync, deletion, arbitrary binary editing, remote images, unsanitized SVG and plugin
  execution remain unavailable.
- Image upload/copy remains deferred unless the same release includes MIME sniffing, attachment-root
  policy, no-overwrite naming, size limits, audit and adversarial tests.
- The attachment picker/insertion UI and document command palette are unavailable. Wikilink heading
  fragments resolve and focus a matching heading in Read mode.
- Document focus in Universe currently projects document-link neighbors only; typed note-to-claim or
  note-to-entity associations are deferred.
- A full keyboard/screen-reader/200%-zoom review remains release qualification work.
