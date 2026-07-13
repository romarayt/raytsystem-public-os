import type Graph from "graphology";
import FA2LayoutSupervisor from "graphology-layout-forceatlas2/worker";
import { FORCE_DETAIL_BUDGET, FORCE_SETTINGS } from "./graphLayout";

// Minimal public surface of graphology-layout-forceatlas2/worker@0.10.1. Keeping the
// factory injectable gives unit tests deterministic lifecycle control while the browser
// integration test exercises the real Blob-backed worker.
export interface LayoutSupervisor {
  start(): void;
  stop(): void;
  kill(): void;
  isRunning(): boolean;
}

export type SupervisorFactory = (graph: Graph, settings: Record<string, unknown>) => LayoutSupervisor;

export type PhysicsState = "layouting" | "running" | "stabilized" | "paused";

export interface ForceControllerOptions {
  reducedMotion: boolean;
  onState: (state: PhysicsState) => void;
  nodeBudget?: number;
  factory?: SupervisorFactory;
  now?: () => number;
  sampleIntervalMs?: number;
  maxDurationMs?: number;
  reducedDurationMs?: number;
}

const defaultFactory: SupervisorFactory = (graph, settings) =>
  new FA2LayoutSupervisor(graph, { settings });

const MAX_SAMPLED_NODES = 96;
const REQUIRED_STABLE_SAMPLES = 3;
const NORMALIZED_STABILITY_EPSILON = 0.0009;

/**
 * Own exactly one ForceAtlas2 supervisor for one immutable-topology active graph.
 *
 * The installed worker has no completion event and can race when quickly stopped and started.
 * Therefore every reheat/resume after coordinates or `fixed` flags changed kills the old worker
 * and constructs a new supervisor. Convergence is sampled externally with a hard time budget.
 */
export class ForceLayoutController {
  private supervisor: LayoutSupervisor | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private sampleTimer: ReturnType<typeof setInterval> | null = null;
  private disposed = false;
  private state: PhysicsState = "stabilized";
  private previousSample: Map<string, { x: number; y: number }> | null = null;
  private stableSamples = 0;
  private startedAt = 0;
  private readonly factory: SupervisorFactory;
  private readonly nodeBudget: number;
  private readonly now: () => number;
  private readonly sampleIds: string[];
  private readonly handleWorkerUpdate: () => void;

  constructor(
    private readonly graph: Graph,
    private readonly options: ForceControllerOptions
  ) {
    this.factory = options.factory ?? defaultFactory;
    this.nodeBudget = options.nodeBudget ?? FORCE_DETAIL_BUDGET;
    this.now = options.now ?? Date.now;
    const nodes = graph.nodes().sort();
    const stride = Math.max(1, Math.ceil(nodes.length / MAX_SAMPLED_NODES));
    this.sampleIds = nodes.filter((_id, index) => index % stride === 0).slice(0, MAX_SAMPLED_NODES);
    this.handleWorkerUpdate = () => {
      if (!this.options.reducedMotion && this.state === "layouting" && this.supervisor?.isRunning()) {
        this.setState("running");
      }
    };
    graph.on("eachNodeAttributesUpdated", this.handleWorkerUpdate);
  }

  /** True means reduced visual detail, never a synchronous-layout fallback. */
  get large(): boolean {
    return this.graph.order > this.nodeBudget;
  }

  get physics(): PhysicsState {
    return this.state;
  }

  isRunning(): boolean {
    return this.supervisor?.isRunning() ?? false;
  }

  start(): void {
    if (this.disposed || this.supervisor) return;
    if (!this.canSimulate()) {
      this.setState("stabilized");
      return;
    }
    this.spawn();
  }

  /** Rebuild worker matrices after drag, pin/unpin, reset, or another coordinate mutation. */
  reheat(): void {
    if (this.disposed || !this.canSimulate()) return;
    this.spawn();
  }

  /** Kill the current matrix before changing coordinates or fixed flags. */
  beginMutation(): void {
    if (this.disposed) return;
    this.clearTimers();
    this.teardownSupervisor();
    this.setState("paused");
  }

