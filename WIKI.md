# Knowledge model

The repository separates evidence, structured knowledge and human views:

- `_raw/` stores exact source bytes and safe source manifests.
- `normalized/<source_revision_id>/<normalization_id>/` stores immutable citation snapshots.
- `ledger/objects/` stores immutable typed records.
- `ledger/generations/` stores complete active-record manifests.
- `ledger/CURRENT` points to the active generation and is the canonical commit point.
- `knowledge/` is generated from the active generation.
- `knowledge/manual/` is the only human-editable knowledge area; edits return through INGEST.

All durable IDs and schemas are versioned. A newer claim supersedes or contradicts an older claim; it never silently overwrites history.
