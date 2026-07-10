/**
 * Agent API - Agent 相关
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  AgentListResponse,
  AgentWorkspaceAgent,
  AgentWorkspaceArtifact,
  AgentWorkspaceConsole,
  AgentWorkspaceConsoleEvent,
  AgentWorkspaceConsoleStep,
  AgentWorkspaceJsonValue,
  AgentWorkspaceLatestContext,
  AgentWorkspaceMemoryContextPolicy,
  AgentWorkspaceParams,
  AgentWorkspaceProjection,
  AgentWorkspaceReferencedMaterials,
  AgentWorkspaceRunSummary,
  AgentWorkspaceSession,
  AgentWorkspaceToolPermission,
  AgentWorkspaceUsedContextSummary,
} from "../../types";

export interface AgentWorkspaceFetchOptions extends RequestInit {
  skipAuth?: boolean;
}

const AGENT_WORKSPACE_CONTRACT_VERSION = "ai-platform.agent-workspace.v1";
const AGENT_WORKSPACE_API_ROUTE = `${API_BASE}/api/agent-workspace`;

const PRIVATE_PROJECTION_KEYS = new Set([
  "default_skill_id",
  "executor_payload",
  "executorPayload",
  "local_path",
  "localPath",
  "raw_skill_id",
  "rawSkillId",
  "sandbox_workdir",
  "sandboxWorkdir",
  "skill_id",
  "skill_ids",
  "skillId",
  "skillIds",
  "source_json",
  "sourceJson",
  "storage_key",
  "storageKey",
  "work_dir",
  "workDir",
]);

const PRIVATE_KEY_PATTERNS = [
  /executor.?payload/i,
  /local.?path/i,
  /raw.?skill/i,
  /sandbox.?workdir/i,
  /skill.?ids?/i,
  /source.?json/i,
  /storage.?key/i,
  /work.?dir/i,
];

const PRIVATE_TEXT_PATTERNS = [
  /[A-Za-z]:\\/,
  /\/home\//,
  /\/tmp\//,
  /\/var\/lib\//,
  /tenants\/[^/\s]+\/private/i,
  /storage_key/i,
  /local_path/i,
  /sandbox_workdir/i,
  /source_json/i,
  /executor_payload/i,
];

function appendQueryParam(
  searchParams: URLSearchParams,
  key: string,
  value: string | number | null | undefined,
): void {
  if (value === undefined || value === null || value === "") return;
  searchParams.set(key, String(value));
}

function withQuery(path: string, query: URLSearchParams): string {
  const serialized = query.toString().replace(/\+/g, "%20");
  return serialized ? `${path}?${serialized}` : path;
}

function isPrivateProjectionKey(key: string): boolean {
  return (
    PRIVATE_PROJECTION_KEYS.has(key) ||
    PRIVATE_PROJECTION_KEYS.has(key.toLowerCase()) ||
    PRIVATE_KEY_PATTERNS.some((pattern) => pattern.test(key))
  );
}

function isUnsafeText(value: string): boolean {
  return PRIVATE_TEXT_PATTERNS.some((pattern) => pattern.test(value));
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asSafeText(value: unknown): string | undefined {
  const text = asString(value)?.trim();
  if (!text || isUnsafeText(text)) return undefined;
  return text;
}

function asNullableSafeText(value: unknown): string | null | undefined {
  if (value === null) return null;
  return asSafeText(value);
}

function asSafeApiPath(value: unknown): string | undefined {
  const text = asString(value)?.trim();
  if (!text || isUnsafeText(text)) return undefined;
  return text.startsWith("/api/") ? text : undefined;
}

function asNullableSafeApiPath(value: unknown): string | null | undefined {
  if (value === null) return null;
  return asSafeApiPath(value);
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  const numberValue =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : undefined;
  return typeof numberValue === "number" && Number.isFinite(numberValue)
    ? numberValue
    : undefined;
}

function compactObject<T extends Record<string, unknown>>(value: T): T {
  for (const key of Object.keys(value)) {
    if (value[key] === undefined) {
      delete value[key];
    }
  }
  return value;
}

function normalizeArray<T>(
  value: unknown,
  normalizeItem: (item: unknown) => T | null | undefined,
): T[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const normalized = normalizeItem(item);
        return normalized ? [normalized] : [];
      })
    : [];
}

function sanitizeProjectionJsonValue(
  value: unknown,
): AgentWorkspaceJsonValue | undefined {
  if (value === null || typeof value === "number" || typeof value === "boolean") {
    return Number.isNaN(value) ? undefined : value;
  }

  if (typeof value === "string") {
    return isUnsafeText(value) ? undefined : value;
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => sanitizeProjectionJsonValue(item))
      .filter((item): item is AgentWorkspaceJsonValue => item !== undefined);
  }

  const source = asRecord(value);
  if (!source) return undefined;

  const output: { [key: string]: AgentWorkspaceJsonValue } = {};
  for (const [key, childValue] of Object.entries(source)) {
    if (isPrivateProjectionKey(key)) continue;
    const sanitized = sanitizeProjectionJsonValue(childValue);
    if (sanitized !== undefined) {
      output[key] = sanitized;
    }
  }
  return output;
}

function normalizeJsonRecord(
  value: unknown,
): { [key: string]: AgentWorkspaceJsonValue } | undefined {
  const sanitized = sanitizeProjectionJsonValue(value);
  return asRecord(sanitized) as
    | { [key: string]: AgentWorkspaceJsonValue }
    | undefined;
}

function normalizeAgent(value: unknown): AgentWorkspaceAgent | null {
  const source = asRecord(value);
  if (!source) return null;
  const agentId = asSafeText(source.agent_id);
  const name = asSafeText(source.name) ?? agentId;
  if (!agentId || !name) return null;
  return {
    agent_id: agentId,
    capability_id: asNullableSafeText(source.capability_id),
    name,
    description: asSafeText(source.description) ?? "",
    status: asSafeText(source.status) ?? "active",
    version: asSafeText(source.version) ?? "platform-managed",
  };
}

function normalizeSession(value: unknown): AgentWorkspaceSession | null {
  const source = asRecord(value);
  if (!source) return null;
  const sessionId = asSafeText(source.session_id);
  const workspaceId = asSafeText(source.workspace_id);
  const agentId = asSafeText(source.agent_id);
  if (!sessionId || !workspaceId || !agentId) return null;
  return compactObject({
    session_id: sessionId,
    workspace_id: workspaceId,
    agent_id: agentId,
    capability_id: asNullableSafeText(source.capability_id),
    title: asSafeText(source.title) ?? "",
    created_at: asNullableSafeText(source.created_at),
    updated_at: asNullableSafeText(source.updated_at),
  }) as AgentWorkspaceSession;
}

function normalizeRunSummary(value: unknown): AgentWorkspaceRunSummary | null {
  const source = asRecord(value);
  if (!source) return null;
  const runId = asSafeText(source.run_id);
  const sessionId = asSafeText(source.session_id);
  const status = asSafeText(source.status);
  if (!runId || !sessionId || !status) return null;
  return compactObject({
    run_id: runId,
    session_id: sessionId,
    agent_id: asNullableSafeText(source.agent_id),
    capability_id: asNullableSafeText(source.capability_id),
    trace_id: asSafeText(source.trace_id) ?? "",
    status,
    progress: asNumber(source.progress) ?? 0,
    result_summary: asSafeText(source.result_summary) ?? "",
    error_code: asNullableSafeText(source.error_code),
    error_message: asNullableSafeText(source.error_message),
    created_at: asNullableSafeText(source.created_at),
    queued_at: asNullableSafeText(source.queued_at),
    started_at: asNullableSafeText(source.started_at),
    finished_at: asNullableSafeText(source.finished_at),
  }) as AgentWorkspaceRunSummary;
}

function normalizeTokenCounts(value: unknown) {
  const source = asRecord(value);
  if (!source) return undefined;
  return compactObject({
    input: asNumber(source.input),
    output: asNumber(source.output),
    total: asNumber(source.total),
  });
}

function normalizeCost(value: unknown) {
  const source = asRecord(value);
  if (!source) return undefined;
  return compactObject({
    estimated_cost_minor: asNumber(source.estimated_cost_minor),
  });
}

function normalizeConsoleEvent(value: unknown): AgentWorkspaceConsoleEvent | null {
  const source = asRecord(value);
  if (!source) return null;
  const payload = normalizeJsonRecord(source.payload);
  return compactObject({
    id: asSafeText(source.id),
    event_id: asSafeText(source.event_id),
    schema_version: asSafeText(source.schema_version),
    sequence: asNumber(source.sequence),
    run_id: asSafeText(source.run_id),
    trace_id: asSafeText(source.trace_id),
    event_type: asSafeText(source.event_type),
    type: asSafeText(source.type),
    stage: asSafeText(source.stage),
    message: asSafeText(source.message),
    severity: asSafeText(source.severity),
    visible_to_user: asBoolean(source.visible_to_user),
    error_code: asNullableSafeText(source.error_code),
    latency_ms: source.latency_ms === null ? null : asNumber(source.latency_ms),
    token_counts: normalizeTokenCounts(source.token_counts),
    cost: normalizeCost(source.cost),
    payload,
    created_at: asNullableSafeText(source.created_at),
  }) as AgentWorkspaceConsoleEvent;
}

function normalizeConsoleStep(value: unknown): AgentWorkspaceConsoleStep | null {
  const source = asRecord(value);
  if (!source) return null;
  return compactObject({
    id: asSafeText(source.id),
    step_id: asSafeText(source.step_id),
    run_id: asSafeText(source.run_id),
    step_key: asSafeText(source.step_key),
    step_kind: asSafeText(source.step_kind),
    status: asSafeText(source.status),
    title: asSafeText(source.title),
    role: asNullableSafeText(source.role),
    sequence: asNumber(source.sequence),
    payload: normalizeJsonRecord(source.payload),
    started_at: asNullableSafeText(source.started_at),
    finished_at: asNullableSafeText(source.finished_at),
    created_at: asNullableSafeText(source.created_at),
    updated_at: asNullableSafeText(source.updated_at),
  }) as AgentWorkspaceConsoleStep;
}

function normalizeConsole(value: unknown): AgentWorkspaceConsole {
  const source = asRecord(value) ?? {};
  return {
    run_id: asNullableSafeText(source.run_id) ?? null,
    status: asSafeText(source.status) ?? "idle",
    next_after_sequence: asNumber(source.next_after_sequence) ?? 0,
    events: normalizeArray(source.events, normalizeConsoleEvent),
    steps: normalizeArray(source.steps, normalizeConsoleStep),
  };
}

function normalizeArtifact(value: unknown): AgentWorkspaceArtifact | null {
  const source = asRecord(value);
  if (!source) return null;
  const artifactId = asSafeText(source.artifact_id) ?? asSafeText(source.id);
  if (!artifactId) return null;
  return compactObject({
    id: asSafeText(source.id) ?? artifactId,
    artifact_id: artifactId,
    artifact_type: asSafeText(source.artifact_type),
    label: asSafeText(source.label),
    content_type: asSafeText(source.content_type),
    size_bytes: asNumber(source.size_bytes),
    download_url: asSafeApiPath(source.download_url),
    preview_url: asNullableSafeApiPath(source.preview_url),
    status: asSafeText(source.status),
    lineage: normalizeJsonRecord(source.lineage),
    manifest: normalizeJsonRecord(source.manifest),
    created_at: asNullableSafeText(source.created_at),
  }) as AgentWorkspaceArtifact;
}

function normalizeToolPermission(
  value: unknown,
): AgentWorkspaceToolPermission | null {
  const source = asRecord(value);
  if (!source) return null;
  const permissionRequestId = asSafeText(source.permission_request_id);
  const runId = asSafeText(source.run_id);
  const toolId = asSafeText(source.tool_id);
  if (!permissionRequestId || !runId || !toolId) return null;
  return compactObject({
    permission_request_id: permissionRequestId,
    session_id: asSafeText(source.session_id) ?? "",
    run_id: runId,
    trace_id: asSafeText(source.trace_id) ?? "",
    tool_id: toolId,
    tool_call_id: asSafeText(source.tool_call_id) ?? "",
    action: asSafeText(source.action) ?? "execute",
    risk_level: asSafeText(source.risk_level) ?? "low",
    write_capable: asBoolean(source.write_capable) ?? false,
    status: asSafeText(source.status) ?? "pending",
    reason: asSafeText(source.reason) ?? "",
    decision_endpoint: asString(source.decision_endpoint) ?? "",
    created_at: asNullableSafeText(source.created_at),
  }) as AgentWorkspaceToolPermission;
}

function normalizeReferencedMaterials(
  value: unknown,
): AgentWorkspaceReferencedMaterials {
  const source = asRecord(value) ?? {};
  return compactObject({
    message_count: asNumber(source.message_count),
    file_count: asNumber(source.file_count),
    artifact_count: asNumber(source.artifact_count),
    memory_record_count: asNumber(source.memory_record_count),
  }) as AgentWorkspaceReferencedMaterials;
}

function normalizeInputKeys(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value.flatMap((item) => {
        const key = asSafeText(item);
        return key ? [key] : [];
      }),
    ),
  ).sort();
}

function normalizeUsedContextSummary(
  value: unknown,
): AgentWorkspaceUsedContextSummary {
  const source = asRecord(value) ?? {};
  return compactObject({
    source: asSafeText(source.source),
    input_keys: normalizeInputKeys(source.input_keys),
    memory_policy_source: asSafeText(source.memory_policy_source),
    long_term_memory_read: asBoolean(source.long_term_memory_read),
  }) as AgentWorkspaceUsedContextSummary;
}

function normalizeLatestContext(value: unknown): AgentWorkspaceLatestContext | null {
  const source = asRecord(value);
  if (!source) return null;
  return {
    source: asSafeText(source.source),
    referenced_materials: normalizeReferencedMaterials(source.referenced_materials),
    used_context_summary: normalizeUsedContextSummary(
      source.used_context_summary,
    ),
  };
}

function normalizeMemoryContextPolicy(
  value: unknown,
  workspaceId: string,
): AgentWorkspaceMemoryContextPolicy {
  const source = asRecord(value) ?? {};
  return compactObject({
    workspace_id: asSafeText(source.workspace_id) ?? workspaceId,
    agent_id: asNullableSafeText(source.agent_id),
    capability_id: asNullableSafeText(source.capability_id),
    memory_enabled: asBoolean(source.memory_enabled) ?? true,
    long_term_memory_enabled: asBoolean(source.long_term_memory_enabled) ?? false,
    retention_days: asNumber(source.retention_days) ?? 90,
    redaction_mode: asSafeText(source.redaction_mode) ?? "standard",
    source: asSafeText(source.source) ?? "default",
    reason: asSafeText(source.reason) ?? "",
    updated_at: asNullableSafeText(source.updated_at),
    latest_context: normalizeLatestContext(source.latest_context),
  }) as AgentWorkspaceMemoryContextPolicy;
}

export function buildAgentWorkspaceUrl(
  params: AgentWorkspaceParams = {},
): string {
  return withQuery(AGENT_WORKSPACE_API_ROUTE, buildAgentWorkspaceQuery(params));
}

function buildAgentWorkspaceQuery(
  params: AgentWorkspaceParams = {},
): URLSearchParams {
  const query = new URLSearchParams();
  appendQueryParam(query, "workspace_id", params.workspace_id);
  appendQueryParam(query, "agent_id", params.agent_id);
  appendQueryParam(query, "session_id", params.session_id);
  return query;
}

export function normalizeAgentWorkspaceProjection(
  response: AgentWorkspaceProjection | null | undefined,
): AgentWorkspaceProjection {
  const source = asRecord(response) ?? {};
  const workspaceId = asSafeText(source.workspace_id) ?? "default";
  return {
    contract_version:
      asSafeText(source.contract_version) ?? AGENT_WORKSPACE_CONTRACT_VERSION,
    workspace_id: workspaceId,
    selected_agent: normalizeAgent(source.selected_agent),
    agents: normalizeArray(source.agents, normalizeAgent),
    sessions: normalizeArray(source.sessions, normalizeSession),
    latest_runs: normalizeArray(source.latest_runs, normalizeRunSummary),
    run_console: normalizeConsole(source.run_console),
    artifacts: normalizeArray(source.artifacts, normalizeArtifact),
    pending_tool_permissions: normalizeArray(
      source.pending_tool_permissions,
      normalizeToolPermission,
    ),
    memory_context_policy: normalizeMemoryContextPolicy(
      source.memory_context_policy,
      workspaceId,
    ),
  };
}

export async function fetchAgentWorkspace(
  params: AgentWorkspaceParams = {},
  options: AgentWorkspaceFetchOptions = {},
): Promise<AgentWorkspaceProjection> {
  const query = buildAgentWorkspaceQuery(params);
  const response = await authFetch<AgentWorkspaceProjection | null>(
    withQuery(AGENT_WORKSPACE_API_ROUTE, query),
    {
      ...options,
      method: "GET",
    },
  );
  return normalizeAgentWorkspaceProjection(response);
}

export const agentApi = {
  /**
   * List all agents
   */
  async list(): Promise<AgentListResponse> {
    return authFetch<AgentListResponse>(`${API_BASE}/api/agents`);
  },

  async workspace(
    params: AgentWorkspaceParams = {},
    options: AgentWorkspaceFetchOptions = {},
  ): Promise<AgentWorkspaceProjection> {
    return fetchAgentWorkspace(params, options);
  },

  /**
   * Stream chat endpoint URL
   */
  getStreamUrl(agentId: string) {
    return `${API_BASE}/${agentId}/stream`;
  },

  /**
   * Non-streaming chat
   */
  async chat(agentId: string, message: string, sessionId?: string) {
    return authFetch(`${API_BASE}/${agentId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  },
};
