import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DigitalEmployeesView, ExecutionRunsView } from "../features/ExecutionViews";
import type { Selection } from "../types";

const features = {
  code_graph_enabled: true,
  graph_first_query_enabled: true,
  digital_employees_enabled: false,
  task_workspaces_enabled: true,
  runtime_execution_enabled: false,
  codex_local_enabled: false,
  claude_local_enabled: false,
  heartbeats_enabled: true,
  scheduled_heartbeats_enabled: false
};

const employeeSnapshot = {
  snapshot_id: "xview_employees",
  section: "employees",
  state: "catalog_only",
  storage_state: "uninitialized",
  features,
  catalog_sha256: "a".repeat(64),
  employees: [{
    employee_id: "employee_builder",
    agent_definition_id: "agent_builder",
    name: "Builder",
    role: "builder",
    description: "Implements approved work inside a managed boundary.",
    runtime_adapter_id: "adapter_codex_local",
    enabled_skill_ids: ["skill_build"],
    configuration_revision: "b".repeat(64),
    configuration_current: null,
    status: "disabled",
    stored_status: null,
    state_source: "feature_gate",
    reason_code: "runtime_execution_disabled",
    current_task_id: null,
    current_session_id: null,
    reporting_manager_id: null,
    budget_policy_id: null,
    concurrency_limit: 1,
    filesystem_policy: {
      mode: "task_worktree",
      allow_workspace_read: true,
      allow_staged_write: true,
      allow_git_read: true,
      allow_git_write: false
    },
    graph_policy: {
      max_depth: 2,
      max_nodes: 40,
      max_edges: 100,
      max_bytes: 48_000,
      include_relations: ["imports", "calls"]
    },
    heartbeat_policy: {
      manual_enabled: true,
      scheduled_enabled: false,
      interval_seconds: 0
    },
    instruction_paths_omitted: true,
    hidden_instruction_path: "/private/AGENTS.md"
  }],
  pagination: { limit: 100, offset: 0, returned: 1 },
  total_catalog_employees: 1
};

const run = {
  run_id: "xrun_demo",
  invocation_id: "xinv_demo",
  invocation_source: "manual",
  employee_id: "employee_builder",
  task_id: "task_demo",
  runtime_adapter_id: "adapter_fake",
  provider: "fake",
  model: null,
  workspace_id: "workspace_demo",
  graph_snapshot_id: "cgraph_demo",
  graph_scope_id: "gscope_demo",
  session_id_before: null,
  session_id_after: "xsession_demo",
  policy_decision_id: "policy_demo",
  approval_id: null,
  task_lease_id: "tlease_demo",
  fencing_token: 3,
  status: "running",
  started_at: "2026-07-12T12:00:00Z",
  ended_at: null,
  exit_code: null,
  usage: {
    input_tokens: 100,
    output_tokens: 50,
    cached_tokens: 25,
    estimated_cost_micros: 300,
    actual_cost_micros: null
  },
  changed_file_count: 2,
  tests: [{ name: "unit", status: "passed" }],
  artifact_ids: [],
  summary: "Preparing a bounded implementation.",
  error_code: null,
  retry_of_run_id: null,
  resumed_from_run_id: null,
  command_omitted: true,
  working_directory_omitted: true,
  safe_command: ["codex", "exec", "SECRET_PROMPT"],
  cwd_token: "/private/worktree",
  environment: { TOKEN: "secret" },
  provider_session_id: "private-provider-session"
};

let runState: "ready" | "uninitialized" = "uninitialized";
let employeeFailure = false;

function responseFor(url: string): { body: unknown; status?: number } {
  if (url === "/api/v1/session") {
    return { body: { csrf_token: "csrf", expires_at_epoch: 99, local_only: true } };
  }
  if (url.startsWith("/api/v1/execution/employees")) {
    return employeeFailure
      ? { body: { error: { code: "execution_unavailable" } }, status: 503 }
      : { body: employeeSnapshot };
  }
  if (url.startsWith("/api/v1/execution/runs")) {
    return {
      body: {
        snapshot_id: "xview_runs",
        section: "execution_runs",
        state: runState,
        storage_state: runState,
        features,
        feature_state: "disabled",
        runs: runState === "ready" ? [run] : [],
        pagination: { limit: 100, offset: 0, returned: runState === "ready" ? 1 : 0 }
      }
    };
  }
  return { body: { snapshot_id: "xview_unknown", state: "ready" } };
}

function renderView(view: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  return render(<QueryClientProvider client={client}>{view}</QueryClientProvider>);
}

beforeEach(() => {
  runState = "uninitialized";
  employeeFailure = false;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.startsWith("http") ? new URL(url).pathname + new URL(url).search : url;
    const response = responseFor(path);
    return Promise.resolve(new Response(JSON.stringify(response.body), {
      status: response.status ?? 200,
      headers: { "Content-Type": "application/json" }
    }));
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("execution views", () => {
  it("shows catalog employees as disabled and selects only the public projection", async () => {
    const onSelect = vi.fn<(selection: Selection) => void>();
    renderView(<DigitalEmployeesView onSelect={onSelect} />);

    expect(await screen.findByText("Цифровые сотрудники выключены feature gate")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть сотрудника Builder" }));

    expect(onSelect).toHaveBeenCalledOnce();
    const selection = onSelect.mock.calls[0]?.[0];
    expect(selection?.status).toBe("disabled");
    expect(selection?.metadata).not.toHaveProperty("configuration_revision");
    expect(selection?.metadata).not.toHaveProperty("hidden_instruction_path");
    expect(JSON.stringify(selection)).not.toContain("/private/AGENTS.md");
  });

  it("renders an explicit uninitialized run state without implying runtime availability", async () => {
    renderView(<ExecutionRunsView onSelect={() => undefined} />);

    expect(await screen.findByText("Запусков пока нет")).toBeInTheDocument();
    expect(screen.getByText(/GET не создаёт базу/i)).toBeInTheDocument();
    expect(screen.getByText(/Runtime выключен/i)).toBeInTheDocument();
  });

  it("selects a run without leaking command, cwd, environment, or provider session", async () => {
    runState = "ready";
    const onSelect = vi.fn<(selection: Selection) => void>();
    renderView(<ExecutionRunsView onSelect={onSelect} />);

    fireEvent.click(await screen.findByRole("button", { name: /fake.*xrun_demo/i }));

    const selection = onSelect.mock.calls[0]?.[0];
    expect(selection?.metadata).not.toHaveProperty("safe_command");
    expect(selection?.metadata).not.toHaveProperty("cwd_token");
    expect(selection?.metadata).not.toHaveProperty("environment");
    expect(selection?.metadata).not.toHaveProperty("provider_session_id");
    const rendered = JSON.stringify(selection);
    expect(rendered).not.toContain("SECRET_PROMPT");
    expect(rendered).not.toContain("/private/worktree");
    expect(rendered).not.toContain("private-provider-session");
  });

  it("renders the shared error state when the execution snapshot fails closed", async () => {
    employeeFailure = true;
    renderView(<DigitalEmployeesView onSelect={() => undefined} />);

    expect(await screen.findByRole("alert", {}, { timeout: 3_000 })).toHaveTextContent("Срез недоступен");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
});
