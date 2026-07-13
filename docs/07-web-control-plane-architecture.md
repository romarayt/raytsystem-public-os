# Web control-plane architecture

Status: implemented architecture for the local raytsystem milestone
Depends on: `docs/02-target-architecture.md`, ADR-001 through ADR-015

## Architecture choice

Use a supervisor/control-plane product topology. raytsystem owns intent, policy, durable state and
audit. Runtime adapters own model/provider-specific execution. Agents receive minimum typed
context, never the entire workspace or another agent's conversational memory by default.

```text
Browser SPA
    ↓ same-origin typed API
Loopback control plane
    ├── verified read model ── knowledge ledger / raw evidence / run manifests
    ├── task command service ─ separate append-only task ledger
    ├── typed code-graph service ─ disposable `.raytsystem/graph/` projection only
    ├── catalog service ────── packs / agents / skills / instruction documents
    ├── graph projector ────── derived multi-plane universe
    └── runtime adapters ───── disabled by default; no generic shell endpoint
```

## State planes

| Plane | Source of truth | UI projection |
|---|---|---|
| Evidence | `_raw/` and immutable normalization snapshots | source/evidence inspector |
| Knowledge | `ledger/` and `ledger/CURRENT` | graph, claims, sources, query views |
| Work | separate `ops/task-ledger/` generations and pointer | Kanban, dependency and timeline views |
| Procedures | Git-tracked packs, skills and instruction documents | catalog and context inspector |
| Runs | committed run manifests/events | run list and gate timeline |
| Coordination | `ops/control.sqlite` | bounded read-only health snapshot |
| Browser state | none | local preferences, filters and camera only |

Task progress must not invalidate the knowledge generation or FTS projection.

## Product surfaces

1. **Command Center** — workspace health, active work, approvals, recent runs and knowledge changes.
2. **Tasks** — validated Kanban/list with dependencies and immutable transition history.
3. **Knowledge Universe** — Orbit, Semantic, Work, Agent, Time and Evidence lenses over one typed
   derived graph.
4. **Runs** — gates, state, recovery and artifact summaries from safe committed records.
5. **Agents** — one user-facing Agent per stable ID, combining its inert definition with nullable
   execution state; role, runtime, skills, access, current task and readiness remain distinguishable.
6. **Skills** — origin, exact hash, trust, permissions and test/evaluation metadata; safe Markdown
   detail plus policy-bound authoring for eligible repo-local skills.
7. **Context** — inert previews of allowlisted instruction documents and packs.
8. **Safety** — local binding, egress policy, disabled adapters, quarantines and approval boundary.

## Knowledge Universe

Node types are `workspace`, `instruction`, `skill`, `agent`, `task`, `run`, `artifact`, `source`,
`evidence`, `claim`, `entity`, `policy`, `adapter`, and bounded code types such as `repository`,
`file`, `module`, `class`, `function`, `test`, `ADR` and `dependency`. Edge types include `contains`, `uses`,
`assigned_to`, `depends_on`, `produced`, `supports`, `cites`, `contradicts`, `supersedes`,
`governed_by` and `executed_by`.

The server returns a generation-bound graph snapshot. Layout is derived and disposable. Every
response carries its knowledge generation, task generation and catalog fingerprint so the client
can discard stale details. The Code lens additionally binds the immutable code snapshot ID, object
hash and logical fingerprint.

### Lenses

- Universe (`Вся вселенная`): all eligible state planes in one derived graph. This is a
  lens, not the Orbit layout.
- Knowledge: relation-focused knowledge topology.
- Work: tasks, runs, agents and artifacts.
- Agent: agents, skills, instructions, policies and adapters.
- Evidence: shortest verified path between a claim/artifact and source evidence.
- Code: architecture, source provenance, confidence, communities, path and impact in the same
  Sigma/Graphology view.

### Layouts

Three complementary ways to read the same verified snapshot, chosen from a labelled toolbar
(`Орбита` / `Связи` / `Структура`, each with an accessible name, `aria-pressed` and a tooltip):

