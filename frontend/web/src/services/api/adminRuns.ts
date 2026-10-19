import {
  apiRequestErrorFromResponse,
  authFetch,
} from "./fetch";
import { authenticatedRequest } from "./authenticatedRequest";

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
  session_title?: string | null;
  task_summary?: string | null;
  user_id: string | null;
  user_display_name?: string | null;
  workspace_id?: string | null;
  workspace_name?: string | null;
  trace_id?: string | null;
  status: string;
  execution_kind?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  skill_id?: string | null;
  skill_name?: string | null;
  created_at?: string | null;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  cancel_requested_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  latency_ms?: number | null;
  latency?: AdminRunLatency;
  model_id?: string | null;
  model_gateway_revision?: number | null;
  input_token_count?: number | null;
  output_token_count?: number | null;
  total_token_count?: number | null;
  estimated_cost_minor?: number | null;
  model_value?: string | null;
  copied_from_run_id?: string | null;
  trace_id_recorded?: boolean;
  model_output?: string;
  input?: Record<string, unknown>;
  result?: Record<string, unknown>;
  contract_version?: string;
  executor_schema_version?: string;
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
  is_interrupt?: boolean;
  operation_id?: string | null;
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

export interface AdminRunDiagnosticProjectionFailure {
  reason: string;
  stage: string;
  location: string;
  frame_shape?: {
    event_type: string;
    block_type: string;
    delta_type: string;
    message_state: string;
    open_block_type: string;
    index_state: string;
    guard: string;
  };
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
      debug?: Record<string, string | number>;
      api_error_status?: number;
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
        debug?: Record<string, string | number>;
        api_error_status?: number;
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
      projection_failure?: AdminRunDiagnosticProjectionFailure | null;
      normalization_losses?: AdminRunDiagnosticLoss[];
    }>;
    tool_lifecycles: AdminRunDiagnosticToolEvidence[];
    tool_calls: AdminRunDiagnosticToolEvidence[];
    tool_policy_denials: AdminRunDiagnosticToolEvidence[];
    executor_protocol?: AdminRunDiagnosticExecutorProtocol | null;
    projection_failure?: AdminRunDiagnosticProjectionFailure | null;
  };
  versions: Record<string, string | null>;
  counts: {
    retained_observations: number;
    omitted_observations: number;
  };
}

export interface AdminRunEvent {
  event_id?: string;
  schema_version?: string;
  sequence?: number;
  trace_id?: string | null;
  type?: string;
  stage?: string | null;
  status?: string | null;
  severity?: string | null;
  visible_to_user?: boolean;
  message?: string | null;
  error_code?: string | null;
  latency_ms?: number | null;
  token_counts?: {
    input: number;
    output: number;
    total: number;
  };
  cost?: {
    estimated_cost_minor: number;
  };
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
  trace_id?: string | null;
  artifact_type: string;
  label: string;
  content_type: string;
  size_bytes: number;
  manifest?: Record<string, unknown>;
  created_at?: string | null;
}

