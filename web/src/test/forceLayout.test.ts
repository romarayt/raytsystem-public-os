import Graph from "graphology";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ForceLayoutController, type LayoutSupervisor, type PhysicsState, type SupervisorFactory } from "../features/forceLayout";

interface FakeSupervisor extends LayoutSupervisor {
  starts: number;
  stops: number;
  killed: boolean;
  running: boolean;
}

function fakeFactory(): { factory: SupervisorFactory; created: FakeSupervisor[] } {
  const created: FakeSupervisor[] = [];
  const factory: SupervisorFactory = () => {
    const supervisor: FakeSupervisor = {
      starts: 0,
      stops: 0,
      killed: false,
      running: false,
      start() {
        this.starts += 1;
        this.running = true;
      },
      stop() {
        this.stops += 1;
        this.running = false;
      },
      kill() {
        this.killed = true;
        this.running = false;
      },
      isRunning() {
        return this.running;
      }
    };
    created.push(supervisor);
    return supervisor;
  };
  return { factory, created };
}

function connectedGraph(order = 6): Graph {
  const graph = new Graph();
  for (let index = 0; index < order; index += 1) {
    graph.addNode(`n${index}`, { x: index, y: -index, fixed: false });
  }
  for (let index = 1; index < order; index += 1) {
    graph.addEdge(`n${index}`, `n${index - 1}`);
  }
  return graph;
}

