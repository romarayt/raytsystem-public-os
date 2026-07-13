---
name: graph
description: Refresh the raytsystem code graph so it reflects every current file, then confirm it is up to date. Use for "graph", "update the graph", "refresh the graph", "rebuild the graph", "make the graph current", "index all files", "обнови граф", "перестрой граф", "актуализируй граф". Writes only the disposable .raytsystem/graph/ plane; never mutates canonical knowledge.
---

# graph — keep the graph current

Refreshes the derived code graph so architecture, impact and navigation queries reflect the actual
files on disk. This writes only the disposable `.raytsystem/graph/` plane — it can never mutate
canonical knowledge, `_raw/`, or the ledger. Report results to the user in plain terms.

## Step 1 — Check freshness

```bash
uv run raytsystem graph status --root <TARGET_PATH> --json
```

Read `state` and `changed_files`. `current` means nothing to do — tell the user it is already up to
date. `stale` or `missing` means a refresh is needed.

## Step 2 — Refresh

- **Incremental** (default — fast, handles changed and deleted files):
  ```bash
  uv run raytsystem graph update --root <TARGET_PATH> --json
  ```
- **Full rebuild** (when the graph is missing, or the user wants a complete rebuild from scratch):
  ```bash
  uv run raytsystem graph rebuild --root <TARGET_PATH> --json
  ```

## Step 3 — Confirm

```bash
uv run raytsystem graph status --root <TARGET_PATH> --json
```

Confirm `state` is now `current` and report the node/edge/file counts. If it is still stale, run the
full rebuild in step 2.

## Optional — refresh the knowledge projections too

The code graph and the knowledge graph are separate. To also rebuild the FTS5 index and the
knowledge projections (`knowledge/graph.json`, `index.md`, `hot.md`) from canonical knowledge:

```bash
uv run raytsystem rebuild-index --root <TARGET_PATH> --json
```

## Never do

- Never treat the derived graph as canonical truth — it is rebuildable, not a source of record.
- Never rebuild the graph as a side effect of a read-only question; only refresh when asked.
- Never push, publish, or send anything externally.
