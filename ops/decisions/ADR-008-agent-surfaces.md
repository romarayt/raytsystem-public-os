# ADR-008: Shared procedures across agent surfaces

Status: Accepted

Date: 2026-07-10

## Context

Codex, ChatGPT Work and Claude discover instructions differently.

## Decision

`AGENTS.md` is the Codex router, `WORK.md` the explicit Work bootstrap and `CLAUDE.md` a compatibility pointer. Canonical procedures live once under `skills/<name>/SKILL.md`.

## Consequences

Surface adapters stay small and testable; workflow logic does not fork by agent product.
