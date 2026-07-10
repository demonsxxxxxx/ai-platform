// ============================================
// Agent Types
// ============================================

export interface AgentOption {
  type: "boolean" | "string" | "number";
  default: boolean | string | number;
  label: string;
  label_key?: string; // i18n translation key for label
  description?: string;
  description_key?: string; // i18n translation key for description
  icon?: string; // lucide-react icon name (e.g., "Brain", "Zap", "Settings")
  options?: { value: string | number; label?: string; label_key?: string }[]; // For select/dropdown type options
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  sort_order?: number;
  supports_sandbox?: boolean;
  options?: Record<string, AgentOption>;
}

export interface AgentListResponse {
  agents: AgentInfo[];
  count: number;
  default_agent?: string;
  allowed_model_ids?: string[] | null;
}

// Workflow event types
export interface WorkflowStepData {
  step_id: string;
  step_name: string;
  agent_id?: string;
  status?: "running" | "completed" | "failed";
  result?: string;
}

// ============================================
// Agent Config Types
// ============================================

// Agent configuration (global)
export interface AgentConfig {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
}

// Global agent config response
export interface GlobalAgentConfigResponse {
  agents: AgentConfig[];
  available_agents: string[];
}

// Role's accessible agents
export interface RoleAgentAssignment {
  role_id: string;
  role_name: string;
  allowed_agents: string[];
}

// Response after updating role's accessible agents
export interface RoleAgentAssignmentResponse {
  role_id: string;
  role_name: string;
  allowed_agents: string[];
}

// User's default agent preference
export interface UserAgentPreference {
  default_agent_id: string | null;
}

// Response for user agent preference operations
export interface UserAgentPreferenceResponse {
  default_agent_id: string | null;
}

// Role's accessible models
export interface RoleModelAssignment {
  role_id: string;
  role_name: string;
  allowed_models: string[];
  configured?: boolean;
}

// ============================================
// Agent Workspace V1 Projection Types
// ============================================

export type AgentWorkspaceJsonValue =
  | string
  | number
  | boolean
  | null
  | AgentWorkspaceJsonValue[]
  | { [key: string]: AgentWorkspaceJsonValue };

export interface AgentWorkspaceAgent {
  agent_id: string;
  capability_id?: string | null;
  name: string;
  description: string;
  status: string;
  version: string;
}

export interface AgentWorkspaceSession {
  session_id: string;
  workspace_id: string;
  agent_id: string;
  capability_id?: string | null;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgentWorkspaceRunSummary {
  run_id: string;
  session_id: string;
  agent_id?: string | null;
  capability_id?: string | null;
  trace_id: string;
  status: string;
  progress: number;
  result_summary: string;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AgentWorkspaceTokenCounts {
  input?: number;
  output?: number;
  total?: number;
}

export interface AgentWorkspaceCost {
  estimated_cost_minor?: number;
}

export interface AgentWorkspaceConsoleEvent {
  id?: string;
  event_id?: string;
  schema_version?: string;
  sequence?: number;
  run_id?: string;
  trace_id?: string;
  event_type?: string;
  type?: string;
  stage?: string;
  message?: string;
  severity?: string;
  visible_to_user?: boolean;
  error_code?: string | null;
  latency_ms?: number | null;
  token_counts?: AgentWorkspaceTokenCounts;
  cost?: AgentWorkspaceCost;
  payload?: { [key: string]: AgentWorkspaceJsonValue };
  created_at?: string | null;
}

export interface AgentWorkspaceConsoleStep {
  id?: string;
  step_id?: string;
  run_id?: string;
  step_key?: string;
  step_kind?: string;
  status?: string;
  title?: string;
  role?: string | null;
  sequence?: number;
  payload?: { [key: string]: AgentWorkspaceJsonValue };
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgentWorkspaceConsole {
  run_id?: string | null;
  status: string;
  next_after_sequence: number;
  events: AgentWorkspaceConsoleEvent[];
  steps: AgentWorkspaceConsoleStep[];
}

export interface AgentWorkspaceArtifact {
  id?: string;
  artifact_id?: string;
  artifact_type?: string;
  label?: string;
  content_type?: string;
  size_bytes?: number;
  download_url?: string;
  preview_url?: string | null;
  status?: string;
  lineage?: { [key: string]: AgentWorkspaceJsonValue };
  manifest?: { [key: string]: AgentWorkspaceJsonValue };
  created_at?: string | null;
}

export interface AgentWorkspaceToolPermission {
  permission_request_id: string;
  session_id: string;
  run_id: string;
  trace_id: string;
  tool_id: string;
  tool_call_id: string;
  action: string;
  risk_level: "low" | "medium" | "high" | string;
  write_capable: boolean;
  status: string;
  reason: string;
  decision_endpoint: string;
  created_at?: string | null;
}

export interface AgentWorkspaceReferencedMaterials {
  message_count?: number;
  file_count?: number;
  artifact_count?: number;
  memory_record_count?: number;
}

export interface AgentWorkspaceUsedContextSummary {
  source?: string;
  input_keys: string[];
  memory_policy_source?: string;
  long_term_memory_read?: boolean;
}

export interface AgentWorkspaceLatestContext {
  source?: string;
  referenced_materials: AgentWorkspaceReferencedMaterials;
  used_context_summary: AgentWorkspaceUsedContextSummary;
}

export interface AgentWorkspaceMemoryContextPolicy {
  workspace_id: string;
  agent_id?: string | null;
  capability_id?: string | null;
  memory_enabled: boolean;
  long_term_memory_enabled: boolean;
  retention_days: number;
  redaction_mode: string;
  source: string;
  reason: string;
  updated_at?: string | null;
  latest_context?: AgentWorkspaceLatestContext | null;
}

export interface AgentWorkspaceProjection {
  contract_version: string;
  workspace_id: string;
  selected_agent: AgentWorkspaceAgent | null;
  agents: AgentWorkspaceAgent[];
  sessions: AgentWorkspaceSession[];
  latest_runs: AgentWorkspaceRunSummary[];
  run_console: AgentWorkspaceConsole;
  artifacts: AgentWorkspaceArtifact[];
  pending_tool_permissions: AgentWorkspaceToolPermission[];
  memory_context_policy: AgentWorkspaceMemoryContextPolicy;
}

export interface AgentWorkspaceParams {
  workspace_id?: string | null;
  agent_id?: string | null;
  session_id?: string | null;
}