- **Орбита (Orbit)** — the deterministic server layout. Nodes sit on concentric rings by semantic
  ring and kind. It never runs a physics simulation, so it is the stable overview. Node sizes are
  hard-capped and labels use level-of-detail so a dense ring never collapses into an opaque disc.
- **Связи (Links)** — raytsystem's own force-directed relationship view (a native mode, not a copy of
  any other tool). Nodes are placed by their real edges: weakly connected components drift apart,
  dense communities gather into distinguishable clusters, and nodes are not pinned to rings.
- **Структура (Structure)** — the layered/hierarchical view across the semantic rings.

### The Links force graph

- **Active subgraph.** Physics runs only over the nodes and edges actually visible under the current
  lens and filters (`visibleIds`/`visibleEdgeIds`), never the whole snapshot behind a `hidden`
  reducer flag. Changing the lens, node/relation/confidence/community/importance filter, the
  changed-only toggle or a graph-query result rebuilds the active subgraph and restarts a short
  physics burst. Filter/search-driven replacements are debounced by 220 ms while already in Links
  mode. Edges whose endpoint is absent are excluded; an explicitly selected otherwise-visible node
  may remain as explained isolated context. The source snapshot fingerprint is preserved and
  canonical state never changes.
- **Off-main-thread worker.** ForceAtlas2 runs inside a web worker via `FA2LayoutSupervisor`, so the
  main thread stays responsive even for the full allowed graph. The old `graph.order <= 500` gate
  that silently left large graphs on ring coordinates is removed: Links runs up to the server's
  first-release projection envelope of 10,000 nodes and 50,000 edges.
- **Deterministic seed.** Initial positions come from a stable per-`node_id` hash: communities occupy
  distinct centres on a golden-angle spiral, members scatter by hash (so god/bridge nodes never
  collapse to one point), and edgeless nodes are parked on the periphery. The same snapshot and
  filters always seed the same starting picture, so `Переразложить` (re-layout) is reproducible.
  A bounded session-only cache restores recent positions and pins for an exact
  snapshot/lens/filter/layout-version signature; it never persists coordinates into the graph
  projection or ledgers.
- **Interaction.** Drag a node to reposition it (the camera holds still and the node is pinned so
  physics keeps it in place); shift-click toggles a pin; double-click focuses a node. The physics
  panel offers play/pause, `Переразложить` (re-layout), `Открепить` (unpin all), a live
  node/edge/pinned count and the physics state (`Раскладываем граф…`, `Физика активна`,
  `Раскладка стабилизирована`, `Физика приостановлена`). Zoom, pan, hover, selection and the
  Inspector keep working. The controller owns one supervisor for one immutable-topology active
  graph. It stops after sampled convergence or a 3/6/10/14-second size-dependent deadline, and is
  killed before coordinate/topology mutation, layout or snapshot change, and unmount. Resume,
  reheat, drag and pin changes construct a fresh supervisor rather than restarting a stopped 0.10.1
  worker, preventing duplicate iteration loops.
- **Local focus.** Selecting a node and choosing `Сфокусироваться` (or double-clicking) dims the rest
  and reveals first-level neighbours computed client-side from the verified edges; a further action
  expands to the second level, and `Весь граф` returns. The camera eases to the local subgraph. Focus
  depth is a view choice and never changes the canonical snapshot.
- **Performance budget.** The server refuses snapshots above 10,000 nodes or 50,000 edges. The
  default Code slice is smaller (2,500 nodes / 12,000 edges), but the worker path is exercised up to
  10,000 synthetic nodes. Above 5,000 active nodes the UI reports `экономный уровень
  детализации`: every active node still participates while global labels and edge ink remain
  deliberately sparse. `prefers-reduced-motion` hides continuous movement, runs a finite
  1–3.5-second worker pass, then reveals the final positions.

Large graphs use progressive disclosure, server-side slices and a list/table fallback that stays
functionally equivalent for the current lens and filters. Labels are shown for important,
selected, pinned and neighbouring nodes rather than every node.

### Troubleshooting

- **Blank or empty graph** — the snapshot has no visible nodes for the lens/filter; the accessible
  list/table view and the filter reset remain available. An empty or edgeless graph never throws.
