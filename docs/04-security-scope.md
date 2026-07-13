# Security and sensitivity scope

Status: M5a synthetic qualification

## Deterministic scanner

`raytsystem_secret_patterns@1.2.0` is a high-confidence fail-closed scanner. It detects:

- private-key PEM headers;
- common AWS, GitHub, GitLab, OpenAI, Anthropic and Slack credential shapes;
- bearer tokens, JWTs, credential-bearing URLs and explicit secret assignments;
- direct email addresses and E.164 international phone numbers.

The scanner runs before normalization and again on decoded/derived content, imported proposals,
promotion candidates, generated knowledge/audit files, SAVE drafts, staged Git bytes and filenames.
A scanner exception or an unknown decision quarantines the input rather than downgrading to a
weaker path. Domain-specific M4 scanner coverage is archived evidence, not an active runtime claim.

Declared zero-leak scope for G6 is the exact M5a candidate-file manifest, generated knowledge,
events, run manifests, staging outputs, draft artifacts and retained qualification reports.
Historical tracked source/config files and ephemeral terminal output are not silently counted as
scanned. `_raw/restricted/` is excluded from that scope by design and is Git-ignored with `0700`
directories and `0600` files.

## Limitations

This is not a universal DLP engine. It does not claim complete detection of:

- names, postal addresses, national identifiers or non-E.164 local phone formats;
- arbitrary passwords, entropy-only secrets or every vendor-specific token;
- obfuscated, encrypted, image-only or unsupported binary content;
- text that an unavailable OCR, ASR or archive decoder would have revealed;
- semantic re-identification from otherwise innocuous facts.

Email/E.164 detection is deliberately conservative and may quarantine public contact details. PII
without a high-confidence match must be declared explicitly; agent policy then blocks hosted
review and model egress for the whole payload.

Restricted quarantine is access-controlled and Git-ignored but is not application-encrypted in
M5a because no approved key provider exists. Therefore M5b pilot inputs must be public/internal and
free of secrets/PII unless the user separately approves a key destination and encrypted restricted-
raw adapter. Restricted bytes are never normalized, promoted, sent to a model or staged in Git.

## Disabled attack surfaces

- Live HTTPS and `yt-dlp` Fetchers are fail-closed unavailable. M5a accepts already captured HTML
  and other explicitly supported workspace-local bytes through offline Extractors. No
  SSRF/redirect/DNS adapter is claimed as tested. Captured HTML drops executable/template and
  deterministic hidden/inline-hidden subtrees,
  but does not render external CSS or JavaScript; any remaining text is still untrusted data.
- Archive extraction and webhooks are unavailable. Tests prove their entry points deny work before
  raw/canonical writes; this is default-deny evidence, not functional qualification.
- OCR, ASR, QMD model assets and additional hosted providers are unavailable pending separate
  artifact/license/destination approval.
- Process `SIGKILL` and injected ENOSPC tests cover software crash recovery, not physical storage
  loss, filesystem corruption or power-loss durability.

These limitations are acceptance boundaries for `READY_FOR_USER_PILOT`, not hidden production
claims.

## Platform subsystem boundaries

The evaluation/observability/governance/lifecycle subsystems keep the following surfaces
fail-closed (see `docs/12-platform-capabilities.md` and ADR-020…ADR-032):

- OTLP export, external notifications, Promptfoo remote generation, external MCP execution, A2A
  network exposure and external KMS are off by default; the A2A gateway additionally refuses
  remote exposure even when its flag is set.
- Promptfoo configs are treated as executable code: JS/Python assertions and exec-like providers
  are rejected unconditionally; the adapter validates and never executes.
- Traces, notifications and MCP invocation records pass the deterministic secret scanner before
  persistence; public exports refuse on unresolved leak findings and strip absolute local paths.
- Restricted-blob encryption reports an honest `unavailable` state: the `cryptography` backend is
  deliberately not installed, so no data is ever claimed to be encrypted. Sensitive ingest remains
  blockable by policy while no key provider exists.
- Emergency `revoke_pending_approvals` invalidates approvals issued before its activation time;
  security circuit breakers latch open and are only closable with a fresh scoped approval.
- Workspace restore refuses traversal members, non-empty destinations and hash mismatches;
  workflow `deterministic_command` nodes resolve only registered operation IDs, never raw shell.

## Managed Documents boundary

The experimental Documents module is a separate, policy-defined state plane. The browser addresses
opaque document/root IDs; it never supplies an absolute path. Configuration may narrow access but
cannot make `_raw`, `normalized`, `ledger`, generated `knowledge`, operational ledgers, `.git`,
`.raytsystem`, secrets, package locks or repo-local skills writable. `knowledge/manual` is the only
generic-editor exception below generated knowledge.

Reads reject symlinked components and hide secret-bearing content from FTS. Writes additionally
require the local session, exact Origin, CSRF, idempotency, expected projection snapshot and current
content SHA-256. The service rechecks policy and file identity immediately before an atomic,
no-overwrite operation. Stale state returns a three-way conflict and never invokes an automatic
merge. Markdown/HTML and frontmatter remain untrusted data: read mode creates allowlisted React
nodes, remote/data images are disabled, complex YAML and lossy Markdown fall back to Source mode.
See ADR-035 and `docs/14-documents-security-review.md` for the release gate.
