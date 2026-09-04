import { authFetch } from "./fetch";

export interface AdminRunsApiClient {
  request<T>(url: string, init?: RequestInit): Promise<T>;
}

const defaultClient: AdminRunsApiClient = {
  request: authFetch,
};

export interface AdminQueueInsight {
  reason?: string;
  depths?: {
    tenant_queued?: number;
    tenant_processing?: number;
  };
  workers?: {
    active?: number;
  };
  capacity?: {
    available_worker_slots?: number | null;
    max_active_worker_runs?: number;
  };
  processing_state?: {
    active?: number;
    stale?: number;
    reclaimable?: number;
  };
}

export interface AdminRunSummary {
  run_id: string;
  session_id: string | null;
  user_id: string;
  workspace_id?: string | null;
  trace_id?: string | null;
  status: string;
  execution_kind?: string | null;
  agent_id?: string | null;
  skill_id?: string | null;
  created_at?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  cancel_requested_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  queue_position?: number | null;
  queue_insight?: AdminQueueInsight | null;
  result?: {
    runtime_diagnostics?: Record<string, unknown> | null;
  } | null;
}

export interface AdminRunEvent {
  event_id?: string;
  type?: string;
  stage?: string | null;
  status?: string | null;
  message?: string | null;
  created_at?: string | null;
}

export interface AdminRunStep {
  step_id?: string;
  title?: string | null;
  step_kind?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminSandboxLease {
  lease_id?: string;
  id?: string;
  status?: string | null;
  provider?: string | null;
  sandbox_mode?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  released_at?: string | null;
}

export interface AdminRunListResponse {
  runs: AdminRunSummary[];
  limit: number;
}

export interface AdminRunDetailResponse {
  run: AdminRunSummary;
  events: AdminRunEvent[];
  steps: AdminRunStep[];
  sandbox_leases: AdminSandboxLease[];
}

export function buildAdminRunsUrl(limit = 50): string {
  const params = new URLSearchParams({ limit: String(limit) });
  return `/api/ai/admin/runs?${params.toString()}`;
}

export async function fetchAdminRuns(
  limit = 50,
  client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunListResponse> {
  return client.request<AdminRunListResponse>(buildAdminRunsUrl(limit), {
    method: "GET",
  });
}

export async function fetchAdminRunDetail(
  runId: string,
  client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunDetailResponse> {
  return client.request<AdminRunDetailResponse>(
    `/api/ai/admin/runs/${encodeURIComponent(runId)}`,
    { method: "GET" },
  );
}

export const adminRunsApi = {
  list: fetchAdminRuns,
  detail: fetchAdminRunDetail,
};
