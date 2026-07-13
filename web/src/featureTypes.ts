export type SystemSectionId =
  | "evals"
  | "traces"
  | "replays"
  | "policies"
  | "tools"
  | "protocols"
  | "packages"
  | "workflows"
  | "notifications"
  | "backups";

export type OperationalState =
  | "ready"
  | "loading"
  | "empty"
  | "disabled"
  | "unavailable"
  | "stale"
  | "degraded"
  | "blocked"
  | "approval_required"
  | "error"
  | "success"
  | "catalog_only";

export interface EmergencySnapshot {
  snapshot_id: string;
  state: OperationalState;
  active_actions: string[];
  revision: number | null;
}

export interface EncryptionProviderStatus {
  state?: string;
  provider?: string;
  reason?: string | null;
}

export interface PlatformFeatures {
  state: OperationalState;
  snapshot_id?: string;
  reason?: string;
  active_feature_flags: Record<string, boolean>;
  feature_config_sha256?: string;
  schema_versions?: Record<string, string>;
  migration?: Record<string, unknown>;
  event_backlog?: number;
  notification_backlog?: number;
  outbox_backlog?: number;
  eval_regression_count?: number;
  trace_storage_size?: number;
  circuit_breakers?: Array<Record<string, unknown>>;
  emergency_state?: EmergencySnapshot;
  mcp_health?: string;
  acp_health?: string;
  a2a_state?: string;
  a2a_network_exposure?: boolean;
  encryption_provider?: EncryptionProviderStatus;
  last_successful_backup?: Record<string, unknown> | null;
  platform_store?: string;
}

export interface SystemSectionSnapshot {
  snapshot_id: string;
  state: OperationalState;
  policy_sha256?: string;
  network_default?: string;
  workspace_default?: string;
  external_actions_default?: string;
  unread_count?: number;
  external_outbox_enabled?: boolean;
  runs?: Array<Record<string, unknown>>;
  baselines?: Array<Record<string, unknown>>;
  traces?: TraceSummary[];
  plans?: Array<Record<string, unknown>>;
  servers?: Array<Record<string, unknown>>;
  tools?: Array<Record<string, unknown>>;
  packages?: Array<Record<string, unknown>>;
  active?: Array<Record<string, unknown>>;
  workflows?: Array<Record<string, unknown>>;
  notifications?: Array<Record<string, unknown>>;
  backups?: Array<Record<string, unknown>>;
  acp?: Record<string, unknown>;
  a2a?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TraceSummary extends Record<string, unknown> {
  trace_id: string;
  root_run_id?: string;
  task_id?: string | null;
  status?: string;
  span_count?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  estimated_cost?: string | number;
  actual_cost?: string | number | null;
  created_at?: string;
  completed_at?: string | null;
}

export interface TraceSpan extends Record<string, unknown> {
  trace_id: string;
  span_id: string;
  parent_span_id?: string | null;
  span_kind: string;
  operation_name: string;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  status?: string;
  retry_count?: number;
  tool_name?: string | null;
  provider?: string | null;
  model?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  estimated_cost?: string | number;
  actual_cost?: string | number | null;
  approval_id?: string | null;
  error_code?: string | null;
  redaction_status?: string;
}

export interface TraceDetail {
  snapshot_id: string;
  trace: TraceSummary;
  spans: TraceSpan[];
}

export interface PolicySimulation {
  simulation_id: string;
  plan_id: string;
  policy_sha256: string;
  allowed_tools: string[];
  blocked_tools: string[];
  secrets_requested: string[];
  required_approvals: string[];
  potential_side_effects: string[];
  outcome: "allowed" | "approval_required" | "blocked";
  reason_codes: string[];
  dry_run: true;
  created_at: string;
}

export type EmergencyAction =
  | "pause_all_employees"
  | "cancel_active_runs"
  | "disable_runtime_execution"
  | "disable_network_adapters"
  | "disable_external_providers"
  | "freeze_task_checkout"
  | "revoke_runtime_sessions"
  | "revoke_pending_approvals"
  | "emergency_budget_stop";

export interface EmergencyReceipt {
  command_id: string;
  state: string;
  revision: number;
  snapshot_id: string;
  event_id: string;
  expected_effect: string[];
  approval_status: string;
  recovery: string;
}
