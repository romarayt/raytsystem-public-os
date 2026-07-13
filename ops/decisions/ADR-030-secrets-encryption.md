# ADR-030 — Restricted secrets encryption

Status: implemented behind feature and provider gates
Date: 2026-07-12

## Context

Restricted material must be storable at rest without inventing cryptography or trusting a key
backend that merely appears installed. Decryption is an authority event, not a convenience.
Concurrent hardening work is landing in `raytsystem.secrets`; this ADR documents the module together
with those in-flight fixes, including rotation and an honest keychain probe.

## Decision

- Use envelope encryption with AES-256-GCM only: a random per-blob data key encrypts the
  plaintext, the provider's wrapping key encrypts the data key, and both operations bind the
  caller's associated data. The integrity hash is domain-separated and bound to key ID and nonce,
  so equal plaintexts never produce equal identifiers across blobs.
- Require a fresh exact approval for every decrypt (`decrypt_secret`, scope `secret_decrypt`,
  hash-bound to the blob), plus a verified binding between the blob and the currently available
  provider, key ID and algorithm.
- Rotate by decrypting under approval, re-encrypting with fresh nonces and data key, bumping the
  recorded key version and actor, and verifying the rotated blob round-trips before the old bytes
  are atomically replaced. Rotation that reproduces old nonce or ciphertext is rejected.
- Confine blob files to `ops/encrypted/` with traversal-checked relative paths and atomic writes.
- Report provider status honestly. The macOS Keychain provider is AVAILABLE only after a proven
  keychain round-trip (find-or-create then read back), never because a binary exists on PATH. The
  environment provider demands a valid 32-byte key. Feature-gated status never probes or mutates a
  backend as a side effect.
- Keep external KMS a fail-closed stub: it always reports UNAVAILABLE and refuses key requests,
  and even reaching it requires `external_kms_enabled` (default off) on top of
  `restricted_encryption_enabled` (default off in the repository configuration).
- Fail closed: encryption raises the secrets subsystem error whenever the feature is off or the
  provider is not provably available. The backup subsystem refuses restricted data unless this
  feature is on, and policy simulation reports `secret_provider_unavailable` for plans requesting
  secrets while it is off.

## Consequences

- There is no ambient decrypt: every read of restricted plaintext leaves an approval trail.
- A machine without a working key backend degrades to "restricted encryption unavailable" instead
  of a false sense of protection.
- External KMS integration, a secret catalog/broker for runtimes and automatic rotation schedules
  are intentionally deferred; today only the encryption boundary and manual rotation exist.

## Alternatives considered

- Custom or password-derived encryption: rejected; AES-GCM via the vetted cryptography backend or
  nothing.
- Treating `security` on PATH as keychain availability: rejected; only a round-trip proves it.
- Cache-friendly decrypt sessions: rejected; per-operation approvals keep authority explicit.
