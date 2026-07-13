# Platform capabilities: evaluation, observability, governance, lifecycle

Status: implemented alongside the code-graph and execution planes; every capability is
feature-gated in `config/platform.yaml`, stores its operational state only in the isolated
append-only `ops/platform.sqlite` store and fails closed when its flag, configuration or store is
missing. Canonical knowledge (`_raw/`, `ledger/`, generated `knowledge/`) is never written by any
subsystem described here.

## Feature flags

`config/platform.yaml` declares all platform flags; the loader rejects unknown, missing or
non-boolean flags and enforces the dependency chain (a child capability cannot be enabled without
its parent). Flags that gate an external surface stay off:

| Flag | Default | Gates |
|---|---|---|
| `evals_enabled` | on | deterministic eval runner, baselines, comparisons |
| `promptfoo_adapter_enabled` | **off** | Promptfoo config validation adapter |
| `telemetry_enabled` | on | local trace/span recording |
| `otel_export_enabled` | **off** | OTLP/JSON export to a local file (plus egress approval) |
| `replay_enabled` | on | replay/fork planning and comparison |
| `policy_simulator_enabled` | on | dry-run policy evaluation |
| `emergency_controls_enabled` | on | kill switch, circuit breakers |
| `mcp_governance_enabled` | on | MCP catalog (catalog-only) |
| `acp_adapter_enabled` | **off** | ACP session boundary |
| `a2a_gateway_enabled` | **off** | loopback-only A2A contracts |
| `pack_lifecycle_enabled` | on | pack quarantine/validate/approve/install/activate/rollback |
| `workflow_engine_enabled` | on | typed workflow DAG engine |
| `notifications_enabled` | on | local inbox |
| `external_notifications_enabled` | **off** | outbox contract transitions (nothing ever sends) |
| `restricted_encryption_enabled` | **off** | restricted blob encryption |
| `backup_enabled` | on | backup/export/restore |
| `promptfoo_remote_generation_enabled` | **off** | remote-feature Promptfoo configs |
| `external_mcp_execution_enabled` | **off** | MCP tool invocation |
| `a2a_network_exposure_enabled` | **off** | refused even when set: the gateway is loopback-only |
| `external_kms_enabled` | **off** | external KMS key providers |

## Evaluation laboratory

- 17 deterministic assertion types (exact/contains/regex/JSON-schema/file/hash/artifact/test/
  exit-status/citation/source-location/task-transition/approval-compliance/forbidden-action/
  budget/no-secret-leak/no-protected-path).
- LLM judges are a separate `EvalJudge` contract, optional and disabled by default; a
  non-deterministic `EvalAssertion` cannot be constructed.
- Baselines are immutable and hash-bound; acceptance requires an explicit scoped approval
  resolvable by `AuthorityResolver`; a forged or tampered baseline fails verification on compare.
- Regressions produce an `eval_regression` inbox notification and can be explicitly rejected via
  `raytsystem eval reject`, recording an `EvalFinding`.
- The Promptfoo adapter is validation-only: code-executing assertions and exec-like providers are
  rejected unconditionally; remote generation/sharing/telemetry keys require the dedicated flag.
- CLI: `raytsystem eval self-test | list | baseline | compare | reject`.

## Observability

- Local trace model: task trace → run span → model/graph-query/retrieval/tool/filesystem/
  approval/test/artifact spans, with parent and hierarchy validation.
- Secrets are redacted before persistence; attribute count and byte budgets come from
  `config/platform.yaml` policy; list endpoints never expose raw payloads.
- OTLP export writes OpenTelemetry-protocol JSON to a local file only, requires
  `otel_export_enabled` plus a destination-bound `export_traces` approval
  (`raytsystem trace export-fingerprint` prints the identity an approval must cover).
- CLI: `raytsystem trace list | detail | export-fingerprint | export-otlp`.

## Replay, fork and compare

- Plans pin the original run's snapshot, generation, instruction/skill/policy hashes and issue a
  new run ID linked to the original; original approval IDs are never carried over.
- Side-effecting steps are either bound to a recorded result hash or marked as requiring a fresh
  approval; nothing re-executes external side effects.
- `RunComparison` populates token/cost/latency/tool-call deltas and eval deltas where records
  exist and explicitly marks unavailable dimensions.
- CLI: `raytsystem replay plan | fork | compare | list`.

## Policy simulator

- `raytsystem policy simulate PLAN.json` and the web «Проверить запуск» action evaluate the exact
  same `evaluate_execution_policy` engine the runtime preflight uses, merge simulator-specific
  boundaries (workspace roots, tools, secrets, side effects, emergency state) and echo the full
  plan facts (employee, task, runtime, provider, model, workspace mode, roots, network, scopes,
  budgets, approvals, policy hash).
- The simulator performs no writes, creates no workspace, calls no model and issues no secrets;
  a present-but-unreadable platform store blocks the simulation.

## Emergency controls and circuit breakers

- Nine global actions with machine-enforcement surfaces: runtime execution, employee pause,
  run cancellation and budget stop gate the execution plane (`assert_runtime_allowed`); network,
  provider, task-checkout and session gates are exposed for their surfaces; pending-approval
  revocation is enforced inside `AuthorityResolver` by activation-time cutoff.
