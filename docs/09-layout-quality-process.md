# Layout quality process

raytsystem treats clipping, accidental overlap, horizontal overflow and inconsistent controls as test
failures. Responsive behavior follows the width of the actual workspace canvas, not only the outer
browser viewport, because a sidebar, inspector and browser zoom can all reduce the usable canvas.

## Install and run

```bash
npm --prefix web ci
npm --prefix web run browser:install
npm --prefix web run test
```

`test` runs the fast JSDOM behavior suite, the real ForceAtlas2 worker lifecycle test and the
real-Chromium layout suite. Chromium is an explicit local test artifact downloaded by Playwright;
it is not committed or distributed in the application bundle.

The graph-specific commands can also be run independently:

```bash
npm --prefix web run test:unit -- --run src/test/graphLayout.test.ts src/test/forceLayout.test.ts
npm --prefix web run test:graph-worker
npm --prefix web run test:layout
```

`test:graph-worker` is not a worker mock. Chromium starts the installed
`graphology-layout-forceatlas2/worker@0.10.1`, verifies that coordinates change off the main
thread, mutates and pins a node, reheats from the new position and proves that disposal stops all
subsequent movement.

## Hard geometry matrix

`web/src/test/layout.browser.test.tsx` renders every route at these CSS viewports:

| Scenario | Viewport |
|---|---:|
| Desktop | 1440×900 |
| Compact desktop | 1280×720 |
| Large desktop | 1920×1080 |
| 125% zoom equivalent | 1152×720 |
| 150% zoom equivalent | 960×600 |
| 175% zoom equivalent | 823×514 |
| 200% zoom equivalent | 720×450 |
| Tablet portrait | 834×1112 |
| Mobile | 390×844 |
| Reflow floor | 320×720 |

The suite also opens the Inspector at 1600×900 (docked) and 1280×800 (overlay) so its presence
cannot bypass workspace container-query contracts.

`web/src/test/layoutAudit.ts` reports all failures together and enforces:

- no horizontal overflow in the document, shell, header, main scroll root or route;
- no visible interactive control outside the viewport, except inside named horizontal scrollers;
- no title/action, Context-card or Safety-card overlap;
- 24 px minimum for every visible interactive target and 44 px for mobile navigation targets;
- equal visible header-control heights;
- a route starts inside `<main>` with `main.scrollTop === 0` after navigation;
- nested content remains inside Context, Safety and Agent cards, adapter rows, policy boundaries and
  header actions.
- Agents has no page-level `.route` nested inside another `.route`, and every stable `agent_id`
  produces at most one visible card.
- shared surface tabs retain a token-bound content gap, keep the active control clear of the outer
  border/separator, expose an unclipped `focus-visible` outline and scroll inside their named
  horizontal scroller on narrow canvases.

Exceptions must be selector-specific and justified in code. Do not add a blanket overflow or
pairwise-overlap exception: the graph orbits and avatar stacks are intentional, but normal panels
are not.

## Knowledge Universe matrix

The deterministic/unit matrix for the graph layout covers:

- Orbit never constructing a ForceAtlas2 worker;
- active-subgraph filtering and endpoint integrity;
- deterministic seeding, session cache, re-layout, pin and unpin;
- one-worker ownership across start, pause/resume, reheat, filter replacement, layout/snapshot
  replacement, StrictMode cleanup and unmount;
- reduced-motion bounded execution, empty and edgeless graphs, global-label LOD and accessible
  list fallback;
- synthetic active graphs with 50, 500, 2,500 and 10,000 nodes. The 5,000-node threshold is visual
  detail metadata, never a physics cutoff, and the historical 500-node fallback must not return.

The production performance envelope is 10,000 snapshot nodes and 50,000 edges. The default Code
slice is 2,500 nodes and 12,000 edges. A large-fixture test must assert bounded construction time
without replacing the real-browser worker lifecycle test.

Manual browser review uses the real current snapshot and checks Orbit, Links while active and
stabilised, local focus, drag/pin/unpin, filters, zoom/pan, desktop/tablet/mobile, dark/light/contrast
themes and `prefers-reduced-motion`. The status panel must respond immediately even while the
worker is still computing.

## Visual baselines

The separate visual suite covers stable, high-value compositions:

- Safety desktop;
- Context at the 150% zoom equivalent;
- header at the 200% zoom equivalent.

Knowledge Universe also has reviewed product-documentation screenshots (not cross-platform pixel
baselines):

- `website/static/img/interface/knowledge-universe/orbit-dark.png`;
- `website/static/img/interface/knowledge-universe/relations-global-dark.png`;
- `website/static/img/interface/knowledge-universe/relations-focus-dark.png`;
- `website/static/img/interface/knowledge-universe/relations-global-light.png`.

Before replacing one, inspect it at native resolution and verify that Orbit has no opaque centre,
Links exposes several distinguishable clusters and bridge edges, local focus preserves context,
and light-theme labels/edges remain legible. A screenshot that merely proves the page rendered is
not an acceptable reviewed reference.

Current reviewed references are Chromium-on-macOS files. They are intentionally not part of the
portable clean-setup gate: Linux and Windows maintainers must establish and review their own
platform baselines before enabling this suite in CI. On a platform with reviewed references, run
`npm --prefix web run test:visual`. If an intentional design change requires new references:

```bash
npm --prefix web run test:visual:update
```

Then inspect every changed PNG in `web/src/test/__screenshots__/` before committing. Never update a
reference merely to make a failing test green. Baselines include browser and platform in their
filenames; a shared CI environment should own additional platform references.

## Fix order

When the gate fails:

1. reproduce the failing route and exact CSS viewport;
2. identify the real containing block or scroll root;
3. fix the smallest shared layout contract (container query, grid minimum, intrinsic sizing or
   scroll reset) instead of adding screenshot-specific coordinates;
4. for graph failures, separate renderer geometry from worker lifecycle, active-subgraph content
   and LOD before changing force settings;
5. rerun `test:layout` and `test:graph-worker`, then unit tests, lint, typecheck and build;
6. run visual tests and review any intentional reference change.

The application uses `.workspace-shell` as a named inline-size container, `.main-content` as the
only route scroll root, fluid grids for Context and Safety, and one header-control size token. These
are invariants, not page-specific styling preferences.

Page-level tabbed routes additionally use one shared surface contract: the route owns one
`Surface`, tabs own padding/button gap/separator/horizontal scrolling and `SurfaceContent` owns one
tokenized gap below them. Nested views return semantic sections; they never add a second `.route`
or local `margin-bottom` to repair spacing. Inspector tabs, graph layout controls and compact
switches are deliberately outside this contract.
