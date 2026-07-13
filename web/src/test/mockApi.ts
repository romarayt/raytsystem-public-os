export const systemFixture = {
  snapshot_id: "view_123",
  loaded_at: "2026-07-11T14:00:00Z",
  fingerprint: {
    knowledge_generation_id: "gen_knowledge",
    knowledge_generation_sha256: "a".repeat(64),
    task_generation_id: "tgen_tasks",
    task_generation_sha256: "b".repeat(64),
    catalog_sha256: "c".repeat(64),
    graph_snapshot_id: "graph_snapshot",
    graph_sha256: "d".repeat(64)
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
    adapters: 1
  },
  attention: { blocked_tasks: 0, failed_runs: 0, restricted_skills: 0 },
  safety: { binding: "loopback_only" }
};

const taskFixture = {
  task_id: "task_example",
  title: "Проверить точный путь к доказательству",
  description: "Пройти по подтверждённой цепочке источников.",
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

export const catalogFixture = {
  catalog_sha256: "c".repeat(64),
  packs: [{ pack_id: "pack_starter", name: "Starter", version: "1", description: "Universal", license_expression: "Apache-2.0", trust_class: "official", agent_ids: ["agent_researcher"], skill_ids: ["safe-skill"], context_paths: ["AGENTS.md"], optional: false }],
  agents: [{ agent_id: "agent_researcher", name: "Researcher", role: "researcher", description: "Source-bound research", version: "1", pack_id: "pack_starter", runtime_adapter_id: "adapter_disabled", skill_ids: ["safe-skill"], context_paths: ["AGENTS.md"], capabilities: ["research"], requested_filesystem_mode: "read_only", approved_data_classes: ["public"], egress_destination: null, accent: "#A99CF8", enabled: false }],
  skills: [{ skill_id: "safe-skill", name: "Safe skill", description: "Inert procedure", version: "1", source_path: "skills/safe-skill/SKILL.md", source_sha256: "e".repeat(64), pack_id: "pack_starter", trust_class: "official", sensitivity: "internal", permissions: [], test_status: "pass", enabled: true }],
  instructions: [
    { document_id: "instruction_agents", kind: "agent_routing", label: "Agent routing", path: "AGENTS.md", content_sha256: "f".repeat(64), size_bytes: 1200, sensitivity: "internal", editable: false },
    { document_id: "instruction_work", kind: "work_bootstrap", label: "Work bootstrap", path: "WORK.md", content_sha256: "1".repeat(64), size_bytes: 2480, sensitivity: "internal", editable: false },
    { document_id: "instruction_claude", kind: "claude_context", label: "Claude context", path: "CLAUDE.md", content_sha256: "2".repeat(64), size_bytes: 910, sensitivity: "internal", editable: false }
  ],
  adapters: [{ adapter_id: "adapter_disabled", name: "Catalog only", version: "1", state: "disabled", isolation_mode: "none", capabilities: [], egress_destination: null, reason: "Execution unavailable." }]
};

function publicAgentDefinition(agent: (typeof catalogFixture.agents)[number]) {
  const { egress_destination, ...definition } = agent;
  return { ...definition, egress_declared: egress_destination !== null };
}

const universeFixture = {
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
    { node_id: "seg_example", kind: "evidence", label: "Evidence segment", subtitle: "text", status: "verified", ring: "evidence", importance: 70, x: 300, y: 0, recorded_at: "2026-07-11T14:00:00Z", source_ref: "src_example", metadata: {} },
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

export const executionFeatureFlagsFixture = {
  code_graph_enabled: true,
  graph_first_query_enabled: true,
  digital_employees_enabled: true,
  task_workspaces_enabled: true,
  runtime_execution_enabled: false,
  codex_local_enabled: false,
  claude_local_enabled: false,
  heartbeats_enabled: true,
  scheduled_heartbeats_enabled: false
};

export const platformFeaturesFixture = {
  state: "available",
  snapshot_id: "pview_layout",
  active_feature_flags: {},
  event_backlog: 0,
  notification_backlog: 0,
  outbox_backlog: 0,
  eval_regression_count: 0,
  trace_storage_size: 0,
  circuit_breakers: [],
  emergency_state: { snapshot_id: "pview_layout", state: "available", active_actions: [], revision: null },
  mcp_health: "catalog_only",
  acp_health: "disabled",
  a2a_state: "disabled",
  a2a_network_exposure: false,
  encryption_provider: { state: "unavailable", provider: "none" },
  last_successful_backup: null,
  platform_store: "uninitialized"
};

export const executionFeaturesFixture = {
  snapshot_id: "xview_features",
  section: "features",
  state: "available",
  storage_state: "uninitialized",
  features: executionFeatureFlagsFixture,
  catalog_sha256: "c".repeat(64),
  limits: {
    max_run_seconds: 3600,
    max_output_bytes: 4194304,
    max_transcript_events: 10000,
    max_context_bytes: 48000,
    max_concurrent_runs: 2
  }
};

export const executionEmployeesFixture = {
  snapshot_id: "xview_employees",
  section: "employees",
  state: "catalog_only",
  storage_state: "uninitialized",
  features: executionFeatureFlagsFixture,
  catalog_sha256: "c".repeat(64),
  employees: [{
    employee_id: "employee_researcher",
    agent_definition_id: "agent_researcher",
    name: "Researcher",
    role: "researcher",
    description: "Source-bound research inside a managed boundary.",
    runtime_adapter_id: "adapter_fake",
    enabled_skill_ids: ["safe-skill"],
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
      max_bytes: 48000,
      include_relations: ["imports", "calls"]
    },
    heartbeat_policy: {
      manual_enabled: true,
      scheduled_enabled: false,
      interval_seconds: 0
    },
    instruction_paths_omitted: true,
    hidden_instruction_path: null
  }],
  pagination: { limit: 100, offset: 0, returned: 1 },
  total_catalog_employees: 1
};

export const unifiedAgentFixture = {
  agent_id: "agent_researcher",
  employee_id: "employee_researcher",
  name: "Researcher",
  role: "researcher",
  description: "Source-bound research inside a managed boundary.",
  pack_id: "pack_starter",
  version: "1",
  definition: publicAgentDefinition(catalogFixture.agents[0]),
  definition_state: "declared",
  execution: null,
  execution_status: "disabled",
  execution_state_source: "feature_gate",
  readiness: "requires_configuration",
  unavailable_reason: "execution_store_uninitialized",
  runtime_adapter: {
    adapter_id: "adapter_disabled",
    name: "Catalog only",
    state: "disabled",
    isolation_mode: "none",
    reason: "Execution unavailable."
  },
  skill_ids: ["safe-skill"],
  skills_count: 1,
  filesystem_policy: {
    mode: "workspace_root_readonly",
    allow_workspace_read: true,
    allow_staged_write: false,
    allow_git_read: true,
    allow_git_write: false
  },
  concurrency_limit: 1,
  current_task_id: null,
  current_session_id: null,
  configuration_revision: "f".repeat(64),
  configuration_current: null,
  approved_data_classes: ["public"],
  egress_declared: false,
  effective_permissions: {
    workspace_read: true,
    staged_write: false,
    git_write: false,
    network: false
  },
  duplicate_execution_record_count: 0
};

export const agentsFixture = {
  snapshot_id: "agents_layout",
  section: "agents",
  state: "catalog_only",
  storage_state: "uninitialized",
  features: executionFeatureFlagsFixture,
  catalog_sha256: "c".repeat(64),
  agents: [unifiedAgentFixture],
  pagination: { limit: 500, offset: 0, returned: 1 },
  total_agents: 1
};

export const agentDetailFixture = {
  snapshot_id: "agent_detail_layout",
  section: "agent_detail",
  state: "catalog_only",
  storage_state: "uninitialized",
  features: executionFeatureFlagsFixture,
  catalog_sha256: "c".repeat(64),
  agent: unifiedAgentFixture,
  instruction: {
    context_paths: ["AGENTS.md"],
    capabilities: ["research"],
    system_boundaries: {},
    limitations: []
  },
  skills: [{ skill_id: "safe-skill", name: "Safe skill", status: "enabled", permissions: [] }],
  runtime: { execution: null, sessions: [], runs: [], budgets: [], leases: [] },
  access: {
    filesystem: unifiedAgentFixture.filesystem_policy,
    data_classes: ["public"],
    tools: [],
    network: { egress_declared: false, approval_required: true },
    approvals: [],
    effective_permissions: unifiedAgentFixture.effective_permissions
  },
  history: { assignments: [], runs: [], configuration_revision: "f".repeat(64), audit_events: [] }
};

export const skillsFixture = {
  catalog_sha256: "c".repeat(64),
  skills: [{
    ...catalogFixture.skills[0],
    policy: {
      skill_id: "safe-skill",
      source_path: "skills/safe-skill/SKILL.md",
      pack_id: "pack_starter",
      trust_class: "official",
      sensitivity: "internal",
      editable: false,
      read_only_reason: "official_skill",
      forkable: true
    },
    related_agents: [{ agent_id: "agent_researcher", name: "Researcher", role: "researcher" }]
  }]
};

export const skillDetailFixture = {
  catalog_sha256: "c".repeat(64),
  skill: catalogFixture.skills[0],
  source: { path: "skills/safe-skill/SKILL.md", sha256: "e".repeat(64), content_available: true, content_restricted: false },
  content: "---\nname: safe-skill\ndescription: Inert procedure\nversion: '1'\npermissions: []\ntest_status: pass\n---\n# Safe skill\n\nRead-only instructions.",
  format: "text",
  content_format: "markdown",
  policy: skillsFixture.skills[0].policy,
  related_agents: skillsFixture.skills[0].related_agents,
  permission_boundary: {
    availability: "declared_ids_only", declared_permission_ids: [],
    filesystem: { availability: "not_modeled", items: [] }, network: { availability: "not_modeled", items: [] },
    tools: { availability: "not_modeled", items: [] }, secrets: { availability: "not_modeled", items: [] },
    approvals: { availability: "not_modeled", items: [] }, side_effects: { availability: "not_modeled", items: [] },
    sensitivity: "internal"
  },
  workflows: { availability: "not_modeled", items: [] },
  tools: { availability: "not_modeled", items: [] },
  tests: { availability: "catalog_metadata", test_status: "pass", evals: [], last_checked_at: null, commands: [], known_limitations: [] },
  history: { availability: "not_initialized", revisions: [], audit_events: [], current_revision_only: true, truncated: false }
};

export const executionRunsFixture = {
  snapshot_id: "xview_runs",
  section: "execution_runs",
  state: "uninitialized",
  storage_state: "uninitialized",
  features: executionFeatureFlagsFixture,
  feature_state: "disabled",
  runs: [],
  pagination: { limit: 100, offset: 0, returned: 0 }
};

export const codeGraphStatusFixture = {
  state: "current",
  snapshot_id: "cgraph_snapshot",
  snapshot_fingerprint: "f".repeat(64),
  file_count: 1,
  node_count: 2,
  edge_count: 1,
  ambiguous_edges: 0,
  changed_files: 0,
  deleted_files: 0,
  changed_paths: [],
  deleted_paths: [],
  paths_truncated: false,
  reason: null
};

const documentId = `doc_${"1".repeat(64)}`;
const documentSummary = {
  document_id: documentId,
  root_id: "manual",
  path: "knowledge/manual/layout-note.md",
  filename: "layout-note.md",
  extension: ".md",
  title: "Layout note",
  kind: "notes",
  mode: "read_write",
  sensitivity: "internal",
  size_bytes: 96,
  content_sha256: "7".repeat(64),
  modified_at: "2026-07-12T12:00:00Z",
  first_seen_at: "2026-07-12T11:00:00Z",
  modified_source: "Git working tree",
  added_source: "raytsystem first seen",
  git_status: "modified",
  is_modified: true,
  is_new: true,
  tags: ["layout"],
  aliases: ["Layout"],
  headings: ["Layout note"],
  outgoing_link_count: 0,
  backlink_count: 0,
  can_edit: true
};
const documentIndex = {
  state: "current",
  file_count: 1,
  last_refresh_at: "2026-07-12T12:00:00Z",
  snapshot_id: `docsnap_${"2".repeat(64)}`,
  message: null
};
const documentList = {
  snapshot_id: documentIndex.snapshot_id,
  index: documentIndex,
  roots: [{ root_id: "manual", label: "manual", path: "knowledge/manual", mode: "read_write", kind: "notes", editable: true }],
  items: [documentSummary],
  next_cursor: null
};

export function responseFor(url: string): unknown {
  const path = url.startsWith("http") ? new URL(url).pathname + new URL(url).search : url;
  if (path === "/api/v1/session") return { csrf_token: "csrf", expires_at_epoch: 99, local_only: true };
  if (path === "/api/v1/system") return systemFixture;
  if (path === "/api/v1/tasks") return { generation_id: "tgen_tasks", generation_sha256: "b".repeat(64), head_event_id: "tevt_a", tasks: [taskFixture] };
  if (path === "/api/v1/catalog") return catalogFixture;
  if (path.startsWith("/api/v1/skills/safe-skill")) return skillDetailFixture;
  if (path === "/api/v1/skills") return skillsFixture;
  if (path.startsWith("/api/v1/agents/agent_researcher")) return agentDetailFixture;
  if (path.startsWith("/api/v1/agents")) return agentsFixture;
  if (path === "/api/v1/runs") return { knowledge_generation_id: "gen_knowledge", runs: [{ run_id: "run_example", operation_type: "ingest", state: "succeeded", generation_id: "gen_knowledge", semantic_noop: false, created_at: "2026-07-11T13:00:00Z", updated_at: "2026-07-11T14:00:00Z", manifest_sha256: "9".repeat(64) }] };
  if (path === "/api/v1/universe") return universeFixture;
  if (path === "/api/v1/knowledge") return { generation_id: "gen_knowledge", generation_sha256: "a".repeat(64), claims: [], entities: [], sources: [], evidence: [] };
  if (path.startsWith("/api/v1/search")) return { graph_snapshot_id: "graph_snapshot", results: [] };
  if (path === "/api/v1/features") return platformFeaturesFixture;
  if (path === "/api/v1/execution/features") return executionFeaturesFixture;
  if (path.startsWith("/api/v1/execution/employees")) return executionEmployeesFixture;
  if (path.startsWith("/api/v1/execution/runs")) return executionRunsFixture;
  if (path === "/api/v1/code-graph/status") return codeGraphStatusFixture;
  if (path.startsWith("/api/v1/systems/")) return { snapshot_id: "pview_layout", state: "ready" };
  if (path === "/api/v1/handbook") return {
    available: true,
    root_articles: [{ slug: "/", title: "База знаний raytsystem", status: "stable", generated: false }],
    sections: [{ id: "getting-started", label: "Начало работы", position: 1, articles: [{ slug: "/getting-started/installation", title: "Установка", status: "stable", generated: false }] }],
    article_count: 2
  };
  if (path.startsWith("/api/v1/handbook/article")) return {
    slug: "/", title: "База знаний raytsystem", status: "stable", generated: false, section: "",
    markdown: "# База знаний raytsystem\n\nЭто публичная база знаний raytsystem.\n\n- [Установка](/getting-started/installation)\n"
  };
  if (path.startsWith("/api/v1/documents/folders")) return { snapshot_id: documentIndex.snapshot_id, items: [], next_cursor: null };
  if (path.startsWith(`/api/v1/documents/${documentId}/links`)) return { snapshot_id: documentIndex.snapshot_id, document_id: documentId, items: [], next_cursor: null };
  if (path.startsWith(`/api/v1/documents/${documentId}/backlinks`)) return { snapshot_id: documentIndex.snapshot_id, document_id: documentId, items: [], next_cursor: null };
  if (path.startsWith(`/api/v1/documents/${documentId}/history`)) return { snapshot_id: documentIndex.snapshot_id, document_id: documentId, items: [], next_cursor: null };
  if (path.startsWith(`/api/v1/documents/${documentId}/graph`)) return {
    snapshot_id: documentIndex.snapshot_id,
    focus_document_id: documentId,
    nodes: [{ node_id: documentId, kind: "manual_document", label: "Layout note", subtitle: "knowledge/manual/layout-note.md", status: "read_write", ring: "document", importance: 84, x: 0, y: 0, recorded_at: "2026-07-12T12:00:00Z", source_ref: null, metadata: { path: "knowledge/manual/layout-note.md", root_id: "manual", sensitivity: "internal" } }],
    edges: [],
    truncated: false
  };
  if (path.startsWith(`/api/v1/documents/${documentId}`)) return {
    snapshot_id: documentIndex.snapshot_id,
    document: documentSummary,
    content: "---\ntags: [layout]\n---\n# Layout note\n\nSafe local document.\n",
    format: "markdown",
    content_sha256: documentSummary.content_sha256,
    line_ending: "lf",
    final_newline: true,
    warnings: [],
    frontmatter: [{ key: "tags", value: ["layout"], type: "tags", editable: true }],
    visual_qualification: { can_open: true, can_save: true, round_trip_safe: true, warnings: [], unsupported_syntax: [] },
    assets: {}
  };
  if (path.startsWith("/api/v1/documents/search") || path.startsWith("/api/v1/documents/recent") || path.startsWith("/api/v1/documents?") || path === "/api/v1/documents") return documentList;
  return {};
}

export function mockFetch(input: RequestInfo | URL): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return Promise.resolve(new Response(JSON.stringify(responseFor(url)), { status: 200, headers: { "Content-Type": "application/json" } }));
}
