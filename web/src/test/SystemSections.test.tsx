import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperationalNotice } from "../components/FeatureState";
import { SystemSections } from "../features/SystemSections";

const featureStatus = {
  state: "ready",
  snapshot_id: "pview_demo",
  active_feature_flags: {
    evals_enabled: true,
    telemetry_enabled: true,
    policy_simulator_enabled: true,
    emergency_controls_enabled: true,
    external_mcp_execution_enabled: false
  },
  event_backlog: 12,
  notification_backlog: 1,
  outbox_backlog: 0,
  eval_regression_count: 0,
  trace_storage_size: 4096,
  circuit_breakers: [],
  emergency_state: { snapshot_id: "pview_demo", state: "ready", active_actions: [], revision: null },
  mcp_health: "catalog_only",
  acp_health: "disabled",
  a2a_state: "disabled",
  a2a_network_exposure: false,
  encryption_provider: { state: "unavailable", provider: "none" },
  last_successful_backup: null,
  platform_store: "ready"
};

function responseFor(url: string, method: string): unknown {
  if (url === "/api/v1/session") return { csrf_token: "csrf", expires_at_epoch: 99, local_only: true };
  if (url === "/api/v1/features") return featureStatus;
  if (url === "/api/v1/systems/traces") return {
    snapshot_id: "pview_demo",
    state: "ready",
    traces: [{
      trace_id: "trace_demo",
      root_run_id: "run_demo",
      status: "ok",
      span_count: 2,
      input_tokens: 10,
      output_tokens: 4,
      cached_tokens: 2,
      estimated_cost: "0.01",
      created_at: "2026-07-12T10:00:00Z",
      completed_at: "2026-07-12T10:00:00.120Z"
    }]
  };
  if (url === "/api/v1/traces/trace_demo") return {
    snapshot_id: "pview_demo",
    trace: {
      trace_id: "trace_demo",
      root_run_id: "run_demo",
      status: "ok",
      span_count: 2,
      input_tokens: 10,
      output_tokens: 4,
      cached_tokens: 2,
      estimated_cost: "0.01"
    },
    spans: [
      { trace_id: "trace_demo", span_id: "span_root", parent_span_id: null, span_kind: "run", operation_name: "run.execute", started_at: "2026-07-12T10:00:00Z", ended_at: "2026-07-12T10:00:00.120Z", duration_ms: 120, status: "ok", retry_count: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, redaction_status: "not_required" },
      { trace_id: "trace_demo", span_id: "span_model", parent_span_id: "span_root", span_kind: "model", operation_name: "model.complete", started_at: "2026-07-12T10:00:00.020Z", ended_at: "2026-07-12T10:00:00.100Z", duration_ms: 80, status: "ok", retry_count: 1, model: "fixture-model", input_tokens: 10, output_tokens: 4, cached_tokens: 2, redaction_status: "redacted" }
    ]
  };
  if (url === "/api/v1/systems/policies") return {
    snapshot_id: "policy_demo",
    state: "ready",
    policy_sha256: "a".repeat(64),
    network_default: "none",
    workspace_default: "staging_only",
    external_actions_default: "approval_required"
  };
  if (url === "/api/v1/systems/notifications") return {
    snapshot_id: "pview_notifications",
    state: "ready",
    notifications: [{
      notification_id: "ntf_demo",
      notification_type: "approval_needed",
      severity: "high",
      state: "unread",
      related_object_id: "task_demo",
      created_at: "2026-07-12T10:00:00Z"
    }]
  };
  if (url === "/api/v1/notifications/ntf_demo/transitions" && method === "POST") return {
    notification_id: "ntf_demo",
    state: "read"
  };
  if (url === "/api/v1/policy-simulations" && method === "POST") return {
    simulation_id: "psim_demo",
    plan_id: "plan_ui_policy_probe",
    policy_sha256: "a".repeat(64),
    allowed_tools: ["read_catalog"],
    blocked_tools: [],
    secrets_requested: [],
    required_approvals: [],
    potential_side_effects: [],
    outcome: "allowed",
    reason_codes: [],
    dry_run: true,
    created_at: "2026-07-12T10:00:00Z"
  };
  return { snapshot_id: "pview_demo", state: "ready" };
}

