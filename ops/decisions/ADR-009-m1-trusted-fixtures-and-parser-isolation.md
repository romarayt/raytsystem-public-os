# ADR-009 — Trusted fixtures, inert normalization and parser isolation

Status: accepted
Date: 2026-07-10

## Context

M1 must autonomously promote synthetic fixtures without turning a caller-controlled `fixture=true`
flag into a real-corpus approval bypass. It must also preserve untrusted source text without letting
Markdown/HTML become active when a normalized snapshot is opened, and it must prove a PDF slice
without downloading model artifacts or trusting an in-process parser with unlimited resources.

## Decision

- Autonomous fixture promotion requires both containment under `[fixtures].root` and an exact
  SHA-256 entry in the repository-controlled fixture manifest. The test-only configuration may
  disable the manifest inside isolated temporary repositories.
- Secret-like or unclassifiable inputs fail closed before normalization. Exact bytes are retained
  only under gitignored `_raw/restricted/` with directory mode `0700` and file mode `0600`.
- Normalized human-readable content uses inert `document.txt`; stable typed locators and a
  hash-bound `excerpts.jsonl` preserve citation resolution. Generated knowledge Markdown escapes
  all source-derived markup.
- PDF extraction uses pinned `pypdf==6.14.2` without extras in a scrubbed, timeout-limited
  subprocess with CPU/address-space/file/file-descriptor limits. A capability preflight selects a
  restrictive macOS `sandbox-exec` profile before any untrusted bytes are supplied; parser failure
  never triggers a weaker retry. The worker also disables Python socket, filesystem and subprocess
  entry points after dependency import. If OS containment is unavailable, the Python guard is
  permitted only for manifest-approved synthetic fixtures. OCR and model downloads remain
  unavailable.
- Captured YouTube JSON, JSON/JSONL, CSV/TSV and PNG/JPEG metadata use deterministic stdlib
  extractors. `yt-dlp` remains a separate future network Fetcher, not an offline parser dependency.
- Git checkpoint construction uses an isolated temporary index plus plumbing commands and writes a
  dedicated `refs/raytsystem/checkpoints/<event_id>` ref. It does not modify the current branch,
  default index or unrelated dirty files, and it scans the exact bytes before object creation.

## Consequences

- Fixture hashes must be deliberately updated when fixture bytes change.
- Normalized snapshots are safe to inspect as text but do not render original Markdown styling.
- PDF text extraction is bounded and deterministic, but image-only/OCR PDFs remain unsupported.
  The Python-only fallback is defense in depth, not an OS sandbox, and therefore cannot parse real
  corpus inputs.
- A local Git checkpoint ref is audit evidence; milestone integration commits are still created by
  the main local writer after all gates pass.
