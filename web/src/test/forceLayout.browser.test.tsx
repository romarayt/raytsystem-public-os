import Graph from "graphology";
import { afterEach, describe, expect, it } from "vitest";
import { ForceLayoutController, type PhysicsState } from "../features/forceLayout";
import { seedForcePositions } from "../features/graphLayout";

function workerGraph(order = 64): Graph {
  const graph = new Graph({ type: "undirected" });
  for (let index = 0; index < order; index += 1) {
    graph.addNode(`n${index}`, {
      bucket: `community:${index % 4}`,
      size: 2,
      fixed: false,
      pinned: false,
      x: 0,
      y: 0
    });
  }
  for (let index = 0; index < order; index += 1) {
    graph.addEdgeWithKey(`ring:${index}`, `n${index}`, `n${(index + 1) % order}`, { weight: 1 });
    if (index % 4 === 0) {
      graph.addEdgeWithKey(`community:${index}`, `n${index}`, `n${(index + 4) % order}`, { weight: 1.3 });
    }
  }
  seedForcePositions(graph);
  return graph;
}

function positions(graph: Graph): Map<string, string> {
  return new Map(graph.mapNodes((id, attributes): [string, string] => [
    id,
    `${Number(attributes.x).toFixed(5)}:${Number(attributes.y).toFixed(5)}`
  ]));
}

function differs(first: ReadonlyMap<string, string>, second: ReadonlyMap<string, string>): boolean {
  for (const [id, position] of first) {
    if (second.get(id) !== position) return true;
  }
  return false;
}

async function waitUntil(predicate: () => boolean, timeoutMs = 4_000): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await new Promise<void>((resolve) => window.setTimeout(resolve, 25));
  }
  throw new Error(`Condition did not become true within ${timeoutMs}ms`);
}

const activeControllers = new Set<ForceLayoutController>();

afterEach(() => {
  for (const controller of activeControllers) controller.dispose();
  activeControllers.clear();
});

describe("ForceAtlas2 browser worker lifecycle", () => {
  it("moves the graph off-thread, honours fixed drag coordinates, reheats, and stops on dispose", async () => {
    expect(typeof Worker).toBe("function");
    const graph = workerGraph();
    const initial = positions(graph);
    const states: PhysicsState[] = [];
    const controller = new ForceLayoutController(graph, {
      reducedMotion: false,
      onState: (state) => states.push(state),
      maxDurationMs: 5_000,
      sampleIntervalMs: 200
    });
    activeControllers.add(controller);

    const startTime = performance.now();
    controller.start();
    expect(performance.now() - startTime).toBeLessThan(250);
    expect(controller.physics).toBe("layouting");
    await waitUntil(() => states.includes("running") && differs(initial, positions(graph)));
    expect(controller.isRunning()).toBe(true);

    controller.beginMutation();
    expect(controller.physics).toBe("paused");
    expect(controller.isRunning()).toBe(false);
    graph.mergeNodeAttributes("n0", { x: 432, y: -210, pinned: true, fixed: true });
    controller.reheat();
    await waitUntil(() => controller.physics === "running");
    await new Promise<void>((resolve) => window.setTimeout(resolve, 100));
    expect(graph.getNodeAttribute("n0", "x")).toBe(432);
    expect(graph.getNodeAttribute("n0", "y")).toBe(-210);

    controller.dispose();
    activeControllers.delete(controller);
    expect(controller.isRunning()).toBe(false);
    await new Promise<void>((resolve) => window.setTimeout(resolve, 50));
    const afterDispose = positions(graph);
    await new Promise<void>((resolve) => window.setTimeout(resolve, 150));
    expect(positions(graph)).toEqual(afterDispose);
  });
});
