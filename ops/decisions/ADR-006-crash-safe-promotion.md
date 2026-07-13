# ADR-006: Single-writer crash-safe promotion

Status: Accepted

Date: 2026-07-10

## Context

A promotion changes several files and may be interrupted or raced by another writer.

## Decision

Use a durable local control DB, renewable fenced operation leases, an additional global `ledger:current` fence for the `ledger/CURRENT` file, immutable objects, a PromotionTxn WAL and a complete generation manifest. Atomically replacing `ledger/CURRENT` is the canonical commit point; views, events and Git are reconciled afterward. Git commits carry event/generation trailers because an event cannot contain the SHA of the same commit that contains it.

## Consequences

The system promises observable all-or-nothing state, not filesystem-wide multi-file atomicity.
