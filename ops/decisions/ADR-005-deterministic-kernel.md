# ADR-005: Deterministic kernel validates semantic proposals

Status: Accepted

Date: 2026-07-10

## Context

Prompts cannot guarantee integrity, idempotency, access control or recovery.

## Decision

Models emit typed proposals only. Project code owns hashing, schemas, policy, leases, validation, promotion and recovery. A model-neutral export/import path works without an API key.

## Consequences

Semantic output that does not satisfy contracts cannot enter canonical state.
