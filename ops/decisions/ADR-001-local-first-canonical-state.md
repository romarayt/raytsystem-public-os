# ADR-001: Local-first canonical state

Status: Accepted

Date: 2026-07-10

## Context

Knowledge must remain inspectable, portable and recoverable without a hosted service.

## Decision

Exact raw, immutable typed records, generation manifests and the `ledger/CURRENT` pointer are canonical. Git stores safe metadata and green checkpoints. SQLite, Markdown views and search indexes are rebuildable.

## Consequences

The system works without a server or API key. Multi-user and remote coordination are deferred.
