import { authFetch } from "./fetch";

export type ToolPermissionDecision = "allow_once" | "allow_for_run" | "deny";

export interface ToolPermissionRequestView {
  permission_request_id: string;
  run_id: string;
  tool_id: string;
  tool_call_id: string;
  risk_level: string;
  write_capable: boolean;
  status: "pending" | "decided" | string;
  decision?: ToolPermissionDecision | null;
}

export interface ToolPermissionDecisionResponse {
  permission_request: ToolPermissionRequestView;
}

export interface ToolPermissionInboxResponse {
  permission_requests: ToolPermissionRequestView[];
  total: number;
  status: "pending" | "decided" | "all" | string;
  limit: number;
}

type ToolPermissionRequestFn = <T>(
  url: string,
  options?: RequestInit,
) => Promise<T>;

export interface DecideToolPermissionOptions {
  request?: ToolPermissionRequestFn;
}

export interface ListToolPermissionInboxOptions {
  limit?: number;
  request?: ToolPermissionRequestFn;
}

export async function decideToolPermission(
  runId: string,
  requestId: string,
  decision: ToolPermissionDecision,
  reason?: string,
  options: DecideToolPermissionOptions = {},
): Promise<ToolPermissionDecisionResponse> {
  const request = options.request || authFetch;
  const body: { decision: ToolPermissionDecision; reason?: string } = {
    decision,
  };
  if (reason) {
    body.reason = reason;
  }
  return request<ToolPermissionDecisionResponse>(
    `/api/ai/runs/${encodeURIComponent(runId)}/tool-permissions/${encodeURIComponent(
      requestId,
    )}/decision`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/** List tenant-wide permission requests for the governed administrator inbox. */
export async function listToolPermissionInbox(
  status: "pending" | "decided" | "all" = "pending",
  options: ListToolPermissionInboxOptions = {},
): Promise<ToolPermissionInboxResponse> {
  const request = options.request || authFetch;
  const params = new URLSearchParams({ status });
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  return request<ToolPermissionInboxResponse>(
    `/api/ai/tool-permissions/inbox?${params.toString()}`,
  );
}

/** Submit a decision through the tenant-scoped administrator inbox endpoint. */
export async function decideToolPermissionInbox(
  requestId: string,
  decision: ToolPermissionDecision,
  reason?: string,
  options: DecideToolPermissionOptions = {},
): Promise<ToolPermissionDecisionResponse> {
  const request = options.request || authFetch;
  const body: { decision: ToolPermissionDecision; reason?: string } = {
    decision,
  };
  if (reason) {
    body.reason = reason;
  }
  return request<ToolPermissionDecisionResponse>(
    `/api/ai/tool-permissions/inbox/${encodeURIComponent(requestId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}
