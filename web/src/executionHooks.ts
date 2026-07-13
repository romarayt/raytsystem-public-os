import { useQuery } from "@tanstack/react-query";
import { getJson } from "./api";
import type {
  AgentDetailSnapshot,
  AgentsSnapshot,
  DigitalEmployeesSnapshot,
  ExecutionApprovalsSnapshot,
  ExecutionBudgetsSnapshot,
  ExecutionFeaturesSnapshot,
  ExecutionRunsSnapshot,
  ExecutionTranscriptSnapshot,
  ExecutionWorkspacesSnapshot,
  TaskExecutionDetailSnapshot
} from "./executionTypes";

const executionQueryDefaults = {
  staleTime: 1_000,
  retry: 1,
  refetchOnWindowFocus: true
} as const;

interface PageParams {
  limit?: number;
  offset?: number;
}

export type EmployeeQuery = PageParams;

export interface ExecutionRunQuery extends PageParams {
  taskId?: string;
  employeeId?: string;
  status?: string;
}

export interface WorkspaceQuery extends PageParams {
  taskId?: string;
  status?: string;
}

export interface ApprovalQuery extends PageParams {
  taskId?: string;
  employeeId?: string;
  runId?: string;
}

export interface BudgetQuery extends PageParams {
  scopeId?: string;
  scopeKind?: string;
}

function withQuery(
  path: string,
  parameters: Record<string, string | number | undefined>
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(parameters)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const rendered = query.toString();
  return rendered ? `${path}?${rendered}` : path;
}

export const useExecutionFeatures = () =>
  useQuery({
    queryKey: ["execution", "features"],
    queryFn: () =>
      getJson<ExecutionFeaturesSnapshot>("/api/v1/execution/features"),
    ...executionQueryDefaults
  });

export const useDigitalEmployees = ({ limit = 100, offset = 0 }: EmployeeQuery = {}) =>
  useQuery({
    queryKey: ["execution", "employees", { limit, offset }],
    queryFn: () =>
      getJson<DigitalEmployeesSnapshot>(
        withQuery("/api/v1/execution/employees", { limit, offset })
      ),
    ...executionQueryDefaults
  });

export const useAgents = ({ limit = 500, offset = 0 }: EmployeeQuery = {}) =>
  useQuery({
    queryKey: ["agents", { limit, offset }],
    queryFn: () =>
      getJson<AgentsSnapshot>(withQuery("/api/v1/agents", { limit, offset })),
    ...executionQueryDefaults
  });

export const useAgentDetail = (
  agentId: string | null,
  expectedCatalogSha256: string | null
) =>
  useQuery({
    queryKey: ["agents", agentId, expectedCatalogSha256],
    queryFn: () =>
      getJson<AgentDetailSnapshot>(
        withQuery(`/api/v1/agents/${encodeURIComponent(agentId ?? "")}`, {
          expected: expectedCatalogSha256 ?? undefined
        })
      ),
    enabled: Boolean(agentId && expectedCatalogSha256),
    ...executionQueryDefaults
  });

export const useExecutionRuns = ({
  taskId,
  employeeId,
  status,
  limit = 100,
  offset = 0
}: ExecutionRunQuery = {}) =>
  useQuery({
    queryKey: [
      "execution",
      "runs",
      { taskId, employeeId, status, limit, offset }
    ],
    queryFn: () =>
      getJson<ExecutionRunsSnapshot>(
        withQuery("/api/v1/execution/runs", {
          task_id: taskId,
          employee_id: employeeId,
          status,
          limit,
          offset
        })
      ),
    ...executionQueryDefaults
  });

export const useExecutionWorkspaces = ({
  taskId,
  status,
  limit = 100,
  offset = 0
}: WorkspaceQuery = {}) =>
  useQuery({
    queryKey: ["execution", "workspaces", { taskId, status, limit, offset }],
    queryFn: () =>
      getJson<ExecutionWorkspacesSnapshot>(
        withQuery("/api/v1/execution/workspaces", {
          task_id: taskId,
          status,
          limit,
          offset
        })
      ),
    ...executionQueryDefaults
  });

export const useExecutionApprovals = ({
  taskId,
  employeeId,
  runId,
  limit = 100,
  offset = 0
}: ApprovalQuery = {}) =>
  useQuery({
    queryKey: [
      "execution",
      "approvals",
      { taskId, employeeId, runId, limit, offset }
    ],
    queryFn: () =>
      getJson<ExecutionApprovalsSnapshot>(
        withQuery("/api/v1/execution/approvals", {
          task_id: taskId,
          employee_id: employeeId,
          run_id: runId,
          limit,
          offset
        })
      ),
    ...executionQueryDefaults
  });

export const useExecutionBudgets = ({
  scopeId,
  scopeKind,
  limit = 100,
  offset = 0
}: BudgetQuery = {}) =>
  useQuery({
    queryKey: [
      "execution",
      "budgets",
      { scopeId, scopeKind, limit, offset }
    ],
    queryFn: () =>
      getJson<ExecutionBudgetsSnapshot>(
        withQuery("/api/v1/execution/budgets", {
          scope_id: scopeId,
          scope_kind: scopeKind,
          limit,
          offset
        })
      ),
    ...executionQueryDefaults
  });

export const useTaskExecutionDetail = (
  taskId: string | null,
  { limit = 100 }: Pick<PageParams, "limit"> = {}
) =>
  useQuery({
    queryKey: ["execution", "tasks", taskId, { limit }],
    queryFn: () =>
      getJson<TaskExecutionDetailSnapshot>(
        withQuery(
          `/api/v1/execution/tasks/${encodeURIComponent(taskId ?? "")}`,
          { limit }
        )
      ),
    enabled: Boolean(taskId),
    ...executionQueryDefaults
  });

export const useExecutionTranscript = (
  runId: string | null,
  {
    afterSequence = -1,
    limit = 250,
    enabled = true
  }: { afterSequence?: number; limit?: number; enabled?: boolean } = {}
) =>
  useQuery({
    queryKey: [
      "execution",
      "runs",
      runId,
      "transcript",
      { afterSequence, limit }
    ],
    queryFn: () =>
      getJson<ExecutionTranscriptSnapshot>(
        withQuery(
          `/api/v1/execution/runs/${encodeURIComponent(runId ?? "")}/transcript`,
          { after_sequence: afterSequence, limit }
        )
      ),
    enabled: enabled && Boolean(runId),
    ...executionQueryDefaults
  });
