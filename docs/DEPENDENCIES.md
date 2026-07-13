# Dependency and license policy

- Every direct dependency must have an accepted use case, version constraint and exact lockfile resolution.
- Donor code requires an exact source commit, license check and attribution record.
- Code and model artifacts are licensed separately.
- Model weights and implicit model downloads require a recorded approval before download.
- GPL code is not incorporated into a distributed permissive-only core without an explicit compliance decision.
- Rebuildable adapters must have a documented fallback.

The Python runtime dependencies are Pydantic, Typer, PyYAML, the isolated local PDF parser pypdf,
NetworkX, Tree-sitter with JavaScript/TypeScript grammars, FastAPI and Uvicorn. The web bundle uses React, TanStack Query, Sigma/Graphology and
Lucide with self-hosted Fontsource assets. Test/tooling dependencies are pytest, pytest-cov, Ruff,
mypy, pre-commit, httpx2, TypeScript, Vite, ESLint, Vitest and Testing Library. No model, graph
database, hosted service or API key is required for M0–M5a or the local web control plane.

## Direct dependency snapshot

Resolved by `uv.lock` on 2026-07-10 from package metadata:

| Package | Version | Declared license | Role |
|---|---:|---|---|
| pydantic | 2.13.4 | MIT | Runtime contracts |
| typer | 0.26.8 | MIT | CLI |
| PyYAML | 6.0.3 | MIT | Policy/source config |
| pypdf | 6.14.2 | BSD-3-Clause | Local PDF text extraction; restricted OS sandbox for real inputs, fixture-only Python fallback |
| networkx | 3.6.1 | BSD-3-Clause | In-memory graph construction; only deterministic sorted JSON is persisted |
| tree-sitter | 0.25.2 | MIT | Native syntax-tree runtime for the derived code graph |
| tree-sitter-javascript | 0.25.0 | MIT | JavaScript/JSX code-graph grammar |
| tree-sitter-typescript | 0.23.2 | MIT | TypeScript/TSX code-graph grammars |
| fastapi | 0.139.0 | MIT | Same-origin typed local HTTP API |
| uvicorn | 0.50.2 | BSD-3-Clause | Loopback-only ASGI server |
| pytest | 8.4.2 | MIT | Tests |
| pytest-cov | 7.1.0 | MIT | Coverage |
| ruff | 0.15.21 | MIT | Lint |
| mypy | 1.20.2 | MIT | Type checking |
| pre-commit | 4.6.0 | MIT | Local gates |
| types-PyYAML | 6.0.12.20260518 | Apache-2.0 | Static types |
| httpx2 | 2.5.0 | BSD-3-Clause | In-process ASGI security tests only |

Transitive versions and hashes are authoritative in `uv.lock`. Dependency metadata is evidence, not a substitute for a release-time legal review.

