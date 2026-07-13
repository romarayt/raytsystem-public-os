import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentsSurface } from "../features/AgentsSurface";
import {
  agentDetailFixture,
  agentsFixture,
  executionFeatureFlagsFixture,
  unifiedAgentFixture
} from "./mockApi";

interface AgentFixtureOptions {
  id: string;
  employeeId?: string;
  name: string;
  role: string;
  readiness: string;
  reason: string;
  definition?: boolean;
  execution?: boolean;
  currentTaskId?: string | null;
}

function executionFixture(options: AgentFixtureOptions) {
  const employeeId = options.employeeId ?? `employee_${options.id.replace(/^agent_/, "")}`;
  return {
    employee_id: employeeId,
    agent_definition_id: options.id,
    name: options.name,
    role: options.role,
    description: `${options.name} execution state`,
    runtime_adapter_id: "adapter_codex_local",
    enabled_skill_ids: ["safe-skill"],
    configuration_revision: "7".repeat(64),
    configuration_current: true,
    status: options.readiness === "running" ? "running" : "idle",
    stored_status: options.readiness === "running" ? "running" : "idle",
    state_source: "persisted_operational_state",
    reason_code: options.reason,
    current_task_id: options.currentTaskId ?? null,
    current_session_id: null,
    reporting_manager_id: null,
    budget_policy_id: null,
    concurrency_limit: 2,
    filesystem_policy: { ...unifiedAgentFixture.filesystem_policy },
    graph_policy: {
      max_depth: 2,
      max_nodes: 40,
      max_edges: 100,
      max_bytes: 48000,
      include_relations: ["imports", "calls"]
    },
    heartbeat_policy: {
      manual_enabled: true,
      scheduled_enabled: false,
      interval_seconds: 0
    },
    instruction_paths_omitted: true
  };
}

function agentFixture(options: AgentFixtureOptions) {
  const employeeId = options.employeeId ?? `employee_${options.id.replace(/^agent_/, "")}`;
  const definition = options.definition === false
    ? null
    : {
        ...unifiedAgentFixture.definition,
        agent_id: options.id,
        name: options.name,
        role: options.role,
        description: `${options.name} catalog description`
      };
  const execution = options.execution ? executionFixture(options) : null;
  return {
    ...unifiedAgentFixture,
    agent_id: options.id,
    employee_id: employeeId,
    name: options.name,
    role: options.role,
    description: `${options.name} user description`,
    definition,
    definition_state: definition ? "declared" : "missing",
    execution,
    execution_status: execution?.status ?? "disabled",
    execution_state_source: execution?.state_source ?? "catalog_projection",
    readiness: options.readiness,
    unavailable_reason: options.reason,
    runtime_adapter: {
      adapter_id: execution?.runtime_adapter_id ?? "adapter_disabled",
      name: execution ? "Codex Local" : "Catalog only",
      state: execution ? "enabled" : "disabled",
      isolation_mode: execution ? "workspace_sandbox" : "none",
      reason: execution ? null : "Execution unavailable."
    },
    concurrency_limit: execution?.concurrency_limit ?? 1,
    current_task_id: options.currentTaskId ?? null
  };
}

const catalogOnly = agentFixture({
  id: "agent_researcher",
  name: "Researcher",
  role: "researcher",
  readiness: "catalog_only",
  reason: "operational_record_missing"
});

const running = agentFixture({
  id: "agent_builder",
  name: "Builder",
  role: "builder",
  readiness: "running",
  reason: "persisted_operational_state",
  execution: true,
  currentTaskId: "task_build_agent_surface"
});

const ready = agentFixture({
  id: "agent_reviewer",
  name: "Reviewer",
  role: "reviewer",
  readiness: "ready",
  reason: "persisted_operational_state",
  execution: true
});

const disabled = agentFixture({
  id: "agent_librarian",
  name: "Librarian",
  role: "librarian",
  readiness: "disabled",
  reason: "catalog_definition_disabled",
  execution: true
});

const executionOnly = agentFixture({
  id: "agent_runtime_orphan",
  name: "Runtime Orphan",
  role: "orchestrator",
  readiness: "degraded",
  reason: "definition_missing",
  definition: false,
  execution: true
});

