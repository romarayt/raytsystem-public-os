import type { AgentDefinition } from "./types";

export interface ExecutionFeatureFlags {
  code_graph_enabled: boolean;
  graph_first_query_enabled: boolean;
  digital_employees_enabled: boolean;
  task_workspaces_enabled: boolean;
  runtime_execution_enabled: boolean;
  codex_local_enabled: boolean;
  claude_local_enabled: boolean;
  heartbeats_enabled: boolean;
  scheduled_heartbeats_enabled: boolean;
}

export interface ExecutionSnapshotBase {
  snapshot_id: string;
  section: string;
  state: string;
  storage_state: string;
  features: ExecutionFeatureFlags;
}

export interface ExecutionPagination {
  limit: number;
  offset: number;
  returned: number;
}

export interface ExecutionFeaturesSnapshot extends ExecutionSnapshotBase {
  catalog_sha256: string;
  limits: {
    max_run_seconds: number;
    max_output_bytes: number;
    max_transcript_events: number;
    max_context_bytes: number;
    max_concurrent_runs: number;
  };
}

export interface EmployeeFilesystemPolicy {
  mode: string;
  allow_workspace_read: boolean;
  allow_staged_write: boolean;
  allow_git_read: boolean;
  allow_git_write: boolean;
}

export interface EmployeeGraphPolicy {
  max_depth: number;
  max_nodes: number;
  max_edges: number;
  max_bytes: number;
  include_relations: string[];
}

export interface EmployeeHeartbeatPolicy {
  manual_enabled: boolean;
  scheduled_enabled: boolean;
  interval_seconds: number;
}

export interface DigitalEmployeeView {
  employee_id: string;
  agent_definition_id: string;
  name: string;
  role: string;
  description: string;
  runtime_adapter_id: string;
  enabled_skill_ids: string[];
  configuration_revision: string;
  configuration_current: boolean | null;
  status: string;
  stored_status: string | null;
  state_source: string;
  reason_code: string;
  current_task_id: string | null;
  current_session_id: string | null;
  reporting_manager_id: string | null;
  budget_policy_id: string | null;
  concurrency_limit: number;
  filesystem_policy: EmployeeFilesystemPolicy;
  graph_policy: EmployeeGraphPolicy;
  heartbeat_policy: EmployeeHeartbeatPolicy;
  instruction_paths_omitted: true;
}

export interface DigitalEmployeesSnapshot extends ExecutionSnapshotBase {
  catalog_sha256: string;
  employees: DigitalEmployeeView[];
  pagination: ExecutionPagination;
  total_catalog_employees: number;
}

export type AgentReadiness =
  | "ready"
  | "disabled"
  | "catalog_only"
  | "running"
  | "requires_configuration"
  | "degraded";

export interface AgentRuntimeAdapterView {
  adapter_id: string;
  name: string;
  state: string;
  isolation_mode: string | null;
  reason: string | null;
}

export type PublicAgentDefinition = Omit<AgentDefinition, "egress_destination"> & {
  egress_declared: boolean;
};

export interface AgentViewModel {
  agent_id: string;
  employee_id: string;
  name: string;
  role: string;
  description: string;
  pack_id: string | null;
  version: string | null;
  definition: PublicAgentDefinition | null;
  definition_state: "configured" | "declared" | "missing";
  execution: DigitalEmployeeView | null;
  execution_status: string;
  execution_state_source: string;
  readiness: AgentReadiness;
  unavailable_reason: string;
  runtime_adapter: AgentRuntimeAdapterView;
  skill_ids: string[];
  skills_count: number;
  filesystem_policy: EmployeeFilesystemPolicy;
  concurrency_limit: number;
  current_task_id: string | null;
  current_session_id: string | null;
  configuration_revision: string;
  configuration_current: boolean | null;
  approved_data_classes: string[];
  egress_declared: boolean;
  effective_permissions: {
    workspace_read: boolean;
    staged_write: boolean;
    git_write: boolean;
    network: boolean;
  };
  duplicate_execution_record_count: number;
}

export interface AgentsSnapshot extends ExecutionSnapshotBase {
  catalog_sha256: string;
  agents: AgentViewModel[];
  pagination: ExecutionPagination;
  total_agents: number;
}

export interface AgentDetailSkill {
  skill_id: string;
  name: string;
  status: string;
  permissions: string[];
}

export interface AgentDetailSnapshot extends ExecutionSnapshotBase {
  catalog_sha256: string;
  agent: AgentViewModel;
  instruction: {
    context_paths: string[];
    capabilities: string[];
    system_boundaries: Record<string, boolean | string>;
    limitations: string[];
  };
  skills: AgentDetailSkill[];
  runtime: {
    execution: DigitalEmployeeView | null;
    sessions: ExecutionSessionView[];
    runs: ExecutionRunView[];
    budgets: BudgetView[];
    leases: Array<Record<string, unknown>>;
  };
  access: {
    filesystem: EmployeeFilesystemPolicy;
    data_classes: string[];
    tools: Array<Record<string, unknown>>;
    network: { egress_declared: boolean; approval_required: boolean };
    approvals: ExecutionApprovalView[];
    effective_permissions: AgentViewModel["effective_permissions"];
  };
  history: {
    assignments: TaskAssignmentView[];
    runs: ExecutionRunView[];
    configuration_revision: string;
    audit_events: Array<Record<string, unknown>>;
  };
}

