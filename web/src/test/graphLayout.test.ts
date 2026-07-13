import Graph from "graphology";
import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode, GraphSnapshot } from "../types";
import {
  NODE_MAX_SIZE,
  activeSignature,
  buildForceGraph,
  cacheForceLayout,
  clearCachedForceLayout,
  clearPins,
  countPinned,
  displaySize,
  keepsGlobalLabel,
  neighbourhood,
  restoreForceLayout,
  seedForcePositions,
  setNodePinned,
  stableHash
} from "../features/graphLayout";

function node(id: string, extra: Partial<GraphNode> = {}): GraphNode {
  return {
    node_id: id,
    kind: extra.kind ?? "module",
    label: extra.label ?? id,
    subtitle: extra.subtitle ?? "",
    status: extra.status ?? "current",
    ring: extra.ring ?? "code",
    importance: extra.importance ?? 50,
    x: extra.x ?? 0,
    y: extra.y ?? 0,
    recorded_at: null,
    source_ref: null,
    metadata: extra.metadata ?? {}
  };
}

function edge(id: string, source: string, target: string, kind = "imports"): GraphEdge {
  return { edge_id: id, source, target, kind, status: "active", directed: true, metadata: {} };
}

function snapshot(nodes: GraphNode[], edges: GraphEdge[]): GraphSnapshot {
  return {
    graph_snapshot_id: "graph_test",
    knowledge_generation_id: "gen",
    knowledge_generation_sha256: "0".repeat(64),
    task_generation_id: null,
    task_generation_sha256: null,
    catalog_sha256: "0".repeat(64),
    code_snapshot_id: "cgraph_test",
    code_snapshot_sha256: "0".repeat(64),
    code_snapshot_fingerprint: "0".repeat(64),
    code_graph_state: "current",
    code_file_count: 1,
    code_node_count: nodes.length,
    code_edge_count: edges.length,
    code_ambiguous_edges: 0,
    code_view_node_count: nodes.length,
    code_view_edge_count: edges.length,
    code_view_truncated: false,
    nodes,
    edges,
    supported_lenses: ["universe", "code"],
    created_at: "2026-07-12T00:00:00Z"
  };
}

const inputs = (snap: GraphSnapshot, visibleIds: Set<string>, visibleEdgeIds: Set<string>) => ({
  snapshot: snap,
  visibleIds,
  visibleEdgeIds,
  labelFor: (item: GraphNode) => item.label,
  colorForKind: () => "#123456",
  edgeColor: "#39424d",
  supportsColor: "#ff8a5b"
});

describe("buildForceGraph", () => {
  it("includes only the active nodes and edges, never the hidden ones", () => {
    const snap = snapshot(
      [node("a"), node("b"), node("hidden")],
      [edge("e1", "a", "b"), edge("e2", "a", "hidden")]
    );
    const graph = buildForceGraph(inputs(snap, new Set(["a", "b"]), new Set(["e1"])));
    expect(graph.order).toBe(2);
    expect(graph.size).toBe(1);
    expect(graph.hasNode("hidden")).toBe(false);
    expect(graph.hasEdge("e1")).toBe(true);
    expect(graph.hasEdge("e2")).toBe(false);
  });

  it("caps node size so no node becomes the background", () => {
    const snap = snapshot([node("big", { importance: 100 })], []);
    const graph = buildForceGraph(inputs(snap, new Set(["big"]), new Set()));
    expect(graph.getNodeAttribute("big", "size")).toBeLessThanOrEqual(NODE_MAX_SIZE);
    expect(displaySize(100)).toBeLessThanOrEqual(NODE_MAX_SIZE);
  });

  it("drops self-loops but keeps parallel relations as physics weight", () => {
    const snap = snapshot(
      [node("a"), node("b")],
      [edge("self", "a", "a"), edge("p1", "a", "b"), edge("p2", "a", "b", "calls")]
    );
    const graph = buildForceGraph(inputs(snap, new Set(["a", "b"]), new Set(["self", "p1", "p2"])));
    // The active subgraph is a multigraph: the self-loop is excluded (the worker cannot lay it out)
    // while both parallel relations survive as separate edges.
    expect(graph.hasEdge("self")).toBe(false);
    expect(graph.size).toBe(2);
  });

  it("does not throw on an empty graph or a graph without edges", () => {
    const empty = buildForceGraph(inputs(snapshot([], []), new Set(), new Set()));
    expect(empty.order).toBe(0);
    seedForcePositions(empty);
    const isolated = buildForceGraph(inputs(snapshot([node("solo")], []), new Set(["solo"]), new Set()));
    seedForcePositions(isolated);
    expect(Number.isFinite(isolated.getNodeAttribute("solo", "x"))).toBe(true);
  });
});

