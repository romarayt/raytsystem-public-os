import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type Graph from "graphology";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../app/App";

type MockEventHandler = (payload?: unknown) => void;

interface SigmaHarness {
  graph: unknown;
  handlers: Map<string, MockEventHandler>;
  mouseHandlers: Map<string, MockEventHandler>;
  cameraHandlers: Map<string, MockEventHandler>;
  settings: Map<string, unknown>;
  viewportPosition: { x: number; y: number };
  cameraEnabled: boolean;
}

const sigmaHarness = vi.hoisted<SigmaHarness>(() => ({
  graph: null,
  handlers: new Map<string, MockEventHandler>(),
  mouseHandlers: new Map<string, MockEventHandler>(),
  cameraHandlers: new Map<string, MockEventHandler>(),
  settings: new Map<string, unknown>(),
  viewportPosition: { x: 321, y: -123 },
  cameraEnabled: true
}));

const workerLifecycle = vi.hoisted(() => ({
  created: vi.fn(),
  started: vi.fn(),
  stopped: vi.fn(),
  killed: vi.fn()
}));

vi.mock("sigma", () => ({
  default: class MockSigma {
    constructor(graph: unknown) {
      sigmaHarness.graph = graph;
      sigmaHarness.handlers.clear();
      sigmaHarness.mouseHandlers.clear();
      sigmaHarness.cameraHandlers.clear();
      sigmaHarness.settings.clear();
    }
    on(event: string, handler: MockEventHandler) { sigmaHarness.handlers.set(event, handler); return this; }
    setSetting(name: string, value: unknown) { sigmaHarness.settings.set(name, value); return this; }
    refresh() { return undefined; }
    kill() { return undefined; }
    setGraph(graph: unknown) { sigmaHarness.graph = graph; return undefined; }
    getDimensions() { return { width: 800, height: 600 }; }
    getCamera() {
      return {
        animatedReset: () => Promise.resolve(),
        animate: () => Promise.resolve(),
        disable: () => { sigmaHarness.cameraEnabled = false; },
        enable: () => { sigmaHarness.cameraEnabled = true; },
        on: (event: string, handler: MockEventHandler) => { sigmaHarness.cameraHandlers.set(event, handler); },
        off: (event: string) => { sigmaHarness.cameraHandlers.delete(event); },
        getState: () => ({ x: 0, y: 0, ratio: 1, angle: 0 })
      };
    }
    getMouseCaptor() {
      return {
        on: (event: string, handler: MockEventHandler) => { sigmaHarness.mouseHandlers.set(event, handler); },
        off: (event: string) => { sigmaHarness.mouseHandlers.delete(event); }
      };
    }
    getCustomBBox() { return null; }
    setCustomBBox() { return undefined; }
    getBBox() { return { x: [0, 0], y: [0, 0] }; }
    viewportToGraph() { return sigmaHarness.viewportPosition; }
    getNodeDisplayData(id: string) {
      const graph = sigmaHarness.graph as {
        hasNode(nodeId: string): boolean;
        getNodeAttribute(nodeId: string, name: string): unknown;
      } | null;
      if (!graph?.hasNode(id)) return undefined;
      return { x: Number(graph.getNodeAttribute(id, "x")), y: Number(graph.getNodeAttribute(id, "y")) };
    }
  }
}));

vi.mock("sigma/rendering", () => ({ drawDiscNodeHover: vi.fn() }));

vi.mock("graphology-layout-forceatlas2", () => ({
  default: { assign: () => undefined }
}));

vi.mock("graphology-layout-forceatlas2/worker", () => ({
  default: class MockForceAtlasWorker {
    private running = false;
    constructor() { workerLifecycle.created(); }
    start() { this.running = true; workerLifecycle.started(); }
    stop() { this.running = false; workerLifecycle.stopped(); }
    kill() { this.running = false; workerLifecycle.killed(); }
    isRunning() { return this.running; }
  }
}));

