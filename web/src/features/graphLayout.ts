import Graph from "graphology";
import type { GraphEdge, GraphNode, GraphSnapshot } from "../types";

// Deterministic, disposable layout helpers for the Knowledge Universe force mode.
// Nothing here touches canonical state: coordinates are an ephemeral visual projection
// recomputed from the verified snapshot. The same snapshot and filters always seed the
// same picture (stable FNV-1a hashing), so a re-layout is reproducible.

export type GraphTheme = "dark" | "light" | "contrast";

// Hard visual caps keep one important node from swallowing its neighbours as a background disc.
export const NODE_MIN_SIZE = 0.8;
export const NODE_MAX_SIZE = 5.2;
// Global level-of-detail: only clearly important nodes keep a label when nothing is focused.
export const GLOBAL_LABEL_IMPORTANCE = 88;
// The server refuses snapshots above this first-release projection budget. ForceAtlas2 still runs
// in a worker up to the same cap; above the detail budget only labels/edge ink are reduced.
export const FORCE_MAX_NODES = 10_000;
export const FORCE_DETAIL_BUDGET = 5_000;
export const FORCE_LAYOUT_VERSION = "raytsystem-fa2-worker-v4";

export interface ForceGraphInputs {
  snapshot: GraphSnapshot;
  visibleIds: ReadonlySet<string>;
  visibleEdgeIds: ReadonlySet<string>;
  labelFor: (node: GraphNode) => string;
  colorForKind: (kind: string) => string;
  edgeColor: string;
  importantEdgeColor?: string;
  supportsColor: string;
}

// Tuned on the real ~2.5k-node graph, not a fixture: LinLog + outbound attraction separates
// dense communities into readable clusters, Barnes–Hut keeps large graphs affordable, adjustSizes
// resists overlap, and a high slowDown lets the worker settle instead of drifting forever.
export const FORCE_SETTINGS = {
  barnesHutOptimize: true,
  barnesHutTheta: 0.65,
  linLogMode: true,
  outboundAttractionDistribution: true,
  adjustSizes: true,
  strongGravityMode: false,
  gravity: 0.65,
  scalingRatio: 30,
  slowDown: 13,
  edgeWeightInfluence: 0.55
} as const;

export function displaySize(importance: number): number {
  return Math.max(NODE_MIN_SIZE, Math.min(NODE_MAX_SIZE, 0.8 + importance / 36));
}

// FNV-1a 32-bit hash mapped to [0, 1). Deterministic across runs and machines.
export function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) / 0x100000000;
}

// Initial cluster bucket: real community first (code lens), then the semantic ring, then kind.
// FA2 will refine from the edges; the seed only needs to be deterministic and pre-separated.
function bucketKey(node: GraphNode): string {
  const community = node.metadata.community_id;
  if (community) return `c:${community}`;
  if (node.ring) return `r:${node.ring}`;
  return `k:${node.kind}`;
}

