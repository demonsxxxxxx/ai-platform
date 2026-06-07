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

type ToolPermissionRequestFn = <T>(
  url: string,
  options?: RequestInit,
) => Promise<T>;

export interface DecideToolPermissionOptions {
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
