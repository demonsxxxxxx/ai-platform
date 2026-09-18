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
  user_id: string | null;
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
  model_output?: string;
  queue_position?: number | null;
  queue_insight?: AdminQueueInsight | null;
}

export type AdminRunDiagnosticCoverage =
  | "full"
  | "partial"
  | "not_collected"
  | "legacy_record"
  | "unsupported_schema"
  | "transport_unavailable";

export interface AdminRunDiagnosticObservation {
  observation_id?: string | null;
  attempt_id?: string | null;
  lease_id?: string | null;
  request_id?: string | null;
  callback_id?: string | null;
  kind: "failure" | "handling";
  source?: string | null;
  stage?: string | null;
  error_code?: string | null;
  exception_type?: string | null;
  message?: string | null;
  stack?: string | null;
  received_at?: string | null;
}

export interface AdminRunDiagnosticLoss {
  field: string;
  reason: string;
  original_bytes?: number | null;
  retained_bytes?: number | null;
  original?: number | null;
  retained?: number | null;
  count?: number | null;
}

export interface AdminRunDiagnosticAttempt {
  attempt_id: string;
  ordinal: number;
  status: string;
  owner_kind: string;
  started_at?: string | null;
  finished_at?: string | null;
  terminal_reason?: string | null;
  error_code?: string | null;
}

export interface AdminRunDiagnosticRun {
  run_id: string;
  session_id: string | null;
  user_id: string | null;
  workspace_id: string;
  status: string;
  trace_id?: string | null;
  created_at?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: string | null;
}

export interface AdminRunDiagnosticToolEvidence {
  tool_name: string;
  invocation_id?: string | null;
  state?: string | null;
  last_stage?: string | null;
  capability_kind?: string | null;
  reason?: string | null;
}

export interface AdminRunDiagnosticProtocolField {
  present: boolean;
  type: string;
  bytes?: number | null;
  non_empty?: boolean | null;
  items?: number | null;
}

export interface AdminRunDiagnosticExecutorProtocol {
  reported: {
    task_status?: string | null;
    terminal_status?: string | null;
    run_id_matches?: boolean | null;
    fields: Record<string, AdminRunDiagnosticProtocolField>;
    additional_field_count: number;
  };
  validation: Array<{
    location: string;
    type: string;
    message: string;
  }>;
  validation_omitted_count: number;
  canonical: {
    status: string;
    error_code: string;
    message_non_empty: boolean;
    answer_receipt_present: boolean;
    structured_error_present: boolean;
  };
}

export interface AdminRunDiagnosticsResponse {
  schema_version: "ai-platform.run-diagnostics.v1";
  diagnostic_id?: string | null;
  revision: number;
  coverage: AdminRunDiagnosticCoverage;
  run: AdminRunDiagnosticRun;
  root?: AdminRunDiagnosticObservation | null;
  handling: AdminRunDiagnosticObservation[];
  losses: AdminRunDiagnosticLoss[];
  attempts: AdminRunDiagnosticAttempt[];
  details: {
    schema_version?: string | null;
    sdk: {
      result_subtype?: string | null;
      stop_reason?: string | null;
      terminal_reason?: string | null;
      exception_type?: string | null;
      exception_message?: string | null;
      exception_traceback?: string | null;
      errors?: unknown;
    };
    observations?: Array<{
      observation_id?: string | null;
      attempt_id?: string | null;
      lease_id?: string | null;
      request_id?: string | null;
      callback_id?: string | null;
      received_at?: string | null;
      source?: string | null;
      stage?: string | null;
      error_code?: string | null;
      sdk: {
        exception_chain?: Array<{
          type: string;
          message?: string | null;
          traceback?: string | null;
          relation?: "cause" | "context";
        }>;
        errors?: unknown;
      };
      tool_lifecycles: AdminRunDiagnosticToolEvidence[];
      tool_calls: AdminRunDiagnosticToolEvidence[];
      tool_policy_denials: AdminRunDiagnosticToolEvidence[];
      executor_protocol?: AdminRunDiagnosticExecutorProtocol | null;
      normalization_losses?: AdminRunDiagnosticLoss[];
    }>;
    tool_lifecycles: AdminRunDiagnosticToolEvidence[];
    tool_calls: AdminRunDiagnosticToolEvidence[];
    tool_policy_denials: AdminRunDiagnosticToolEvidence[];
    executor_protocol?: AdminRunDiagnosticExecutorProtocol | null;
  };
  versions: Record<string, string | null>;
  counts: {
    retained_observations: number;
    omitted_observations: number;
  };
}

export interface AdminRunEvent {
  event_id?: string;
  sequence?: number;
  type?: string;
  stage?: string | null;
  status?: string | null;
  severity?: string | null;
  message?: string | null;
  error_code?: string | null;
  payload?: Record<string, unknown>;
  created_at?: string | null;
}

export interface AdminRunStep {
  step_id?: string;
  title?: string | null;
  step_kind?: string | null;
  status?: string | null;
  payload?: Record<string, unknown>;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminRunArtifact {
  artifact_id: string;
  artifact_type: string;
  label: string;
  content_type: string;
  size_bytes: number;
  created_at?: string | null;
}

export interface AdminWorkerExecutionAction {
  ordinal: number;
  label: string;
  category: string;
  status: string;
  input_summary: string;
  result_summary: string;
  duration_ms: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminWorkerExecution {
  response: string;
  actions: AdminWorkerExecutionAction[];
  model: {
    turn_count?: number | null;
    duration_ms?: number | null;
    stop_category?: string | null;
  };
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
  worker_execution?: AdminWorkerExecution;
  events: AdminRunEvent[];
  steps: AdminRunStep[];
  artifacts?: AdminRunArtifact[];
  sandbox_leases: AdminSandboxLease[];
}

export interface AdminRunListOptions {
  limit?: number;
  userId?: string;
  status?: string;
}

export function readAdminRunDeepLinkScope(search?: string): {
  userId: string | null;
  runId: string | null;
} {
  const params = new URLSearchParams(
    search ?? (typeof window === "undefined" ? "" : window.location.search),
  );
  return {
    userId: params.get("user_id")?.trim() || null,
    runId: params.get("run_id")?.trim() || null,
  };
}

function normalizeListOptions(
  options: number | AdminRunListOptions,
): AdminRunListOptions {
  return typeof options === "number" ? { limit: options } : options;
}

export function buildAdminRunsUrl(
  options: number | AdminRunListOptions = {},
): string {
  const normalized = normalizeListOptions(options);
  const params = new URLSearchParams({ limit: String(normalized.limit ?? 50) });
  if (normalized.userId) params.set("user_id", normalized.userId);
  if (normalized.status) params.set("status", normalized.status);
  return `/api/ai/admin/runs?${params.toString()}`;
}

export async function fetchAdminRuns(
  options: number | AdminRunListOptions = {},
  client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunListResponse> {
  return client.request<AdminRunListResponse>(buildAdminRunsUrl(options), {
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

export async function fetchAdminRunDiagnostics(
  runId: string,
  client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunDiagnosticsResponse> {
  const response = await client.request<AdminRunDiagnosticsResponse | null>(
    `/api/ai/admin/runs/${encodeURIComponent(runId)}/diagnostics`,
    { method: "GET" },
  );
  if (!response || typeof response !== "object") {
    throw new Error("admin_run_diagnostics_response_invalid");
  }
  return response;
}

export const adminRunsApi = {
  list: fetchAdminRuns,
  detail: fetchAdminRunDetail,
  diagnostics: fetchAdminRunDiagnostics,
};
