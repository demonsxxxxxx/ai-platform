import type {
  Message,
  ConnectionStatus,
  FormField,
  MessageAttachment,
  SelectedSkillRequest,
} from "../../types";
import type { SelectedSkillRecoverableCode } from "../useSelectedSkillTask";

export type SubmissionOutcome =
  | { status: "accepted" }
  | { status: "recoverable_error"; code: SelectedSkillRecoverableCode }
  | { status: "failed" };

// Event types from backend
export type EventType =
  | "metadata"
  | "message:chunk"
  | "user:message"
  | "user:cancel"
  | "thinking"
  | "tool:start"
  | "tool:result"
  | "todo:updated"
  | "summary"
  | "run_event"
  | "artifact_card"
  | "agent:call"
  | "agent:result"
  | "approval_required"
  | "sandbox:starting"
  | "sandbox:ready"
  | "sandbox:error"
  | "token:usage"
  | "skills:changed"
  | "queue_update"
  | "complete"
  | "done"
  | "error";

export interface StreamEvent {
  event: EventType;
  data: string;
}

export interface EventData {
  session_id?: string;
  agent_id?: string;
  agent_name?: string;
  tool?: string;
  tool_call_id?: string;
  args?: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  success?: boolean;
  content?: string;
  thinking_id?: string;
  error?: string;
  type?: string;
  step_name?: string;
  step_id?: string;
  input?: string;
  depth?: number;
  // approval_required event fields
  id?: string;
  message?: string;
  choices?: string[];
  default?: string;
  // sandbox event fields
  sandbox_id?: string;
  work_dir?: string;
  // token:usage event fields
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  duration?: number;
  timestamp?: string;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  model_id?: string;
  model?: string;
  // user:message event fields
  message_id?: string;
  attachments?: Array<{
    id: string;
    key: string;
    name: string;
    type: string;
    mime_type: string;
    size: number;
    url: string;
  }>;
  // user:cancel event fields
  user_id?: string;
  run_id?: string;
  // skills:changed event fields
  action?: string;
  skill_name?: string;
  files_count?: number;
  // queue_update event fields
  status?: string;
  queue_position?: number;
  // ai-platform run_event fields
  event_id?: string;
  sequence?: number;
  event_type?: string;
  stage?: string;
  severity?: "info" | "warning" | "error" | string;
  payload?: Record<string, unknown>;
  tool_permission_card?: Record<string, unknown>;
  created_at?: string;
  // ai-platform artifact_card fields
  artifact_id?: string;
  artifact_type?: string;
  label?: string;
  content_type?: string;
  size_bytes?: number;
  download_url?: string;
  preview_url?: string | null;
  // todo event fields
  todos?: Array<{
    content: string;
    activeForm?: string;
    status: "pending" | "in_progress" | "completed";
  }>;
  updated_index?: number;
  // summary event fields
  summary_id?: string;
}

export interface UseAgentOptions {
  onApprovalRequired?: (approval: {
    id: string;
    message: string;
    type: string;
    fields?: FormField[];
    expires_at?: string | null;
    timeout?: number;
  }) => void;
  onClearApprovals?: () => void;
  getEnabledTools?: () => string[];
  getDisabledSkills?: () => string[];
  getDisabledMcpTools?: () => string[];
  getAgentOptions?: () => Record<string, boolean | string | number>;
  onSkillAdded?: (
    skillName: string,
    description: string,
    filesCount: number,
  ) => void;
  onStreamDone?: () => void;
}

// Subagent tracking item
export interface SubagentStackItem {
  agent_id: string;
  depth: number;
  message_id: string;
}

// History event data structure
export interface HistoryEventData {
  content?: string;
  tool?: string;
  tool_call_id?: string;
  args?: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  success?: boolean;
  error?: string;
  depth?: number;
  agent_id?: string;
  agent_name?: string;
  input?: string;
  timestamp?: string;
  event_id?: string;
  run_id?: string;
  sequence?: number;
  event_type?: string;
  stage?: string;
  severity?: string;
  message?: string;
  payload?: Record<string, unknown>;
  tool_permission_card?: Record<string, unknown>;
  created_at?: string;
  artifact_id?: string;
  artifact_type?: string;
  label?: string;
  content_type?: string;
  size_bytes?: number;
  download_url?: string;
  preview_url?: string | null;
  sandbox_id?: string;
  work_dir?: string;
  thinking_id?: string;
  todos?: Array<{
    content: string;
    activeForm?: string;
    status: "pending" | "in_progress" | "completed";
  }>;
  updated_index?: number;
  attachments?: Array<{
    id: string;
    key: string;
    name: string;
    type: string;
    mime_type: string;
    size: number;
    url: string;
  }>;
  message_id?: string;
}

// History event from backend
export interface HistoryEvent {
  id?: string | number;
  event_type: string;
  data: HistoryEventData | unknown;
  timestamp?: string;
  run_id?: string;
}

// Return type for useAgent hook
export interface UseAgentReturn {
  messages: Message[];
  isLoading: boolean;
  isLoadingHistory: boolean;
  error: string | null;
  sessionId: string | null;
  currentProjectId: string | null;
  currentRunId: string | null;
  isReconnecting: boolean;
  connectionStatus: ConnectionStatus;
  newlyCreatedSession: BackendSession | null;
  isInitializingSandbox: boolean;
  sandboxError: string | null;
  sendMessage: (
    content: string,
    agentOptions?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    selectedSkill?: SelectedSkillRequest | null,
  ) => Promise<SubmissionOutcome>;
  stopGeneration: () => Promise<void>;
  clearMessages: () => void;
  loadHistory: (
    targetSessionId: string,
    targetRunId?: string,
  ) => Promise<SessionConfig | null>;
  reconnectSSE: () => Promise<void>;
  setPendingProjectId: (id: string | null) => void;
  autoExpandProjectId: string | null;
  clearAutoExpandProjectId: (id?: string | null) => void;
}

// Session configuration restored from metadata
export interface SessionConfig {
  agent_options?: Record<string, boolean | string | number>;
  disabled_tools?: string[];
  disabled_skills?: string[];
  disabled_mcp_tools?: string[];
}

// Backend session type (simplified)
export interface BackendSession {
  id: string;
  agent_id: string;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  metadata: Record<string, unknown>;
  name?: string;
}

// Constants
export const API_BASE = "/api";