describe("seedForcePositions", () => {
  it("is deterministic for the same snapshot and filters", () => {
    const snap = snapshot([node("a"), node("b"), node("c")], [edge("e1", "a", "b"), edge("e2", "b", "c")]);
    const first = buildForceGraph(inputs(snap, new Set(["a", "b", "c"]), new Set(["e1", "e2"])));
    const second = buildForceGraph(inputs(snap, new Set(["a", "b", "c"]), new Set(["e1", "e2"])));
    seedForcePositions(first);
    seedForcePositions(second);
    for (const id of ["a", "b", "c"]) {
      expect(first.getNodeAttribute(id, "x")).toBe(second.getNodeAttribute(id, "x"));
      expect(first.getNodeAttribute(id, "y")).toBe(second.getNodeAttribute(id, "y"));
    }
  });

  it("separates distinct communities around different centres", () => {
    const nodes = [
      node("a1", { metadata: { community_id: "1" } }),
      node("a2", { metadata: { community_id: "1" } }),
      node("b1", { metadata: { community_id: "2" } }),
      node("b2", { metadata: { community_id: "2" } })
    ];
    const edges = [edge("e1", "a1", "a2"), edge("e2", "b1", "b2")];
    const graph = buildForceGraph(inputs(snapshot(nodes, edges), new Set(["a1", "a2", "b1", "b2"]), new Set(["e1", "e2"])));
    seedForcePositions(graph);
    const centroid = (ids: string[]) => ids.reduce(
      (acc, id) => ({ x: acc.x + graph.getNodeAttribute(id, "x") / ids.length, y: acc.y + graph.getNodeAttribute(id, "y") / ids.length }),
      { x: 0, y: 0 }
    );
    const a = centroid(["a1", "a2"]);
    const b = centroid(["b1", "b2"]);
    const distance = Math.hypot(a.x - b.x, a.y - b.y);
    expect(distance).toBeGreaterThan(600);
  });

  it("parks edgeless nodes on the periphery, away from the connected core", () => {
    const nodes = [node("hub"), node("spoke"), node("lonely")];
    const graph = buildForceGraph(inputs(snapshot(nodes, [edge("e1", "hub", "spoke")]), new Set(["hub", "spoke", "lonely"]), new Set(["e1"])));
    seedForcePositions(graph);
    const radius = (id: string) => Math.hypot(
      Number(graph.getNodeAttribute(id, "x")),
      Number(graph.getNodeAttribute(id, "y"))
    );
    expect(radius("lonely")).toBeGreaterThan(radius("hub"));
  });
});

describe("ephemeral force cache", () => {
  it("restores force coordinates and pins only for the exact cache key", () => {
    const snap = snapshot([node("a"), node("b")], [edge("e1", "a", "b")]);
    const ids = new Set(["a", "b"]);
    const edgeIds = new Set(["e1"]);
    const cacheKey = activeSignature(snap.graph_snapshot_id, ids, edgeIds, "code");
    const first = buildForceGraph(inputs(snap, ids, edgeIds));
    seedForcePositions(first);
    first.mergeNodeAttributes("a", { x: 321, y: -123 });
    setNodePinned(first, "a", true);
    cacheForceLayout(cacheKey, first);

    const restored = buildForceGraph(inputs(snap, ids, edgeIds));
    seedForcePositions(restored);
    expect(restoreForceLayout("another-snapshot", restored)).toBe(false);
    expect(restoreForceLayout(cacheKey, restored)).toBe(true);
    expect(restored.getNodeAttribute("a", "x")).toBe(321);
    expect(restored.getNodeAttribute("a", "y")).toBe(-123);
    expect(restored.getNodeAttribute("a", "pinned")).toBe(true);
    expect(restored.getNodeAttribute("a", "fixed")).toBe(true);
    clearCachedForceLayout(cacheKey);
  });

  it("returns to the same deterministic seed after re-layout clears the cache", () => {
    const snap = snapshot([node("a"), node("b")], [edge("e1", "a", "b")]);
    const ids = new Set(["a", "b"]);
    const edgeIds = new Set(["e1"]);
    const cacheKey = activeSignature(snap.graph_snapshot_id, ids, edgeIds, "code");
    const expected = buildForceGraph(inputs(snap, ids, edgeIds));
    seedForcePositions(expected);
    const expectedX = Number(expected.getNodeAttribute("a", "x"));
    expected.setNodeAttribute("a", "x", 999);
    cacheForceLayout(cacheKey, expected);
    clearCachedForceLayout(cacheKey);

    const reset = buildForceGraph(inputs(snap, ids, edgeIds));
    seedForcePositions(reset);
    expect(reset.getNodeAttribute("a", "x")).toBe(expectedX);
    expect(restoreForceLayout(cacheKey, reset)).toBe(false);
  });
});

