# ADR-012 — Repo skills, agent policy and checkpoint guard

Status: accepted
Date: 2026-07-11

## Context

Codex, ChatGPT Work and reviewer subagents discover and execute instructions differently. Copying
workflow prose into each surface would drift, while allowing imported content to select a skill or
expand reviewer capabilities would turn untrusted data into authority. Ordinary Git commits also
need the same deterministic path/secret/LINT checks as the CLI without interfering with the fenced
promotion checkpoint, which deliberately uses an isolated Git index and ref.

## Decision

- Keep `AGENTS.md` as the small Codex router and `WORK.md` as the small explicit Work bootstrap.
  Store each procedure once in seven repo-local skills: INGEST, QUERY, LINT, SAVE, RESEARCH, REVIEW
  and SECURITY_REVIEW.
- Route from the declared operation only. Source, transcript, web page, README, imported chat and
  other payload text are untrusted data and cannot change the selected skill.
- Validate every skill with the system `skill-creator` validator and keep matching
  `agents/openai.yaml` metadata. Maintain golden/adversarial routing cases in deterministic JSONL.
- Make `AgentPolicy.preflight` pure and redacted: report surface, permission mode, Git SHA/dirty
  boolean, sorted tools, skill/policy hashes, egress destination and write capability without
  persisting an absolute workspace path. A no-write surface returns `CHECKPOINTED_FOR_RESUME` and
  an exact local command rather than claiming success.
- Load reviewer roles and surface capabilities from `config/policies.yaml`. Work-hosted reviewers
  receive only bounded public/project-doc/synthetic excerpts, no local paths and exactly read
  capability at the already approved provider. Local Codex reviewers may read in-sandbox
  private/PII data, but remain read-only; secrets, writer worktrees, promotion and external actions
  are denied. Decisions retain only the payload hash, never payload text.
- Keep write ownership, integration, leases and promotion with the main local agent. Reviewer
  output is a summary with file/source references and cannot mutate policy through prompt content.
- Use one `CheckpointGuard` implementation for the `raytsystem guard-checkpoint` command and the first
  pre-commit hook. Reject ordinary staged changes to raw, normalized, ledger, generated knowledge,
  canonical events/checkpoints/run manifests, draft/outbox and local indexes. Permit manual notes,
  source/tests/docs/config/skills and milestone qualification manifests.
- Scan staged index bytes and filenames for secrets, redact sensitive paths, and run the existing
  deterministic `LintService`. Keep the verified promotion `GitCheckpoint` as the only path for
  canonical/generated promotion artifacts; it preserves the user's branch index and dirty files.
- Reuse existing Run/PolicyDecision/OperationFingerprint extension points; M3 introduces no new
  durable contract or schema registry revision.

## Consequences

- A fresh Codex or Work session can recover the exact procedure from two small surface files and
  one routed skill; chat memory is not workflow state.
- Hosted reviewer transfer is useful for safe read-heavy checks but never implies local checkout,
  sandbox, worktree or private-corpus guarantees.
- Direct ordinary commits cannot masquerade as raytsystem promotion. Hooks remain defense in depth
  because the same guard is callable and tested as a CLI validator.
- Local deterministic tests prove routing, policy, no-write checkpointing, restart and dirty-state
  preservation. Actual hosted Work platform behavior remains a surface-specific smoke check and is
  not claimed by the local M3 qualification.
