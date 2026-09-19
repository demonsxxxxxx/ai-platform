import { authFetch } from "./fetch";

export interface AdminUsersApiClient {
  request<T>(url: string, init?: RequestInit): Promise<T>;
}

const defaultClient: AdminUsersApiClient = {
  request: authFetch,
};

export interface AdminUserSummary {
  user_id: string;
  display_name: string;
  status: string;
  created_at?: string | null;
  session_count: number;
  run_count: number;
  queued_run_count: number;
  running_run_count: number;
  succeeded_run_count: number;
  failed_run_count: number;
  cancelled_run_count: number;
  last_activity_at?: string | null;
}

export interface AdminUserSession {
  session_id: string;
  workspace_id: string;
  agent_id: string;
  status: string;
  purpose: string;
  run_count: number;
  failed_run_count: number;
  last_run_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AdminUserRun {
  run_id: string;
  session_id: string;
  workspace_id: string;
  status: string;
  agent_id: string;
  execution_kind: string;
  skill_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminUserAuditEntry {
  audit_id: string;
  actor_user_id?: string | null;
  action: string;
  target_type: string;
  target_id: string;
  relations: Array<"actor" | "subject" | "run" | "session">;
  run_id?: string | null;
  session_id?: string | null;
  trace_id?: string | null;
  created_at?: string | null;
}

export interface AdminUserListResponse {
  schema_version: "ai-platform.admin-user-diagnostics.v1";
  users: AdminUserSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface AdminUserDiagnosticsResponse {
  schema_version: "ai-platform.admin-user-diagnostics.v1";
  user: AdminUserSummary;
  sessions: AdminUserSession[];
  runs: AdminUserRun[];
  audit: AdminUserAuditEntry[];
  limits: {
    sessions: number;
    runs: number;
    audit: number;
  };
}

export interface AdminUserListOptions {
  search?: string;
  offset?: number;
  limit?: number;
}

export function buildAdminUsersUrl(options: AdminUserListOptions = {}): string {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 50),
  });
  const search = options.search?.trim();
  if (search) params.set("search", search);
  return `/api/ai/admin/users?${params.toString()}`;
}

export async function fetchAdminUsers(
  options: AdminUserListOptions = {},
  client: AdminUsersApiClient = defaultClient,
): Promise<AdminUserListResponse> {
  return client.request<AdminUserListResponse>(buildAdminUsersUrl(options), {
    method: "GET",
  });
}

export async function fetchAdminUserDiagnostics(
  userId: string,
  client: AdminUsersApiClient = defaultClient,
): Promise<AdminUserDiagnosticsResponse> {
  return client.request<AdminUserDiagnosticsResponse>(
    `/api/ai/admin/users/${encodeURIComponent(userId)}/diagnostics`,
    { method: "GET" },
  );
}

export const adminUsersApi = {
  list: fetchAdminUsers,
  diagnostics: fetchAdminUserDiagnostics,
};