export function buildForceGraph(inputs: ForceGraphInputs): Graph {
  const { snapshot, visibleIds, visibleEdgeIds, labelFor, colorForKind, edgeColor, importantEdgeColor, supportsColor } = inputs;
  // Keep direction and parallel real relations for Sigma and hover semantics. ForceAtlas2 treats
  // them as attraction weights, while self-loops are excluded because the installed worker does
  // not support them.
  const graph = new Graph({ multi: true, type: "directed", allowSelfLoops: false });
  for (const node of snapshot.nodes) {
    if (!visibleIds.has(node.node_id)) continue;
    const baseColor = colorForKind(node.kind);
    graph.addNode(node.node_id, {
      label: labelFor(node),
      kind: node.kind,
      status: node.status,
      importance: node.importance,
      bucket: bucketKey(node),
      isGod: node.metadata.is_god === "true",
      isBridge: node.metadata.is_bridge === "true",
      pinned: false,
      fixed: false,
      size: displaySize(node.importance),
      color: baseColor,
      baseColor,
      // Placeholder; overwritten deterministically by seedForcePositions before the worker starts.
      x: 0,
      y: 0
    });
  }
  for (const edge of snapshot.edges) {
    if (!visibleEdgeIds.has(edge.edge_id)) continue;
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    if (edge.source === edge.target) continue;
    if (graph.hasEdge(edge.edge_id)) continue;
    const supports = edge.kind === "supports";
    const structurallyImportant = [edge.source, edge.target].some((id) => (
      Boolean(graph.getNodeAttribute(id, "isGod")) ||
      Boolean(graph.getNodeAttribute(id, "isBridge")) ||
      String(graph.getNodeAttribute(id, "kind")) === "workspace" ||
      String(graph.getNodeAttribute(id, "kind")) === "repository"
    ));
    graph.addEdgeWithKey(edge.edge_id, edge.source, edge.target, {
      kind: edge.kind,
      directed: edge.directed,
      weight: supports ? 1.35 : 1,
      color: supports ? supportsColor : structurallyImportant ? (importantEdgeColor ?? edgeColor) : edgeColor,
      baseColor: supports ? supportsColor : structurallyImportant ? (importantEdgeColor ?? edgeColor) : edgeColor,
      size: supports ? 0.58 : structurallyImportant ? 0.46 : 0.18,
      type: "line"
    });
  }
  return graph;
}

// Deterministic pre-layout: communities occupy distinct points on a golden-angle spiral, members
// scatter by a stable
// per-id hash (so god/bridge nodes never collapse to one point), and edgeless nodes are parked
// on an outer ring instead of piling in the middle.
export function seedForcePositions(graph: Graph, radius = 1_000): void {
  const buckets = new Map<string, string[]>();
  graph.forEachNode((id, attributes) => {
    const key = String(attributes.bucket ?? "k:node");
    const list = buckets.get(key);
    if (list) list.push(id);
    else buckets.set(key, [id]);
  });
  const keys = [...buckets.keys()].sort();
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const radialStep = radius * 0.64;
  const outerRadius = radialStep * Math.sqrt(keys.length + 1) * 1.55;
  keys.forEach((key, bucketIndex) => {
    const centreAngle = bucketIndex * goldenAngle + (stableHash(`${key}#angle`) - 0.5) * 0.34;
    const centreRadius = radialStep * Math.sqrt(bucketIndex + 0.65) * (0.92 + stableHash(`${key}#radius`) * 0.16);
    const centreX = centreRadius * Math.cos(centreAngle);
    const centreY = centreRadius * Math.sin(centreAngle);
    const members = (buckets.get(key) ?? []).sort();
    for (const id of members) {
      const degree = graph.degree(id);
      const hx = stableHash(id);
      const hy = stableHash(`${id}#y`);
      if (degree === 0) {
        const angle = 2 * Math.PI * hx;
        const peripheryRadius = outerRadius * (1 + 0.12 * hy);
        graph.mergeNodeAttributes(id, {
          x: peripheryRadius * Math.cos(angle),
          y: peripheryRadius * Math.sin(angle)
        });
        continue;
      }
      const spread = Math.min(radius * 0.62, 80 + Math.sqrt(members.length) * 14);
      graph.mergeNodeAttributes(id, {
        x: centreX + (hx - 0.5) * spread,
        y: centreY + (hy - 0.5) * spread
      });
    }
  });
}

