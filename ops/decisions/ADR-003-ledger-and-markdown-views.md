# ADR-003: Structured ledger and Markdown views

Status: Accepted

Date: 2026-07-10

## Context

Markdown is excellent for people but insufficient as the only validated machine state.

## Decision

Typed ledger records are canonical knowledge. Markdown pages are generated views. `knowledge/manual/` is the only human-editable zone and returns through INGEST.

## Consequences

Generated pages must carry a generation ID and be rebuildable; direct edits are rejected.
