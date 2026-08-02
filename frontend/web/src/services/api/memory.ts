/**
 * Memory API - 记忆空间
 */

import { authFetch } from "./fetch";

export interface AiPlatformApiClient {
  request<T>(url: string, init?: RequestInit): Promise<T>;
}

const defaultAiPlatformClient: AiPlatformApiClient = {
  request: authFetch,
};

export interface MemoryPolicy {
  tenant_id: string;
  workspace_id: string;
  user_id: string;
  agent_id: string | null;
  memory_enabled: boolean;
  long_term_memory_enabled: boolean;
  retention_days: number;
  source: string;
  reason: string;
  updated_by: string;
  updated_at: string | null;
}

export interface MemoryPolicyResponse {
  memory_policy: MemoryPolicy;
}

export interface MemoryPolicyRequest {
  workspace_id: string;
  agent_id?: string | null;
  memory_enabled: boolean;
  long_term_memory_enabled: boolean;
  retention_days: number;
  reason?: string;
}

export interface AdminMemoryPoliciesParams {
  workspace_id?: string;
  user_id?: string;
  agent_id?: string;
  limit?: number;
}

export interface AdminMemoryPoliciesResponse {
  memory_policies: MemoryPolicy[];
  summary: {
    workspace_id: string;
    user_id?: string | null;
    agent_id?: string | null;
    returned_count: number;
    limit: number;
  };
}

export interface AiPlatformMemoryRecord {
  memory_record_id: string;
  tenant_id: string;
  workspace_id: string;
  user_id: string;
  agent_id: string | null;
  session_id: string | null;
  record_type: string;
  content?: string;
  metadata: Record<string, unknown>;
  status: string;
  expires_at?: string | null;
  deleted_at?: string | null;
  created_at: string | null;
  updated_at?: string | null;
}

export interface MemoryRecordsParams {
  workspace_id?: string;
  agent_id?: string;
  session_id?: string;
  status?: "active" | "deleted" | "all";
  limit?: number;
}

export interface MemoryRecordsResponse {
  memory_records: AiPlatformMemoryRecord[];
}

export interface CleanupExpiredMemoryResponse {
  deleted_count: number;
  memory_records: AiPlatformMemoryRecord[];
}

const PRIVATE_MEMORY_KEYS = new Set([
  "payload",
  "raw_payload",
  "rawPayload",
  "request_payload",
  "requestPayload",
  "decision_payload",
  "decisionPayload",
  "private_payload",
  "privatePayload",
  "executor_private_payload",
  "executorPrivatePayload",
  "storage_key",
  "storageKey",
  "runtime_path",
  "runtimePath",
  "work_dir",
  "workDir",
  "command_sha256",
  "commandSha256",
  "fingerprint",
  "secret",
  "password",
  "api_key",
  "apiKey",
  "access_token",
  "refresh_token",
  "id_token",
]);

const PRIVATE_MEMORY_KEY_PATTERNS = [
  /secret/i,
  /token/i,
  /api.?key/i,
  /password/i,
  /raw.?command/i,
  /raw.?payload/i,
  /request.?payload/i,
  /decision.?payload/i,
  /private.?payload/i,
  /executor.?private/i,
  /storage.?key/i,
  /runtime.?path/i,
  /work.?dir/i,
  /command.?sha/i,
  /fingerprint/i,
];

function isPrivateMemoryKey(key: string): boolean {
  return (
    PRIVATE_MEMORY_KEYS.has(key) ||
    PRIVATE_MEMORY_KEYS.has(key.toLowerCase()) ||
    PRIVATE_MEMORY_KEY_PATTERNS.some((pattern) => pattern.test(key))
  );
}

function appendParam(
  query: URLSearchParams,
  key: string,
  value: string | number | null | undefined,
) {
  if (value === undefined || value === null || value === "") return;
  query.set(key, String(value));
}

function withQuery(path: string, query: URLSearchParams): string {
  const serialized = query.toString().replace(/\+/g, "%20");
  return serialized ? `${path}?${serialized}` : path;
}

function normalizeMetadata(
  metadata: unknown,
): Record<string, string | number | boolean | null> {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return {};
  }
  const publicMetadata: Record<string, string | number | boolean | null> = {};
  for (const [key, value] of Object.entries(metadata)) {
    if (isPrivateMemoryKey(key)) continue;
    if (
      value === null ||
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      publicMetadata[key] = value;
    }
  }
  return publicMetadata;
}

export function buildMemoryPolicyUrl(params: {
  workspace_id?: string;
  agent_id?: string | null;
} = {}): string {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "agent_id", params.agent_id);
  return withQuery("/api/ai/memory/policy", query);
}

export function buildMemoryRecordsUrl(params: MemoryRecordsParams = {}): string {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "agent_id", params.agent_id);
  appendParam(query, "session_id", params.session_id);
  appendParam(query, "status", params.status);
  appendParam(query, "limit", params.limit);
  return withQuery("/api/ai/memory/records", query);
}