// Order-independent fingerprint of the active set, used to detect when filters changed enough
// to rebuild the physics subgraph. Sorting avoids iteration-order differences between callers.
function fingerprintSet(ids: ReadonlySet<string>): string {
  let hash = 0x811c9dc5;
  for (const id of [...ids].sort()) {
    for (let index = 0; index < id.length; index += 1) {
      hash ^= id.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    hash ^= 0xff;
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function activeSignature(
  snapshotId: string,
  visibleIds: ReadonlySet<string>,
  visibleEdgeIds: ReadonlySet<string>,
  lens: string
): string {
  return [
    FORCE_LAYOUT_VERSION,
    snapshotId,
    lens,
    visibleIds.size,
    visibleEdgeIds.size,
    fingerprintSet(visibleIds),
    fingerprintSet(visibleEdgeIds)
  ].join(":");
}

// A node keeps a global label only when it is structurally important. Hover and selection
// widen this in the reducer; strong zoom is handled by Sigma's own label grid.
export function keepsGlobalLabel(kind: string, importance: number, isGod: boolean, isBridge = false): boolean {
  return isGod || isBridge || importance >= GLOBAL_LABEL_IMPORTANCE || kind === "repository" || kind === "workspace";
}

interface CachedNodePosition {
  x: number;
  y: number;
  pinned: boolean;
}

interface CachedForceLayout {
  positions: Map<string, CachedNodePosition>;
  touchedAt: number;
}

const FORCE_CACHE_LIMIT = 6;
const forceLayoutCache = new Map<string, CachedForceLayout>();

/** Save only disposable client coordinates and pin state; never mutate the source snapshot. */
export function cacheForceLayout(key: string, graph: Graph): void {
  const positions = new Map<string, CachedNodePosition>();
  graph.forEachNode((id, attributes) => {
    const x = Number(attributes.x);
    const y = Number(attributes.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    positions.set(id, { x, y, pinned: Boolean(attributes.pinned) });
  });
  forceLayoutCache.delete(key);
  forceLayoutCache.set(key, { positions, touchedAt: Date.now() });
  while (forceLayoutCache.size > FORCE_CACHE_LIMIT) {
    const oldest = [...forceLayoutCache.entries()].sort((a, b) => a[1].touchedAt - b[1].touchedAt)[0]?.[0];
    if (!oldest) break;
    forceLayoutCache.delete(oldest);
  }
}

/** Restore an exact force-view cache entry. Missing/new nodes keep their deterministic seed. */
export function restoreForceLayout(key: string, graph: Graph): boolean {
  const cached = forceLayoutCache.get(key);
  if (!cached) return false;
  let restored = 0;
  for (const [id, position] of cached.positions) {
    if (!graph.hasNode(id)) continue;
    graph.mergeNodeAttributes(id, {
      x: position.x,
      y: position.y,
      pinned: position.pinned,
      fixed: position.pinned
    });
    restored += 1;
  }
  cached.touchedAt = Date.now();
  return restored > 0;
}

export function clearCachedForceLayout(key: string): void {
  forceLayoutCache.delete(key);
}

export function setNodePinned(graph: Graph, id: string, pinned: boolean): void {
  if (!graph.hasNode(id)) return;
  graph.mergeNodeAttributes(id, { pinned, fixed: pinned });
}

export function clearPins(graph: Graph): void {
  graph.forEachNode((id, attributes) => {
    if (attributes.pinned) graph.mergeNodeAttributes(id, { pinned: false, fixed: false });
  });
}

export function countPinned(graph: Graph): number {
  let total = 0;
  graph.forEachNode((_id, attributes) => {
    if (attributes.pinned) total += 1;
  });
  return total;
}

// Client-side neighbourhood for local focus. Uses the already-loaded verified edges, so no
// server traversal is duplicated; depth 1 or 2 hops from the focus root.
export function neighbourhood(
  edges: readonly GraphEdge[],
  visibleIds: ReadonlySet<string>,
  root: string,
  depth: 1 | 2
): Set<string> {
  const focus = new Set<string>([root]);
  let frontier = new Set<string>([root]);
  for (let hop = 0; hop < depth; hop += 1) {
    const next = new Set<string>();
    for (const edge of edges) {
      if (frontier.has(edge.source) && visibleIds.has(edge.target) && !focus.has(edge.target)) next.add(edge.target);
      if (frontier.has(edge.target) && visibleIds.has(edge.source) && !focus.has(edge.source)) next.add(edge.source);
    }
    for (const id of next) focus.add(id);
    frontier = next;
    if (!frontier.size) break;
  }
  return focus;
}
