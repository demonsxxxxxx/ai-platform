import type {
  Message,
  ConnectionStatus,
  FormField,
  MessageAttachment,
  SelectedAgentProfileRequest,
  SelectedSkillRequest,
} from "../../types";
import type { ExecutionTimelineKind } from "../../types/message";
import type { SelectedSkillRecoverableCode } from "../useSelectedSkillTask";
import type {
  RunControlCancelResult,
  RunControlLifecycle,
} from "./runControlLifecycle";

export type SubmissionOutcome =
  | { status: "accepted" }
  | { status: "recoverable_error"; code: SelectedSkillRecoverableCode }
  | { status: "failed" };

export type StopGenerationResult = RunControlCancelResult;

export const CHAT_PUBLIC_PROJECTION_VERSION =
  "ai-platform.chat-public-projection.v1";
export const PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION =
  "ai-platform.public-execution-event.v1";
export const PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION =
  "ai-platform.public-execution-event.v2";
export const PUBLIC_AGENT_PROGRESS_SCHEMA_VERSION =
  "ai-platform.public-agent-progress.v1";
export const PUBLIC_AGENT_PROGRESS_EVENT_TYPE = "agent_public_progress";
export type PublicExecutionEventType = "execution_step" | "execution_progress" | "execution_step_completed" | "execution_step_failed";
export const PUBLIC_EXECUTION_EVENT_TYPES: ReadonlySet<PublicExecutionEventType> = new Set(["execution_step", "execution_progress", "execution_step_completed", "execution_step_failed"]);
const PUBLIC_EXECUTION_V1_EVENT_FIELDS = "schema_version event_id sequence run_id step_id kind stage status title summary progress safe_file_name artifact_public_id created_at".split(" ");
const PUBLIC_EXECUTION_V2_EVENT_FIELDS = "schema_version event_id sequence run_id step_id presentation_kind kind stage status progress safe_label created_at".split(" ");
const PUBLIC_EXECUTION_STATUSES: Record<PublicExecutionEventType, string> = { execution_step: "running", execution_progress: "running", execution_step_completed: "completed", execution_step_failed: "failed" };
const EXECUTION_TIMELINE_KINDS = new Set<ExecutionTimelineKind>(["analysis", "capability", "file_read", "processing", "generation", "verification", "artifact", "collaboration"]);
const PUBLIC_EXECUTION_V2_PRESENTATIONS = new Set([
  "skill:capability:execution",
  "mcp:capability:execution",
  "read:file_read:read",
  "read:file_read:search",
  "write:generation:edit",
  "processing:processing:data",
  "agent:collaboration:execution",
  "artifact:generation:artifact",
  "verification:verification:artifact",
  "adjustment:processing:execution",
  "read:file_read:attachments",
  "skill:capability:skills",
  "processing:processing:sandbox_preparation",
  "processing:processing:sandbox_submission",
  "verification:verification:artifact_validation",
  "artifact:generation:artifact_recovery",
]);
const PUBLIC_EXECUTION_V2_STATIC_LABELS: Record<string, string> = {
  "read:file_read:read": "Reading authorized files",
  "read:file_read:search": "Finding authorized files",
  "write:generation:edit": "Updating authorized files",
  "processing:processing:data": "Data processing",
  "agent:collaboration:execution": "Coordinating task",
  "artifact:generation:artifact": "Generating artifact",
  "verification:verification:artifact": "Checking result",
  "adjustment:processing:execution": "Adjusting result",
  "read:file_read:attachments": "Preparing authorized attachments",
  "skill:capability:skills": "Loading authorized Skills",
  "processing:processing:sandbox_preparation": "Preparing controlled execution",
  "processing:processing:sandbox_submission": "Running controlled task",
  "processing:processing:execution": "Waiting for the model response",
  "verification:verification:artifact_validation": "Checking generated results",
  "artifact:generation:artifact_recovery": "Preparing result recovery",
};