export interface TaskAssignmentView {
  assignment_id: string;
  task_id: string;
  task_generation_id: string;
  task_revision: number;
  employee_id: string;
  runtime_adapter_id: string;
  budget_policy_id: string | null;
  approval_policy_id: string | null;
  graph_scope_id: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface TaskWorkspaceView {
  workspace_id: string;
  task_id: string;
  mode: string;
  git_commit: string;
  graph_snapshot_id: string;
  graph_fingerprint: string;
  manifest_sha256: string;
  status: string;
  created_at: string;
  updated_at: string;
  paths_omitted: true;
}

export interface TaskGraphScopeView {
  graph_scope_id: string;
  task_id: string;
  graph_snapshot_id: string;
  graph_fingerprint: string;
  generation_fingerprint: string;
  seed_node_ids: string[];
  max_depth: number;
  max_nodes: number;
  max_edges: number;
  max_bytes: number;
  include_relations: string[];
  root_count: number;
  roots_omitted: true;
  created_at: string;
}

export interface ExecutionTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  estimated_cost_micros: number | null;
  actual_cost_micros: number | null;
}

export interface ExecutionRunTest {
  name: string;
  status: string;
}

export interface ExecutionRunView {
  run_id: string;
  invocation_id: string;
  invocation_source: string;
  employee_id: string;
  task_id: string;
  runtime_adapter_id: string;
  provider: string;
  model: string | null;
  workspace_id: string;
  graph_snapshot_id: string;
  graph_scope_id: string;
  session_id_before: string | null;
  session_id_after: string | null;
  policy_decision_id: string;
  approval_id: string | null;
  task_lease_id: string;
  fencing_token: number;
  status: string;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  usage: ExecutionTokenUsage;
  changed_file_count: number;
  tests: ExecutionRunTest[];
  artifact_ids: string[];
  summary: string;
  error_code: string | null;
  retry_of_run_id: string | null;
  resumed_from_run_id: string | null;
  command_omitted: true;
  working_directory_omitted: true;
}

export interface ExecutionSessionView {
  session_id: string;
  runtime_adapter_id: string;
  provider: string;
  model: string | null;
  task_id: string;
  employee_id: string;
  workspace_id: string;
  graph_snapshot_id: string;
  context_snapshot_sha256: string;
  compatibility_sha256: string;
  started_at: string;
  last_resumed_at: string | null;
  status: string;
  previous_run_id: string | null;
  usage_totals: ExecutionTokenUsage;
  incompatibility_reason: string | null;
  provider_session_present: boolean;
  provider_session_omitted: true;
}

export interface BudgetTokenLimit {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}

export interface BudgetUsageView {
  tokens: BudgetTokenLimit;
  estimated_cost_micros: number | null;
  actual_cost_micros: number | null;
  run_count: number;
  heartbeat_count: number;
  updated_at: string;
}

export interface BudgetView {
  budget_policy_id: string;
  scope_kind: string;
  scope_id: string;
  token_limit: BudgetTokenLimit;
  cost_limit_micros: number | null;
  run_limit: number;
  heartbeat_limit: number;
  active_run_action: string;
  created_at: string;
  usage: BudgetUsageView | null;
}

export interface ExecutionApprovalView {
  approval_id: string;
  action: string;
  payload_sha256: string;
  employee_id: string | null;
  task_id: string | null;
  run_id: string | null;
  workspace_id: string | null;
  scope: string[];
  approved_by: string;
  approved_at: string;
  expires_at: string;
  destination_present: boolean;
  destination_omitted: true;
}

export interface ExecutionCommentView {
  comment_id: string;
  task_id: string;
  kind: string;
  actor: string;
  run_id: string | null;
  body: string;
  created_at: string;
}

export interface ExecutionRunsSnapshot extends ExecutionSnapshotBase {
  feature_state: string;
  runs: ExecutionRunView[];
  pagination: ExecutionPagination;
}

export interface ExecutionWorkspacesSnapshot extends ExecutionSnapshotBase {
  feature_state: string;
  workspaces: TaskWorkspaceView[];
  pagination: ExecutionPagination;
}

export interface ExecutionApprovalsSnapshot extends ExecutionSnapshotBase {
  feature_state: string;
  approvals: ExecutionApprovalView[];
  pagination: ExecutionPagination;
}

export interface ExecutionBudgetsSnapshot extends ExecutionSnapshotBase {
  feature_state: string;
  budgets: BudgetView[];
  pagination: ExecutionPagination;
}

export interface TaskExecutionDetailSnapshot extends ExecutionSnapshotBase {
  task_id: string;
  section_limit: number;
  assignments: TaskAssignmentView[];
  workspaces: TaskWorkspaceView[];
  graph_scopes: TaskGraphScopeView[];
  runs: ExecutionRunView[];
  sessions: ExecutionSessionView[];
  budgets: BudgetView[];
  approvals: ExecutionApprovalView[];
  comments: ExecutionCommentView[];
}

export interface TranscriptEventView {
  transcript_event_id: string;
  run_id: string;
  sequence: number;
  stream: string;
  event_type: string;
  text: string;
  redacted: boolean;
  created_at: string;
}

export interface ExecutionTranscriptSnapshot extends ExecutionSnapshotBase {
  run_id: string;
  after_sequence: number;
  events: TranscriptEventView[];
  returned: number;
}
