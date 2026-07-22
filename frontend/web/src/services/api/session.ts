/**
 * Session API - 会话管理
 */

import type {
  SessionEventsResponse,
  RunSummary,
  MessageAttachment,
  SelectedSkillRequest,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const DEFAULT_CHAT_AGENT_ID = "general-agent";

// Backend Session type (matches backend Session schema)
export interface BackendSession {
  id: string;
  user_id?: string;
  agent_id: string;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  name?: string;
  metadata: Record<string, unknown>;
  unread_count?: number;
}

// Session list response type
export interface SessionListResponse {
  sessions: BackendSession[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export interface SessionRunsQuery {
  limit?: number;
  trace_id?: string;
}

export interface SessionInputFile {
  file_id: string;
  run_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  preview_url: string | null;
  download_url: string;
  created_at?: string | null;
}

export interface SessionInputFilesResponse {
  session_id: string;
  files: SessionInputFile[];
}

export interface CapabilitySuggestion {
  capability_id: string;
  label: string;
  reason: string;
}

export interface ChatIntentDecision {
  agent_id?: string;
}

export interface ChatStreamQueuedResponse {
  session_id: string;
  run_id: string;
  trace_id: string;
  status: "queued";
  submission_id?: string;
  queue_position?: number;
  queue_insight?: unknown;
  intent_decision?: ChatIntentDecision;
}

export interface ChatStreamPendingAdmissionResponse {
  session_id: string;
  run_id: string;
  status: "accepted_pending_enqueue";
  submission_id: string;
  intent_decision?: ChatIntentDecision;
}

export interface ChatStreamNeedsConfirmationResponse {
  session_id?: string | null;
  run_id?: null;
  status: "needs_confirmation";
  submission_id?: string;
  suggestions: CapabilitySuggestion[];
  intent_decision?: ChatIntentDecision;
}

export type ChatStreamResponse =
  | ChatStreamQueuedResponse
  | ChatStreamPendingAdmissionResponse
  | ChatStreamNeedsConfirmationResponse;

export const CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION =
  "chat_submission_resolution.v2" as const;

export interface DurableChatSubmissionResolution {
  protocol_version?: typeof CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION;
  submission_id: string;
  state:
    | "queued"
    | "accepted_pending_enqueue"
    | "enqueue_failed"
    | "needs_confirmation"
    | "rejected_before_persist";
  submission_disposition?: "rejected_before_persist";
  rejection_code?: string;
  outcome?: ChatStreamResponse;
}

/** A server-versioned, principal-scoped proof that no ledger row exists yet. */
export interface ChatSubmissionPreLedgerAbsenceResolution {
  protocol_version: typeof CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION;
  submission_id: string;
  state: "absent_before_ledger";
}

export type ChatSubmissionResolution =
  | DurableChatSubmissionResolution
  | ChatSubmissionPreLedgerAbsenceResolution;

/** Compatibility status projection with its authoritative platform value. */
export interface ChatRunStatusResponse {
  session_id: string;
  run_id?: string;
  status?: string | null;
  raw_status?: string | null;
  error?: string;
}

export function isChatStreamNeedsConfirmation(
  response: ChatStreamResponse | unknown,
): response is ChatStreamNeedsConfirmationResponse {
  return (
    typeof response === "object" &&
    response !== null &&
    (response as { status?: unknown }).status === "needs_confirmation"
  );
}

/** Prefer the authoritative routed agent while preserving a known session agent. */
export function resolveChatSessionAgentId(
  response: ChatStreamResponse,
  currentAgentId: string,
): string {
  const routedAgentId = response.intent_decision?.agent_id?.trim();
  return routedAgentId || currentAgentId || DEFAULT_CHAT_AGENT_ID;
}

export function buildMessageForkUrl(
  sessionId: string,
  messageId: string,
): string {
  return `${API_BASE}/api/sessions/${sessionId}/messages/${messageId}/fork`;
}

export function buildMessageCheckpointUrl(
  sessionId: string,
  messageId: string,
): string {
  return `${API_BASE}/api/sessions/${sessionId}/messages/${messageId}/checkpoints`;
}

export function buildCheckpointForkUrl(
  sessionId: string,
  checkpointId: string,
): string {
  return `${API_BASE}/api/sessions/${sessionId}/checkpoints/${checkpointId}/fork`;
}

function getBrowserTimezone(): string | undefined {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return typeof timezone === "string" && timezone.trim() ? timezone : undefined;
}

export function buildSubmitChatBody({
  message,
  sessionId,
  agentOptions,
  attachments,
  disabledSkills,
  enabledSkills,
  disabledMcpTools,
  userTimezone,
  selectedSkill,
  submissionId,
}: {
  message: string;
  sessionId?: string;
  agentOptions?: Record<string, boolean | string | number>;
  attachments?: MessageAttachment[];
  disabledSkills?: string[];
  enabledSkills?: string[];
  disabledMcpTools?: string[];
  userTimezone?: string;
  selectedSkill?: SelectedSkillRequest | null;
  submissionId?: string;
}): Record<string, unknown> {
  const body: Record<string, unknown> = {
    message,
    session_id: sessionId,
    agent_options: agentOptions,
    attachments,
    disabled_skills: selectedSkill ? undefined : disabledSkills,
    enabled_skills: selectedSkill ? undefined : enabledSkills,
    disabled_mcp_tools: disabledMcpTools,
  };

  if (submissionId) {
    body.submission_id = submissionId;
  }

  if (selectedSkill) {
    body.selected_skill = selectedSkill;
  }

  if (userTimezone) {
    body.user_timezone = userTimezone;
  }
  return body;
}

export function buildSessionRunsUrl(
  sessionId: string,
  options?: SessionRunsQuery,
): string {
  const searchParams = new URLSearchParams();
  if (options?.limit) {
    searchParams.set("limit", String(options.limit));
  }
  if (options?.trace_id) {
    searchParams.set("trace_id", options.trace_id);
  }

  const queryString = searchParams.toString();
  return `${API_BASE}/api/sessions/${sessionId}/runs${
    queryString ? `?${queryString}` : ""
  }`;
}

export function buildSessionInputFilesUrl(sessionId: string): string {
  return `${API_BASE}/api/ai/chat/sessions/${encodeURIComponent(sessionId)}/files`;
}

export function buildRunCancelUrl(runId: string): string {
  return `${API_BASE}/api/ai/runs/${runId}/cancel`;
}

export type RunControlMutationAction = "retry" | "resume";

/** Build the idempotent retry route for one exact opaque operation. */
export function buildRunRetryUrl(runId: string, operationId: string): string {
  return `${API_BASE}/api/ai/runs/${encodeURIComponent(runId)}/retry?operation_id=${encodeURIComponent(operationId)}`;
}

/** Build the idempotent checkpoint-resume route for one exact operation. */
export function buildRunResumeUrl(runId: string, operationId: string): string {
  return `${API_BASE}/api/ai/runs/${encodeURIComponent(runId)}/resume?operation_id=${encodeURIComponent(operationId)}`;
}

/** Build the GET-only authoritative resolver for one run-control operation. */
export function buildRunControlOperationUrl(
  runId: string,
  action: RunControlMutationAction,
  operationId: string,
): string {
  return `${API_BASE}/api/ai/runs/${encodeURIComponent(runId)}/control-operations/${action}/${encodeURIComponent(operationId)}`;
}

/** A newly queued child acknowledged for one exact run-control operation. */
export interface RunControlChildResponse {
  source_run_id?: string;
  action?: RunControlMutationAction;
  operation_id?: string;
  run_id: string;
  session_id: string;
  status: string;
  queue_position?: number;
  queue_insight?: unknown;
}

/** A linearized operation lookup; `absent` is authoritative for this scope. */
export interface RunControlOperationResponse {
  source_run_id: string;
  action: RunControlMutationAction;
  operation_id: string;
  run_id: string | null;
  session_id: string | null;
  status: string;
}

/** Build the single supported Chat submission endpoint. */
export function buildSubmitChatUrl(agentId = DEFAULT_CHAT_AGENT_ID): string {
  return `${API_BASE}/api/chat/stream?agent_id=${encodeURIComponent(agentId)}`;
}

export function buildChatSubmissionUrl(submissionId: string): string {
  return `${API_BASE}/api/chat/submissions/${encodeURIComponent(submissionId)}`;
}

export function buildChatSubmissionRetryAdmissionUrl(submissionId: string): string {
  return `${buildChatSubmissionUrl(submissionId)}/retry-admission`;
}

export function buildSessionListUrl(params?: {
  status?: string;
  limit?: number;
  skip?: number;
  search?: string;
}): string {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", params.limit.toString());
  if (params?.skip) searchParams.set("skip", params.skip.toString());
  if (params?.search) searchParams.set("search", params.search);
  const query = searchParams.toString();
  return `${API_BASE}/api/sessions${query ? `?${query}` : ""}`;
}

export const sessionApi = {
  /**
   * List all sessions with pagination
   */
  async list(params?: {
    status?: string;
    limit?: number;
    skip?: number;
    search?: string;
  }): Promise<SessionListResponse | BackendSession[]> {
    return authFetch<SessionListResponse | BackendSession[]>(
      buildSessionListUrl(params),
    );
  },

  /**
   * Get a session
   */
  async get(sessionId: string): Promise<BackendSession | null> {
    try {
      return await authFetch<BackendSession>(
        `${API_BASE}/api/sessions/${sessionId}`,
      );
    } catch (error) {
      if ((error as Error).message.includes("404")) {
        return null;
      }
      throw error;
    }
  },

  /**
   * Get all session events
   */
  async getEvents(
    sessionId: string,
    options?: {
      event_types?: string[];
      run_id?: string;
      exclude_run_id?: string;
    },
  ): Promise<SessionEventsResponse & { run_id?: string }> {
    const searchParams = new URLSearchParams();
    if (options?.event_types && options.event_types.length > 0) {
      searchParams.set("event_types", options.event_types.join(","));
    }
    if (options?.run_id) {
      searchParams.set("run_id", options.run_id);
    }
    if (options?.exclude_run_id) {
      searchParams.set("exclude_run_id", options.exclude_run_id);
    }

    const url = `${API_BASE}/api/sessions/${sessionId}/events${
      searchParams.toString() ? `?${searchParams}` : ""
    }`;
    return authFetch<SessionEventsResponse & { run_id?: string }>(url);
  },

  /**
   * Get all runs for a session
   */
  async getRuns(
    sessionId: string,
    options?: SessionRunsQuery,
  ): Promise<{ session_id: string; runs: RunSummary[]; count: number }> {
    return authFetch(buildSessionRunsUrl(sessionId, options));
  },

  /** Load the authoritative persistent input-file projection for a session. */
  async getInputFiles(sessionId: string): Promise<SessionInputFilesResponse> {
    return authFetch(buildSessionInputFilesUrl(sessionId));
  },

  /**
   * Delete a session
   */
  async delete(sessionId: string) {
    return authFetch(`${API_BASE}/api/sessions/${sessionId}`, {
      method: "DELETE",
    });
  },

  /**
   * Update session status
   */
  async updateStatus(sessionId: string, status: "active" | "archived") {
    return authFetch(
      `${API_BASE}/api/sessions/${sessionId}/status?status=${status}`,
      {
        method: "PATCH",
      },
    );
  },

  /**
   * Clear messages for a session
   */
  async clearMessages(sessionId: string) {
    return authFetch(`${API_BASE}/api/sessions/${sessionId}/clear-messages`, {
      method: "POST",
    });
  },

  /**
   * Generate title for session using LLM
   */
  async generateTitle(
    sessionId: string,
    message: string,
    lang: string = "en",
  ): Promise<{ title: string; session_id: string }> {
    return authFetch(
      `${API_BASE}/api/sessions/${sessionId}/generate-title?message=${encodeURIComponent(
        message,
      )}&lang=${encodeURIComponent(lang)}`,
      {
        method: "POST",
      },
    );
  },

  /**
   * Get session task status
   */
  async getStatus(
    sessionId: string,
    runId?: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<ChatRunStatusResponse> {
    const params = runId ? `?run_id=${runId}` : "";
    return authFetch(
      `${API_BASE}/api/chat/sessions/${sessionId}/status${params}`,
      { signal: options.signal },
    );
  },

  /**
   * Cancel a queued or running platform run.
   */
  async cancelRun(
    runId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<{
    run_id: string;
    session_id?: string | null;
    status: string;
  }> {
    return authFetch(buildRunCancelUrl(runId), {
      method: "POST",
      signal: options.signal,
    });
  },

  /** Create or resolve one queued retry child under an opaque operation id. */
  async retryRun(
    runId: string,
    operationId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<RunControlChildResponse> {
    return authFetch(buildRunRetryUrl(runId, operationId), {
      method: "POST",
      signal: options.signal,
    });
  },

  /** Create or resolve one checkpoint-resume child under an opaque operation id. */
  async resumeRun(
    runId: string,
    operationId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<RunControlChildResponse> {
    return authFetch(buildRunResumeUrl(runId, operationId), {
      method: "POST",
      signal: options.signal,
    });
  },

  /** Linearize with POST and resolve its exact durable child or safe absence. */
  async resolveRunControlOperation(
    runId: string,
    action: RunControlMutationAction,
    operationId: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<RunControlOperationResponse> {
    return authFetch(buildRunControlOperationUrl(runId, action, operationId), {
      signal: options.signal,
    });
  },

  /**
   * Submit a chat message (returns immediately)
   */
  async submitChat(
    message: string,
    sessionId?: string,
    agentOptions?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    disabledSkills?: string[],
    disabledMcpTools?: string[],
    selectedSkill?: SelectedSkillRequest | null,
    submissionId?: string,
    agentId?: string,
  ): Promise<ChatStreamResponse> {
    const body = buildSubmitChatBody({
      message,
      sessionId,
      agentOptions,
      attachments,
      disabledSkills,
      disabledMcpTools,
      userTimezone: getBrowserTimezone(),
      selectedSkill,
      submissionId,
    });
    return authFetch(buildSubmitChatUrl(agentId), {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async getChatSubmission(submissionId: string): Promise<ChatSubmissionResolution> {
    return authFetch(buildChatSubmissionUrl(submissionId), { cache: "no-store" });
  },

  async retryChatSubmissionAdmission(submissionId: string): Promise<ChatSubmissionResolution> {
    return authFetch(buildChatSubmissionRetryAdmissionUrl(submissionId), {
      method: "POST",
    });
  },

  /**
   * Update session (including name and metadata)
   */
  async update(
    sessionId: string,
    data: { name?: string; metadata?: Record<string, unknown> },
  ): Promise<{ status: string; session: BackendSession }> {
    return authFetch(`${API_BASE}/api/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  /**
   * Mark session as read (clear unread count)
   */
  async markRead(sessionId: string): Promise<void> {
    await authFetch(`${API_BASE}/api/sessions/${sessionId}/mark-read`, {
      method: "POST",
    });
  },

  async forkMessage(
    sessionId: string,
    messageId: string,
  ): Promise<{ session: BackendSession; source_session_id: string }> {
    return authFetch(buildMessageForkUrl(sessionId, messageId), {
      method: "POST",
    });
  },

  async createCheckpoint(
    sessionId: string,
    messageId: string,
    name?: string,
  ): Promise<{
    checkpoint: {
      id: string;
      name: string;
      message_id: string;
      created_at?: string;
    };
  }> {
    return authFetch(buildMessageCheckpointUrl(sessionId, messageId), {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },

  async forkCheckpoint(
    sessionId: string,
    checkpointId: string,
  ): Promise<{ session: BackendSession; source_session_id: string }> {
    return authFetch(buildCheckpointForkUrl(sessionId, checkpointId), {
      method: "POST",
    });
  },
};