- Circuit breakers cover the twelve configured triggers; security breakers latch and can only be
  closed manually with a fresh approval; non-security breakers auto-recover at most a bounded
  number of times.
- Every transition appends hash-chained audit events; recovery always needs a fresh approval.
- CLI: `raytsystem emergency activate | status | recover | close-breaker`.

## MCP governance

- Catalog-only: servers, tools, resources and prompts are recorded with pinned versions and
  hashes; states are `discovered → quarantined → validated → approved → enabled/disabled/
  degraded/blocked` with monotonic guards.
- Per-tool policy defaults to `catalog_only`; invocation is impossible while
  `external_mcp_execution_enabled` is off, and enforces input/output byte limits, timeouts and
  result redaction when it ever runs.
- CLI: `raytsystem mcp list | validate | approve | transition`.

## Protocols (ACP / A2A)

- ACP: capability negotiation, hashed rotating resume tokens, session lifecycle
  (ready/streaming/cancelled/closed/failed), event sequencing and permission checks; refuses
  sessions with no execution-session record. Deep lease/budget integration remains with the
  execution plane's runtime interface.
- A2A: loopback-only contract boundary — agent-card projection, submission, status, cancellation
  and quarantined local proposal mapping; incoming tasks are never trusted and network exposure is
  refused even if the flag is set.
- CLI: `raytsystem protocols status`.

## Pack lifecycle

- `discover → inspect → quarantine → validate → evaluate → approve → install → activate →
  update → rollback`; installation never activates; activation re-verifies the pinned tree hash
  and supersedes the prior revision; rollback promotes a prior installed revision as a new active
  head without deleting history.
- Approval verifies referenced eval runs exist and passed; unsigned packs require an explicit
  `unsigned_pack` approval scope; dependencies must be pinned.
- CLI: `raytsystem package discover | inspect | update | validate | approve | install | activate |
  rollback`.

## Workflow DAG

- Typed nodes (`task`, `agent`, `deterministic_command`, `review`, `approval`, `condition`,
  `wait`, `artifact`, `notification`, `subworkflow`); `deterministic_command` resolves only
  registered operation IDs — raw shell strings fail validation.
- Cycle rejection, per-node retry policies, timeout enforcement, approval gates bound to fresh
  approvals, explicit pause/resume/cancel with state guards, persisted step outputs for crash
  recovery, and DAG graph data for the UI.
- CLI: `raytsystem workflow list | approve | cancel`.

## Notifications

- Producer API (`emit`) with dedup keys; eval regressions, emergency activations and migration
  plans emit inbox entries; transitions (`read`/`acknowledged`/`resolved`) are feature-gated and
  snapshot-bound in the web UI.
- The external outbox is contract-only: destination allowlist
  (`policy.notification_destinations`, empty = refuse all), per-send egress approval, bounded
  retries into `dead_letter`; no sending code exists in this release.
- CLI: `raytsystem notifications list | transition`.

## Secrets encryption

- `KeyProvider` interface with macOS-Keychain and environment providers; a provider reports
  `available` only after a proven round-trip; a missing provider yields an honest `unavailable`
  state and the system never claims data is encrypted.
- Envelope encryption uses AESGCM with authenticated tags, atomic writes, decrypt approvals and
  key rotation. The `cryptography` package is intentionally **not** a dependency yet; until it is
  approved and installed every provider reports `unavailable` and restricted encryption stays off.
- CLI: `raytsystem secrets-status`.

## Backup, export and restore

- Four kinds with distinct root sets: private backup (full, includes the platform-store
  snapshot), public release export (redacts secrets and absolute paths, refuses on unresolved
  leak findings, keeps license notices), diagnostic export (configs and run reports, no canonical
  knowledge or restricted data) and workspace transfer (private minus machine-local state,
  absolute paths stripped).
- Restore verifies every member hash, refuses traversal members and non-empty destinations, and
  runs the core doctor against the restored tree.
- CLI: `raytsystem backup | export --kind | restore [--dry-run]`.

## Init, upgrade and migrations

- `raytsystem init [--template software|content|research]` is dry-run by default,
  idempotent, never overwrites existing files, reports conflicts exactly and writes the declared
  template assets (skills, policy profile, eval suite fixtures) as pure data.
- Migrations are a versioned registry of deterministic steps journaled into the platform store's
  `migration_journal`; `apply` is resumable and idempotent, each step persists a report record
  whose hash matches `MigrationRecord.report_sha256`; `raytsystem upgrade --apply --confirm` takes a
  verified backup first.

## Self-monitoring

`raytsystem doctor`, `raytsystem status`, `raytsystem platform-status` and the Safety/systems UI report:
active flags, schema versions, migration state, event/notification/outbox backlogs, trace storage
size, eval regression count, circuit breakers, emergency state, MCP/ACP health, A2A exposure
(always disabled), encryption provider state and the last successful backup. A corrupted platform
store degrades status instead of crashing it, and doctor fails on `error`/`degraded` platform
state.

## Decision records

ADR-020 … ADR-032 in `ops/decisions/` cover evaluation, the trace model, replay/fork, policy
simulation, emergency controls, MCP governance, the ACP and A2A boundaries, pack lifecycle, the
workflow DAG, secrets encryption, backup/export/restore and workspace templates/migrations.
