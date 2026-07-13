import { AlertTriangle, Braces, Crosshair, Filter, Focus, GitCompareArrows, List, Network, Orbit, Pause, Pin, PinOff, Play, RefreshCw, RotateCcw, RotateCw, Search, Share2, Shrink, Sparkles, Table2, Waypoints } from "lucide-react";
import Graph from "graphology";
import Sigma from "sigma";
import { useEffect, useMemo, useRef, useState } from "react";
import { shortId } from "../api";
import { useCodeGraphMutation, useCodeGraphQuery, useCodeGraphStatus, useCodeGraphTraversal, useDocumentGraph, useUniverse } from "../hooks";
import { catalogDescription, isolationLabel, kindLabel, lensLabel, localizedCatalogLabel, relationLabel, roleLabel, statusLabel } from "../presentation";
import type { CodeGraphQueryResult, EdgeConfidence, GraphLens, GraphNode, GraphSnapshot, Selection } from "../types";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "../components/StatePanel";
import { createGraphNodeHoverRenderer } from "./graphRendering";
import { FORCE_DETAIL_BUDGET, activeSignature, buildForceGraph, cacheForceLayout, clearCachedForceLayout, clearPins, countPinned, displaySize, keepsGlobalLabel, neighbourhood, restoreForceLayout, seedForcePositions, setNodePinned, stableHash } from "./graphLayout";
import { ForceLayoutController, type PhysicsState, type SupervisorFactory } from "./forceLayout";

type Layout = "orbit" | "force" | "structured";
type ViewMode = "graph" | "list";

const darkKindColor: Record<string, string> = {
  workspace: "#f2eee6",
  generation: "#ff8a5b",
  task_generation: "#63d8d2",
  instruction: "#d7cfbe",
  pack: "#ddbb65",
  agent: "#ff8a5b",
  skill: "#ddbb65",
  adapter: "#78a9ff",
  task: "#63d8d2",
  run: "#75d4a1",
  claim: "#a99cf8",
  entity: "#c8bfff",
  evidence: "#ffb06f",
  source: "#75d4a1",
  manual_document: "#63d8d2",
  documentation_document: "#78a9ff",
  generated_document: "#a6afbb",
  document: "#ddbb65",
  repository: "#ff8a5b", directory: "#748091", file: "#78a9ff", module: "#63d8d2",
  package: "#ddbb65", class: "#a99cf8", function: "#75d4a1", method: "#8bd7ae",
  api_endpoint: "#ffb06f", database_table: "#ef7391", database_schema: "#d38aa6",
  configuration: "#a6afbb", test: "#e5c461", adr: "#c8bfff", rationale: "#9e93dc", dependency: "#ff7085"
};

const lightKindColor: Record<string, string> = {
  workspace: "#1f252b",
  generation: "#c9502b",
  task_generation: "#16827e",
  instruction: "#6d6250",
  pack: "#8a6818",
  agent: "#c9502b",
  skill: "#8a6818",
  adapter: "#3568b8",
  task: "#16827e",
  run: "#287a50",
  claim: "#6759c4",
  entity: "#7469b9",
  evidence: "#bd5c19",
  source: "#287a50",
  manual_document: "#16827e",
  documentation_document: "#3568b8",
  generated_document: "#59616b",
  document: "#8a6818",
  repository: "#c9502b", directory: "#56616d", file: "#3568b8", module: "#16827e",
  package: "#8a6818", class: "#6759c4", function: "#287a50", method: "#347f5d",
  api_endpoint: "#bd5c19", database_table: "#b7435f", database_schema: "#9d5872",
  configuration: "#59616b", test: "#8a6818", adr: "#7469b9", rationale: "#665ca5", dependency: "#bd3e54"
};

const codeKinds = new Set(["repository", "directory", "file", "module", "package", "class", "function", "method", "api_endpoint", "database_table", "database_schema", "configuration", "test", "adr", "rationale", "dependency"]);

const lensKinds: Record<GraphLens, ReadonlySet<string>> = {
  universe: new Set(),
  knowledge: new Set(["workspace", "generation", "claim", "entity", "evidence", "source"]),
  work: new Set(["workspace", "task_generation", "task", "run", "agent", "skill"]),
  agent: new Set(["workspace", "pack", "agent", "skill", "instruction", "adapter"]),
  evidence: new Set(["workspace", "generation", "claim", "evidence", "source"]),
  code: new Set(["workspace", ...codeKinds])
};

function isVisible(kind: string, lens: GraphLens): boolean {
  return lens === "universe" || lensKinds[lens].has(kind);
}

function nodeLabel(node: GraphNode): string {
  if (node.kind === "workspace") return "Рабочее пространство raytsystem";
  if (node.kind === "generation") return "Активное поколение знаний";
  if (node.kind === "task_generation") return "Активное поколение задач";
  return localizedCatalogLabel(node.node_id, node.label);
}

function nodeSubtitle(node: GraphNode): string {
  if (node.kind === "workspace") return "Локальная система управления";
  if (node.kind === "generation") return "Канонический срез знаний";
  if (node.kind === "task_generation") return "Операционный срез задач";
  if (node.kind === "adapter") return isolationLabel(node.subtitle);
  if (node.kind === "agent") return roleLabel(node.subtitle);
  if (node.kind === "instruction") return "Документ инструкций";
  if (["pack", "skill"].includes(node.kind)) return catalogDescription(node.node_id, node.subtitle);
  return node.subtitle;
}

