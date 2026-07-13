# Execution-plane threat model

Status: implemented controls for the feature-gated first delivery  
Updated: 2026-07-12

## Protected assets and trust boundaries

Protected assets are canonical evidence and knowledge, the append-only Task Ledger, user source
files and Git history, skill source/revision history, provider credentials, approvals, run
transcripts and the host outside one managed workspace.

The browser, task text, repository content, graph labels, imported `AGENTS.md`/`SKILL.md`, model
output and CLI JSON are untrusted data. Authority comes only from typed server configuration,
current immutable bindings, policy decisions, scoped approvals and live fencing tokens.

```text
browser (untrusted fields)
  → loopback HTTP/session/CSRF boundary
  → typed execution service and policy
  → task lease + managed workspace + bounded context
  → no-shell runtime process group
  → redaction/validation
  → operational store and task review

canonical knowledge promotion: separate existing approval/promotion boundary

local skill authoring: typed skill ID → preview/CAS → atomic no-follow write → catalog re-read
```

## Security invariants

- GET/HEAD opens execution state read-only and never initializes a database, graph or workspace.
- HTTP accepts typed IDs, bounded enums, expected generation and idempotency key. It never accepts
  executable, argv, shell fragment, environment, cwd or arbitrary path.
- Skill authoring accepts content only for a typed skill ID. It derives
  `skills/<skill_id>/SKILL.md`, requires both expected catalog and source SHA-256, and refuses a
  symlink, unsafe hardlink, unexpected destination, restricted sensitivity or non-editable origin.
- Real runtime execution requires the global runtime flag, the provider flag and an `ALLOW` policy
  decision. Internal/private provider egress additionally requires an unexpired approval bound to
  payload hash, employee, task, run, workspace, destination and scope.
- Workspaces stay below `.raytsystem/workspaces`; symlinks and unsafe hardlinks fail closed. Runtime
  cwd is a server-created capability token, not client text.
- One live task owner is enforced by TTL lease, control epoch and fencing token. Every durable run
  update rechecks the current task/graph/workspace bindings and live fence.
- Runtime output cannot update `_raw/`, `ledger/`, generated knowledge or promotion pointers.
- An edited or forked skill is recorded as `test_status: pending`; saving never executes the skill,
  its tools or its workflows, and never converts a submitted `pass` into attested evidence.
- Secrets are redacted before transcript/store/UI boundaries. The operational store scans payloads,
  rejects dangerous command authority and hashes every record/receipt.
- No runtime path performs push, publish, deploy, send, deletion, payment, external-root access or
  real-corpus promotion without a separate action-specific approval.

## Threats and controls

| Threat | Primary controls | Residual risk / operator action |
|---|---|---|
| DNS rebinding / cross-site write | loopback bind, Host/Origin allowlists, same-site session, CSRF | do not reverse-proxy without a separate deployment decision |
| UI shell/argument injection | no command fields; complete adapter argv grammar; stdin prompt; no shell | new adapter options require code review and tests |
| Path traversal / cwd swap | typed managed-cwd token, fixed root, no-follow checks immediately before launch | filesystem/OS compromise remains outside process isolation |
| Symlink/hardlink race | `lstat`, no-follow reads, single-link immutable files, byte-identical manifest reuse | writable filesystem owner can still deny service |
| Malicious task/graph/instructions | content is inert context; bounded graph; scanner; no authority from text | model may produce a bad patch; human review remains required |
| Credential leak in env/output | fixed env allowlist, stdin prompt, output cap and redaction, store rescan | unknown secret formats can evade patterns; inspect before provider use |
| Provider/private-corpus egress | provider destination policy and exact expiring approval | provider itself remains an external trust boundary |
| Duplicate execution | idempotency receipt, one task lease, employee concurrency limit | an expired process is cancelled; stale fence blocks persistence |
| Stale task/graph/session | expected task generation/revision, current graph verification, workspace/session fingerprints | repository changes require refresh/new session |
| Stale skill editor | expected catalog and source SHA-256, final descriptor recheck, typed `409` conflict | compare current/proposed text and reapply manually; no automatic instruction merge |
| Skill path/symlink escape | server-derived typed path, fixed `skills/` root, `O_NOFOLLOW`, regular-file and single-link checks, guarded no-replace install plus fsync recovery journal | writable owner can still deny service; ambiguous topology or recovery state fails closed |
| Malicious `SKILL.md` | content remains inert; safe Markdown renderer without executable HTML/embeds; frontmatter, size, UTF-8, sensitivity and secret scans | prose can still be misleading; it never grants authority or runs on save |
| Unauthorized skill overwrite | server editability policy; official/pinned/generated/restricted/external sources read-only; fork is separate preview/confirm | forked local copy requires fresh verification and stays pending |
| Crash during execution | durable queued/running state, expiring lease, higher recovery fence, idempotent retry | external side effects cannot be rolled back; therefore they are pre-gated |
| Transcript/storage forgery | canonical JSON hashes, head/index binding, contiguous sequence, read-time validation | local database deletion loses operational history but not canonical knowledge |
| Permission bypass | forbidden flags at command construction and again at persistence | future bypass support requires a new ADR and exact approval; none exists now |

## Runtime-specific boundary

Codex uses its workspace sandbox and ignores user config for execution behavior. Claude is more
conservative in this delivery: empty MCP config, `dontAsk`, no Bash tool and a fixed read/edit tool
set. Both processes receive a controlled environment and run in a separate process group with
timeout, bounded output, graceful terminate and forced kill.

The CLIs may still read their own local authentication state and contact their provider when the
corresponding egress approval is valid. The deterministic fake adapter has no process or network.

## Recovery checklist

1. Disable `runtime_execution_enabled` to stop new launches.
2. Cancel visible running processes; pause the affected employee.
3. Inspect run status, transcript redaction state, task lease expiry/fence and workspace manifest.
4. Run `raytsystem doctor`, `raytsystem status`, code-graph status and the checkpoint guard.
5. If a process crashed, let the lease expire or explicitly recover it; never reuse the stale fence.
6. Rebuild graph/context when repository or graph bindings changed. Start a new session when the
   compatibility fingerprint differs.
7. Move the task to review only after diff/tests and approval bindings are verified.

For a skill-edit conflict, do not overwrite the current file or copy one side over the other.
Reopen the latest catalog revision, inspect the server diff, manually reapply the intended change,
preview again and save with fresh hashes. A successful write still remains pending until the
relevant skill verification is run and recorded.

Do not delete a workspace as a recovery shortcut. Retention/deletion policy is not part of this
delivery.
