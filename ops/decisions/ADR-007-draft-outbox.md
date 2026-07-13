# ADR-007: Draft/outbox for external actions

Status: Accepted

Date: 2026-07-10

## Context

Retries and prompt injection make direct side effects unsafe.

## Decision

All external actions are first represented as typed draft outbox records. Send, publish, upload, delete, pay, push, PR and release always require explicit scoped approval and an idempotency key.

## Consequences

M0–M5a contain no executor that performs those side effects.