export const CHAT_PUBLIC_PROGRESS_EVENT_TYPES: ReadonlySet<string> = new Set([
  "queued",
  "run_started",
  "tool_call_started",
  "tool_call_completed",
  "agent_step_started",
  "agent_step_reused",
  "agent_step_completed",
  "agent_step_blocked",
  "agent_step_failed",
  "subagent_started",
  "subagent_completed",
  "subagent_failed",
  "run_child_created",
  "capability_selected",
  "intent_detected",
  "intent_confirmed",
  "context_snapshot_created",
  "file_bound",
  "artifact_created",
  "cancel_requested",
  "cancel_requested_but_completed",
  PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
]);

export type AssistantTextProjectionKind =
  | "assistant_delta"
  | "assistant_final";

// Event types from backend
export type EventType =
  | "metadata"
  | "stream_open"
  | "message:chunk"
  | "final_detail"
  | "user:message"
  | "user:cancel"
  | "thinking"
  | "tool:start"
  | "tool:result"
  | "todo:updated"
  | "summary"
  | "run_event"
  | "execution_step"
  | "execution_progress"
  | "execution_step_completed"
  | "execution_step_failed"
  | "artifact_card"
  | "agent:call"
  | "agent:result"
  | "approval_required"
  | "sandbox:starting"
  | "sandbox:ready"
  | "sandbox:error"
  | "token:usage"
  | "skills:changed"
  | "heartbeat"
  | "complete"
  | "done"
  | "end"
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
  detail_kind?: string;
  detail_code?: string;
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
  duration_ms?: number;
  timestamp?: string;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  model_id?: string;
  model?: string;
  // user:message event fields
  message_id?: string;
  locked_skill_label?: string;
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
  // Public terminal and transport status fields
  status?: string;
  trace_ref?: string | null;
  causation_event_id?: string | null;
  evidence_refs?: string[];
  artifact_refs?: string[];
  // Versioned public Chat projection fields
  projection_version?: string;
  projection_kind?: string;
  progress_kind?: string;
  wait_reason?: string | null;
  // ai-platform run_event fields
  event_id?: string;
  sequence?: number;
  event_type?: string;
  stage?: string;
  severity?: "info" | "warning" | "error" | string;
  payload?: Record<string, unknown>;
  tool_permission_card?: Record<string, unknown>;
  created_at?: string;
  // Strict ai-platform public execution timeline v1 fields
  schema_version?: string;
  kind?: string;
  title?: string;
  summary?: string;
  progress?: { current: number; total: number };
  safe_file_name?: string | null;
  artifact_public_id?: string | null;
  presentation_kind?: string;
  safe_label?: string;
  // v4 public Render Contract fields
  operation_id?: string;
  subagent_id?: string;
  category?: string;
  display_name?: string;
  input_summary?: string;
  result_summary?: string;
  failure_category?: string;
  denial_code?: string;
  current_category?: string;
  progress_percent?: number;
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

/** True only for the versioned public assistant-text projection contract. */
export function isAssistantTextProjection(
  data: EventData,
): data is EventData & {
  projection_version: typeof CHAT_PUBLIC_PROJECTION_VERSION;
  projection_kind: AssistantTextProjectionKind;
  content: string;
} {
  return (
    data.projection_version === CHAT_PUBLIC_PROJECTION_VERSION &&
    (data.projection_kind === "assistant_delta" ||
      data.projection_kind === "assistant_final") &&
    typeof data.content === "string"
  );
}

function isOpaquePublicId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
}

function isExecutionTimelineKind(value: unknown): value is ExecutionTimelineKind {
  return EXECUTION_TIMELINE_KINDS.has(value as ExecutionTimelineKind);
}

function isExecutionProgress(value: unknown): value is { current: number; total: number } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const { current, total } = value as { current?: unknown; total?: unknown };
  return (
    Object.keys(value).length === 2 && Object.hasOwn(value, "current") && Object.hasOwn(value, "total") &&
    typeof current === "number" && typeof total === "number" && Number.isInteger(current) && Number.isInteger(total) &&
    total > 0 &&
    current >= 0 &&
    current <= total
  );
}