export interface AdminWorkerExecutionAction {
  ordinal: number;
  sequence?: number;
  invocation_id?: string | null;
  label: string;
  category: string;
  status: string;
  input_summary: string;
  result_summary: string;
  duration_ms: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AdminWorkerExecutionMessage {
  ordinal: number;
  kind: "answer" | "commentary";
  text: string;
  sequence: number;
  created_at?: string | null;
}

export interface AdminWorkerExecution {
  response: string;
  messages?: AdminWorkerExecutionMessage[];
  answer_projection?: {
    status: "available" | "incomplete" | "invalid" | "unknown";
    incomplete_messages: number;
    invalid_messages: number;
  };
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
  workspace?: Record<string, unknown>;
  lease_payload?: Record<string, unknown>;
}

export interface AdminRunListResponse {
  runs: AdminRunSummary[];
  limit: number;
}

export interface AdminRunLatency {
  total_ms: number | null;
  queue_ms: number | null;
  execution_ms: number | null;
  server_first_text_ms: number | null;
  browser_first_text_ms: number | null;
}

export interface AdminRunMetricSummary {
  sample_count: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

export interface AdminRunMonitorResponse {
  measurement: "run_lifecycle.v1";
  window: { from_at: string; to_at: string };
  counts: { total: number; succeeded: number; failed: number; cancelled: number; queued: number; running: number };
  success_rate: number | null;
  latency: Record<keyof AdminRunLatency, AdminRunMetricSummary>;
  trend?: Array<{ from_at: string; succeeded: number; failed: number; running: number }>;
  models: Array<{ model_id: string; model_value: string; model_gateway_revision: number | null }>;
  runs: AdminRunSummary[];
  limit: number;
  has_more: boolean;
  order: "slow" | "recent";
}

export interface AdminRunInvestigationStage {
  stage_id: string;
  label: string;
  source: "run_lifecycle" | "platform_ledger" | "run_attempt" | "sdk_reported" | "sdk_tool_hooks";
  status: string;
  attempt_id: string | null;
  operation_id: string | null;
  event_id: string | null;
  causation_event_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  offset_ms: number | null;
  receipt_duration_ms: number | null;
  duration_ms: number | null;
}

export interface AdminRunInvestigation extends AdminRunLatency {
  run_id: string;
  elapsed_ms: number | null;
  first_public_text_at: string | null;
  observed_at: string;
  browser_measurement: "browser_dom_commit.v1" | null;
  timeline_source: "platform_receipts";
  durations_additive: false;
  events_truncated: boolean;
  unbound_event_count: number;
  missing_measurements: Array<keyof AdminRunLatency>;
  stages: AdminRunInvestigationStage[];
}

export interface AdminRunMonitorOptions extends AdminRunListOptions {
  hours?: number;
  fromAt?: string;
  toAt?: string;
  agentId?: string;
  modelId?: string;
  modelGatewayRevision?: number;
  order?: "slow" | "recent";
}

export interface AdminRunRuntimeHealth {
  state: "queued" | "terminal" | "healthy" | "stale" | "unknown" | "awaiting_reconciliation";
  observed_at: string;
  attempt_id: string | null;
  async_dispatch_accepted: boolean;
  queue_last_heartbeat_at: string | null;
  queue_lease_expires_at: string | null;
  heartbeat_source: "executor" | "sandbox" | null;
  heartbeat_at: string | null;
}

export interface AdminRunDetailResponse {
  run: AdminRunSummary;
  runtime_health?: AdminRunRuntimeHealth;
  worker_execution: AdminWorkerExecution;
  events: AdminRunEvent[];
  steps: AdminRunStep[];
  artifacts: AdminRunArtifact[];
  sandbox_leases: AdminSandboxLease[];
  skill_snapshots: Array<Record<string, unknown>>;
  audit: Array<Record<string, unknown>>;
}

export interface AdminRunTrajectoryEvent {
  event_id: string;
  sequence: number;
  kind: "message" | "action" | "observation" | "error";
  source_type: string;
  recorded_at: string | null;
  attempt_id: string | null;
  message_id: string | null;
  causation_event_id: string | null;
  operation_id: string | null;
  summary?: string;
  text_length?: number;
  category?: string;
  stage?: string;
  duration_ms?: number;
  outcome?: string;
}

export interface AdminRunTrajectoryPage {
  contract_version: "ai-platform.admin-run-trajectory.v1";
  run_id: string;
  after_sequence: number;
  next_after_sequence: number;
  has_more: boolean;
  source_count: number;
  omitted: { private: number; unsupported: number; invalid: number };
  events: AdminRunTrajectoryEvent[];
}

export interface AdminRunDiagnosticExport {
  blob: Blob;
  filename: string;
  exportId: string | null;
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

function monitorQuery(options: AdminRunMonitorOptions): URLSearchParams {
  const params = new URLSearchParams({ hours: String(options.hours ?? 24) });
  if (options.fromAt) params.set("from_at", options.fromAt);
  if (options.toAt) params.set("to_at", options.toAt);
  if (options.userId) params.set("user_id", options.userId);
  if (options.agentId) params.set("agent_id", options.agentId);
  if (options.modelId) params.set("model_id", options.modelId);
  if (options.modelGatewayRevision != null) params.set("model_gateway_revision", String(options.modelGatewayRevision));
  if (options.status) params.set("status", options.status);
  if (options.order) params.set("order", options.order);
  if (options.limit != null) params.set("limit", String(options.limit));
  return params;
}

export async function fetchAdminRunMonitor(
  options: AdminRunMonitorOptions = {}, client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunMonitorResponse> {
  const response = await client.request<AdminRunMonitorResponse>(`/api/ai/admin/runs/monitor?${monitorQuery(options)}`, { method: "GET" });
  if (!response || response.measurement !== "run_lifecycle.v1" || !Array.isArray(response.runs) || !Array.isArray(response.models) || !response.window || !response.counts || !response.latency) {
    throw new Error("admin_run_monitor_response_invalid");
  }
  return response;
}

export async function fetchAdminRunInvestigation(
  runId: string, client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunInvestigation> {
  const response = await client.request<AdminRunInvestigation>(
    `/api/ai/admin/runs/${encodeURIComponent(runId)}/investigation`, { method: "GET" },
  );
  if (!response || response.run_id !== runId || response.timeline_source !== "platform_receipts" || !Array.isArray(response.stages)) {
    throw new Error("admin_run_investigation_response_invalid");
  }
  return response;
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

export async function fetchAdminRunTrajectory(
  runId: string,
  afterSequence = 0,
  client: AdminRunsApiClient = defaultClient,
): Promise<AdminRunTrajectoryPage> {
  const params = new URLSearchParams({
    after_sequence: String(afterSequence),
    limit: "100",
  });
  const response = await client.request<AdminRunTrajectoryPage>(
    `/api/ai/admin/runs/${encodeURIComponent(runId)}/trajectory?${params}`,
    { method: "GET" },
  );
  if (
    !response ||
    response.contract_version !== "ai-platform.admin-run-trajectory.v1" ||
    response.run_id !== runId ||
    !Array.isArray(response.events) ||
    !Number.isSafeInteger(response.next_after_sequence) ||
    response.next_after_sequence < afterSequence
  ) {
    throw new Error("admin_run_trajectory_response_invalid");
  }
  return response;
}

function diagnosticExportFilename(response: Response, runId: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      const decoded = decodeURIComponent(encoded);
      if (/^[a-zA-Z0-9._-]+\.zip$/.test(decoded)) return decoded;
    } catch {
      // Use the safe fallback below.
    }
  }
  const safeRunId = runId.replace(/[^a-zA-Z0-9._-]/g, "_");
  return `run-diagnostics-${safeRunId}.zip`;
}

export async function exportAdminRunDiagnostics(
  runId: string,
): Promise<AdminRunDiagnosticExport> {
  const response = await authenticatedRequest(
    `/api/ai/admin/runs/${encodeURIComponent(runId)}/diagnostic-exports`,
    { method: "POST" },
  );
  if (!response.ok) throw await apiRequestErrorFromResponse(response);
  return {
    blob: await response.blob(),
    filename: diagnosticExportFilename(response, runId),
    exportId: response.headers.get("X-Diagnostic-Export-Id"),
  };
}

export const adminRunsApi = {
  list: fetchAdminRuns,
  monitor: fetchAdminRunMonitor,
  investigation: fetchAdminRunInvestigation,
  detail: fetchAdminRunDetail,
  diagnostics: fetchAdminRunDiagnostics,
  trajectory: fetchAdminRunTrajectory,
  exportDiagnostics: exportAdminRunDiagnostics,
};