describe("ForceLayoutController lifecycle", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("spawns exactly one supervisor and enters the layouting state on start", () => {
    const { factory, created } = fakeFactory();
    const states: PhysicsState[] = [];
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: (s) => states.push(s), factory });
    controller.start();
    expect(created).toHaveLength(1);
    expect(created[0].starts).toBe(1);
    expect(controller.isRunning()).toBe(true);
    expect(controller.physics).toBe("layouting");
    expect(states).toContain("layouting");
    controller.dispose();
  });

  it("fails closed without listeners or timers when the worker factory throws", () => {
    const graph = connectedGraph();
    const states: PhysicsState[] = [];
    const controller = new ForceLayoutController(graph, {
      reducedMotion: false,
      onState: (state) => states.push(state),
      factory: () => { throw new Error("worker unavailable"); }
    });

    expect(() => controller.start()).not.toThrow();
    expect(controller.physics).toBe("paused");
    expect(controller.isRunning()).toBe(false);
    expect(states).toContain("paused");
    expect(graph.listenerCount("eachNodeAttributesUpdated")).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect(() => controller.dispose()).not.toThrow();
  });

  it("kills a partially created supervisor when worker start throws", () => {
    const graph = connectedGraph();
    const killed = vi.fn();
    const controller = new ForceLayoutController(graph, {
      reducedMotion: false,
      onState: () => {},
      factory: () => ({
        start: () => { throw new Error("blob worker rejected"); },
        stop: () => {},
        kill: killed,
        isRunning: () => false
      })
    });

    controller.start();
    expect(killed).toHaveBeenCalledTimes(1);
    expect(controller.physics).toBe("paused");
    expect(graph.listenerCount("eachNodeAttributesUpdated")).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("is idempotent: a second start does not create a second worker", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    controller.start();
    expect(created).toHaveLength(1);
    controller.dispose();
  });

  it("transitions layouting -> running once the worker reports node movement", () => {
    const { factory } = fakeFactory();
    const graph = connectedGraph();
    const states: PhysicsState[] = [];
    const controller = new ForceLayoutController(graph, { reducedMotion: false, onState: (s) => states.push(s), factory });
    controller.start();
    graph.updateEachNodeAttributes((_id, attr) => ({ ...attr, x: Number(attr.x) + 1 }));
    expect(controller.physics).toBe("running");
    controller.dispose();
  });

  it("kills the supervisor and stops reporting on dispose", () => {
    const { factory, created } = fakeFactory();
    const states: PhysicsState[] = [];
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: (s) => states.push(s), factory });
    controller.start();
    const before = states.length;
    controller.dispose();
    expect(created[0].killed).toBe(true);
    expect(controller.isRunning()).toBe(false);
    controller.reheat();
    expect(states.length).toBe(before);
  });

  it("respawns a single fresh worker on reheat, killing the previous one", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    controller.reheat();
    expect(created).toHaveLength(2);
    expect(created[0].killed).toBe(true);
    expect(created[1].running).toBe(true);
    expect(controller.isRunning()).toBe(true);
    controller.dispose();
  });

  it("keeps only the latest worker alive across repeated reheats", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    controller.reheat();
    controller.reheat();
    expect(created).toHaveLength(3);
    expect(created[0].killed).toBe(true);
    expect(created[1].killed).toBe(true);
    expect(created[2].running).toBe(true);
    expect(created.filter((worker) => worker.running)).toHaveLength(1);
    controller.dispose();
  });

  it("auto-stabilises after the hard time budget and stops the worker", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory, maxDurationMs: 3000 });
    controller.start();
    expect(created[0].running).toBe(true);
    vi.advanceTimersByTime(3200);
    expect(created[0].stops).toBeGreaterThanOrEqual(1);
    expect(controller.physics).toBe("stabilized");
    controller.dispose();
  });

  it("stabilises early once sampled positions stop moving", () => {
    const { factory, created } = fakeFactory();
    let clock = 0;
    const controller = new ForceLayoutController(connectedGraph(), {
      reducedMotion: false,
      onState: () => {},
      factory,
      now: () => clock,
      sampleIntervalMs: 400,
      maxDurationMs: 60_000
    });
    controller.start();
    for (let tick = 0; tick < 5; tick += 1) {
      clock += 400;
      vi.advanceTimersByTime(400);
    }
    expect(controller.physics).toBe("stabilized");
    expect(created[0].stops).toBeGreaterThanOrEqual(1);
    controller.dispose();
  });

  it("pauses and resumes with a fresh worker and no leak", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    controller.pause();
    expect(controller.physics).toBe("paused");
    expect(created[0].running).toBe(false);
    controller.resume();
    expect(controller.physics).toBe("layouting");
    expect(created).toHaveLength(2);
    expect(created[0].killed).toBe(true);
    expect(created.filter((worker) => worker.running)).toHaveLength(1);
    expect(controller.isRunning()).toBe(true);
    controller.dispose();
  });

  it("beginMutation kills the worker before coordinates change (drag/pin path)", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    controller.beginMutation();
    expect(created[0].killed).toBe(true);
    expect(controller.physics).toBe("paused");
    controller.reheat();
    expect(created).toHaveLength(2);
    controller.dispose();
  });

  it("runs a bounded worker under reduced motion and settles on the short budget", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(), { reducedMotion: true, onState: () => {}, factory, reducedDurationMs: 800 });
    controller.start();
    expect(created).toHaveLength(1);
    vi.advanceTimersByTime(900);
    expect(controller.physics).toBe("stabilized");
    controller.dispose();
  });

  it.each([501, 10_000])("starts worker physics for a %i-node graph instead of falling back to orbit", (order) => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(order), {
      reducedMotion: false,
      onState: () => {},
      factory
    });
    controller.start();
    expect(created).toHaveLength(1);
    expect(created[0].running).toBe(true);
    expect(controller.large).toBe(order > 5_000);
    controller.dispose();
  });

  it("uses the detail budget only as LOD metadata, never as a worker cutoff", () => {
    const { factory, created } = fakeFactory();
    const controller = new ForceLayoutController(connectedGraph(5), {
      reducedMotion: false,
      onState: () => {},
      factory,
      nodeBudget: 2
    });
    controller.start();
    expect(controller.large).toBe(true);
    expect(created).toHaveLength(1);
    expect(controller.physics).toBe("layouting");
    controller.dispose();
  });

  it("does not simulate a single-node or edgeless graph", () => {
    const { factory, created } = fakeFactory();
    const single = new Graph();
    single.addNode("solo", { x: 0, y: 0 });
    const controller = new ForceLayoutController(single, { reducedMotion: false, onState: () => {}, factory });
    controller.start();
    expect(created).toHaveLength(0);
    expect(controller.physics).toBe("stabilized");
    expect(() => {
      controller.dispose();
      controller.dispose();
    }).not.toThrow();

    const edgeless = new Graph();
    edgeless.addNode("a", { x: 0, y: 0 });
    edgeless.addNode("b", { x: 1, y: 1 });
    const controller2 = new ForceLayoutController(edgeless, { reducedMotion: false, onState: () => {}, factory });
    controller2.start();
    expect(created).toHaveLength(0);
    expect(controller2.physics).toBe("stabilized");
    controller2.dispose();
  });

  it("models a filter/snapshot rebuild: old controller killed before the new one runs", () => {
    const { factory, created } = fakeFactory();
    const controllerA = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controllerA.start();
    controllerA.dispose();
    const controllerB = new ForceLayoutController(connectedGraph(), { reducedMotion: false, onState: () => {}, factory });
    controllerB.start();
    expect(created[0].killed).toBe(true);
    expect(created[1].running).toBe(true);
    controllerB.dispose();
  });
});