pypdf evidence: [PyPI package metadata](https://pypi.org/project/pypdf/),
[6.14.2 license](https://raw.githubusercontent.com/py-pdf/pypdf/6.14.2/LICENSE), and
[official text-extraction scope](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).
The core reader does not fetch model assets; OCR remains out of scope and unavailable. Parser
containment capability is preflighted before input and is included in the adapter/operation
fingerprint, so parser failure can never trigger a weaker retry.

NetworkX evidence: [PyPI package metadata](https://pypi.org/project/networkx/),
[3.6.1 license](https://github.com/networkx/networkx/blob/networkx-3.6.1/LICENSE.txt), and
[official documentation](https://networkx.org/documentation/stable/). No upstream source is copied
and no pickle/gpickle is persisted. The optional `types-networkx` package could not be approved by
the managed dependency environment during M2, so the single third-party import is marked
`type: ignore[import-untyped]`; strict mypy remains enabled for all raytsystem code.

Tree-sitter packages are local parsers only. raytsystem does not download grammars or models at
runtime, execute source files, or persist upstream parser objects. Extracted nodes/edges retain the
grammar/extractor version in their provenance.

## Execution-plane design references

Graphify (MIT, public commit `0efb2a443c85c04f31620fb8f60d138b8483af05`; unreleased 0.9.13
tree, latest observed release v0.9.12 at `35665a76ba26da0e1bfcab074fede19c94fc5c89`) and Paperclip
(MIT, public commit `e4e12bfb890a0fdf4c7de092362472c50a584533`) were inspected as behavioral
and architectural references on 2026-07-12. raytsystem uses a clean-room native implementation. No
application source, asset, database or generated output from either project is redistributed.
Details and rejected trust defaults are recorded in ADR-016 through ADR-019.

## Direct frontend dependency snapshot

Resolved by `web/package-lock.json` and rechecked on 2026-07-13. The production bundle is self-hosted and makes
no runtime request to a CDN, font host, analytics service or package registry.

| Package | Version | Declared license | Role |
|---|---:|---|---|
| react / react-dom | 19.2.7 | MIT | UI runtime |
| @tanstack/react-query | 5.101.2 | MIT | Typed server-state cache |
| @milkdown/kit | 7.21.2 | MIT | Lazy, Markdown-first visual editor; no required cloud backend |
| codemirror | 6.0.2 | MIT | Exact Source-mode editor umbrella |
| @codemirror/lang-markdown | 6.5.0 | MIT | Markdown language support for Source mode |
| @codemirror/lint | 6.9.7 | MIT | Bounded document diagnostics in Source mode |
| graphology | 0.26.0 | MIT | Disposable client graph and active-subgraph model |
| graphology-layout-forceatlas2 | 0.10.1 | MIT | Worker-backed interactive Links layout; no synchronous large-graph `assign` |
| sigma | 3.0.3 | MIT | WebGL knowledge-universe renderer and graph interaction surface |
| lucide-react | 1.24.0 | ISC | Interface icons |
| @fontsource-variable/bricolage-grotesque | 5.2.10 | OFL-1.1 | Self-hosted display font |
| @fontsource-variable/ibm-plex-sans | 5.2.8 | OFL-1.1 | Self-hosted interface font |
| @fontsource/ibm-plex-mono | 5.2.7 | OFL-1.1 | Self-hosted monospace font |
| vite / @vitejs/plugin-react | 8.1.4 / 6.0.3 | MIT | Reproducible frontend build |
| typescript | 6.0.3 | Apache-2.0 | Static type checking |
| eslint / @eslint/js | 10.7.0 / 10.0.1 | MIT | Frontend linting |
| typescript-eslint | 8.63.0 | MIT | TypeScript lint integration |
| vitest | 4.1.10 | MIT | Frontend tests |
| jsdom | 29.1.1 | MIT | Browser-like unit-test environment |
| @testing-library/react / jest-dom | 16.3.2 / 6.9.1 | MIT | Accessible component tests |
| @vitest/browser-playwright | 4.1.10 | MIT | Real-browser component, geometry and screenshot tests |
| playwright | 1.61.1 | Apache-2.0 | Pinned Chromium automation provider for layout gates |

All transitive JavaScript artifacts and integrity hashes are authoritative in
`web/package-lock.json`. The Fontsource packages redistribute the upstream typefaces under
OFL-1.1; the bundle includes only local font files and CSS. A release-time legal review remains
required before public distribution.

The Documents editor spike selected direct `@milkdown/kit` rather than a cloud service or the
larger React/Crepe convenience surface. Real Chromium parse→serialize qualification and measured
bundle evidence are recorded in ADR-035. Tiptap Markdown 3.27.3 (MIT) was evaluated but is not a
dependency because its Markdown facility is still Beta. The four editor rows are exact in
`web/package.json` and resolved with integrity hashes in `web/package-lock.json`. A full `npm audit`
on 2026-07-12 examined 443 production/development/optional/peer dependencies and reported zero
known vulnerabilities at every severity; the production-only audit also reported zero. The exact
production dependency locations and their discovered license/notice texts are generated from the
lockfile by `npm --prefix web run licenses:generate`, and `npm --prefix web run licenses:check`
fails if `web/public/licenses/THIRD-PARTY-JS-LICENSES.txt` drifts.

The isolated editor spike measured 435.70 kB raw / 131.22 kB gzip for Milkdown
core+CommonMark+GFM and 607.53 / 207.79 kB for CodeMirror basicSetup+Markdown+lint. The reviewed
2026-07-13 Vite production artifacts are separate lazy chunks: Visual 456.48 / 137.45 kB and Source
607.69 / 207.86 kB, raw / gzip. The entry points differ, so spike and production numbers are
reported separately rather than treated as one bundle measurement. The same build's main chunk is
744.00 / 206.64 kB and Vite still emits its `>500 kB` warning, so further splitting remains an
explicit performance follow-up.

The ForceAtlas2 integration uses the worker API shipped by the pinned 0.10.1 package. raytsystem owns
the worker lifecycle explicitly: one supervisor per immutable-topology active subgraph, `kill`
before replacement or coordinate/fixed-flag mutation, and a fresh supervisor for resume/reheat.
Orbit and Structure do not load force physics. The worker uses only disposable client coordinates;
it never writes canonical knowledge, ledger state or the code-graph projection.

## Historical M4 donor review (retired)

The retired domain prototype was implemented as a clean-room behavioral adaptation: no donor
runtime code, prompt file, screenshot or binary asset entered the distributed core, and it added no
package or model dependency. Its private local inventory was never a redistributable dependency and
is not part of the domain-neutral active source tree.

## Lock evidence

M5a itself added no package, service, model or network dependency. The later local web-control-plane
milestone added the explicitly listed server, browser and test dependencies. Playwright's Chromium
binary is a local test artifact installed by an explicit command; it is not shipped in the raytsystem
wheel, web bundle or repository. Current lock hashes:

- `uv.lock`: `fe7aea595b60364bccffc6c952f1164c2d2e0b91792ff1d834a414546a5d1a8f`
- `web/package-lock.json`: `b0616fef488864baf7a33e426430ccf0d61359ab966cff5ee0571379921f9324`

Live Fetchers, encryption/key providers, OCR/ASR and QMD assets remain unapproved rather than
being installed implicitly. Font copyright notices and the complete OFL-1.1 text are distributed
inside the web bundle at `licenses/FONTS-OFL-1.1.txt`; exact production JavaScript copyright and
permission texts are distributed at `licenses/THIRD-PARTY-JS-LICENSES.txt`.

The platform subsystems (evals, telemetry, replay, policy simulator, emergency controls, MCP
governance, protocols, pack lifecycle, workflows, notifications, backup, templates, migrations)
added no new package dependency. Restricted-blob encryption imports the `cryptography` AESGCM
backend dynamically; the package is deliberately **not** in `pyproject.toml`, so on a clean
checkout every key provider honestly reports `unavailable` and no encryption capability is
claimed. Adding `cryptography` (Apache-2.0/BSD dual) requires an explicit user approval under
this policy before restricted encryption can ever activate.
