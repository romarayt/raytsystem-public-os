# raytsystem — current status

Updated: 2026-07-13  
Release line: pre-1.0  
Production readiness: not claimed

This page describes the shipped behavior of the public snapshot. It intentionally excludes private
development history, local run identifiers, workstation state, and unpublished roadmap evidence.

## Capability matrix

| Capability | Status | Evidence / boundary |
|---|---|---|
| Python CLI | Available | `src/raytsystem/cli.py`; deterministic commands and typed JSON output |
| Loopback web UI | Available | `src/raytsystem/webapp/`; refuses non-`127.0.0.1` binds |
| Command Center | Available | Read-only workspace, task, run, catalog, and safety projections |
| Documents | Experimental | Policy-managed Markdown roots, guarded writes, revisions, search, backlinks, source/visual modes |
| Knowledge Universe | Available for local verified/derived views | Knowledge, work, agents, evidence, and code lenses; derived graph is not canonical truth |
| Agents and Skills catalog | Available | Inert definitions and permission/status inspection; definitions do not imply execution |
| Task ledger | Available | Durable transitions, dependencies, idempotency, history, and fenced checkout paths |
| Runs and execution views | Available for recorded state | Real provider execution remains disabled by default |
| INGEST | Available for synthetic fixtures; real promotion gated | Real-corpus promotion requires externally authenticated, hash-bound approval |
| QUERY | Available | Generation-bound local retrieval with verified citations or explicit gaps |
| LINT | Available | Deterministic integrity, provenance, projection, link, alias, and secret checks |
| SAVE | Available as DRAFT-only | Writes reviewable staging/draft artifacts; cannot promote or publish |
| Code graph | Available | Derived `.raytsystem/graph/` plane with status/update/rebuild/query commands |
| Backup/export/restore | Available, pre-1.0 | Local guarded primitives; operators must verify backups before migration |
| Workflow engine | Experimental | Local typed DAG controls exist; external effects remain policy-gated |
| Tool Hub media adapters | Experimental | Typed local-first contracts; URL/private hosted analysis requires destination-bound approval |
| MCP governance | Catalog-only by default | External MCP execution is disabled |
| External model execution | Disabled by default | Runtime, Codex local, and Claude local invocation bridges require separate feature/configuration decisions |
| External notifications and telemetry export | Disabled by default | No destination is allowlisted in shipped configuration |

## Shipped security defaults

The defaults in `config/platform.yaml`, `config/policies.yaml`, and
`config/runtime-adapters.yaml` are fail-closed:

- web serving is loopback-only;
- network policy defaults to none;
- external actions require explicit scoped approval;
- imported content remains data, never instructions;
- private/PII/secret model egress requires approval;
- remote generation, external MCP, external notifications, external KMS, OTLP export, and A2A
  network exposure are off;
- the public repository contains only a genesis ledger scaffold, no user corpus or local runtime
  database.

## Supported environment

- Python `>=3.12,<3.15`.
- macOS and Linux; WSL 2 is the supported Windows path. Native Windows is not release-tested.
- `uv` for locked Python installation and commands.
- Node.js 22 for UI/documentation development only; ordinary use relies on the checked-in reviewed
  frontend bundle.

## Known limitations

- This is a source-checkout installation; packaged desktop installers are not shipped.
- Managed Documents and several recovery/integration surfaces remain experimental.
- Provider adapters are contracts until explicitly configured and enabled; the default UI does not
  run a model.
- A visible UI section or CLI group is not proof that its external runtime is enabled.
- Native Windows, multi-user remote serving, and stable hosted-provider presets are not qualified.
- Pre-1.0 schemas, migrations, and CLI details may change with documented migration guidance.

## Verification commands

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pre-commit run --all-files

npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run browser:install
npm --prefix web run test
npm --prefix web run test:visual
npm --prefix web run build
npm --prefix web run screenshots:github

npm --prefix website ci
npm --prefix website run typecheck
npm --prefix website run check:reference
npm --prefix website run check:coverage
npm --prefix website run check:frontmatter
npm --prefix website run build

uv run raytsystem doctor
uv run raytsystem status
uv run raytsystem graph status --json
uv run raytsystem guard-checkpoint --json
```

The publication report records the result of each command against the final one-commit public
snapshot. A failed required gate blocks publication.
