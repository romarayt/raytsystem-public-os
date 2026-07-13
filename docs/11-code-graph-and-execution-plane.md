# Code graph and digital-employee operations

Status: first safe delivery; real runtimes disabled by default  
Updated: 2026-07-12

## What is implemented

- native derived code graph with incremental update/rebuild and bounded query, explain, neighbors,
  shortest-path and impact operations;
- graph-first routing for architecture queries with explicit stale/fallback reporting;
- typed digital employees projected from inert catalog definitions;
- one Agents read projection that joins definition and execution state by stable ID without
  collapsing catalog/execution storage ownership;
- linked assignments, graph scopes, workspaces, fenced task leases, sessions, runs, comments,
  budgets, approvals and transcripts;
- deterministic fake runtime and reviewed Codex/Claude local adapter implementations;
- manual heartbeat orchestration, pause/resume/cancel and crash recovery primitives;
- loopback typed API/UI projections; no generic process or path endpoint.

raytsystem remains the only control plane. `ops/task-ledger/` remains the only task journal. The code
graph and execution database are operational/derived state and never replace `ledger/CURRENT`.

## Agent read projection

The browser treats Agent as one user entity with two independently owned branches:

```text
AgentViewModel
├── definition: AgentDefinition | null       # catalog plane
└── execution: DigitalEmployeeView | null   # execution plane
```

The join uses only stable `agent_id` / `employee_id` and `agent_definition_id`. Display names,
translations and list positions are not identity. A definition without execution remains visible
as catalog-only. An execution record without its definition, a mismatched binding or duplicate
records remain visible as degraded diagnostics. A disabled runtime or uninitialized execution
store changes readiness on the same Agent; it never creates a second list.

The specialized detail surface keeps Overview, Instruction, Skills, Runtime, Access and History
separate. It returns only sanitized relative context references, typed permissions and safe
operational summaries; credentials, absolute paths, restricted payloads and raw transcripts do not
cross the API boundary. Tool/workflow/history relationships are shown only when typed metadata is
available, not inferred from instruction prose.

## Skill authoring and execution separation

Skill authoring is a local catalog mutation, not runtime execution. Eligible `pack_local` skills
can be previewed and saved through a CAS-bound service; read-only official/pinned content can be
copied only through a separate preview-and-confirm fork. Restricted or unverified-origin content
cannot be copied. Every save/fork creates a pending revision and audit event, re-reads the file
through `CatalogService` and returns the new source/catalog hashes. It does not launch an employee,
run a skill, invoke Tool Hub, contact a provider or mutate a workflow/task.

## Safe defaults and feature flags

The repository configuration is intentionally non-executing:

```toml
[features]
code_graph_enabled = true
graph_first_query_enabled = true
digital_employees_enabled = true
task_workspaces_enabled = true
runtime_execution_enabled = false
codex_local_enabled = false
claude_local_enabled = false
heartbeats_enabled = true
scheduled_heartbeats_enabled = false
```

`runtime_execution_enabled` and the selected provider flag are both required. A provider run also
needs policy approval for its egress destination. Scheduled heartbeats are not part of the first
delivery; changing their flag alone does not create a scheduler.

## Code-graph lifecycle

```bash
uv run raytsystem graph status --json
uv run raytsystem graph update --json
uv run raytsystem graph rebuild --json
uv run raytsystem graph query "How does task checkout work?" --depth 2 --json
uv run raytsystem graph explain TaskService --json
uv run raytsystem graph neighbors TaskService --depth 1 --json
uv run raytsystem graph path TaskService ControlDB --json
uv run raytsystem graph impact src/raytsystem/tasking.py --json
uv run raytsystem graph benchmark --json
```

Update/rebuild are explicit writes. Status and query operations are read-only. A source/config/
extractor change makes the current graph stale; graph-first workspace preparation then fails closed
until update or rebuild succeeds.

The same operations are available in the existing Universe as the Russian **Код** lens. It adds
node, relation and confidence filters; one/two-hop graph query; communities; god/bridge nodes;
changed-file filtering; shortest path; impact; freshness/progress states; and a semantic HTML table
fallback. The Inspector exposes bounded graph actions. “Open source” remains disabled until a
reviewed local editor adapter exists; the UI never assembles or executes shell commands.

`status` performs a content-hash freshness check and returns bounded changed/deleted relative
paths. `update` reuses only semantically validated per-file cache entries. `rebuild` ignores the
cache. Both write immutable snapshot/manifest objects, then swap `.raytsystem/graph/CURRENT` under a
renewed fenced lease; an abandoned or prepared WAL never masquerades as a current build.

Snapshots live below `.raytsystem/graph/` and are disposable. Do not create or commit
`graphify-out/`.

To disable the plane without deleting its rebuildable artifacts:

```toml
[features]
code_graph_enabled = false
graph_first_query_enabled = false
```

To reset it, stop the local UI, remove only `.raytsystem/graph/`, restore both flags and run `uv run
raytsystem graph rebuild --json`. Never remove or rewrite canonical ledger objects for a graph reset.

