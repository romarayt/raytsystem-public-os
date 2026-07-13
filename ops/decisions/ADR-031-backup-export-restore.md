# ADR-031 — Backup, export and restore

Status: implemented behind feature flags
Date: 2026-07-12

## Context

One "backup" verb hides four different disclosure surfaces: a private safety copy, a public
release export, a diagnostic bundle for a bug report and a machine-to-machine transfer. Treating
them identically either leaks restricted data outward or cripples the private copy. Concurrent
hardening work is landing in `raytsystem.backup`; this ADR documents the module together with those
in-flight fixes, including the distinct diagnostic and transfer kinds.

## Decision

- Define four typed `BackupKind`s with distinct root sets and redaction rules. PRIVATE captures
  full local state including `_raw/`, canonical knowledge and a consistent SQLite snapshot of the
  platform store, unredacted. PUBLIC captures the release surface with secret-bearing files
  removed and absolute local paths redacted. DIAGNOSTIC captures configs and ops manifests plus a
  synthesized platform status summary — never canonical knowledge — at public-grade redaction.
  TRANSFER is the private set minus machine-local state (no platform snapshot) with absolute
  paths stripped, because the bundle moves between machines.
- Abort any redacting export that still contains unresolved disclosure findings after redaction;
  partial redaction is a failure, not a warning.
- Write bundles atomically as deterministic zips (fixed timestamps, 0600 member modes, traversal
  validation) with a hash- and ID-verified `BackupManifest` and a hashed redaction report, then
  record the backup and its bundle hash in the isolated `ops/platform.sqlite` store with a
  hash-chained event. Migrations accept only backups recorded there in state `created`.
- Verify strictly before trust: bounded member counts and sizes, exact member-set match against
  the manifest, per-file hash checks and manifest/redaction hash verification.
- Restore only into an empty destination, re-verifying every member hash while staging, then
  atomically renaming the staged tree into place; an optional doctor callback reports
  passed/failed/not_run without ever masking failure. Restores always require projection rebuild.
- Include restricted encrypted blobs only in non-disclosure kinds, only when
  `restricted_encryption_enabled` is on, and only when every included file validates as an
  `EncryptedBlob`. A leak scan over any bundle reports restricted content and absolute paths.
- Fail closed: create/restore raise the backup subsystem error when `backup_enabled` is off.

## Consequences

- The disclosure level is chosen by type, not by operator memory; a diagnostic bundle physically
  cannot contain `_raw/` or the knowledge ledger.
- Bundles are self-verifying evidence: a flipped byte fails verification before restore begins.
- Scheduled backups, remote destinations and any upload of bundles are intentionally deferred;
  the subsystem writes local files only.

## Alternatives considered

- One backup format with flags: rejected; disclosure boundaries must be structural.
- Restoring over an existing workspace: rejected; merge-on-restore is unrecoverable when wrong.
- Trusting the manifest inside an unverified bundle: rejected; verification precedes every use.
