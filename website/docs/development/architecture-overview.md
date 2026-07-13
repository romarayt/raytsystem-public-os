---
title: "Architecture overview"
description: "Compact architecture of raytsystem: loopback UI, deterministic kernel, immutable evidence, task history, catalogs, projections, and approval boundaries."
audience: [developer, operator]
status: stable
feature_flags: []
related_commands: ["uv run raytsystem status", "uv run raytsystem graph status --json"]
related_pages: [/security/overview, /knowledge/editable-vs-immutable, /code-graph/overview, /development/contributing]
source_of_truth:
  - path: README.md
  - path: src/raytsystem/webapp/app.py
  - path: src/raytsystem/storage.py
  - path: src/raytsystem/tasking.py
  - path: src/raytsystem/catalog.py
last_verified_against: "commit c6a4bfa / schema v1.4.0"
---

# Architecture overview

## What the user gets

raytsystem runs a same-origin web interface on loopback and a deterministic Python CLI over the same
workspace contracts. The UI combines verified state from knowledge, tasks, agents, skills, runs,
and safety without giving the browser arbitrary filesystem access.

```text
Browser (127.0.0.1) → FastAPI boundary → deterministic kernel
                                      ├─ immutable evidence and generations
                                      ├─ append-only task history
                                      ├─ allowlisted agent/skill/context catalog
                                      └─ rebuildable search, Markdown, and graph projections
```

## Trust and write boundaries

- Imported content is untrusted data, never an instruction source.
- `_raw/`, normalized snapshots, ledger objects/generations, and generated knowledge are not edited
  directly.
- LLM output remains a proposal until deterministic validation and authorized promotion succeed.
- Browser writes require same-origin session, CSRF, and idempotency bindings.
- External side effects and real-corpus promotion require separate scoped approval.

## Expected operational result

`uv run raytsystem status` reads canonical pointers without creating databases or indexes.
`uv run raytsystem graph status --json` reports whether the derived code graph is current. A stale
graph is reported as stale; read-only architecture work falls back to targeted source search rather
than rebuilding it silently.

## Failure and recovery

Rebuildable projections can be recreated from verified canonical state. Pointer, hash, generation,
or policy mismatches fail closed. Use `raytsystem doctor`, the relevant troubleshooting article, and
a dry-run restore plan before any recovery write.

See [Security](/security/overview), [editable and immutable areas](/knowledge/editable-vs-immutable),
[code graph](/code-graph/overview), and [contributing](/development/contributing).