  pause(): void {
    if (this.disposed) return;
    this.clearTimers();
    this.supervisor?.stop();
    this.setState("paused");
  }

  resume(): void {
    if (this.disposed || !this.canSimulate()) {
      if (!this.disposed) this.setState("stabilized");
      return;
    }
    // Never stop()->start() the same 0.10.1 supervisor: an in-flight response can create
    // overlapping iteration loops. A fresh supervisor starts from current graph coordinates.
    this.spawn();
  }

  toggle(): void {
    if (this.state === "running" || this.state === "layouting") this.pause();
    else this.resume();
  }

  stabilize(): void {
    this.clearTimers();
    this.supervisor?.stop();
    if (!this.disposed) this.setState("stabilized");
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.clearTimers();
    this.teardownSupervisor();
    this.graph.removeListener("eachNodeAttributesUpdated", this.handleWorkerUpdate);
  }

  private canSimulate(): boolean {
    return this.graph.order > 1 && this.graph.size > 0;
  }

  private spawn(): void {
    this.clearTimers();
    this.teardownSupervisor();
    this.previousSample = null;
    this.stableSamples = 0;
    this.startedAt = this.now();
    let supervisor: LayoutSupervisor | null = null;
    try {
      supervisor = this.factory(this.graph, FORCE_SETTINGS);
      this.supervisor = supervisor;
      supervisor.start();
    } catch {
      try {
        supervisor?.kill();
      } catch {
        // A partially constructed supervisor may not support a second teardown.
      }
      this.supervisor = null;
      this.setState("paused");
      this.dispose();
      return;
    }
    this.setState("layouting");
    const duration = this.options.reducedMotion
      ? (this.options.reducedDurationMs ?? Math.min(3_500, 1_000 + this.graph.order * 0.25))
      : (this.options.maxDurationMs ?? this.defaultDuration());
    this.stopTimer = setTimeout(() => this.stabilize(), duration);
    if (!this.options.reducedMotion) {
      this.sampleTimer = setInterval(
        () => this.sampleConvergence(),
        this.options.sampleIntervalMs ?? 400
      );
    }
  }

  private defaultDuration(): number {
    if (this.graph.order <= 100) return 3_000;
    if (this.graph.order <= 750) return 6_000;
    if (this.graph.order <= 3_000) return 10_000;
    return 14_000;
  }

  private sampleConvergence(): void {
    if (this.disposed || !this.supervisor?.isRunning() || !this.sampleIds.length) return;
    const current = new Map<string, { x: number; y: number }>();
    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;
    let squaredMovement = 0;
    let compared = 0;
    for (const id of this.sampleIds) {
      if (!this.graph.hasNode(id)) continue;
      const x = Number(this.graph.getNodeAttribute(id, "x"));
      const y = Number(this.graph.getNodeAttribute(id, "y"));
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      current.set(id, { x, y });
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      const previous = this.previousSample?.get(id);
      if (previous) {
        squaredMovement += (x - previous.x) ** 2 + (y - previous.y) ** 2;
        compared += 1;
      }
    }
    if (!compared) {
      this.previousSample = current;
      return;
    }
    const diagonal = Math.max(1, Math.hypot(maxX - minX, maxY - minY));
    const normalizedMovement = Math.sqrt(squaredMovement / compared) / diagonal;
    this.stableSamples = normalizedMovement < NORMALIZED_STABILITY_EPSILON ? this.stableSamples + 1 : 0;
    this.previousSample = current;
    if (this.now() - this.startedAt >= 1_000 && this.stableSamples >= REQUIRED_STABLE_SAMPLES) {
      this.stabilize();
    }
  }

  private clearTimers(): void {
    if (this.stopTimer !== null) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (this.sampleTimer !== null) {
      clearInterval(this.sampleTimer);
      this.sampleTimer = null;
    }
  }

  private teardownSupervisor(): void {
    if (!this.supervisor) return;
    try {
      this.supervisor.kill();
    } catch {
      // A supervisor may already be dead; disposal and StrictMode cleanup must stay idempotent.
    }
    this.supervisor = null;
  }

  private setState(state: PhysicsState): void {
    this.state = state;
    if (!this.disposed) this.options.onState(state);
  }
}