let agentsResponse: Record<string, unknown>;
let detailResponse: Record<string, unknown>;

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}

function renderAgents() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentsSurface onOpenSkill={vi.fn()} />
    </QueryClientProvider>
  );
}

function visibleAgentNames(): string[] {
  const list = screen.getByLabelText("Единый список агентов");
  return within(list).getAllByRole("button").map((button) =>
    button.getAttribute("aria-label")?.replace("Открыть агента ", "") ?? ""
  );
}

beforeEach(() => {
  window.history.replaceState({}, "", "/agents");
  agentsResponse = {
    ...agentsFixture,
    agents: [catalogOnly],
    total_agents: 1,
    pagination: { limit: 500, offset: 0, returned: 1 }
  };
  detailResponse = { ...agentDetailFixture, agent: catalogOnly };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const value = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = value.startsWith("http") ? new URL(value).pathname + new URL(value).search : value;
    if (path === "/api/v1/session") {
      return Promise.resolve(jsonResponse({ csrf_token: "csrf", expires_at_epoch: 99, local_only: true }));
    }
    if (path.startsWith("/api/v1/agents/")) return Promise.resolve(jsonResponse(detailResponse));
    if (path.startsWith("/api/v1/agents")) return Promise.resolve(jsonResponse(agentsResponse));
    return Promise.resolve(jsonResponse({}));
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/agents");
});

