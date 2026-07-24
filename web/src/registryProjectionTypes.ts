export type RegistryProjectionState =
  | "disabled"
  | "not_configured"
  | "not_implemented"
  | "degraded"
  | "stale"
  | "ready";

export interface RegistryProjectionAgentEvidence {
  agent_id: string;
  status?: string;
  source_hash?: string | null;
  evidence_refs?: string[];
}

export interface RegistryProjectionProjectSkill {
  skill_id: string;
  name?: string;
  description?: string;
  test_status?: string;
  source_hash?: string | null;
  evidence_refs?: string[];
}

export interface RegistryProjectionWarning {
  code: string;
  paths?: string[];
  message?: string;
}

export interface RegistryProjectionSnapshot {
  protocol: "raytsystem-registry-projection";
  protocol_version: string;
  feature: "registry_projection_enabled";
  feature_config_sha256: string;
  enabled: boolean;
  state: RegistryProjectionState;
  snapshot_path: string;
  manifest_path: string;
  catalog_sha256: string | null;
  matched_agents: RegistryProjectionAgentEvidence[];
  project_skills: RegistryProjectionProjectSkill[];
  warnings: RegistryProjectionWarning[];
  side_effects: {
    write: false;
    repair: false;
    sync: false;
    reindex: false;
    external_send: false;
    execution: false;
  };
}