function toSelection(node: GraphNode, snapshot: GraphSnapshot): Selection {
  const snapshotId = ["generation", "claim", "entity", "source", "evidence"].includes(node.kind)
    ? snapshot.knowledge_generation_id
    : ["task", "task_generation"].includes(node.kind)
      ? snapshot.task_generation_id ?? snapshot.graph_snapshot_id
      : ["pack", "agent", "skill", "instruction", "adapter"].includes(node.kind)
        ? snapshot.catalog_sha256
        : codeKinds.has(node.kind)
          ? snapshot.code_snapshot_id ?? snapshot.graph_snapshot_id
          : snapshot.graph_snapshot_id;
  return {
    id: node.node_id,
    kind: node.kind,
    label: nodeLabel(node),
    status: node.status,
    subtitle: nodeSubtitle(node),
    metadata: node.metadata,
    snapshotId,
    plane: codeKinds.has(node.kind) ? "code" : undefined
  };
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

function visualNodeLabel(node: GraphNode): string {
  const label = nodeLabel(node);
  return label.length <= 72 ? label : `${label.slice(0, 69).trimEnd()}…`;
}

function focusCameraOnNodes(sigma: Sigma, ids: ReadonlySet<string>): void {
  const points = [...ids]
    .map((id) => sigma.getNodeDisplayData(id))
    .filter((data) => Boolean(data));
  if (!points.length) return;
  const xs = points.map((point) => point!.x);
  const ys = points.map((point) => point!.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const { width, height } = sigma.getDimensions();
  const ratio = Math.max(
    0.08,
    Math.min(1.2, Math.max((maxX - minX) * 1.4, (maxY - minY) * (width / Math.max(height, 1)) * 1.4))
  );
  void sigma.getCamera().animate(
    { x: (minX + maxX) / 2, y: (minY + maxY) / 2, ratio },
    { duration: prefersReducedMotion() ? 0 : 420 }
  );
}

function legendForLens(lens: GraphLens): Array<[string, string]> {
  if (lens === "code") return [["repository", "Репозитории"], ["module", "Модули"], ["class", "Классы"], ["function", "Функции"], ["test", "Тесты"]];
  if (lens === "knowledge") return [["generation", "Поколения"], ["claim", "Знания"], ["entity", "Сущности"], ["source", "Источники"], ["evidence", "Доказательства"]];
  if (lens === "work") return [["task_generation", "Поколения задач"], ["task", "Задачи"], ["run", "Запуски"], ["agent", "Агенты"], ["skill", "Навыки"]];
  if (lens === "agent") return [["pack", "Пакеты"], ["agent", "Агенты"], ["skill", "Навыки"], ["instruction", "Инструкции"], ["adapter", "Адаптеры"]];
  if (lens === "evidence") return [["claim", "Утверждения"], ["evidence", "Фрагменты"], ["source", "Источники"], ["generation", "Поколения"]];
  return [["workspace", "Пространство"], ["manual_document", "Документы"], ["claim", "Знания"], ["task", "Работа"], ["skill", "Возможности"], ["repository", "Код"]];
}

function lensControlLabel(lens: GraphLens): string {
  return lens === "universe" ? "Вся вселенная" : lensLabel(lens);
}

// Layout/visual tests mount a real Chromium but must never spawn a background FA2 worker;
// jsdom has no Worker at all. In both cases we substitute an inert supervisor so lifecycle
// assertions stay deterministic and the main thread never blocks on physics.
function inertSupervisorFactory(): SupervisorFactory {
  return () => ({ start() {}, stop() {}, kill() {}, isRunning: () => false });
}

const structuredRings = ["core", "instruction", "capability", "work", "document", "code", "knowledge", "evidence", "application"];

function SigmaCanvas({
  snapshot,
  lens,
  layout,
  theme,
  visibleIds,
  visibleEdgeIds,
  filtersPending,
  selectedId,
  onSelect,
  onClear
}: {
  snapshot: GraphSnapshot;
  lens: GraphLens;
  layout: Layout;
  theme: "dark" | "light" | "contrast";
  visibleIds: ReadonlySet<string>;
  visibleEdgeIds: ReadonlySet<string>;
  filtersPending: boolean;
  selectedId: string | null;
  onSelect: (selection: Selection) => void;
  onClear: () => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const renderer = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const controllerRef = useRef<ForceLayoutController | null>(null);
  const builtSignatureRef = useRef<string>("");
  const cacheKeyRef = useRef<string>("");
  const selectedRef = useRef<string | null>(selectedId);
  const hoverRef = useRef<string | null>(null);
  const zoomedInRef = useRef(false);
  const focusRef = useRef<Set<string> | null>(null);
  const focusRootRef = useRef<string | null>(null);
  const draggingRef = useRef<string | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const dragMovedRef = useRef(false);
  const suppressStageClickRef = useRef(false);
  const suppressNodeClickRef = useRef(false);
  const lensRef = useRef(lens);
  const layoutRef = useRef(layout);
  const visibleIdsRef = useRef(visibleIds);
  const visibleEdgeIdsRef = useRef(visibleEdgeIds);
  const onSelectRef = useRef(onSelect);
  const onClearRef = useRef(onClear);

  const [physics, setPhysics] = useState<PhysicsState>("stabilized");
  const [activeCount, setActiveCount] = useState<{ nodes: number; edges: number }>({ nodes: 0, edges: 0 });
  const [pinned, setPinned] = useState(0);
  const [selectedPinned, setSelectedPinned] = useState(false);
  const [focusRoot, setFocusRoot] = useState<string | null>(null);
  const [focusDepth, setFocusDepth] = useState<1 | 2>(1);
  const [hoverDirection, setHoverDirection] = useState<{ incoming: number; outgoing: number } | null>(null);
  const reducedMotion = usePrefersReducedMotion();

  const signature = useMemo(
    () => activeSignature(snapshot.graph_snapshot_id, visibleIds, visibleEdgeIds, lens),
    [lens, snapshot.graph_snapshot_id, visibleEdgeIds, visibleIds]
  );

  useEffect(() => {
    selectedRef.current = selectedId;
    renderer.current?.refresh({ skipIndexation: true });
  }, [selectedId]);

  useEffect(() => {
    const graph = graphRef.current;
    setSelectedPinned(Boolean(selectedId && graph?.hasNode(selectedId) && graph.getNodeAttribute(selectedId, "pinned")));
  }, [pinned, selectedId]);

  useEffect(() => {
    lensRef.current = lens;
    layoutRef.current = layout;
    visibleIdsRef.current = visibleIds;
    visibleEdgeIdsRef.current = visibleEdgeIds;
    onSelectRef.current = onSelect;
    onClearRef.current = onClear;
    if (focusRootRef.current && !visibleIds.has(focusRootRef.current)) {
      setFocusRoot(null);
      setFocusDepth(1);
    }
    if (layout !== "force" && focusRootRef.current) {
      setFocusRoot(null);
      setFocusDepth(1);
    }
    // Orbit and Structured hide filtered nodes in the reducer; the force subgraph is rebuilt
    // by the signature effect below, so a plain refresh is enough here.
    if (layout !== "force") renderer.current?.refresh({ skipIndexation: true });
  }, [layout, lens, onClear, onSelect, visibleEdgeIds, visibleIds]);

  // Recompute the local-focus set (client-side neighbourhood over verified edges) whenever the
  // focus root or depth changes; drives dimming, labels and the focus camera.
  useEffect(() => {
    if (!focusRoot) {
      focusRef.current = null;
      focusRootRef.current = null;
      renderer.current?.refresh({ skipIndexation: true });
      return;
    }
    const activeEdges = snapshot.edges.filter((edge) => visibleEdgeIdsRef.current.has(edge.edge_id));
    focusRef.current = neighbourhood(activeEdges, visibleIdsRef.current, focusRoot, focusDepth);
    focusRootRef.current = focusRoot;
    const sigma = renderer.current;
    if (sigma) {
      sigma.refresh({ skipIndexation: true });
      focusCameraOnNodes(sigma, focusRef.current);
    }
  }, [focusDepth, focusRoot, signature, snapshot.edges]);

  // Heavy lifecycle: build the graph + renderer for the current layout/snapshot/theme. Filter
  // changes do NOT re-run this (see the signature effect); only a structural context change does.
  useEffect(() => {
    if (!container.current) return;
    const colors = theme === "light" ? lightKindColor : darkKindColor;
    const labelColor = theme === "light" ? "#27313b" : "#dce2e8";
    const edgeColor = theme === "light" ? "rgba(72, 82, 92, 0.28)" : theme === "contrast" ? "rgba(255, 255, 255, 0.42)" : "rgba(112, 130, 150, 0.3)";
    const importantEdgeColor = theme === "light" ? "rgba(56, 66, 76, 0.58)" : theme === "contrast" ? "rgba(255, 255, 255, 0.82)" : "rgba(164, 181, 198, 0.56)";
    const dimmedColor = theme === "light" ? "#d8d6cf" : theme === "contrast" ? "#303030" : "#22282f";
    const supportsColor = theme === "light" ? "#c9502b" : "#ff8a5b";
    const incomingColor = theme === "light" ? "#9b5525" : "#ffad70";
    const outgoingColor = theme === "light" ? "#116f72" : "#63d8d2";
    const selectedColor = theme === "light" ? "#5e50b4" : "#c8bfff";
    const colorForKind = (kind: string) => colors[kind] ?? (theme === "light" ? "#59616b" : "#a6afbb");
    const denseOverview = snapshot.nodes.length > 1_200;
    const nodesById = new Map(snapshot.nodes.map((node) => [node.node_id, node]));

    const buildBase = (): Graph => {
      const graph = new Graph({ multi: true, type: "directed", allowSelfLoops: false });
      for (const node of snapshot.nodes) {
        graph.addNode(node.node_id, {
          label: visualNodeLabel(node),
          kind: node.kind,
          status: node.status,
          x: node.x,
          y: node.y,
          size: layout === "orbit" && codeKinds.has(node.kind)
            ? node.metadata.is_god === "true" || node.metadata.is_bridge === "true"
              ? 3.2
              : Math.max(0.5, Math.min(1.6, 0.45 + node.importance / 95))
            : displaySize(node.importance),
          color: colorForKind(node.kind),
          baseColor: colorForKind(node.kind),
          importance: node.importance,
          isGod: node.metadata.is_god === "true",
          isBridge: node.metadata.is_bridge === "true",
          pinned: false
        });
      }
      for (const edge of snapshot.edges) {
        if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
        // Self-loops and repeated keys are valid data but not drawable; the list fallback keeps them.
        if (edge.source === edge.target || graph.hasEdge(edge.edge_id)) continue;
        const supports = edge.kind === "supports";
        const sourceNode = nodesById.get(edge.source);
        const targetNode = nodesById.get(edge.target);
        const importantEndpoint = [sourceNode, targetNode].some((node) => Boolean(node && (
          node.importance >= 86 ||
          node.metadata.is_god === "true" ||
          node.metadata.is_bridge === "true" ||
          node.kind === "workspace" ||
          node.kind === "repository"
        )));
        graph.addEdgeWithKey(edge.edge_id, edge.source, edge.target, {
          kind: edge.kind,
          color: supports ? supportsColor : edgeColor,
          baseColor: supports ? supportsColor : edgeColor,
          size: supports ? 0.82 : 0.18,
          lodVisible: !denseOverview || supports || importantEndpoint || stableHash(edge.edge_id) < 0.075,
          type: "line"
        });
      }
      if (layout === "structured") {
        for (const [ringIndex, ring] of structuredRings.entries()) {
          const ringNodes = snapshot.nodes.filter((node) => node.ring === ring).sort((a, b) => a.node_id.localeCompare(b.node_id));
          ringNodes.forEach((node, index) => {
            graph.setNodeAttribute(node.node_id, "x", ringIndex * 260 - 780);
            graph.setNodeAttribute(node.node_id, "y", (index - (ringNodes.length - 1) / 2) * 95);
          });
        }
      }
      return graph;
    };

    const buildActive = (): Graph => {
      const graph = buildForceGraph({
        snapshot,
        visibleIds: visibleIdsRef.current,
        visibleEdgeIds: visibleEdgeIdsRef.current,
        labelFor: visualNodeLabel,
        colorForKind,
        edgeColor,
        importantEdgeColor,
        supportsColor
      });
      seedForcePositions(graph);
      const key = activeSignature(snapshot.graph_snapshot_id, visibleIdsRef.current, visibleEdgeIdsRef.current, lensRef.current);
      restoreForceLayout(key, graph);
      cacheKeyRef.current = key;
      return graph;
    };

    const graph = layout === "force" ? buildActive() : buildBase();
    graphRef.current = graph;
    // Record the active set this render built so the filter effect only rebuilds on real change.
    builtSignatureRef.current = activeSignature(snapshot.graph_snapshot_id, visibleIdsRef.current, visibleEdgeIdsRef.current, lensRef.current);

    const sigma = new Sigma(graph, container.current, {
      renderEdgeLabels: false,
      labelColor: { color: labelColor },
      labelFont: "IBM Plex Sans Variable",
      labelWeight: "500",
      labelSize: 11,
      defaultDrawNodeHover: createGraphNodeHoverRenderer(theme),
      labelDensity: layout === "force" ? 0.065 : 0.14,
      labelGridCellSize: layout === "force" ? 170 : 145,
      labelRenderedSizeThreshold: layout === "force" ? 9 : layout === "orbit" ? 3 : 7,
      defaultNodeColor: theme === "light" ? "#59616b" : "#a6afbb",
      defaultEdgeColor: edgeColor,
      minCameraRatio: 0.05,
      maxCameraRatio: 6,
      minEdgeThickness: layout === "force" ? 0.25 : 0.08,
      stagePadding: 44,
      zIndex: true,
      allowInvalidContainer: false
    });
    renderer.current = sigma;

    sigma.setSetting("nodeReducer", (node, data) => {
      const active = graphRef.current;
      const kind = String(data.kind);
      // In force mode the graph is already the active subgraph; orbit/structured hide here.
      if (layoutRef.current !== "force" && (!isVisible(kind, lensRef.current) || !visibleIdsRef.current.has(node))) {
        return { ...data, hidden: true };
      }
      const focusSet = focusRef.current;
      if (focusSet) {
        const inFocus = focusSet.has(node);
        const isRoot = focusRootRef.current === node;
        return {
          ...data,
          label: inFocus ? String(data.label) : "",
          color: inFocus ? String(data.color) : dimmedColor,
          size: Number(data.size) * (isRoot ? 1.7 : inFocus ? 1.2 : 0.85),
          zIndex: isRoot ? 10 : inFocus ? 5 : 0,
          highlighted: isRoot
        };
      }
      const pointer = hoverRef.current ?? selectedRef.current;
      const isHovered = hoverRef.current === node;
      const isSelected = selectedRef.current === node;
      const isFocus = isHovered || isSelected;
      const isNeighbor = pointer && active ? active.hasNode(pointer) && active.hasNode(node) && active.areNeighbors(pointer, node) : false;
      const dimmed = Boolean(pointer && !isFocus && !isNeighbor);
      const isPinned = Boolean(data.pinned);
      const keepsLabel = isFocus || isNeighbor || isPinned || (zoomedInRef.current && Number(data.importance) >= 52) || keepsGlobalLabel(kind, Number(data.importance), Boolean(data.isGod), Boolean(data.isBridge));
      return {
        ...data,
        label: keepsLabel ? String(data.label) : "",
        color: isSelected ? selectedColor : dimmed ? dimmedColor : String(data.color),
        size: Number(data.size) * (isSelected ? 1.6 : isHovered ? 1.3 : isNeighbor ? 1.16 : isPinned ? 1.12 : 1),
        zIndex: isSelected ? 12 : isHovered ? 10 : isNeighbor ? 5 : isPinned ? 4 : 1,
        highlighted: isSelected || isPinned
      };
    });
    sigma.setSetting("edgeReducer", (edge, data) => {
      const active = graphRef.current;
      if (!active || !active.hasEdge(edge)) return { ...data, hidden: true };
      const [source, target] = active.extremities(edge);
      if (layoutRef.current !== "force") {
        const sourceVisible = isVisible(String(active.getNodeAttribute(source, "kind")), lensRef.current);
        const targetVisible = isVisible(String(active.getNodeAttribute(target, "kind")), lensRef.current);
        if (!sourceVisible || !targetVisible || !visibleIdsRef.current.has(source) || !visibleIdsRef.current.has(target) || !visibleEdgeIdsRef.current.has(edge)) {
          return { ...data, hidden: true };
        }
      }
      const focusSet = focusRef.current;
      if (focusSet) {
        const inFocus = focusSet.has(source) && focusSet.has(target);
        return { ...data, hidden: false, color: inFocus ? String(data.color) : dimmedColor, size: Number(data.size) * (inFocus ? 1.5 : 0.35) };
      }
      const pointer = hoverRef.current ?? selectedRef.current;
      const outgoing = pointer === source;
      const incoming = pointer === target;
      const connected = outgoing || incoming;
      const overviewLodHidden = layoutRef.current === "orbit" && data.lodVisible === false && !connected;
      return {
        ...data,
        hidden: overviewLodHidden,
        color: pointer && !connected ? dimmedColor : outgoing ? outgoingColor : incoming ? incomingColor : String(data.color),
        size: Number(data.size) * (connected ? 2.4 : 1)
      };
    });

    const camera = sigma.getCamera();
    const handleCameraUpdate = ({ ratio }: { ratio: number }) => {
      const zoomedIn = ratio < 0.34;
      if (zoomedIn === zoomedInRef.current) return;
      zoomedInRef.current = zoomedIn;
      sigma.refresh({ skipIndexation: true });
    };
    camera.on("updated", handleCameraUpdate);

    sigma.on("clickNode", ({ node, event }) => {
      if (suppressNodeClickRef.current) return;
      const original = event.original;
      // Shift-click toggles a pin instead of selecting, so pinning never fights the inspector.
      if (layoutRef.current === "force" && original instanceof MouseEvent && original.shiftKey) {
        const graphNow = graphRef.current;
        if (graphNow?.hasNode(node)) {
          controllerRef.current?.beginMutation();
          setNodePinned(graphNow, node, !graphNow.getNodeAttribute(node, "pinned"));
          setPinned(countPinned(graphNow));
          controllerRef.current?.reheat();
          sigma.refresh({ skipIndexation: true });
        }
        return;
      }
      const selected = snapshot.nodes.find((item) => item.node_id === node);
      if (selected) onSelectRef.current(toSelection(selected, snapshot));
    });
    sigma.on("doubleClickNode", ({ node, event }) => {
      if (layoutRef.current !== "force") return;
      event.preventSigmaDefault();
      setFocusDepth(1);
      setFocusRoot(node);
    });
    sigma.on("clickStage", () => {
      if (suppressStageClickRef.current) return;
      onClearRef.current();
    });
    sigma.on("enterNode", ({ node }) => {
      hoverRef.current = node;
      const graphNow = graphRef.current;
      if (graphNow?.hasNode(node)) {
        setHoverDirection({ incoming: graphNow.inDegree(node), outgoing: graphNow.outDegree(node) });
      }
      if (container.current) container.current.style.cursor = draggingRef.current ? "grabbing" : "pointer";
      sigma.refresh({ skipIndexation: true });
    });
    sigma.on("leaveNode", () => {
      hoverRef.current = null;
      setHoverDirection(null);
      if (container.current) container.current.style.cursor = "default";
      sigma.refresh({ skipIndexation: true });
    });

    // Obsidian-style drag: only in force mode. Moving a node fixes it (FA2 honours `fixed`), so it
    // stays where the user drops it; the camera is held still for the duration of the drag.
    sigma.on("downNode", ({ node, event }) => {
      if (layoutRef.current !== "force") return;
      draggingRef.current = node;
      dragStartRef.current = { x: event.x, y: event.y };
      dragMovedRef.current = false;
      if (!sigma.getCustomBBox()) sigma.setCustomBBox(sigma.getBBox());
      camera.disable();
      if (container.current) container.current.style.cursor = "grabbing";
      event.preventSigmaDefault();
    });
    sigma.on("moveBody", ({ event }) => {
      const node = draggingRef.current;
      if (!node) return;
      const graphNow = graphRef.current;
      if (!graphNow?.hasNode(node)) return;
      const start = dragStartRef.current;
      if (!dragMovedRef.current && start && Math.hypot(event.x - start.x, event.y - start.y) < 3) return;
      if (!dragMovedRef.current) {
        controllerRef.current?.beginMutation();
        setNodePinned(graphNow, node, true);
        setPinned(countPinned(graphNow));
      }
      const position = sigma.viewportToGraph(event);
      dragMovedRef.current = true;
      graphNow.setNodeAttribute(node, "x", position.x);
      graphNow.setNodeAttribute(node, "y", position.y);
      event.preventSigmaDefault();
      if (event.original instanceof Event && event.original.cancelable) {
        event.original.preventDefault();
        event.original.stopPropagation();
      }
    });
    const endDrag = () => {
      const dragged = draggingRef.current;
      camera.enable();
      if (!dragged) return;
      const moved = dragMovedRef.current;
      draggingRef.current = null;
      dragStartRef.current = null;
      dragMovedRef.current = false;
      if (container.current) container.current.style.cursor = "default";
      if (moved) {
        suppressStageClickRef.current = true;
        suppressNodeClickRef.current = true;
        window.setTimeout(() => {
          suppressStageClickRef.current = false;
          suppressNodeClickRef.current = false;
        }, 0);
      }
      if (moved) {
        if (cacheKeyRef.current && graphRef.current) cacheForceLayout(cacheKeyRef.current, graphRef.current);
        controllerRef.current?.reheat();
      }
    };
    sigma.on("upNode", endDrag);
    sigma.on("upStage", endDrag);
    const mouseCaptor = sigma.getMouseCaptor();
    mouseCaptor.on("mouseup", endDrag);
    const stage = container.current;
    const handlePointerCancel = () => endDrag();
    const handleVisibility = () => { if (document.visibilityState !== "visible") endDrag(); };
    stage.addEventListener("pointercancel", handlePointerCancel);
    stage.addEventListener("lostpointercapture", handlePointerCancel);
    window.addEventListener("blur", handlePointerCancel);
    document.addEventListener("visibilitychange", handleVisibility);

    if (layout === "force") {
      const useRealWorker = typeof Worker !== "undefined" && document.documentElement.dataset.layoutTest !== "true";
      const controller = new ForceLayoutController(graph, {
        reducedMotion,
        onState: setPhysics,
        factory: useRealWorker ? undefined : inertSupervisorFactory()
      });
      controllerRef.current = controller;
      setActiveCount({ nodes: graph.order, edges: graph.size });
      setPinned(countPinned(graph));
      controller.start();
    } else {
      setActiveCount({ nodes: graph.order, edges: graph.size });
      setPhysics("stabilized");
    }

    const resetTimer = window.setTimeout(() => sigma.getCamera().animatedReset({ duration: reducedMotion ? 0 : 420 }), 80);
    return () => {
      window.clearTimeout(resetTimer);
      if (layout === "force" && cacheKeyRef.current && graphRef.current) {
        cacheForceLayout(cacheKeyRef.current, graphRef.current);
      }
      draggingRef.current = null;
      dragStartRef.current = null;
      dragMovedRef.current = false;
      camera.enable();
      camera.off("updated", handleCameraUpdate);
      mouseCaptor.off("mouseup", endDrag);
      stage.removeEventListener("pointercancel", handlePointerCancel);
      stage.removeEventListener("lostpointercapture", handlePointerCancel);
      window.removeEventListener("blur", handlePointerCancel);
      document.removeEventListener("visibilitychange", handleVisibility);
      controllerRef.current?.dispose();
      controllerRef.current = null;
      sigma.kill();
      renderer.current = null;
      graphRef.current = null;
    };
    // Filter/search changes are handled by the signature effect below, not by a full rebuild.
  }, [layout, reducedMotion, snapshot, theme]);

  // Filter changes inside force mode: rebuild only the active subgraph and swap it into the live
  // renderer, then restart a short physics burst. Never leaves the previous worker running.
  useEffect(() => {
    if (layout !== "force") return;
    const sigma = renderer.current;
    if (!sigma || builtSignatureRef.current === signature) return;
    const colors = theme === "light" ? lightKindColor : darkKindColor;
    const edgeColor = theme === "light" ? "rgba(72, 82, 92, 0.28)" : theme === "contrast" ? "rgba(255, 255, 255, 0.42)" : "rgba(112, 130, 150, 0.3)";
    const importantEdgeColor = theme === "light" ? "rgba(56, 66, 76, 0.58)" : theme === "contrast" ? "rgba(255, 255, 255, 0.82)" : "rgba(164, 181, 198, 0.56)";
    const supportsColor = theme === "light" ? "#c9502b" : "#ff8a5b";
    const graph = buildForceGraph({
      snapshot,
      visibleIds: visibleIdsRef.current,
      visibleEdgeIds: visibleEdgeIdsRef.current,
      labelFor: visualNodeLabel,
      colorForKind: (kind: string) => colors[kind] ?? (theme === "light" ? "#59616b" : "#a6afbb"),
      edgeColor,
      importantEdgeColor,
      supportsColor
    });
    seedForcePositions(graph);
    if (graphRef.current && cacheKeyRef.current) cacheForceLayout(cacheKeyRef.current, graphRef.current);
    controllerRef.current?.dispose();
    restoreForceLayout(signature, graph);
    sigma.setGraph(graph);
    graphRef.current = graph;
    builtSignatureRef.current = signature;
    cacheKeyRef.current = signature;
    setActiveCount({ nodes: graph.order, edges: graph.size });
    setPinned(countPinned(graph));
    const useRealWorker = typeof Worker !== "undefined" && document.documentElement.dataset.layoutTest !== "true";
    const controller = new ForceLayoutController(graph, {
      reducedMotion,
      onState: setPhysics,
      factory: useRealWorker ? undefined : inertSupervisorFactory()
    });
    controllerRef.current = controller;
    controller.start();
    sigma.refresh();
    if (focusRef.current) window.requestAnimationFrame(() => focusCameraOnNodes(sigma, focusRef.current ?? new Set()));
  }, [layout, reducedMotion, signature, snapshot, theme]);

  const relayout = () => {
    const graph = graphRef.current;
    if (!graph) return;
    controllerRef.current?.beginMutation();
    if (cacheKeyRef.current) clearCachedForceLayout(cacheKeyRef.current);
    clearPins(graph);
    setPinned(0);
    seedForcePositions(graph);
    if (cacheKeyRef.current) cacheForceLayout(cacheKeyRef.current, graph);
    controllerRef.current?.reheat();
    renderer.current?.refresh();
  };

  const unpinAll = () => {
    const graph = graphRef.current;
    if (!graph) return;
    controllerRef.current?.beginMutation();
    clearPins(graph);
    setPinned(0);
    if (cacheKeyRef.current) cacheForceLayout(cacheKeyRef.current, graph);
    controllerRef.current?.reheat();
    renderer.current?.refresh({ skipIndexation: true });
  };

  const toggleSelectedPin = () => {
    const graph = graphRef.current;
    if (!selectedId || !graph?.hasNode(selectedId)) return;
    controllerRef.current?.beginMutation();
    setNodePinned(graph, selectedId, !graph.getNodeAttribute(selectedId, "pinned"));
    setPinned(countPinned(graph));
    if (cacheKeyRef.current) cacheForceLayout(cacheKeyRef.current, graph);
    controllerRef.current?.reheat();
    renderer.current?.refresh({ skipIndexation: true });
  };

  const physicsLabel = filtersPending
    ? "Обновляем активный подграф…"
    : physics === "layouting"
      ? "Раскладываем граф…"
      : physics === "running"
        ? "Физика активна"
        : physics === "paused"
          ? "Физика приостановлена"
          : "Раскладка стабилизирована";

  return (
    <div className={`sigma-stage${reducedMotion ? " reduced-motion" : ""}`} aria-label="Интерактивная вселенная знаний" aria-busy={layout === "force" && (physics === "layouting" || filtersPending)} data-layout={layout} data-physics={physics}>
      <div className="cosmic-grid" aria-hidden="true"><i /><i /><i /><i /></div>
      <div ref={container} className="sigma-container" aria-hidden="true" />
      <button className="fit-graph" type="button" onClick={() => { void renderer.current?.getCamera().animatedReset({ duration: prefersReducedMotion() ? 0 : 360 }); }}><Crosshair size={15} /> Показать всё</button>
      {layout === "force" ? (
        <div className="physics-panel" role="group" aria-label="Управление силовой раскладкой">
          <div className={`physics-status state-${filtersPending ? "layouting" : physics}`} aria-live="polite"><i />{physicsLabel}</div>
          <span className="physics-count">{activeCount.nodes} узлов · {activeCount.edges} связей{pinned ? ` · ${pinned} закреплено` : ""}</span>
          {hoverDirection ? <span className="physics-direction"><b>↓ {hoverDirection.incoming}</b> входящих · <b>↑ {hoverDirection.outgoing}</b> исходящих</span> : null}
          {activeCount.nodes > FORCE_DETAIL_BUDGET ? <span className="physics-budget" title="Все узлы участвуют в worker-раскладке; подписи и толщина рёбер снижены">экономный уровень детализации</span> : null}
          {reducedMotion ? <span className="physics-budget">движение скрыто до конечной worker-итерации</span> : null}
          <div className="physics-actions">
            <button type="button" aria-label={physics === "running" || physics === "layouting" ? "Приостановить физику" : "Запустить физику"} title={physics === "running" || physics === "layouting" ? "Пауза" : "Пуск"} onClick={() => controllerRef.current?.toggle()}>{physics === "running" || physics === "layouting" ? <Pause size={14} /> : <Play size={14} />}</button>
            <button type="button" aria-label="Переразложить граф" title="Переразложить" onClick={relayout}><RotateCw size={14} /></button>
            {selectedId && visibleIds.has(selectedId) ? <button type="button" aria-label={selectedPinned ? "Открепить выбранный узел" : "Закрепить выбранный узел"} title={selectedPinned ? "Открепить выбранный" : "Закрепить выбранный"} onClick={toggleSelectedPin}>{selectedPinned ? <PinOff size={14} /> : <Pin size={14} />}</button> : null}
            <button type="button" aria-label="Открепить узлы" title="Открепить всё" onClick={unpinAll} disabled={!pinned}><PinOff size={14} /></button>
            {focusRoot ? (
              <>
                <button type="button" aria-label="Показать больше соседей" title="Ещё уровень" onClick={() => setFocusDepth((value) => (value === 1 ? 2 : 1))}><Focus size={14} /> {focusDepth === 1 ? "2 уровня" : "1 уровень"}</button>
                <button type="button" aria-label="Вернуться ко всему графу" title="Ко всему графу" onClick={() => { setFocusRoot(null); setFocusDepth(1); void renderer.current?.getCamera().animatedReset({ duration: prefersReducedMotion() ? 0 : 360 }); }}><Shrink size={14} /> Весь граф</button>
              </>
            ) : selectedId && visibleIds.has(selectedId) ? (
              <button type="button" aria-label="Сфокусироваться на выбранном" title="Сфокусироваться" onClick={() => { setFocusDepth(1); setFocusRoot(selectedId); }}><Focus size={14} /> Сфокусироваться</button>
            ) : null}
          </div>
        </div>
      ) : null}
      <div className="graph-legend" aria-label="Легенда графа">
        {legendForLens(lens).map(([kind, label]) => <span key={kind}><i style={{ background: (theme === "light" ? lightKindColor : darkKindColor)[kind] }} />{label}</span>)}
      </div>
    </div>
  );
}

export function Universe({
  theme,
  selectedId,
  focusedDocumentId,
  onSelect,
  onClear
}: {
  theme: "dark" | "light" | "contrast";
  selectedId: string | null;
  focusedDocumentId?: string | null;
  onSelect: (selection: Selection) => void;
  onClear: () => void;
}) {
  const universe = useUniverse();
  const documentGraph = useDocumentGraph(focusedDocumentId ?? null);
  const graph = useMemo<GraphSnapshot | undefined>(() => {
    const base = universe.data;
    const slice = documentGraph.data;
    if (!base || !slice) return base;
    const nodes = new Map(base.nodes.map((node) => [node.node_id, node]));
    const edges = new Map(base.edges.map((edge) => [edge.edge_id, edge]));
    for (const node of slice.nodes) nodes.set(node.node_id, node);
    for (const edge of slice.edges) edges.set(edge.edge_id, edge);
    return { ...base, nodes: [...nodes.values()], edges: [...edges.values()] };
  }, [documentGraph.data, universe.data]);
  const documentFocusIds = useMemo(
    () => new Set(documentGraph.data?.nodes.map((node) => node.node_id) ?? []),
    [documentGraph.data?.nodes]
  );
  const [lens, setLens] = useState<GraphLens>("universe");
  const [layout, setLayout] = useState<Layout>("orbit");
  const [view, setView] = useState<ViewMode>("graph");
  const [query, setQuery] = useState("");
  const [naturalQuery, setNaturalQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [relationFilter, setRelationFilter] = useState("all");
  const [confidenceFilter, setConfidenceFilter] = useState<EdgeConfidence | "all">("all");
  const [communityFilter, setCommunityFilter] = useState("all");
  const [importanceFilter, setImportanceFilter] = useState<"all" | "god" | "bridge">("all");
  const [changedOnly, setChangedOnly] = useState(false);
  const [depth, setDepth] = useState<1 | 2>(2);
  const [pathSource, setPathSource] = useState("");
  const [pathTarget, setPathTarget] = useState("");
  const [graphResult, setGraphResult] = useState<CodeGraphQueryResult | null>(null);
  const codeStatus = useCodeGraphStatus(lens === "code");
  const graphQuery = useCodeGraphQuery();
  const neighbors = useCodeGraphTraversal("neighbors");
  const pathQuery = useCodeGraphTraversal("path");
  const impact = useCodeGraphTraversal("impact");
  const updateGraph = useCodeGraphMutation("update");
  const rebuildGraph = useCodeGraphMutation("rebuild");
  const codeNodes = useMemo(
    () => (graph?.nodes ?? []).filter((node) => codeKinds.has(node.kind)),
    [graph?.nodes]
  );
  const codeKindsAvailable = useMemo(
    () => [...new Set(codeNodes.map((node) => node.kind))].sort(),
    [codeNodes]
  );
  const communities = useMemo(
    () => [...new Set(codeNodes.map((node) => node.metadata.community_id).filter(Boolean))].sort(),
    [codeNodes]
  );
  const changedPaths = useMemo(
    () => new Set(codeStatus.data?.changed_paths ?? []),
    [codeStatus.data?.changed_paths]
  );
  const resultIds = useMemo(
    () => graphResult ? new Set(graphResult.nodes.map((node) => node.node_id)) : null,
    [graphResult]
  );
  const nodes = useMemo(
    () =>
      (graph?.nodes ?? []).filter(
        (node) => {
          if (focusedDocumentId && lens === "universe" && documentGraph.data && !documentFocusIds.has(node.node_id)) return false;
          if (!isVisible(node.kind, lens)) return false;
          const matchesText = `${node.label} ${nodeLabel(node)} ${node.subtitle} ${node.kind} ${kindLabel(node.kind)} ${node.status} ${statusLabel(node.status)}`.toLowerCase().includes(query.toLowerCase());
          if (!matchesText || lens !== "code") return matchesText;
          if (kindFilter !== "all" && node.kind !== kindFilter) return false;
          if (communityFilter !== "all" && node.metadata.community_id !== communityFilter) return false;
          if (importanceFilter === "god" && node.metadata.is_god !== "true") return false;
          if (importanceFilter === "bridge" && node.metadata.is_bridge !== "true") return false;
          if (changedOnly && !changedPaths.has(node.metadata.path ?? "")) return false;
          if (resultIds && !resultIds.has(node.node_id)) return false;
          return true;
        }
      ),
    [changedOnly, changedPaths, communityFilter, documentFocusIds, documentGraph.data, focusedDocumentId, graph?.nodes, importanceFilter, kindFilter, lens, query, resultIds]
  );
  const visibleIds = useMemo(() => new Set(nodes.map((node) => node.node_id)), [nodes]);
  const visibleEdges = useMemo(
    () => (graph?.edges ?? []).filter((edge) => {
      if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) return false;
      if (lens !== "code") return true;
      if (relationFilter !== "all" && edge.kind !== relationFilter) return false;
      if (confidenceFilter !== "all" && edge.metadata.confidence !== confidenceFilter) return false;
      if (graphResult && !graphResult.edges.some((resultEdge) => resultEdge.edge_id === edge.metadata.code_edge_id)) return false;
      return true;
    }),
    [confidenceFilter, graph?.edges, graphResult, lens, relationFilter, visibleIds]
  );
  const visibleEdgeIds = useMemo(() => new Set(visibleEdges.map((edge) => edge.edge_id)), [visibleEdges]);
  const forceVisibleIds = useMemo(() => {
    if (lens !== "code" || (relationFilter === "all" && confidenceFilter === "all")) return visibleIds;
    const endpoints = new Set<string>();
    for (const edge of visibleEdges) {
      endpoints.add(edge.source);
      endpoints.add(edge.target);
    }
    // Preserve an explicitly selected, otherwise-visible node as an explained isolated context
    // instead of making it disappear when a relationship filter excludes all of its edges.
    if (selectedId && visibleIds.has(selectedId)) endpoints.add(selectedId);
    return endpoints;
  }, [confidenceFilter, lens, relationFilter, selectedId, visibleEdges, visibleIds]);
  const [forceVisibility, setForceVisibility] = useState<{ nodeIds: ReadonlySet<string>; edgeIds: ReadonlySet<string> }>(() => ({ nodeIds: forceVisibleIds, edgeIds: visibleEdgeIds }));
  const priorLayoutRef = useRef<Layout>(layout);
  useEffect(() => {
    const alreadyInForce = priorLayoutRef.current === "force";
    priorLayoutRef.current = layout;
    const apply = () => setForceVisibility((current) => current.nodeIds === forceVisibleIds && current.edgeIds === visibleEdgeIds ? current : { nodeIds: forceVisibleIds, edgeIds: visibleEdgeIds });
    if (layout !== "force" || !alreadyInForce) {
      apply();
      return;
    }
    const timer = window.setTimeout(apply, 220);
    return () => window.clearTimeout(timer);
  }, [forceVisibleIds, layout, visibleEdgeIds]);
  const layoutVisibleIds = layout === "force" ? forceVisibility.nodeIds : visibleIds;
  const layoutVisibleEdgeIds = layout === "force" ? forceVisibility.edgeIds : visibleEdgeIds;
  const forceFiltersPending = layout === "force" && (layoutVisibleIds !== forceVisibleIds || layoutVisibleEdgeIds !== visibleEdgeIds);
  const codeRelations = useMemo(
    () => [...new Set((graph?.edges ?? []).filter((edge) => edge.metadata.code_edge_id).map((edge) => edge.kind))].sort(),
    [graph?.edges]
  );
  const canTraverse = codeStatus.data?.state === "current" && Boolean(codeStatus.data.snapshot_id);
  const mutationPending = updateGraph.isPending || rebuildGraph.isPending;
  const selectedNode = selectedId ? graph?.nodes.find((node) => node.node_id === selectedId) ?? null : null;
  const selectedHidden = Boolean(selectedId && (!selectedNode || !visibleIds.has(selectedId)));

  const revealSelected = () => {
    setLens("universe");
    setQuery("");
    setKindFilter("all");
    setRelationFilter("all");
    setConfidenceFilter("all");
    setCommunityFilter("all");
    setImportanceFilter("all");
    setChangedOnly(false);
    setGraphResult(null);
  };

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ operation: "neighbors" | "impact" | "path-source" | "refresh" | "ambiguous"; nodeId: string; direction?: "both" | "in" | "out" }>).detail;
      const snapshotId = codeStatus.data?.snapshot_id;
      if (!detail) return;
      if (detail.operation === "path-source") {
        setPathSource(detail.nodeId);
        return;
      }
      if (detail.operation === "ambiguous") {
        setConfidenceFilter("AMBIGUOUS");
        setGraphResult(null);
        return;
      }
      if (detail.operation === "refresh") {
        updateGraph.mutate({ expected_snapshot_id: snapshotId ?? null });
        return;
      }
      if (!snapshotId || !canTraverse) return;
      if (detail.operation === "impact") {
        impact.mutate({ node_id: detail.nodeId, expected_snapshot_id: snapshotId, depth: 3 }, { onSuccess: setGraphResult });
      } else {
        neighbors.mutate({ node_id: detail.nodeId, expected_snapshot_id: snapshotId, depth, direction: detail.direction ?? "both" }, { onSuccess: setGraphResult });
      }
    };
    window.addEventListener("raytsystem:code-action", listener);
    return () => window.removeEventListener("raytsystem:code-action", listener);
  }, [canTraverse, codeStatus.data?.snapshot_id, depth, impact, neighbors, updateGraph]);

  if (universe.isLoading || (focusedDocumentId && documentGraph.isLoading)) return <LoadingState label="Читаем проверенный срез графа…" />;
  if (universe.isError || !universe.data) return <ErrorState error={universe.error} onRetry={() => void universe.refetch()} />;
  if (focusedDocumentId && documentGraph.isError) return <ErrorState error={documentGraph.error} onRetry={() => void documentGraph.refetch()} />;
  if (!graph?.nodes.length) return <EmptyState title="Нет канонического поколения знаний">Импортируйте источники через документированный CLI-процесс, чтобы создать проверенную вселенную.</EmptyState>;
  return (
    <div className="route universe-route">
      <div className="universe-toolbar">
        <div className="lens-switch" aria-label="Слой вселенной">
          {graph.supported_lenses.map((item) => (
            <button aria-pressed={lens === item} className={lens === item ? "active" : ""} type="button" key={item} onClick={() => setLens(item)}>
              {item === "universe" ? <Orbit size={15} /> : item === "evidence" ? <Focus size={15} /> : item === "code" ? <Braces size={15} /> : <Waypoints size={15} />}
              {lensControlLabel(item)}
            </button>
          ))}
        </div>
        <label className="graph-search"><span className="sr-only">Найти узел вселенной</span><Search size={15} /><input aria-label="Найти узел вселенной" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти узел" /></label>
        <div className="layout-switch" role="toolbar" aria-label="Раскладка графа">
          <button aria-label="Орбита — обзор слоёв" aria-pressed={layout === "orbit"} className={layout === "orbit" ? "active" : ""} type="button" onClick={() => setLayout("orbit")} title="Орбита — детерминированный обзор слоёв"><Orbit size={15} /> Орбита</button>
          <button aria-label="Связи — интерактивный граф отношений" aria-pressed={layout === "force"} className={layout === "force" ? "active" : ""} type="button" onClick={() => setLayout("force")} title="Связи — интерактивный force-граф отношений"><Sparkles size={15} /> Связи</button>
          <button aria-label="Структура — иерархическое представление" aria-pressed={layout === "structured"} className={layout === "structured" ? "active" : ""} type="button" onClick={() => setLayout("structured")} title="Структура — слоистое иерархическое представление"><Share2 size={15} /> Структура</button>
        </div>
        <div className="view-switch" aria-label="Граф или список"><button aria-pressed={view === "graph"} className={view === "graph" ? "active" : ""} type="button" onClick={() => setView("graph")}><Waypoints size={15} /> Граф</button><button aria-pressed={view === "list"} className={view === "list" ? "active" : ""} type="button" onClick={() => setView("list")}><List size={15} /> Список</button></div>
      </div>
      <div className="universe-meta">
        <div className="universe-fingerprint"><span><i /> {focusedDocumentId ? "document projection" : "проверенный срез"}</span><code>{shortId(focusedDocumentId ? documentGraph.data?.snapshot_id : lens === "code" ? graph.code_snapshot_id : graph.graph_snapshot_id, 12, 8)}</code><span>видно {nodes.length} из {lens === "code" ? graph.code_node_count : graph.nodes.length}</span><span><Filter size={13} /> линза: {lensControlLabel(lens)}</span></div>
        {focusedDocumentId ? <div className="selection-hidden-note" role="status"><Network size={14} /><span>Фокус на документе и его ссылках. Эта проекция не делает заметку canonical claim.</span>{documentGraph.data?.truncated ? <em>Срез ограничен бюджетом</em> : null}</div> : null}
        {selectedHidden ? <div className="selection-hidden-note" role="status"><AlertTriangle size={14} /><span>{selectedNode ? "Выбранный узел скрыт текущим слоем или фильтрами; Inspector остаётся открыт." : "Выбранный узел отсутствует в новом snapshot; Inspector показывает предыдущий контекст."}</span>{selectedNode ? <button type="button" onClick={revealSelected}>Показать выбранный</button> : null}</div> : null}
        {lens === "code" ? (
          <section className="code-graph-console panel" aria-label="Управление графом кода">
            <header>
              <div className={`code-freshness state-${codeStatus.data?.state ?? "missing"}`}><i /><span>{statusLabel(codeStatus.data?.state ?? "missing")}</span><code>{shortId(codeStatus.data?.snapshot_fingerprint, 9, 6)}</code></div>
              <div className="code-metrics"><span>{codeStatus.data?.file_count ?? 0} файлов</span><span>{codeStatus.data?.node_count ?? 0} узлов</span><span>{codeStatus.data?.edge_count ?? 0} связей</span><span>{codeStatus.data?.ambiguous_edges ?? 0} неоднозначных</span></div>
              <div className="code-maintenance">
                <button type="button" onClick={() => void codeStatus.refetch()} disabled={codeStatus.isFetching} title="Полная проверка свежести"><RefreshCw className={codeStatus.isFetching ? "spin" : ""} size={14} /> Проверить</button>
                <button type="button" onClick={() => updateGraph.mutate({ expected_snapshot_id: codeStatus.data?.snapshot_id ?? null })} disabled={mutationPending || codeStatus.data?.state === "missing"}><GitCompareArrows size={14} /> Обновить изменённое</button>
                <button type="button" className={codeStatus.data?.state === "missing" ? "primary-action" : undefined} onClick={() => rebuildGraph.mutate({ expected_snapshot_id: codeStatus.data?.snapshot_id ?? null })} disabled={mutationPending}><RotateCcw className={rebuildGraph.isPending ? "spin" : ""} size={14} /> {codeStatus.data?.state === "missing" ? "Построить граф" : "Пересобрать"}</button>
              </div>
            </header>
            {codeStatus.data?.state !== "current" ? <div className="code-warning"><AlertTriangle size={14} /><span>{codeStatus.data?.state === "missing" ? "Постройте локальный граф. Канонические знания не изменятся." : "Граф устарел или ещё не проверен: traversal временно заблокирован."}</span></div> : null}
            <form className="graph-natural-query" onSubmit={(event) => {
              event.preventDefault();
              const snapshotId = codeStatus.data?.snapshot_id;
              if (!snapshotId || !naturalQuery.trim() || !canTraverse) return;
              graphQuery.mutate({ query: naturalQuery.trim(), expected_snapshot_id: snapshotId, depth }, { onSuccess: setGraphResult });
            }}>
              <Network size={16} /><input value={naturalQuery} onChange={(event) => setNaturalQuery(event.target.value)} aria-label="Вопрос к графу кода" placeholder="Как связаны QueryService и Universe?" /><select aria-label="Глубина графового запроса" value={depth} onChange={(event) => setDepth(Number(event.target.value) as 1 | 2)}><option value={1}>1 переход</option><option value={2}>2 перехода</option></select><button type="submit" disabled={!canTraverse || graphQuery.isPending}>Исследовать</button>
            </form>
            <div className="code-filter-grid" aria-label="Фильтры графа кода">
              <label>Тип узла<select aria-label="Тип узла" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">Все типы</option>{codeKindsAvailable.map((kind) => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}</select></label>
              <label>Тип связи<select aria-label="Тип связи" value={relationFilter} onChange={(event) => setRelationFilter(event.target.value)}><option value="all">Все связи</option>{codeRelations.map((relation) => <option key={relation} value={relation}>{relationLabel(relation)}</option>)}</select></label>
              <label>Достоверность<select aria-label="Достоверность связи" value={confidenceFilter} onChange={(event) => setConfidenceFilter(event.target.value as EdgeConfidence | "all")}><option value="all">Любая</option><option value="EXTRACTED">Извлечено</option><option value="INFERRED">Предположено</option><option value="AMBIGUOUS">Неоднозначно</option></select></label>
              <label>Сообщество<select aria-label="Сообщество" value={communityFilter} onChange={(event) => setCommunityFilter(event.target.value)}><option value="all">Все</option>{communities.map((community) => <option key={community} value={community}>#{community}</option>)}</select></label>
              <label>Роль узла<select aria-label="Роль узла" value={importanceFilter} onChange={(event) => setImportanceFilter(event.target.value as "all" | "god" | "bridge")}><option value="all">Все</option><option value="god">God nodes</option><option value="bridge">Bridge nodes</option></select></label>
              <label className="code-check"><input type="checkbox" checked={changedOnly} onChange={(event) => setChangedOnly(event.target.checked)} disabled={!codeStatus.data?.changed_paths.length} /> Только изменённое</label>
            </div>
            <div className="code-path-builder">
              <GitCompareArrows size={15} /><select aria-label="Начало пути" value={pathSource} onChange={(event) => setPathSource(event.target.value)}><option value="">Откуда</option>{codeNodes.map((node) => <option key={node.node_id} value={node.node_id}>{nodeLabel(node)}</option>)}</select><span>→</span><select aria-label="Конец пути" value={pathTarget} onChange={(event) => setPathTarget(event.target.value)}><option value="">Куда</option>{codeNodes.map((node) => <option key={node.node_id} value={node.node_id}>{nodeLabel(node)}</option>)}</select><button type="button" disabled={!canTraverse || !pathSource || !pathTarget || pathQuery.isPending} onClick={() => {
                const snapshotId = codeStatus.data?.snapshot_id;
                if (!snapshotId) return;
                pathQuery.mutate({ source_node_id: pathSource, target_node_id: pathTarget, expected_snapshot_id: snapshotId }, { onSuccess: setGraphResult });
              }}>Кратчайший путь</button><button type="button" disabled={!canTraverse || !selectedId || impact.isPending} onClick={() => {
                const snapshotId = codeStatus.data?.snapshot_id;
                if (!snapshotId || !selectedId) return;
                impact.mutate({ node_id: selectedId, expected_snapshot_id: snapshotId, depth: 3 }, { onSuccess: setGraphResult });
              }}>Влияние выбранного</button>
            </div>
            {graphResult ? <div className="code-query-result" aria-live="polite"><strong>{graphResult.operation === "path" ? "Кратчайший путь" : graphResult.operation === "impact" ? "Область влияния" : "Графовый контекст"}</strong><span>{graphResult.nodes.length} узлов · {graphResult.edges.length} связей · {graphResult.estimated_context_bytes} байт</span>{graphResult.truncated ? <em>ответ ограничен бюджетом</em> : null}<button type="button" onClick={() => setGraphResult(null)}>Сбросить</button>{graphResult.ordered_node_ids.length ? <ol>{graphResult.ordered_node_ids.map((nodeId) => <li key={nodeId}>{graphResult.nodes.find((node) => node.node_id === nodeId)?.label ?? shortId(nodeId)}</li>)}</ol> : null}<ul>{graphResult.edges.slice(0, 12).map((edge) => <li key={edge.edge_id}><code>{graphResult.nodes.find((node) => node.node_id === edge.source)?.label ?? shortId(edge.source)}</code><span>{relationLabel(edge.relation)} · {statusLabel(edge.confidence)}</span><code>{graphResult.nodes.find((node) => node.node_id === edge.target)?.label ?? shortId(edge.target)}</code></li>)}</ul></div> : null}
          </section>
        ) : null}
      </div>
      {!nodes.length ? (
        <EmptyState title="Узлы не найдены" action={<button className="secondary-button" type="button" onClick={() => setQuery("")}>Сбросить поиск</button>}>Измените запрос или выберите другой слой.</EmptyState>
      ) : view === "graph" ? (
        <SigmaCanvas snapshot={graph} lens={lens} layout={layout} theme={theme} visibleIds={layoutVisibleIds} visibleEdgeIds={layoutVisibleEdgeIds} filtersPending={forceFiltersPending} selectedId={selectedId} onSelect={onSelect} onClear={onClear} />
      ) : (
        <section className="graph-table panel" aria-label="Доступный список узлов графа">
          <header><Table2 size={17} /><strong>Объекты вселенной</strong><span>Равноценное представление для клавиатуры</span></header>
          <table><thead><tr><th>Объект</th><th>Тип</th><th>Путь</th><th>Статус</th><th>Связи</th></tr></thead><tbody>{nodes.map((node) => {
              const connections = visibleEdges.filter((edge) => edge.source === node.node_id || edge.target === node.node_id).length;
              return <tr key={node.node_id}><td><button type="button" onClick={() => onSelect(toSelection(node, graph))}><i style={{ background: (theme === "light" ? lightKindColor : darkKindColor)[node.kind] ?? "#a6afbb" }} /><span><strong>{nodeLabel(node)}</strong><small>{shortId(node.node_id, 11, 7)}</small></span></button></td><td>{kindLabel(node.kind)}</td><td><code>{node.metadata.path || "—"}{node.metadata.start_line ? `:${node.metadata.start_line}` : ""}</code></td><td><StatusPill status={node.status} /></td><td>{connections}</td></tr>;
            })}</tbody></table>
        </section>
      )}
    </div>
  );
}
