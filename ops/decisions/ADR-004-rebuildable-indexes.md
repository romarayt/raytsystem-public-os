# ADR-004: Rebuildable indexes

Status: Accepted

Date: 2026-07-10

## Context

Search and graph engines must not become competing sources of truth.

## Decision

SQLite FTS5 is the baseline projection. QMD and NetworkX remain adapters/derived outputs and can be deleted and rebuilt from canonical state.

## Consequences

Every index requires a deterministic rebuild command and equivalence checks.
