---
name: graph
description: Refresh the raytsystem code graph so it reflects every current file. Use for "graph", "обнови граф", "перестрой граф", "актуализируй граф", "update the graph".
---

Bring the raytsystem code graph up to date so it reflects every current file. It writes only the
disposable `.raytsystem/graph/` plane and never mutates canonical knowledge.

1. Check freshness: `uv run raytsystem graph status --json`.
2. Refresh: `uv run raytsystem graph update --json` (incremental — handles changed and deleted files),
   or `uv run raytsystem graph rebuild --json` for a full rebuild if the graph is missing.
3. Confirm `state` is `current` and report the node/edge/file counts.