export function buildAdminMemoryPoliciesUrl(
  params: AdminMemoryPoliciesParams = {},
): string {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "user_id", params.user_id);
  appendParam(query, "agent_id", params.agent_id);
  appendParam(query, "limit", params.limit);
  return withQuery("/api/ai/admin/memory/policies", query);
}

export function buildAdminMemoryRecordsUrl(
  params: Omit<MemoryRecordsParams, "agent_id" | "session_id"> = {},
): string {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "status", params.status);
  appendParam(query, "limit", params.limit);
  return withQuery("/api/ai/admin/memory/records", query);
}

export function buildCleanupExpiredMemoryUrl(params: {
  workspace_id?: string;
  limit?: number;
} = {}): string {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "limit", params.limit);
  return withQuery("/api/ai/admin/memory/retention/cleanup", query);
}

export function normalizeMemoryRecord(
  record: Record<string, unknown>,
): AiPlatformMemoryRecord {
  return {
    memory_record_id: String(record.memory_record_id ?? ""),
    tenant_id: String(record.tenant_id ?? ""),
    workspace_id: String(record.workspace_id ?? ""),
    user_id: String(record.user_id ?? ""),
    agent_id: record.agent_id ? String(record.agent_id) : null,
    session_id: record.session_id ? String(record.session_id) : null,
    record_type: String(record.record_type ?? "memory"),
    content:
      typeof record.content === "string" ? record.content : undefined,
    metadata: normalizeMetadata(record.metadata),
    status: String(record.status ?? "active"),
    expires_at:
      typeof record.expires_at === "string" ? record.expires_at : null,
    deleted_at:
      typeof record.deleted_at === "string" ? record.deleted_at : null,
    created_at:
      typeof record.created_at === "string" ? record.created_at : null,
    updated_at:
      typeof record.updated_at === "string" ? record.updated_at : null,
  };
}

export async function fetchMemoryPolicy(
  params: { workspace_id?: string; agent_id?: string | null } = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<MemoryPolicyResponse> {
  return client.request<MemoryPolicyResponse>(buildMemoryPolicyUrl(params), {
    method: "GET",
  });
}

export async function setMemoryPolicy(
  request: MemoryPolicyRequest,
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<MemoryPolicyResponse> {
  return client.request<MemoryPolicyResponse>("/api/ai/memory/policy", {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function fetchAdminMemoryPolicies(
  params: AdminMemoryPoliciesParams = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<AdminMemoryPoliciesResponse> {
  return client.request<AdminMemoryPoliciesResponse>(
    buildAdminMemoryPoliciesUrl(params),
    { method: "GET" },
  );
}

export async function fetchMemoryRecords(
  params: MemoryRecordsParams = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<MemoryRecordsResponse> {
  if (!params.session_id) {
    throw new Error("memory_session_id_required");
  }
  const response = await client.request<MemoryRecordsResponse>(
    buildMemoryRecordsUrl(params),
    { method: "GET" },
  );
  return {
    memory_records: (response.memory_records ?? []).map((record) =>
      normalizeMemoryRecord(record as unknown as Record<string, unknown>),
    ),
  };
}

export async function fetchAdminMemoryRecords(
  params: Omit<MemoryRecordsParams, "agent_id" | "session_id"> = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<MemoryRecordsResponse> {
  const response = await client.request<MemoryRecordsResponse>(
    buildAdminMemoryRecordsUrl(params),
    { method: "GET" },
  );
  return {
    memory_records: (response.memory_records ?? []).map((record) =>
      normalizeMemoryRecord(record as unknown as Record<string, unknown>),
    ),
  };
}

export async function deleteMemoryRecord(
  recordId: string,
  params: { workspace_id?: string; agent_id?: string; session_id?: string; reason?: string } = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<{ memory_record: AiPlatformMemoryRecord }> {
  const query = new URLSearchParams();
  appendParam(query, "workspace_id", params.workspace_id);
  appendParam(query, "agent_id", params.agent_id);
  appendParam(query, "session_id", params.session_id);
  appendParam(query, "reason", params.reason);
  const response = await client.request<{ memory_record: Record<string, unknown> }>(
    withQuery(`/api/ai/memory/records/${encodeURIComponent(recordId)}`, query),
    { method: "DELETE" },
  );
  return {
    memory_record: normalizeMemoryRecord(response.memory_record),
  };
}

export async function cleanupExpiredMemoryRecords(
  params: { workspace_id?: string; limit?: number } = {},
  client: AiPlatformApiClient = defaultAiPlatformClient,
): Promise<CleanupExpiredMemoryResponse> {
  const response = await client.request<CleanupExpiredMemoryResponse>(
    buildCleanupExpiredMemoryUrl(params),
    { method: "POST" },
  );
  return {
    deleted_count: response.deleted_count,
    memory_records: (response.memory_records ?? []).map((record) =>
      normalizeMemoryRecord(record as unknown as Record<string, unknown>),
    ),
  };
}