## Lifecycle hooks

The first pre-commit hook remains `raytsystem-checkpoint-guard`. raytsystem adds explicit local
`post-commit` and `post-checkout` stages that run `raytsystem graph update --json`. The hooks are
incremental, local-only and safe to rerun; they do not install themselves, change global Git/Codex
configuration or invoke Graphify. A failed hook leaves the prior derived snapshot readable but
stale.

## Security boundary

- fixed roots and files come only from `config/raytsystem.toml`;
- descriptor-based reads reject traversal, symlinks, hardlinks, protected zones and secret-bearing
  filenames;
- parser workers have timeout, CPU/address-space/file-descriptor/output limits;
- extracted labels/metadata are bounded and secret-shaped values are redacted before cache or API;
- cache, manifest and snapshot loads verify canonical bytes, hashes, deterministic IDs, source
  provenance, edge closure, community closure and configured limits;
- graph query output is seed-first but fails closed if even the minimum result exceeds the hard
  byte budget;
- API traversal accepts typed node IDs, never a filesystem path, cwd, argv or command.

## Reproducible benchmark

`benchmarks/codegraph/questions.jsonl` contains the declared raytsystem architecture questions and
expected source paths. `raytsystem graph benchmark` compares a bounded ordinary lexical/file-read
baseline with the same code-graph byte limit, reports hashed questions, context bytes, source-file
reads, search operations, coverage, source-reference accuracy, latency, fallback count and stale
failures. The command refuses a stale graph. Results are observations, not marketing claims; the
40% context-reduction target is reported as pass/fail without adjustment.

## Workspace lifecycle

1. Select a current task and employee by typed ID.
2. Verify task generation, employee configuration, Git commit and current graph.
3. Derive a workspace ID and graph scope.
4. Create a detached worktree below `.raytsystem/workspaces/<workspace-id>/repo`.
5. Write immutable manifest and bounded context files; create empty artifacts/log directories.
6. On retry, verify exact bytes and Git/graph bindings. Never overwrite drift.
7. Retain the workspace until a future explicit retention policy authorizes cleanup.

The browser never supplies the directory. `workspace_root_readonly`, `task_worktree` and
`approved_external_root` exist as typed modes; external roots remain unavailable without a
separate approval path.

## Heartbeat and lease lifecycle

```text
manual trigger
→ feature / policy / budget preflight
→ atomic task checkout (TTL + epoch + fence)
→ task running
→ workspace and graph freshness
→ invocation/session compatibility
→ adapter execute or resume
→ bounded redacted transcript and usage
→ lease renew/release
→ task review, blocked or cancelled
```

The same idempotency key cannot create a second run. One task has one live owner; one employee has
one active run by default. Pause/cancel signals the process group and releases or revokes the lease.
After a crash, a new owner receives a higher fence; output from the stale fence cannot persist.

## Session lifecycle

A session fingerprint binds employee, task, adapter, provider/model, managed workspace, repository
commit, instruction bundle, policy, graph and context. Resume is allowed only on an exact compatible
fingerprint and resumable status. Otherwise raytsystem creates a new session and records a typed reason
such as `workspace_changed`, `graph_changed` or `compatibility_fingerprint_changed`.

## Budgets and approvals

Budgets can bind employee, task, project, run or workspace and track input/output/cached tokens,
estimated/actual cost, run count and heartbeat count. A hard limit blocks a new run; token limits do
not depend on approximate currency conversion.

Runtime approvals are exact, expiring records. Provider execution binds action, payload hash,
employee, task, run, workspace, destination and scope. Changed content, workspace or destination
invalidates the approval. Permission bypass, external roots, push/publish/send/delete/payment,
private-corpus egress and real-corpus promotion never share a generic approval.

## Operator verification

```bash
uv run pytest
uv run ruff check .
uv run mypy
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run test
npm --prefix web run build
uv run raytsystem doctor
uv run raytsystem status
uv run raytsystem guard-checkpoint --json
```

The graph-first benchmark is deterministic and model-free; it compares the same declared questions
and references under lexical baseline and graph retrieval. Its measured reduction/coverage must be
reported as observed. Do not treat the 40% target as a forced pass condition.

## Migration notes

- Contract schema version is `1.4.0`; earlier schema directories remain immutable.
- Existing tasks are unchanged. Execution state is stored as linked records, not inserted into old
  task objects.
- Existing task and knowledge pointers keep their meaning.
- Runtime flags default off, so upgrading does not launch a process or contact a provider.
- GET/HEAD does not create execution tables. The execution schema initializes only on an explicit
  write path in the existing control database.
- Skill save/fork initializes only its local authoring revision/audit state. It writes no
  execution session, task or canonical knowledge state, and every new revision starts with
  `test_status: pending`.

See ADR-016 through ADR-019, ADR-034 and
[`docs/10-execution-security.md`](10-execution-security.md).