- **Links layout looks static** — physics stops once the graph stabilises (the panel shows
  `Раскладка стабилизирована`). Press play or `Переразложить` to re-heat; drag re-heats automatically.
- **Reduced motion** — with `prefers-reduced-motion` the Links layout computes a finite pass and does
  not animate continuously; this is expected, not a hang.
- **Very large snapshot** — the panel shows a simplified-detail note; this is the documented budget,
  not missing data. Filter, focus, zoom and the list/table fallback expose the active view without
  requiring every label to be drawn at once.

## Task ledger

Task state is operational, not canonical knowledge. The ledger uses immutable content-addressed
task snapshots and events plus immutable board generations. `ops/task-ledger/CURRENT` is its sole
commit point. Objects written before a failed pointer swap are unreachable and harmless; events
and history are never deleted.

Commands require an idempotency key and expected board generation. Legal transitions are
deterministically validated. Dragging a card is merely a request to that command path; the UI rolls
back if validation rejects it.

The browser permits task commands and expected-snapshot-bound update/rebuild of the disposable
code graph only. It does not execute a model, shell command, canonical promotion or external
action.

## Catalog and packs

The universal core discovers only allowlisted Git-tracked roots. Catalog files and skill bodies are
untrusted data during discovery and inert text in the UI.

```text
packs/<pack-id>/
├── pack.yaml
├── agents/
├── skills/
├── workflows/
├── schemas/
└── fixtures/
```

The retired YouTube production vertical is not part of the active catalog or runtime. Historical
schemas and legacy readers remain inert compatibility artifacts. Universal agents in the starter
pack are orchestrator, researcher, builder, reviewer and librarian. They declare runtime adapters
and capabilities but are not falsely reported as running.

The catalog/execution split is an ownership boundary, not two product entities. The Agents API
projects one view per stable `agent_id`: an `AgentDefinition` branch plus nullable employee state
joined only through `employee_id` / `agent_definition_id`. Definition-only records remain visible as
catalog-only; execution records with a missing or mismatched definition remain visible as degraded
diagnostics. Display names never participate in the join.

Canonical agent and skill names stay English (`Builder`, `Researcher`, `raytsystem-watch`,
`raytsystem-query`). Russian presentation applies to roles, statuses, descriptions, field labels,
actions, errors and hints. Canonical-name lookup is separate from localized label/description
lookup, so presentation cannot rewrite identity.

## Skill document and authoring boundary

The skill detail projection returns the complete allowlisted `SKILL.md` body when sensitivity
policy permits. The SPA renders headings, paragraphs, lists, tables, blockquotes, links, inline
code and code blocks as inert React nodes. It never executes embedded HTML, script, iframe,
event handler, remote embed or a command copied from the document. Raw mode presents the exact
allowed text.

Editability is computed by the server rather than declared by the browser:

- an enabled, non-restricted, user-trusted `pack_local` skill at exactly
  `skills/<skill_id>/SKILL.md` is editable;
- official, pinned, generated, historical, external and unverified-origin skills are read-only;
- safe read-only skills may be forked into a unique local copy, while restricted or unverified
  content is neither editable nor forkable.

Preview/save and fork accept a typed skill ID, never a path. The HTTP request body is capped at
64 KiB even though lower catalog readers retain their own independent document limit. Writes
require the normal local session, same-origin CSRF and idempotency gates plus both the expected
catalog and source SHA-256.
The service validates UTF-8, size, frontmatter, directory/name agreement, permissions, submitted
test status and sensitivity; then performs no-follow descriptor checks and guarded no-replace
installation or creation. A bounded fsync recovery journal ties the namespace transition to the
exact SQLite scope/idempotency-key/request receipt; startup recovery and a shared catalog-reader
fence across UI projections, execution employee projection and workspace preparation prevent
uncommitted content from becoming observable or executable context. Ambiguous third-party state
is preserved for manual recovery. It
rehydrates through `CatalogService`, records a revision and audit event, returns the new hashes and
invalidates only related skill queries. An edited or copied skill is always
`test_status: pending` until independently verified; save does not run the skill or external tools.
Stale hashes produce a typed conflict and are never auto-merged. See ADR-034.

