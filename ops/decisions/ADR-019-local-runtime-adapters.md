# ADR-019 — Feature-gated local runtime adapters

Status: implemented behind feature and policy gates
Date: 2026-07-12

## Context

raytsystem needs deterministic tests and optional Codex/Claude CLI execution. A generic process
adapter, raw UI command or inherited environment would turn the control plane into a shell/RCE
surface. Provider CLIs also create an egress boundary even when their process runs locally.

## Decision

- Define a typed adapter protocol with health, command preparation, execution, cancellation and
  resume. Ship a deterministic in-process fake adapter for integration tests.
- Build Codex argv from a complete allowlisted grammar: fixed executable, `exec --json`, managed
  `-C`, `--sandbox read-only|workspace-write`, `--ignore-user-config`, no inherited shell
  environment and prompt on stdin. Never add a dangerous bypass flag.
- Keep Claude behind its own feature and exact Anthropic egress approval. Use `--bare`, empty MCP
  config, `dontAsk`, a fixed non-Bash tool set and prompt on stdin. Never use
  `--dangerously-skip-permissions`.
- Resolve the executable locally, pass a small fixed environment allowlist, use no shell, start a
  new process group, cap time/output, redact before persistence, terminate gracefully and then kill
  the process group after the configured grace period.
- Bind resume to employee, task, runtime, workspace, repository commit, instructions, policy,
  graph and context fingerprints. Incompatibility starts a new session with a typed reason.
- Require a policy decision before invoking any real adapter. Provider egress, external roots,
  push/publish/send/delete/payment and canonical promotion require separate exact approvals.
- Keep `runtime_execution_enabled`, `codex_local_enabled` and `claude_local_enabled` false in the
  repository configuration.

## Consequences

- There is no HTTP or task field capable of supplying an executable, argv, environment or cwd.
- Missing Claude/Codex binaries degrade only that adapter's health; the control plane continues.
- Local CLI does not mean no egress. Private/internal provider execution remains approval-bound.
- A future tool capability requires an explicit command-grammar and policy change, not a free-form
  config entry.

## Alternatives considered

- Paperclip's generic process adapter/default bypass settings: rejected as incompatible with the
  raytsystem trust boundary.
- Shell command strings: rejected because quoting is not an authorization model.
- Inheriting the complete host environment: rejected because it leaks secrets and authority.
