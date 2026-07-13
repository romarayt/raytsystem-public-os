# ADR-002: Immutable raw and versioned evidence

Status: Accepted

Date: 2026-07-10

## Context

Claims require stable evidence even when parsers or normalization settings change.

## Decision

Raw bytes are content-addressed. Logical sources have immutable revisions. Normalization snapshots are keyed by source revision, adapter/version and configuration; old snapshots are never overwritten.

## Consequences

Storage grows append-only. Retention and compaction require explicit reviewed policies.