const system = {
  snapshot_id: "view_123",
  loaded_at: "2026-07-11T14:00:00Z",
  fingerprint: {
    knowledge_generation_id: "gen_knowledge",
    knowledge_generation_sha256: "a".repeat(64),
    task_generation_id: "tgen_tasks",
    task_generation_sha256: "b".repeat(64),
    catalog_sha256: "c".repeat(64),
    graph_snapshot_id: "graph_snapshot",
    graph_sha256: "d".repeat(64),
    code_snapshot_id: "cgraph_snapshot",
    code_snapshot_sha256: "e".repeat(64),
    code_snapshot_fingerprint: "f".repeat(64),
    code_graph_state: "current"
  },
  counts: {
    claims: 1,
    entities: 0,
    sources: 1,
    evidence: 1,
    runs: 1,
    tasks: { inbox: 1, planned: 0, ready: 0, running: 0, review: 0, blocked: 0, done: 0, cancelled: 0 },
    agents: 1,
    skills: 1,
    adapters: 1,
    code_files: 1,
    code_nodes: 2,
    code_edges: 1,
    code_ambiguous_edges: 0
  },
  attention: { blocked_tasks: 0, failed_runs: 0, restricted_skills: 0 },
  safety: { binding: "loopback_only" }
};

const task = {
  task_id: "task_example",
  title: "Inspect evidence",
  description: "Follow the verified path.",
  status: "inbox",
  priority: "high",
  project_id: "project_default",
  mission_id: null,
  assignee_ids: [],
  skill_ids: [],
  dependency_ids: [],
  artifact_ids: [],
  tags: [],
  blocked_reason: null,
  sensitivity: "internal",
  created_by: "user:local",
  revision: 1,
  created_at: "2026-07-11T14:00:00Z",
  updated_at: "2026-07-11T14:00:00Z"
};

const catalog = {
  catalog_sha256: "c".repeat(64),
  packs: [{ pack_id: "pack_starter", name: "Starter", version: "1", description: "Universal", license_expression: "Apache-2.0", trust_class: "official", agent_ids: ["agent_researcher"], skill_ids: ["safe-skill"], context_paths: ["AGENTS.md"], optional: false }],
  agents: [{ agent_id: "agent_researcher", name: "Researcher", role: "researcher", description: "Source-bound research", version: "1", pack_id: "pack_starter", runtime_adapter_id: "adapter_disabled", skill_ids: ["safe-skill"], context_paths: ["AGENTS.md"], capabilities: ["research"], requested_filesystem_mode: "read_only", approved_data_classes: ["public"], egress_destination: null, accent: "#A99CF8", enabled: false }],
  skills: [{ skill_id: "safe-skill", name: "Safe skill", description: "Inert procedure", version: "1", source_path: "skills/safe-skill/SKILL.md", source_sha256: "e".repeat(64), pack_id: "pack_starter", trust_class: "official", sensitivity: "internal", permissions: [], test_status: "pass", enabled: true }],
  instructions: [{ document_id: "instruction_agents", kind: "agent_routing", label: "Agent routing", path: "AGENTS.md", content_sha256: "f".repeat(64), size_bytes: 1200, sensitivity: "internal", editable: false }],
  adapters: [{ adapter_id: "adapter_disabled", name: "Catalog only", version: "1", state: "disabled", isolation_mode: "none", capabilities: [], egress_destination: null, reason: "Execution unavailable." }]
};

function publicAgentDefinition(agent: (typeof catalog.agents)[number]) {
  const { egress_destination, ...definition } = agent;
  return { ...definition, egress_declared: egress_destination !== null };
}

const skills = {
  catalog_sha256: "c".repeat(64),
  skills: [{
    ...catalog.skills[0],
    policy: {
      skill_id: "safe-skill", source_path: "skills/safe-skill/SKILL.md", pack_id: "pack_starter",
      trust_class: "official", sensitivity: "internal", editable: false,
      read_only_reason: "official_skill", forkable: true
    },
    related_agents: [{ agent_id: "agent_researcher", name: "Researcher", role: "researcher" }]
  }]
};