function renderSystems(path: string) {
  window.history.replaceState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><SystemSections /></QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    return Promise.resolve(new Response(JSON.stringify(responseFor(url, method)), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Systems control plane", () => {
  it("renders a redacted trace waterfall with timing, retries and token data", async () => {
    renderSystems("/systems?section=traces");
    expect(await screen.findByText(/Flight recorder/i)).toBeInTheDocument();
    expect(await screen.findByText("model.complete")).toBeInTheDocument();
    expect(screen.getByText(/retry 1/i)).toBeInTheDocument();
    expect(screen.getAllByText(/16 tokens/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/redaction redacted/i)).toBeInTheDocument();
    expect(screen.queryByText(/sk-proj-secret-value/i)).not.toBeInTheDocument();
  });

  it("runs a bounded dry-run policy probe without shell, environment or side effects", async () => {
    renderSystems("/systems?section=policies");
    fireEvent.click(await screen.findByRole("button", { name: "Проверить запуск" }));
    expect(await screen.findByText("Allowed tools")).toBeInTheDocument();
    expect(screen.getByText("read_catalog")).toBeInTheDocument();

    const fetchMock = vi.mocked(fetch);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      return url === "/api/v1/policy-simulations" && init?.method === "POST";
    })).toBe(true));
    const call = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      return url === "/api/v1/policy-simulations" && init?.method === "POST";
    });
    const rawBody = call?.[1]?.body;
    expect(typeof rawBody).toBe("string");
    if (typeof rawBody !== "string") throw new Error("Expected a JSON request body");
    const body = JSON.parse(rawBody) as { plan: Record<string, unknown> };
    expect(body.plan.network_access).toBe("none");
    expect(body.plan.potential_side_effects).toEqual([]);
    expect(body.plan).not.toHaveProperty("command");
    expect(body.plan).not.toHaveProperty("environment");
    expect(body.plan).not.toHaveProperty("tool_arguments");
  });

  it("requires explicit scope acknowledgement before an emergency command", async () => {
    renderSystems("/systems?section=policies");
    const button = await screen.findByRole("button", { name: "Применить блокировку" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("Почему требуется аварийная блокировка"), { target: { value: "Подтверждённый инцидент" } });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Понимаю область/i }));
    expect(button).toBeEnabled();
  });

  it("acknowledges an inbox notification through the snapshot-bound transition endpoint", async () => {
    renderSystems("/systems?section=notifications");
    fireEvent.click(await screen.findByRole("button", { name: "Прочитано" }));

    const fetchMock = vi.mocked(fetch);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      return url === "/api/v1/notifications/ntf_demo/transitions" && init?.method === "POST";
    })).toBe(true));
    const call = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      return url === "/api/v1/notifications/ntf_demo/transitions" && init?.method === "POST";
    });
    const rawBody = call?.[1]?.body;
    expect(typeof rawBody).toBe("string");
    if (typeof rawBody !== "string") throw new Error("Expected a JSON request body");
    const body = JSON.parse(rawBody) as Record<string, unknown>;
    expect(body.state).toBe("read");
    expect(body.expected_snapshot_id).toBe("pview_notifications");
    const headers = call?.[1]?.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBeTruthy();
  });

  it("presents every required operational state explicitly", () => {
    render(<>{(["empty", "disabled", "unavailable", "stale", "degraded", "blocked", "approval_required", "error", "success"] as const).map((state) => <OperationalNotice state={state} key={state} />)}</>);
    expect(screen.getByText("Пока нет записей")).toBeInTheDocument();
    expect(screen.getByText("Функция выключена")).toBeInTheDocument();
    expect(screen.getByText("Локальный источник недоступен")).toBeInTheDocument();
    expect(screen.getByText("Срез устарел")).toBeInTheDocument();
    expect(screen.getByText("Работа ограничена")).toBeInTheDocument();
    expect(screen.getByText("Операции заблокированы")).toBeInTheDocument();
    expect(screen.getByText("Требуется подтверждение")).toBeInTheDocument();
    expect(screen.getByText("Система сообщила об ошибке")).toBeInTheDocument();
    expect(screen.getByText("Операция подтверждена")).toBeInTheDocument();
  });
});
