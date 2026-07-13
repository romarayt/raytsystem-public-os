export type TaskStatus =
  | "inbox"
  | "planned"
  | "ready"
  | "running"
  | "review"
  | "blocked"
  | "done"
  | "cancelled";

export type TaskPriority = "low" | "normal" | "high" | "urgent";

export interface AgentTask {
  task_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  project_id: string;
  mission_id: string | null;
  assignee_ids: string[];
  skill_ids: string[];
  dependency_ids: string[];
  artifact_ids: string[];
  tags: string[];
  blocked_reason: string | null;
  sensitivity: string;
  created_by: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface TaskBoard {
  generation_id: string | null;
  generation_sha256: string | null;
  head_event_id: string | null;
  tasks: AgentTask[];
}

export interface TaskCommandResult {
  generation_id: string;
  generation_sha256: string;
  event_id: string;
  task: AgentTask;
  no_op: boolean;
}

export interface RuntimeAdapter {
  adapter_id: string;
  name: string;
  version: string;
  state: "disabled" | "available" | "configured" | "degraded";
  isolation_mode: string;
  capabilities: string[];
  egress_destination: string | null;
  reason: string | null;
}

export interface PackManifest {
  pack_id: string;
  name: string;
  version: string;
  description: string;
  license_expression: string;
  trust_class: string;
  agent_ids: string[];
  skill_ids: string[];
  context_paths: string[];
  optional: boolean;
}

export interface AgentDefinition {
  agent_id: string;
  name: string;
  role: string;
  description: string;
  version: string;
  pack_id: string;
  runtime_adapter_id: string;
  skill_ids: string[];
  context_paths: string[];
  capabilities: string[];
  requested_filesystem_mode: string;
  approved_data_classes: string[];
  egress_destination: string | null;
  accent: string;
  enabled: boolean;
}

export interface SkillDefinition {
  skill_id: string;
  name: string;
  description: string;
  version: string;
  source_path: string;
  source_sha256: string;
  pack_id: string;
  trust_class: string;
  sensitivity: string;
  permissions: string[];
  test_status: "pass" | "pending" | "unavailable";
  enabled: boolean;
}

export interface SkillEditPolicy {
  skill_id: string;
  source_path: string;
  pack_id: string;
  trust_class: string;
  sensitivity: string;
  editable: boolean;
  read_only_reason: string | null;
  forkable: boolean;
}

export interface RelatedAgentRef {
  agent_id: string;
  name: string;
  role: string;
}

export interface SkillListItem extends SkillDefinition {
  policy: SkillEditPolicy;
  related_agents: RelatedAgentRef[];
}

export interface SkillsSnapshot {
  catalog_sha256: string;
  skills: SkillListItem[];
}

export interface SkillToolRef {
  tool_id: string;
  provider: string;
  access: "read" | "write" | "read_write" | "unknown";
  approval_policy: string;
  health: string;
}

export interface SkillWorkflowRef {
  workflow_id: string;
  name: string;
  active: boolean;
}

export interface SkillRevisionRef {
  skill_revision_id: string | null;
  record_revision: number;
  record_state: string;
  source_sha256: string | null;
  catalog_sha256: string | null;
  test_status: string | null;
  changed_at: string | null;
  operation: string | null;
}

export interface SkillAuditEventRef {
  event_id: string;
  sequence: number;
  event_type: string;
  actor_id: string;
  recorded_at: string;
  payload_sha256: string;
  previous_event_sha256: string | null;
  payload: Record<string, unknown>;
}

export interface SkillTestDetail {
  availability: string;
  test_status: string;
  evals: Array<{ eval_id: string; status: string }>;
  last_checked_at: string | null;
  commands: string[];
  known_limitations: string[];
}

export interface SkillPermissionBoundarySection {
  availability: string;
  items: string[];
}

export interface SkillPermissionBoundary {
  availability: string;
  declared_permission_ids: string[];
  filesystem: SkillPermissionBoundarySection;
  network: SkillPermissionBoundarySection;
  tools: SkillPermissionBoundarySection;
  secrets: SkillPermissionBoundarySection;
  approvals: SkillPermissionBoundarySection;
  side_effects: SkillPermissionBoundarySection;
  sensitivity: string;
}

export interface SkillDetailSnapshot {
  catalog_sha256: string;
  skill: SkillDefinition;
  source: {
    path: string;
    sha256: string;
    content_available: boolean;
    content_restricted: boolean;
  };
  content: string | null;
  format: "text";
  content_format: "markdown";
  policy: SkillEditPolicy;
  related_agents: RelatedAgentRef[];
  permission_boundary: SkillPermissionBoundary;
  workflows: { availability: string; items: SkillWorkflowRef[] };
  tools: { availability: string; items: SkillToolRef[] };
  tests: SkillTestDetail;
  history: {
    availability: string;
    revisions: SkillRevisionRef[];
    audit_events: SkillAuditEventRef[];
    current_revision_only: boolean;
    truncated: boolean;
  };
}

export interface SkillValidationIssue {
  field: string;
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface SkillValidationResult {
  valid: boolean;
  errors: SkillValidationIssue[];
  warnings: SkillValidationIssue[];
  size_bytes: number;
  source_sha256: string;
  sensitivity: string;
  requested_test_status: string;
  effective_test_status: string;
}

export interface SkillSavePreview {
  operation: "skill_save_preview";
  skill_id: string;
  source_path: string;
  policy: SkillEditPolicy;
  expected_catalog_sha256: string;
  expected_source_sha256: string;
  current_catalog_sha256: string;
  current_source_sha256: string;
  proposed_source_sha256: string;
  normalized_content: string;
  diff: string;
  validation: SkillValidationResult;
  affected_agents: RelatedAgentRef[];
}

export interface SkillForkPreview {
  operation: "skill_fork_preview";
  source_skill_id: string;
  new_skill_id: string;
  destination: string;
  source_unchanged: true;
  expected_catalog_sha256: string;
  expected_source_sha256: string;
  proposed_source_sha256: string;
  diff: string;
  validation: SkillValidationResult;
  ownership_after_create: { pack_id: string; trust_class: string };
}

export interface SkillWriteResult {
  operation: "save" | "fork";
  skill_id: string;
  source_skill_id: string;
  source_path: string;
  source_sha256: string;
  catalog_sha256: string;
  skill_revision_id: string;
  record_revision: number;
  audit_event_id: string;
  test_status: "pending";
  validation: SkillValidationResult;
  affected_agents: RelatedAgentRef[];
  cache_invalidation: { scope: string; skill_ids: string[] };
}

export interface InstructionDocument {
  document_id: string;
  kind: string;
  label: string;
  path: string;
  content_sha256: string;
  size_bytes: number;
  sensitivity: string;
  editable: boolean;
}

export interface CatalogSnapshot {
  catalog_sha256: string;
  packs: PackManifest[];
  agents: AgentDefinition[];
  skills: SkillDefinition[];
  instructions: InstructionDocument[];
  adapters: RuntimeAdapter[];
}

export interface RunSummary {
  run_id: string;
  operation_type: string;
  state: string;
  generation_id: string | null;
  semantic_noop: boolean;
  created_at: string;
  updated_at: string;
  manifest_sha256: string;
}

export interface RunList {
  knowledge_generation_id: string;
  runs: RunSummary[];
}

export interface GraphNode {
  node_id: string;
  kind: string;
  label: string;
  subtitle: string;
  status: string;
  ring: string;
  importance: number;
  x: number;
  y: number;
  recorded_at: string | null;
  source_ref: string | null;
  metadata: Record<string, string>;
}

export interface GraphEdge {
  edge_id: string;
  source: string;
  target: string;
  kind: string;
  status: string;
  directed: boolean;
  metadata: Record<string, string>;
}

export type GraphLens = "universe" | "knowledge" | "work" | "agent" | "evidence" | "code";

export type CodeGraphState = "missing" | "current" | "unchecked" | "stale" | "building" | "error";
export type EdgeConfidence = "EXTRACTED" | "INFERRED" | "AMBIGUOUS";

export interface GraphSnapshot {
  graph_snapshot_id: string;
  knowledge_generation_id: string;
  knowledge_generation_sha256: string;
  task_generation_id: string | null;
  task_generation_sha256: string | null;
  catalog_sha256: string;
  code_snapshot_id: string | null;
  code_snapshot_sha256: string | null;
  code_snapshot_fingerprint: string | null;
  code_graph_state: CodeGraphState;
  code_file_count: number;
  code_node_count: number;
  code_edge_count: number;
  code_ambiguous_edges: number;
  code_view_node_count: number;
  code_view_edge_count: number;
  code_view_truncated: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  supported_lenses: GraphLens[];
  created_at: string;
}

export interface DocumentGraphSlice {
  snapshot_id: string;
  focus_document_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export interface SystemSnapshot {
  snapshot_id: string;
  loaded_at: string;
  fingerprint: {
    knowledge_generation_id: string;
    knowledge_generation_sha256: string;
    task_generation_id: string | null;
    task_generation_sha256: string | null;
    catalog_sha256: string;
    graph_snapshot_id: string;
    graph_sha256: string;
    code_snapshot_id: string | null;
    code_snapshot_sha256: string | null;
    code_snapshot_fingerprint: string | null;
    code_graph_state: CodeGraphState;
    execution_feature_snapshot_id: string;
  };
  counts: {
    claims: number;
    entities: number;
    sources: number;
    evidence: number;
    runs: number;
    tasks: Record<TaskStatus, number>;
    agents: number;
    skills: number;
    adapters: number;
    code_files: number;
    code_nodes: number;
    code_edges: number;
    code_ambiguous_edges: number;
  };
  attention: {
    blocked_tasks: number;
    failed_runs: number;
    restricted_skills: number;
  };
  safety: Record<string, string>;
}

export interface KnowledgeSnapshot {
  generation_id: string;
  generation_sha256: string;
  claims: Array<{
    claim_id: string;
    statement: string;
    status: string;
    recorded_at: string;
    evidence_count: number;
  }>;
  entities: Array<{
    entity_id: string;
    label: string;
    entity_type: string;
    status: string;
  }>;
  sources: Array<{
    source_id: string;
    label: string;
    source_type: string;
    trust: string;
    sensitivity: string;
  }>;
  evidence: Array<{
    evidence_id: string;
    source_id: string;
    locator_kind: string;
    excerpt_sha256: string;
  }>;
}

export interface Selection {
  id: string;
  kind: string;
  label: string;
  status: string;
  subtitle?: string;
  metadata?: Record<string, string>;
  snapshotId?: string;
  plane?: "universe" | "knowledge" | "tasks" | "catalog" | "code";
}

export interface CodeGraphStatus {
  state: CodeGraphState;
  snapshot_id: string | null;
  snapshot_fingerprint: string | null;
  file_count: number;
  node_count: number;
  edge_count: number;
  ambiguous_edges: number;
  changed_files: number;
  deleted_files: number;
  changed_paths: string[];
  deleted_paths: string[];
  paths_truncated: boolean;
  reason: string | null;
}

export interface CodeSourceLocation {
  start_line: number;
  start_column: number;
  end_line: number;
  end_column: number;
}

export interface CodeNode {
  node_id: string;
  kind: string;
  label: string;
  qualified_name: string;
  path: string | null;
  location: CodeSourceLocation | null;
  community_id: number | null;
  is_god: boolean;
  is_bridge: boolean;
  metadata: Record<string, string>;
}

export interface CodeEdge {
  edge_id: string;
  source: string;
  target: string;
  relation: string;
  confidence: EdgeConfidence;
  source_file: string;
  source_location: CodeSourceLocation | null;
  metadata: Record<string, string>;
}

export interface CodeGraphQueryNode {
  node_id: string;
  kind: string;
  label: string;
  qualified_name: string;
  path: string | null;
  location: CodeSourceLocation | null;
  community_id: number | null;
  is_god: boolean;
  is_bridge: boolean;
  depth: number;
}

export interface CodeGraphQueryEdge {
  edge_id: string;
  source: string;
  target: string;
  relation: string;
  confidence: EdgeConfidence;
  source_file: string;
  source_location: CodeSourceLocation | null;
}

export interface CodeGraphQueryResult {
  operation: "query" | "explain" | "neighbors" | "path" | "impact";
  snapshot_id: string;
  snapshot_fingerprint: string;
  nodes: CodeGraphQueryNode[];
  edges: CodeGraphQueryEdge[];
  seed_node_ids: string[];
  ordered_node_ids: string[];
  truncated: boolean;
  estimated_context_bytes: number;
  fallback_reason: string | null;
}

export interface SearchResult {
  id: string;
  kind: string;
  label: string;
  subtitle: string;
  status: string;
  snapshot_id: string;
}

export interface HandbookArticleRef {
  slug: string;
  title: string;
  status: string;
  generated: boolean;
}

export interface HandbookSection {
  id: string;
  label: string;
  position: number;
  articles: HandbookArticleRef[];
}

export interface HandbookTree {
  available: boolean;
  root_articles: HandbookArticleRef[];
  sections: HandbookSection[];
  article_count: number;
}

export interface HandbookArticle {
  slug: string;
  title: string;
  status: string;
  generated: boolean;
  section: string;
  markdown: string;
}