const unifiedAgent = {
  agent_id: "agent_researcher",
  employee_id: "employee_researcher",
  name: "Researcher",
  role: "researcher",
  description: "Source-bound research",
  pack_id: "pack_starter",
  version: "1",
  definition: publicAgentDefinition(catalog.agents[0]),
  definition_state: "declared",
  execution: null,
  execution_status: "disabled",
  execution_state_source: "feature_gate",
  readiness: "requires_configuration",
  unavailable_reason: "execution_store_uninitialized",
  runtime_adapter: { adapter_id: "adapter_disabled", name: "Catalog only", state: "disabled", isolation_mode: "none", reason: "Execution unavailable." },
  skill_ids: ["safe-skill"],
  skills_count: 1,
  filesystem_policy: { mode: "workspace_root_readonly", allow_workspace_read: true, allow_staged_write: false, allow_git_read: true, allow_git_write: false },
  concurrency_limit: 1,
  current_task_id: null,
  current_session_id: null,
  configuration_revision: "f".repeat(64),
  configuration_current: null,
  approved_data_classes: ["public"],
  egress_declared: false,
  effective_permissions: { workspace_read: true, staged_write: false, git_write: false, network: false },
  duplicate_execution_record_count: 0
};

const universe = {
  graph_snapshot_id: "graph_snapshot",
  knowledge_generation_id: "gen_knowledge",
  knowledge_generation_sha256: "a".repeat(64),
  task_generation_id: "tgen_tasks",
  task_generation_sha256: "b".repeat(64),
  catalog_sha256: "c".repeat(64),
  code_snapshot_id: "cgraph_snapshot",
  code_snapshot_sha256: "e".repeat(64),
  code_snapshot_fingerprint: "f".repeat(64),
  code_graph_state: "current",
  code_file_count: 1,
  code_node_count: 2,
  code_edge_count: 1,
  code_ambiguous_edges: 0,
  code_view_node_count: 2,
  code_view_edge_count: 1,
  code_view_truncated: false,
  supported_lenses: ["universe", "knowledge", "work", "agent", "evidence", "code"],
  created_at: "2026-07-11T14:00:00Z",
  nodes: [
    { node_id: "workspace_current", kind: "workspace", label: "raytsystem workspace", subtitle: "Local", status: "local_only", ring: "core", importance: 100, x: 0, y: 0, recorded_at: null, source_ref: null, metadata: {} },
    { node_id: "clm_example", kind: "claim", label: "Evidence remains addressable", subtitle: "en", status: "supported", ring: "knowledge", importance: 82, x: 200, y: 0, recorded_at: "2026-07-11T14:00:00Z", source_ref: null, metadata: {} },
    { node_id: "seg_example", kind: "evidence", label: "Evidence seg…", subtitle: "text", status: "verified", ring: "evidence", importance: 70, x: 300, y: 0, recorded_at: "2026-07-11T14:00:00Z", source_ref: "src_example", metadata: {} },
    { node_id: "src_example", kind: "source", label: "fixture.md", subtitle: "markdown", status: "internal", ring: "evidence", importance: 64, x: 400, y: 0, recorded_at: "2026-07-11T14:00:00Z", source_ref: null, metadata: {} },
    { node_id: "cnode_module", kind: "module", label: "raytsystem.universe", subtitle: "src/raytsystem/universe.py", status: "current", ring: "code", importance: 84, x: 500, y: 0, recorded_at: "2026-07-11T14:00:00Z", source_ref: null, metadata: { path: "src/raytsystem/universe.py", start_line: "1", community_id: "2", is_god: "true", is_bridge: "false" } }
  ],
  edges: [
    { edge_id: "gedge_a", source: "src_example", target: "seg_example", kind: "contains", status: "active", directed: true, metadata: {} },
    { edge_id: "gedge_b", source: "seg_example", target: "clm_example", kind: "supports", status: "active", directed: true, metadata: {} },
    { edge_id: "gedge_code", source: "workspace_current", target: "cnode_module", kind: "contains", status: "extracted", directed: true, metadata: { code_edge_id: "cedge_code", confidence: "EXTRACTED" } },
    { edge_id: "gedge_selfloop", source: "cnode_module", target: "cnode_module", kind: "calls", status: "extracted", directed: true, metadata: { code_edge_id: "cedge_selfloop", confidence: "EXTRACTED" } }
  ]
};