/** Reject all partial, expanded, or raw-tool-shaped execution payloads. */
export function isPublicExecutionEvent(
  eventType: string,
  data: EventData,
): data is EventData & {
  schema_version:
    | typeof PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION
    | typeof PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION;
  event_id: string;
  sequence: number;
  run_id: string;
  step_id: string;
  kind: ExecutionTimelineKind;
  stage: string;
  status: "running" | "completed" | "failed";
  title?: string;
  summary?: string;
  presentation_kind?: string;
  safe_label?: string;
  progress: { current: number; total: number };
  safe_file_name: string | null;
  artifact_public_id: string | null;
  created_at: string | null;
} {
  if (!PUBLIC_EXECUTION_EVENT_TYPES.has(eventType as PublicExecutionEventType)) {
    return false;
  }
  const source = data as unknown as Record<string, unknown>;
  if (
    !isOpaquePublicId(source.event_id) ||
    !isOpaquePublicId(source.run_id) ||
    !isOpaquePublicId(source.step_id) ||
    typeof source.sequence !== "number" ||
    !Number.isSafeInteger(source.sequence) ||
    source.sequence < 0 ||
    typeof source.stage !== "string" || !source.stage ||
    !isExecutionProgress(source.progress) ||
    (source.safe_file_name !== null && typeof source.safe_file_name !== "string") ||
    (source.artifact_public_id !== null && !isOpaquePublicId(source.artifact_public_id)) ||
    (source.created_at !== null && typeof source.created_at !== "string")
  ) {
    return false;
  }
  if (!isExecutionTimelineKind(source.kind) || source.status !== PUBLIC_EXECUTION_STATUSES[eventType as PublicExecutionEventType]) {
    return false;
  }
  if (source.schema_version === PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION) {
    return (
      Object.keys(source).length === PUBLIC_EXECUTION_V1_EVENT_FIELDS.length &&
      PUBLIC_EXECUTION_V1_EVENT_FIELDS.every((key) => Object.hasOwn(source, key)) &&
      typeof source.title === "string" && !!source.title &&
      typeof source.summary === "string" && !!source.summary &&
      (source.safe_file_name === null || typeof source.safe_file_name === "string") &&
      (source.artifact_public_id === null || isOpaquePublicId(source.artifact_public_id))
    );
  }
  if (source.schema_version !== PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION) return false;
  const presentation = `${String(source.presentation_kind)}:${String(source.kind)}:${String(source.stage)}`;
  const hasSafeLabel = Object.hasOwn(source, "safe_label");
  const expectedSafeLabel = PUBLIC_EXECUTION_V2_STATIC_LABELS[presentation];
  return (
    Object.keys(source).length ===
      PUBLIC_EXECUTION_V2_EVENT_FIELDS.length - (hasSafeLabel ? 0 : 1) &&
    PUBLIC_EXECUTION_V2_EVENT_FIELDS
      .filter((key) => key !== "safe_label" || hasSafeLabel)
      .every((key) => Object.hasOwn(source, key)) &&
    PUBLIC_EXECUTION_V2_PRESENTATIONS.has(presentation) &&
    (!hasSafeLabel ||
      (typeof source.safe_label === "string" &&
        source.safe_label.length > 0 &&
        source.safe_label.length <= 96 &&
        !/[\\/:.;`'"<>|{}[\]$\n\r\t]/.test(source.safe_label) &&
        (expectedSafeLabel === undefined || source.safe_label === expectedSafeLabel))) &&
    (expectedSafeLabel === undefined || source.safe_label === expectedSafeLabel)
  );
}

const PUBLIC_AGENT_PROGRESS_MESSAGES: Record<string, Record<string, string>> = {
  attachment_materialization: {
    started: "Preparing authorized attachments",
    progress: "Preparing authorized attachments",
    completed: "Authorized attachments are ready",
    failed: "Authorized attachments could not be prepared",
  },
  skill_staging: {
    started: "Loading authorized Skills",
    progress: "Loading authorized Skills",
    completed: "Authorized Skills are ready",
    failed: "Authorized Skills could not be loaded",
  },
  sandbox_preparation: {
    started: "Preparing controlled execution",
    progress: "Preparing controlled execution",
    completed: "Controlled execution is ready",
    failed: "Controlled execution could not be prepared",
  },
  sandbox_submission: {
    started: "Running controlled task",
    progress: "Controlled task is still running",
    completed: "Controlled task has completed",
    failed: "Controlled task did not complete",
  },
  model_wait: {
    started: "Waiting for the model response",
    progress: "Waiting for the model response",
    completed: "Model response is ready",
    failed: "Model response was not available",
  },
  artifact_validation: {
    started: "Checking generated results",
    progress: "Checking generated results",
    completed: "Generated results have been checked",
    failed: "Generated results could not be checked",
  },
  artifact_recovery: {
    started: "Preparing result recovery",
    progress: "Preparing result recovery",
    completed: "Result recovery is ready",
    failed: "Result recovery did not complete",
  },
};

/** Accept only a fixed server-owned public phase message. */
export function isPublicAgentProgressEvent(data: EventData): boolean {
  const payload = data.payload as Record<string, unknown> | undefined;
  if (
    data.projection_version !== CHAT_PUBLIC_PROJECTION_VERSION ||
    data.event_type !== PUBLIC_AGENT_PROGRESS_EVENT_TYPE ||
    typeof data.stage !== "string" ||
    typeof data.message !== "string" ||
    !payload ||
    Object.keys(payload).length !== 5 ||
    payload.schema_version !== PUBLIC_AGENT_PROGRESS_SCHEMA_VERSION ||
    typeof payload.phase !== "string" ||
    typeof payload.lifecycle !== "string" ||
    typeof payload.step_id !== "string" ||
    payload.step_id !== `phase_${payload.phase}` ||
    payload.message !== data.message ||
    payload.phase !== data.stage
  ) {
    return false;
  }
  return PUBLIC_AGENT_PROGRESS_MESSAGES[payload.phase]?.[payload.lifecycle] === data.message;
}

/** A persisted sequence that proves new public progress on this run. */
export function isSequencedPublicChatEvent(
  eventType: string,
  data: EventData,
): boolean {
  const hasSequence =
    typeof data.sequence === "number" &&
    Number.isSafeInteger(data.sequence) &&
    data.sequence >= 0;
  return (
    hasSequence &&
    (PUBLIC_EXECUTION_EVENT_TYPES.has(eventType as PublicExecutionEventType)
      ? isPublicExecutionEvent(eventType, data)
      : eventType === "run_event" ||
        eventType === "artifact_card" ||
        (eventType === "message:chunk" &&
          isAssistantTextProjection(data)))
  );
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
  projection_version?: string;
  projection_kind?: string;
  content?: string;
  detail_kind?: string;
  detail_code?: string;
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
  progress_kind?: string;
  wait_reason?: string | null;
  message?: string;
  payload?: Record<string, unknown>;
  tool_permission_card?: Record<string, unknown>;
  created_at?: string;
  schema_version?: string;
  kind?: string;
  title?: string;
  summary?: string;
  progress?: { current: number; total: number };
  safe_file_name?: string | null;
  artifact_public_id?: string | null;
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
  locked_skill_label?: string;
}

// History event from backend
export interface HistoryEvent {
  id?: string | number;
  /** Production compatibility history mirrors the public outer event type. */
  type?: string;
  /** Monotonic persisted run-event cursor; synthetic history entries omit it. */
  sequence?: number;
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
    selectedAgentProfile?: SelectedAgentProfileRequest | null,
  ) => Promise<SubmissionOutcome>;
  canRetryPendingSubmission: boolean;
  retryPendingSubmission: () => Promise<void>;
  stopGeneration: () => Promise<StopGenerationResult>;
  clearMessages: () => void;
  loadHistory: (
    targetSessionId: string,
    targetRunId?: string,
  ) => Promise<SessionConfig | null>;
  reconnectSSE: () => Promise<void>;
  /** Stable lifecycle subscription used by persistent Run Playback panels. */
  runControlLifecycle: RunControlLifecycle;
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