describe("AgentsSurface", () => {
  it("renders every stable agent ID once in one list, including catalog-only and degraded execution-only states", async () => {
    agentsResponse = {
      ...agentsFixture,
      features: {
        ...executionFeatureFlagsFixture,
        digital_employees_enabled: false,
        runtime_execution_enabled: false
      },
      agents: [catalogOnly, executionOnly],
      total_agents: 2,
      pagination: { limit: 500, offset: 0, returned: 2 }
    };

    const { container } = renderAgents();
    const list = await screen.findByLabelText("Единый список агентов");

    expect(within(list).getAllByRole("button")).toHaveLength(2);
    expect(within(list).getAllByRole("button", { name: "Открыть агента Researcher" })).toHaveLength(1);
    expect(within(list).getAllByRole("button", { name: "Открыть агента Runtime Orphan" })).toHaveLength(1);
    expect(screen.getAllByText("Только каталог").length).toBeGreaterThan(0);
    expect(screen.getByText("Нарушена целостность")).toBeInTheDocument();
    expect(screen.getByText("Определение агента отсутствует")).toBeInTheDocument();
    expect(screen.getByText("Выполнение отключено")).toBeInTheDocument();
    expect(screen.queryByText("Цифровые сотрудники")).not.toBeInTheDocument();
    expect(screen.queryByText("Определения каталога")).not.toBeInTheDocument();
    expect(container.querySelector(".route .route")).toBeNull();
  });

  it("filters the unified list by readiness and searches canonical English names and Russian roles", async () => {
    agentsResponse = {
      ...agentsFixture,
      agents: [catalogOnly, running, ready, disabled, executionOnly],
      total_agents: 5,
      pagination: { limit: 500, offset: 0, returned: 5 }
    };
    renderAgents();

    await screen.findByLabelText("Единый список агентов");
    const select = screen.getByRole("combobox", { name: "Фильтр агентов" });
    const expectedByFilter = [
      ["ready", ["Reviewer"]],
      ["disabled", ["Librarian"]],
      ["catalog_only", ["Researcher"]],
      ["running", ["Builder"]],
      ["setup", ["Runtime Orphan"]],
      ["all", ["Researcher", "Builder", "Reviewer", "Librarian", "Runtime Orphan"]]
    ] as const;

    for (const [filter, expected] of expectedByFilter) {
      fireEvent.change(select, { target: { value: filter } });
      await waitFor(() => expect(visibleAgentNames()).toEqual(expected));
    }

    const search = screen.getByRole("textbox", { name: "Найти агента" });
    fireEvent.change(search, { target: { value: "Builder" } });
    await waitFor(() => expect(visibleAgentNames()).toEqual(["Builder"]));

    fireEvent.change(search, { target: { value: "реализация" } });
    await waitFor(() => expect(visibleAgentNames()).toEqual(["Builder"]));
  });

  it("opens the specialized agent detail with all six accessible tabs and no nested route", async () => {
    detailResponse = {
      ...agentDetailFixture,
      agent: catalogOnly,
      instruction: {
        context_paths: ["AGENTS.md"],
        capabilities: ["research"],
        system_boundaries: {},
        limitations: []
      }
    };
    const { container } = renderAgents();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть агента Researcher" }));
    const tabList = await screen.findByRole("tablist", { name: "Разделы подробностей агента" });
    const detailHeading = screen.getByRole("heading", { name: "Researcher", level: 2 });
    await waitFor(() => expect(detailHeading).toHaveFocus());
    expect(within(tabList).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Обзор",
      "Инструкция",
      "Skills",
      "Runtime",
      "Доступ",
      "История"
    ]);
    expect(screen.getByRole("heading", { name: "Профиль агента" })).toBeInTheDocument();

    const destinations = [
      ["Инструкция", "Контекст и возможности"],
      ["Skills", "Назначенные Skills"],
      ["Runtime", "Состояние выполнения"],
      ["Доступ", "Файловая система и данные"],
      ["История", "Безопасная история выполнения"]
    ] as const;
    for (const [tab, heading] of destinations) {
      fireEvent.click(within(tabList).getByRole("tab", { name: tab }));
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }

    fireEvent.click(within(tabList).getByRole("tab", { name: "Runtime" }));
    expect(screen.getByText("Сессии ещё не создавались или хранилище выполнения не инициализировано.")).toBeInTheDocument();
    expect(screen.getByText("Бюджеты для агента не настроены.")).toBeInTheDocument();
    expect(screen.getByText("Активных аренд задач нет.")).toBeInTheDocument();

    fireEvent.click(within(tabList).getByRole("tab", { name: "Доступ" }));
    expect(screen.getByText("Связанные инструменты Tool Hub не заявлены.")).toBeInTheDocument();
    expect(screen.getByText("Активных подтверждений нет.")).toBeInTheDocument();

    fireEvent.click(within(tabList).getByRole("tab", { name: "История" }));
    expect(screen.getByText("Назначений задач ещё нет.")).toBeInTheDocument();
    expect(screen.getByText("Запусков для агента ещё нет.")).toBeInTheDocument();
    expect(screen.getByText("Аудит-события для этого агента не зафиксированы.")).toBeInTheDocument();

    expect(container.querySelector(".route .route")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Все агенты" }));
    const returnTarget = await screen.findByRole("button", { name: "Открыть агента Researcher" });
    await waitFor(() => expect(returnTarget).toHaveFocus());
  });

  it("renders localized safe agent detail records without paths, commands, credentials, or raw summaries", async () => {
    const sensitiveMarker = "DO_NOT_RENDER_SECRET";
    detailResponse = {
      ...agentDetailFixture,
      agent: {
        ...catalogOnly,
        definition: {
          ...catalogOnly.definition,
          context_paths: ["/private/operator/AGENTS.md"],
          egress_declared: true,
          egress_destination: `https://${sensitiveMarker}.example`
        }
      },
      instruction: {
        context_paths: ["/private/operator/AGENTS.md"],
        capabilities: ["research"],
        system_boundaries: {
          canonical_knowledge_write: false,
          external_side_effects: "approval_required",
          runtime_output_is_untrusted: true
        },
        limitations: ["catalog_definition_is_inert", "sensitive_runtime_fields_are_omitted"]
      },
      runtime: {
        execution: null,
        sessions: [{
          session_id: "session_active_01",
          status: "active",
          task_id: "task_research_01",
          provider: "local-provider",
          model: "safe-model",
          started_at: "2026-07-12T10:00:00Z",
          provider_session_id: sensitiveMarker
        }],
        runs: [],
        budgets: [{
          budget_policy_id: "budget_research_01",
          token_limit: { input_tokens: 1000, output_tokens: 500, cached_tokens: 250 },
          run_limit: 4,
          active_run_action: "block_new",
          usage: { run_count: 1 }
        }],
        leases: [{
          lease_id: "lease_research_01",
          task_id: "task_research_01",
          status: "active",
          expires_at: "2026-07-12T11:00:00Z",
          fencing_token: 3,
          path: `/private/${sensitiveMarker}`,
          command: `run ${sensitiveMarker}`
        }]
      },
      access: {
        filesystem: unifiedAgentFixture.filesystem_policy,
        data_classes: ["public", "internal"],
        effective_permissions: {
          workspace_read: true,
          staged_write: false,
          git_write: false,
          network: false
        },
        network: { egress_declared: true, approval_required: true },
        tools: [{
          tool_id: "tool_safe_search",
          provider: "Tool Hub",
          access: "read",
          health: "available",
          approval_policy: "approval_required",
          command: `tool ${sensitiveMarker}`,
          credentials: sensitiveMarker
        }],
        approvals: [{
          approval_id: "approval_external_01",
          action: "external_send",
          scope: ["network_egress"],
          expires_at: "2026-07-12T12:00:00Z",
          destination_present: true,
          destination: sensitiveMarker
        }]
      },
      history: {
        configuration_revision: "f".repeat(64),
        assignments: [{
          assignment_id: "assignment_research_01",
          task_id: "task_research_01",
          task_revision: 2,
          runtime_adapter_id: "adapter_codex_local"
        }],
        runs: [{
          run_id: "run_research_01",
          status: "succeeded",
          task_id: "task_research_01",
          provider: "local-provider",
          model: "safe-model",
          changed_file_count: 2,
          tests: [{ name: "safe-test", status: "pass" }],
          summary: sensitiveMarker,
          command: sensitiveMarker,
          working_directory: `/private/${sensitiveMarker}`
        }],
        audit_events: [{
          event_id: "audit_agent_01",
          event_type: "agent_configuration_changed",
          status: "confirmed",
          sequence: 7,
          recorded_at: "2026-07-12T10:30:00Z",
          payload: { credential: sensitiveMarker }
        }]
      }
    };
    renderAgents();

    fireEvent.click(await screen.findByRole("button", { name: "Открыть агента Researcher" }));
    const tabList = await screen.findByRole("tablist", { name: "Разделы подробностей агента" });
    expect(screen.getByRole("heading", { name: "Researcher", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Роль: исследование")).toBeInTheDocument();
    expect(screen.getByText("agent_researcher")).toBeInTheDocument();

    fireEvent.click(within(tabList).getByRole("tab", { name: "Инструкция" }));
    expect(screen.getByText("Запись в канонические знания")).toBeInTheDocument();
    expect(screen.getByText("Требует подтверждения")).toBeInTheDocument();
    expect(screen.getByText(/значения путей скрыты/)).toBeInTheDocument();

    fireEvent.click(within(tabList).getByRole("tab", { name: "Runtime" }));
    expect(screen.getByRole("heading", { name: "Сессии" })).toBeInTheDocument();
    expect(screen.getAllByText("Активно").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Лимиты: вход 1000/)).toBeInTheDocument();
    expect(screen.getAllByText(/Задача task_research_01/).length).toBeGreaterThanOrEqual(2);

    fireEvent.click(within(tabList).getByRole("tab", { name: "Доступ" }));
    expect(screen.getByRole("heading", { name: "Эффективные разрешения" })).toBeInTheDocument();
    expect(screen.getByText("tool_safe_search")).toBeInTheDocument();
    expect(screen.getByText(/Внешняя отправка/)).toBeInTheDocument();
    expect(screen.getByText(/назначение скрыто/)).toBeInTheDocument();

    fireEvent.click(within(tabList).getByRole("tab", { name: "История" }));
    expect(screen.getByText(/Конфигурация агента изменена/)).toBeInTheDocument();
    expect(screen.getByText(/изменено файлов: 2/)).toBeInTheDocument();

    expect(document.body).not.toHaveTextContent("/private/operator/AGENTS.md");
    expect(document.body).not.toHaveTextContent(sensitiveMarker);
  });
});