let codeGraphState = "current";

function responseFor(url: string): unknown {
  if (url === "/api/v1/session") return { csrf_token: "csrf", expires_at_epoch: 99, local_only: true };
  if (url === "/api/v1/system") return system;
  if (url === "/api/v1/tasks") return { generation_id: "tgen_tasks", generation_sha256: "b".repeat(64), head_event_id: "tevt_a", tasks: [task] };
  if (url === "/api/v1/catalog") return catalog;
  if (url === "/api/v1/skills") return skills;
  if (url.startsWith("/api/v1/skills/safe-skill")) return {
    catalog_sha256: "c".repeat(64), skill: catalog.skills[0],
    source: { path: "skills/safe-skill/SKILL.md", sha256: "e".repeat(64), content_available: true, content_restricted: false },
    content: "---\nname: safe-skill\ndescription: Inert procedure\nversion: '1'\npermissions: []\ntest_status: pass\n---\n# Safe skill",
    format: "text", content_format: "markdown", policy: skills.skills[0].policy,
    related_agents: skills.skills[0].related_agents,
    permission_boundary: {
      availability: "declared_ids_only", declared_permission_ids: [],
      filesystem: { availability: "not_modeled", items: [] }, network: { availability: "not_modeled", items: [] },
      tools: { availability: "not_modeled", items: [] }, secrets: { availability: "not_modeled", items: [] },
      approvals: { availability: "not_modeled", items: [] }, side_effects: { availability: "not_modeled", items: [] },
      sensitivity: "internal"
    },
    workflows: { availability: "not_modeled", items: [] }, tools: { availability: "not_modeled", items: [] },
    tests: { availability: "catalog_metadata", test_status: "pass", evals: [], last_checked_at: null, commands: [], known_limitations: [] },
    history: { availability: "not_initialized", revisions: [], audit_events: [], current_revision_only: true, truncated: false }
  };
  if (url.startsWith("/api/v1/agents/agent_researcher")) return {
    snapshot_id: "agent_detail_test", section: "agent_detail", state: "catalog_only", storage_state: "uninitialized",
    features: { digital_employees_enabled: true, runtime_execution_enabled: false }, catalog_sha256: "c".repeat(64),
    agent: unifiedAgent,
    instruction: { context_paths: ["AGENTS.md"], capabilities: ["research"], system_boundaries: {}, limitations: [] },
    skills: [{ skill_id: "safe-skill", name: "Safe skill", status: "enabled", permissions: [] }],
    runtime: { execution: null, sessions: [], runs: [], budgets: [], leases: [] },
    access: { filesystem: unifiedAgent.filesystem_policy, data_classes: ["public"], tools: [], network: { egress_declared: false, approval_required: true }, approvals: [], effective_permissions: unifiedAgent.effective_permissions },
    history: { assignments: [], runs: [], configuration_revision: "f".repeat(64), audit_events: [] }
  };
  if (url.startsWith("/api/v1/agents")) return {
    snapshot_id: "agents_test", section: "agents", state: "catalog_only", storage_state: "uninitialized",
    features: { digital_employees_enabled: true, runtime_execution_enabled: false }, catalog_sha256: "c".repeat(64),
    agents: [unifiedAgent], pagination: { limit: 500, offset: 0, returned: 1 }, total_agents: 1
  };
  if (url === "/api/v1/runs") return { knowledge_generation_id: "gen_knowledge", runs: [{ run_id: "run_example", operation_type: "ingest", state: "succeeded", generation_id: "gen_knowledge", semantic_noop: false, created_at: "2026-07-11T13:00:00Z", updated_at: "2026-07-11T14:00:00Z", manifest_sha256: "9".repeat(64) }] };
  if (url === "/api/v1/universe") return universe;
  if (url === "/api/v1/code-graph/status") return { state: codeGraphState, snapshot_id: codeGraphState === "missing" ? null : "cgraph_snapshot", snapshot_fingerprint: codeGraphState === "missing" ? null : "f".repeat(64), file_count: codeGraphState === "missing" ? 0 : 1, node_count: codeGraphState === "missing" ? 0 : 2, edge_count: codeGraphState === "missing" ? 0 : 1, ambiguous_edges: 0, changed_files: codeGraphState === "stale" ? 1 : 0, deleted_files: 0, changed_paths: codeGraphState === "stale" ? ["src/raytsystem/universe.py"] : [], deleted_paths: [], paths_truncated: false, reason: null };
  if (url === "/api/v1/code-graph/nodes/cnode_module?expected=cgraph_snapshot") return { snapshot_id: "cgraph_snapshot", node: { node_id: "cnode_module", kind: "module", label: "raytsystem.universe", qualified_name: "raytsystem.universe", path: "src/raytsystem/universe.py", community_id: 2, is_god: true, is_bridge: false, incoming_edges: 1, outgoing_edges: 0 } };
  if (url === "/api/v1/knowledge") return { generation_id: "gen_knowledge", generation_sha256: "a".repeat(64), claims: [], entities: [], sources: [], evidence: [] };
  if (url === "/api/v1/knowledge/claim/clm_example?expected=gen_knowledge") return {
    generation_id: "gen_knowledge",
    generation_sha256: "a".repeat(64),
    kind: "claim",
    claim: { claim_id: "clm_example", statement: "Evidence remains addressable", status: "supported", language: "en", evidence_ids: ["seg_example"], relation_ids: [], supersedes: [], contradicts: [], recorded_at: "2026-07-11T14:00:00Z" }
  };
  if (url === "/api/v1/knowledge/evidence/seg_example?expected=gen_knowledge") return {
    generation_id: "gen_knowledge",
    generation_sha256: "a".repeat(64),
    kind: "evidence",
    evidence: { evidence_id: "seg_example", source_id: "src_example", source_label: "fixture.md", excerpt: "The evidence is exact.", excerpt_sha256: "8".repeat(64), content_sha256: "7".repeat(64) }
  };
  if (url.startsWith("/api/v1/search")) return { graph_snapshot_id: "graph_snapshot", results: [] };
  if (url === "/api/v1/features") return {
    state: "available",
    snapshot_id: "pview_test",
    active_feature_flags: {},
    emergency_state: { snapshot_id: "pview_test", state: "available", active_actions: [], revision: null }
  };
  if (url === "/api/v1/execution/features") return {
    snapshot_id: "exec_view_test",
    section: "features",
    state: "available",
    storage_state: "uninitialized",
    features: {
      code_graph_enabled: true,
      graph_first_query_enabled: true,
      digital_employees_enabled: true,
      task_workspaces_enabled: true,
      runtime_execution_enabled: false,
      codex_local_enabled: false,
      claude_local_enabled: false,
      heartbeats_enabled: true,
      scheduled_heartbeats_enabled: false
    },
    catalog_sha256: "c".repeat(64),
    limits: {
      max_run_seconds: 3600,
      max_output_bytes: 4194304,
      max_transcript_events: 10000,
      max_context_bytes: 48000,
      max_concurrent_runs: 2
    }
  };
  return {};
}