describe("pin helpers", () => {
  it("pins and unpins a node by toggling both pinned and fixed", () => {
    const graph = new Graph();
    graph.addNode("n", { pinned: false, fixed: false });
    setNodePinned(graph, "n", true);
    expect(graph.getNodeAttribute("n", "pinned")).toBe(true);
    expect(graph.getNodeAttribute("n", "fixed")).toBe(true);
    expect(countPinned(graph)).toBe(1);
    setNodePinned(graph, "n", false);
    expect(graph.getNodeAttribute("n", "fixed")).toBe(false);
    expect(countPinned(graph)).toBe(0);
  });

  it("clears every pin at once", () => {
    const graph = new Graph();
    graph.addNode("a", { pinned: true, fixed: true });
    graph.addNode("b", { pinned: true, fixed: true });
    clearPins(graph);
    expect(countPinned(graph)).toBe(0);
    expect(graph.getNodeAttribute("a", "fixed")).toBe(false);
  });
});

describe("label level-of-detail", () => {
  it("keeps labels only for structurally important nodes at global scale", () => {
    expect(keepsGlobalLabel("module", 40, false)).toBe(false);
    expect(keepsGlobalLabel("module", 90, false)).toBe(true);
    expect(keepsGlobalLabel("function", 20, true)).toBe(true);
    expect(keepsGlobalLabel("function", 20, false, true)).toBe(true);
    expect(keepsGlobalLabel("repository", 10, false)).toBe(true);
  });
});

describe("neighbourhood focus", () => {
  const edges = [edge("e1", "root", "a"), edge("e2", "a", "b"), edge("e3", "root", "c")];
  const visible = new Set(["root", "a", "b", "c"]);

  it("returns first-level neighbours at depth 1", () => {
    const set = neighbourhood(edges, visible, "root", 1);
    expect([...set].sort()).toEqual(["a", "c", "root"]);
  });

  it("expands to the second level at depth 2", () => {
    const set = neighbourhood(edges, visible, "root", 2);
    expect(set.has("b")).toBe(true);
  });
});

describe("activeSignature", () => {
  it("changes on snapshot, visible set or lens and is stable otherwise", () => {
    const base = activeSignature("snap1", new Set(["a", "b"]), new Set(["e1"]), "code");
    expect(activeSignature("snap1", new Set(["a", "b"]), new Set(["e1"]), "code")).toBe(base);
    expect(activeSignature("snap1", new Set(["a"]), new Set(["e1"]), "code")).not.toBe(base);
    expect(activeSignature("snap1", new Set(["a", "b"]), new Set(["e2"]), "code")).not.toBe(base);
    expect(activeSignature("snap2", new Set(["a", "b"]), new Set(["e1"]), "code")).not.toBe(base);
    expect(activeSignature("snap1", new Set(["a", "b"]), new Set(["e1"]), "knowledge")).not.toBe(base);
  });

  it("produces a stable hash for identical ids", () => {
    expect(stableHash("raytsystem.universe")).toBe(stableHash("raytsystem.universe"));
    expect(stableHash("a")).not.toBe(stableHash("b"));
  });
});

describe("performance budget", () => {
  it.each([
    [50, 500],
    [500, 750],
    [2_500, 1_500],
    [10_000, 4_000]
  ])("builds and deterministically seeds a %i-node active projection within %ims", (size, budgetMs) => {
    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];
    for (let index = 0; index < size; index += 1) {
      nodes.push(node(`n${index}`, { metadata: { community_id: String(index % 40) } }));
      if (index > 0) edges.push(edge(`e${index}`, `n${index}`, `n${index - 1}`));
    }
    const visibleIds = new Set(nodes.map((item) => item.node_id));
    const visibleEdgeIds = new Set(edges.map((item) => item.edge_id));
    const started = performance.now();
    const graph = buildForceGraph(inputs(snapshot(nodes, edges), visibleIds, visibleEdgeIds));
    seedForcePositions(graph);
    const elapsed = performance.now() - started;
    expect(graph.order).toBe(size);
    expect(graph.size).toBe(size - 1);
    expect(elapsed).toBeLessThan(budgetMs);
  });
});