The first detail contract reports Tool Hub and workflow relationships as `not_modeled`; it never
infers them from Markdown. Skill history exposes only the current authoring revision when present,
not a fabricated complete revision chain.

## Runtime adapters

The contract binds an invocation to exact agent, skill, context, policy and config hashes. A
runtime adapter declares capabilities, isolation mode, egress destination, model destination and
health. The default adapter is disabled. Future ACP, Codex, Claude Code, Hermes or OpenHands
connectors can implement the interface without becoming canonical state.

No public endpoint accepts a shell string, arbitrary executable, arbitrary local path or raw tool
arguments. Enabling execution is a separate reviewed milestone.

## HTTP trust boundary

- bind to `127.0.0.1` by default; remote binding is rejected in v1;
- pin one resolved workspace root at process start;
- strict `Host` and `Origin` allowlists protect against DNS rebinding and cross-origin writes;
- issue an in-memory same-site session and require a separate CSRF header for writes;
- no CORS wildcard, remote scripts, fonts, favicons, analytics or URL previews;
- CSP blocks framing, plugins, inline script and arbitrary connections;
- list endpoints omit exact raw excerpts, absolute paths and sensitive locators;
- detail endpoints rehydrate from the same verified generation and apply disclosure policy;
- skill mutations derive `skills/<skill_id>/SKILL.md` server-side, require catalog/source CAS and
  reject symlinks, unsafe hardlinks, traversal, oversized or sensitivity-blocked content;
- all errors are typed/redacted; stack traces and terminal output never enter API responses;
- GET/HEAD endpoints make zero filesystem writes.

`read_regular_file` remains the only low-level catalog/context reader. The web API accepts typed
IDs rather than paths.

## Documents plane

`/documents` is distinct from both the read-only Handbook and canonical Knowledge. Its typed API
is backed by `DocumentService` plus a disposable `.raytsystem/documents.sqlite` projection. Allowed
workspace files remain authoritative. Search, properties, links and backlinks are projections and
cannot promote a note into a claim.

Document roots come from the pinned workspace configuration and resolve server-side. Listing,
search, recent views, details, links, backlinks, history and a bounded graph-focus slice return
relative paths only. Create/update/rename/move/create-folder are the complete mutation set for this
release; delete is absent. Every mutation uses the common same-origin controls plus expected
document snapshot/hash, no-follow containment, collision refusal, atomic persistence, revision
provenance and hash-only audit. GET/HEAD never scans or refreshes the index as a side effect;
startup maintenance and explicit protected refresh/rebuild own projection writes.

CodeMirror loads only for Source mode. Milkdown loads only for a visual-qualified active document;
the server and client block visual save when the exact Markdown round trip would change protected
syntax, frontmatter, line endings or final-newline state. The focused Universe slice uses distinct
document node kinds so an unverified manual note cannot look like a canonical claim. Full details
and state ownership are in ADR-035 and `docs/13-documents-workspace-architecture.md`.

## Frontend direction

The visual language is **precision instrument × cinematic observatory**: carbon surfaces, bone
text and restrained tangerine/periwinkle/cyan/gold semantic channels. Glow indicates focus or live
activity only. Status also uses text and shape. Motion is finite, purposeful and disabled or
reduced under `prefers-reduced-motion`.

The SPA uses React/TypeScript, TanStack Query and Sigma.js/Graphology. It is a self-hosted static
bundle served by the same loopback process. There is an accessible list/table alternative for the
graph and keyboard access to navigation, command palette, task transitions and inspectors.

Page-level routes use a single route container. Reusable surface tabs own their padding, button
gap, active/hover/focus states, separator, narrow-screen horizontal scrolling and the fixed gap to
content. Nested agent/skill views render semantic sections instead of nesting complete routes.
Inspector tabs, graph layout controls and compact switches remain separate components because
their interaction contract is different.

## Open-source boundary

The core moves from `Proprietary` to Apache-2.0 with a real `LICENSE`. Reference products with
modified, branding or fair-code licenses are design studies only. No code, logo, screenshot or
asset is copied. Frontend dependencies and fonts are pinned and recorded with their exact license.
