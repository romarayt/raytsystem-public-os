# Third-party notices

This project resolves exact dependency artifacts in `uv.lock`. The following direct runtime
dependency was added after the M0 foundation:

## pypdf 6.14.2

- Purpose: offline text extraction from already captured local PDF bytes.
- License: BSD 3-Clause.
- Upstream: <https://github.com/py-pdf/pypdf/tree/6.14.2>
- License text: <https://github.com/py-pdf/pypdf/blob/6.14.2/LICENSE>
- Adaptation: no upstream source copied; the package is invoked without extras inside an raytsystem
  subprocess with timeout/resource limits. A restrictive macOS sandbox is capability-selected
  before bytes are supplied; the weaker Python guard is restricted to manifest-approved synthetic
  fixtures. OCR/model assets are not installed.

## NetworkX 3.6.1

- Purpose: in-memory construction of the rebuildable typed knowledge graph.
- License: BSD 3-Clause.
- Upstream: <https://github.com/networkx/networkx/tree/networkx-3.6.1>
- License text: <https://github.com/networkx/networkx/blob/networkx-3.6.1/LICENSE.txt>
- Adaptation: no upstream source copied. raytsystem writes a sorted canonical JSON graph only; no
  pickle/gpickle or NetworkX runtime state is treated as canonical.

The complete direct dependency/license snapshot is maintained in `docs/DEPENDENCIES.md`.

## Tree-sitter code-graph parsers

- `tree-sitter` 0.25.2 — MIT — local syntax-tree runtime.
- `tree-sitter-javascript` 0.25.0 — MIT — JavaScript/JSX grammar.
- `tree-sitter-typescript` 0.23.2 — MIT — TypeScript/TSX grammars.
- Adaptation: packages are called as parsers; no upstream source is copied. raytsystem persists only
  its own typed, content-addressed code nodes, edges and provenance.

## Architectural references: Graphify and Paperclip

- Graphify — MIT, Copyright (c) 2026 Safi Shamsi — inspected at
  `0efb2a443c85c04f31620fb8f60d138b8483af05` (unreleased 0.9.13 tree); latest observed
  release `v0.9.12` resolves to `35665a76ba26da0e1bfcab074fede19c94fc5c89`.
- Paperclip — MIT, LICENSE states Copyright (c) 2025 Paperclip AI — inspected at
  `e4e12bfb890a0fdf4c7de092362472c50a584533`.
- Purpose: behavioral comparison for code-graph retrieval and digital-employee orchestration.
- Adaptation: clean-room native implementation only. No source, logo, screenshot, database,
  generated graph or runtime package from either application is included. Their permission-bypass,
  mutable-task and automatic-instruction defaults are not adopted.

M5a adds no third-party package or copied code. Its captured-HTML adapter and qualification tooling
use only the Python standard library.

## Local web control plane

- FastAPI 0.139.0 — MIT — typed same-origin HTTP API.
- Uvicorn 0.50.2 — BSD 3-Clause — loopback ASGI server.
- React/React DOM 19.2.7 — MIT — browser UI runtime.
- TanStack Query 5.101.2 — MIT — browser server-state cache.
- Graphology 0.26.0 and Graphology ForceAtlas2 0.10.1 — MIT — disposable graph model/layout.
- Sigma.js 3.0.3 — MIT — WebGL graph rendering.
- Lucide React 1.24.0 — ISC — interface icons.
- Bricolage Grotesque, IBM Plex Sans and IBM Plex Mono Fontsource packages — OFL 1.1 —
  self-hosted font assets.
- Milkdown Kit 7.21.2 — MIT — local Markdown-first visual editor, loaded only for a qualified
  document; no hosted backend or analytics is required.
- CodeMirror 6.0.2, CodeMirror Markdown 6.5.0 and CodeMirror Lint 6.9.7 — MIT — local Source-mode
  editing, language support and diagnostics.

No upstream application source, logo, screenshot or reference-product asset was copied. Production
and development versions, including the editor graph, are pinned with integrity hashes in
`web/package-lock.json`; the complete direct license matrix is in `docs/DEPENDENCIES.md`. A full
`npm audit` on 2026-07-12 reported zero known vulnerabilities across the 443-package resolved graph.
A qualified distributable web bundle carries complete font OFL/copyright text and production
JavaScript copyright/permission texts under `licenses/`; regenerating and reviewing that bundle
notice remains a release gate whenever this dependency graph changes.

## Historical user-local procedural review (retired)

- Purpose: clean-room review of a retired domain workflow and its safety rules.
- Provenance: the private local inventory was not distributed with the open-source repository.
- License evidence: no license file observed in the approved donor roots.
- Adaptation: no source, prompt body, screenshot or binary was copied into the distributed core;
  the active product no longer ships that workflow runtime, pack or skill.