function renderApp(path = "/command-center") {
  window.history.replaceState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

beforeEach(() => {
  codeGraphState = "current";
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return Promise.resolve(new Response(JSON.stringify(responseFor(url)), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
  localStorage.clear();
  sigmaHarness.graph = null;
  sigmaHarness.handlers.clear();
  sigmaHarness.mouseHandlers.clear();
  sigmaHarness.cameraHandlers.clear();
  sigmaHarness.settings.clear();
  sigmaHarness.cameraEnabled = true;
  workerLifecycle.created.mockClear();
  workerLifecycle.started.mockClear();
  workerLifecycle.stopped.mockClear();
  workerLifecycle.killed.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("raytsystem control plane", () => {
  it("renders a truthful command center without fake live agents", async () => {
    renderApp();
    expect(await screen.findByText(/Все ваши системы/i)).toBeInTheDocument();
    expect(screen.getByText(/объявленн.*профил/i)).toBeInTheDocument();
    expect(screen.queryByText(/агентов онлайн/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/выполнение отключено/i).length).toBeGreaterThan(0);
  });

  it.each([
    ["/tasks", "Задачи"],
    ["/runs", "Запуски"],
    ["/agents", "Агенты"],
    ["/skills", "Навыки"],
    ["/context", "Контекст"],
    ["/safety", "Безопасность"]
  ])("renders route %s from verified API data", async (path, heading) => {
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
  });

  it("resets the real scroll container when a route changes", async () => {
    renderApp("/context");
    await screen.findByRole("heading", { level: 1, name: "Контекст" });
    const main = screen.getByRole("main");
    main.scrollTop = 420;
    main.scrollLeft = 18;

    fireEvent.click(within(screen.getByLabelText("Основная навигация")).getByRole("button", { name: "Безопасность" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Безопасность" })).toBeInTheDocument();
    await waitFor(() => {
      expect(main.scrollTop).toBe(0);
      expect(main.scrollLeft).toBe(0);
    });
  });

  it("blocks SPA route navigation while an editor reports unsaved changes", async () => {
    const { container } = renderApp("/skills");
    await screen.findByRole("heading", { level: 1, name: "Навыки" });
    const editor = document.createElement("section");
    editor.dataset.editorScope = "skill";
    editor.dataset.unsavedChanges = "true";
    editor.dataset.editorLocation = "/skills?skill=local-review";
    document.body.append(editor);
    fireEvent.click(within(screen.getByLabelText("Основная навигация")).getByRole("button", { name: "Безопасность" }));

    const alert = screen.getByRole("alertdialog", { name: "Покинуть редактор?" });
    expect(screen.getByRole("heading", { level: 1, name: "Навыки" })).toBeInTheDocument();
    expect(container).toHaveProperty("inert", true);

    fireEvent.click(within(alert).getByRole("button", { name: "Продолжить редактирование" }));
    expect(screen.queryByRole("alertdialog", { name: "Покинуть редактор?" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Навыки" })).toBeInTheDocument();

    fireEvent.click(within(screen.getByLabelText("Основная навигация")).getByRole("button", { name: "Безопасность" }));
    fireEvent.click(within(screen.getByRole("alertdialog", { name: "Покинуть редактор?" })).getByRole("button", { name: "Покинуть без сохранения" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Безопасность" })).toBeInTheDocument();
    editor.remove();
  });

  it("uses a board glyph for the active task presentation", async () => {
    renderApp("/tasks");
    const board = await screen.findByRole("button", { name: "Доска" });
    expect(board.querySelector(".lucide-columns-3")).toBeInTheDocument();
    expect(board.querySelector(".lucide-list-filter")).not.toBeInTheDocument();
  });

  it("offers an accessible list equivalent for the universe", async () => {
    renderApp("/universe");
    expect(await screen.findByRole("heading", { level: 1, name: "Вселенная" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /^Список$/ }));
    expect(await screen.findByText("Evidence remains addressable")).toBeInTheDocument();
    expect(screen.getByText(/Равноценное представление для клавиатуры/i)).toBeInTheDocument();
  });

  it("switches between the three universe layouts and shows physics controls only for the links graph", async () => {
    vi.stubGlobal("Worker", class BrowserWorkerCapability {});
    renderApp("/universe");
    expect(await screen.findByRole("button", { name: "Орбита — обзор слоёв" })).toHaveAttribute("aria-pressed", "true");
    expect(workerLifecycle.created).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Управление силовой раскладкой" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Связи — интерактивный граф отношений" }));
    expect(await screen.findByRole("group", { name: "Управление силовой раскладкой" })).toBeInTheDocument();
    await waitFor(() => expect(workerLifecycle.created).toHaveBeenCalledTimes(1));
    expect(workerLifecycle.started).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Переразложить граф" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открепить узлы" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Структура — иерархическое представление" }));
    expect(screen.queryByRole("group", { name: "Управление силовой раскладкой" })).not.toBeInTheDocument();
    await waitFor(() => expect(workerLifecycle.killed).toHaveBeenCalledTimes(1));
  });

  it("keeps global force labels sparse while preserving labels for structural landmarks", async () => {
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: "Связи — интерактивный граф отношений" }));
    await waitFor(() => expect(sigmaHarness.settings.has("nodeReducer")).toBe(true));
    const graph = sigmaHarness.graph as Graph;
    const reducer = sigmaHarness.settings.get("nodeReducer") as (
      id: string,
      attributes: Record<string, unknown>
    ) => Record<string, unknown>;
    const claim = reducer("clm_example", graph.getNodeAttributes("clm_example"));
    const godNode = reducer("cnode_module", graph.getNodeAttributes("cnode_module"));
    expect(claim.label).toBe("");
    expect(godNode.label).toBe("raytsystem.universe");
  });

  it("announces the bounded reduced-motion force pass instead of showing continuous movement", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => true)
    })));
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: "Связи — интерактивный граф отношений" }));
    expect(await screen.findByText("движение скрыто до конечной worker-итерации")).toBeInTheDocument();
    expect(screen.getByLabelText("Интерактивная вселенная знаний")).toHaveClass("reduced-motion");
  });

  it("updates force coordinates during drag, pins the node, and keeps hover/selection/focus working", async () => {
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: "Связи — интерактивный граф отношений" }));
    await waitFor(() => expect(sigmaHarness.handlers.has("downNode")).toBe(true));
    const graph = sigmaHarness.graph as Graph;
    const preventSigmaDefault = vi.fn();

    act(() => {
      sigmaHarness.handlers.get("downNode")?.({ node: "workspace_current", event: { x: 10, y: 10, preventSigmaDefault } });
    });
    expect(sigmaHarness.cameraEnabled).toBe(false);
    expect(graph.getNodeAttribute("workspace_current", "pinned")).toBe(false);

    act(() => {
      sigmaHarness.handlers.get("moveBody")?.({
        event: { x: 20, y: 20, original: new Event("pointermove", { cancelable: true }), preventSigmaDefault }
      });
      sigmaHarness.handlers.get("upNode")?.();
    });
    expect(preventSigmaDefault).toHaveBeenCalled();
    expect(sigmaHarness.cameraEnabled).toBe(true);
    expect(graph.getNodeAttribute("workspace_current", "x")).toBe(321);
    expect(graph.getNodeAttribute("workspace_current", "y")).toBe(-123);
    expect(graph.getNodeAttribute("workspace_current", "pinned")).toBe(true);
    expect(graph.getNodeAttribute("workspace_current", "fixed")).toBe(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "Открепить узлы" })).toBeEnabled());

    act(() => sigmaHarness.handlers.get("enterNode")?.({ node: "workspace_current" }));
    expect(document.querySelector(".physics-direction")?.textContent).toMatch(/входящих.*исходящих/);
    act(() => sigmaHarness.handlers.get("leaveNode")?.());

    act(() => {
      sigmaHarness.handlers.get("clickNode")?.({
        node: "clm_example",
        event: { original: new MouseEvent("click") }
      });
    });
    const focus = await screen.findByRole("button", { name: "Сфокусироваться на выбранном" });
    fireEvent.click(focus);
    expect(await screen.findByRole("button", { name: "Вернуться ко всему графу" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Открепить узлы" }));
    expect(graph.getNodeAttribute("workspace_current", "pinned")).toBe(false);
    expect(graph.getNodeAttribute("workspace_current", "fixed")).toBe(false);
  });

  it("does not restart paused physics for a node click without drag movement", async () => {
    vi.stubGlobal("Worker", class BrowserWorkerCapability {});
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: "Связи — интерактивный граф отношений" }));
    await waitFor(() => expect(workerLifecycle.created).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Приостановить физику" }));
    expect(await screen.findByText("Физика приостановлена")).toBeInTheDocument();

    act(() => {
      sigmaHarness.handlers.get("downNode")?.({
        node: "workspace_current",
        event: { x: 10, y: 10, preventSigmaDefault: vi.fn() }
      });
      sigmaHarness.handlers.get("upNode")?.();
    });

    expect(workerLifecycle.created).toHaveBeenCalledTimes(1);
    expect(workerLifecycle.started).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Физика приостановлена")).toBeInTheDocument();
  });

  it("keeps the accessible list equivalent working in the links layout", async () => {
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: "Связи — интерактивный граф отношений" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Список$/ }));
    expect(await screen.findByText("Evidence remains addressable")).toBeInTheDocument();
  });

  it("provides a Russian code lens with freshness, filters and table fallback", async () => {
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: /^Код$/ }));
    expect(await screen.findByText("Актуально")).toBeInTheDocument();
    expect(screen.getByLabelText("Тип узла")).toBeInTheDocument();
    expect(screen.getByLabelText("Тип связи")).toBeInTheDocument();
    expect(screen.getByLabelText("Достоверность связи")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Обновить изменённое/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Список$/ }));
    const table = await screen.findByRole("table");
    expect(table).toBeInTheDocument();
    expect(within(table).getByText("raytsystem.universe")).toBeInTheDocument();
  });

  it.each([
    ["missing", "Не построено"],
    ["stale", "Устарело"],
    ["building", "Обновляется"],
    ["error", "Ошибка"]
  ])("renders the %s code-graph state and blocks traversal", async (state, label) => {
    codeGraphState = state;
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: /^Код$/ }));
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Исследовать" })).toBeDisabled();
  });

  it("offers a build button for a fresh directory without a code graph", async () => {
    codeGraphState = "missing";
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: /^Код$/ }));
    const build = await screen.findByRole("button", { name: /Построить граф/i });
    expect(build).toBeEnabled();
    expect(screen.getByRole("button", { name: /Обновить изменённое/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^Пересобрать$/ })).not.toBeInTheDocument();
  });

  it("opens a generation-bound claim to its exact evidence excerpt", async () => {
    renderApp("/universe");
    fireEvent.click(await screen.findByRole("button", { name: /^Список$/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Evidence remains addressable/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Открыть фрагмент/ }));
    expect(await screen.findByText("The evidence is exact.")).toBeInTheDocument();
    expect(screen.getByText(/Сервер отклоняет детали/i)).toBeInTheDocument();
  });

  it("keeps the command palette to safe local actions", async () => {
    renderApp();
    await screen.findByText(/Все ваши системы/i);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const palette = await screen.findByRole("dialog", { name: "Палитра команд" });
    const createTask = within(palette).getByRole("button", { name: /Создать задачу/i });
    expect(createTask).toBeInTheDocument();
    const input = within(palette).getByRole("textbox", { name: /Поиск команд/i });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(createTask).toHaveFocus();
    expect(screen.queryByText(/Запустить агента/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Открыть shell/i)).not.toBeInTheDocument();
  });

  it("opens task creation with an explicit canonical boundary", async () => {
    renderApp("/tasks");
    await screen.findByRole("heading", { level: 1, name: "Задачи" });
    const trigger = await screen.findByRole("button", { name: /Новая задача/i });
    trigger.focus();
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog", { name: "Создать задачу" })).toBeInTheDocument();
    expect(screen.getByText(/Канонические знания останутся нетронутыми/i)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Название" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Создать задачу" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
