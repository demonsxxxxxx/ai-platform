import { authFetch } from "./fetch";

/** Redacted, read-only compatibility shape for pre-#475 audit evidence. */
export interface ToolPermissionHistoryResponse {
  permission_requests: ToolPermissionHistoryView[];
  total: number;
  status: "pending" | "decided" | "all" | string;
  limit: number;
}

export interface ToolPermissionHistoryView {
  request_id: string;
  run_id: string;
  tool_id: string;
  tool_display: string;
  risk_level: string;
  write_capable: boolean;
  status: string;
  expires_at?: string | null;
  allowed_decisions: [];
}

type ToolPermissionRequestFn = <T>(url: string, options?: RequestInit) => Promise<T>;

export interface ListToolPermissionHistoryOptions {
  limit?: number;
  request?: ToolPermissionRequestFn;
  signal?: AbortSignal;
}

/** Read historical records only. Runtime model-tool decisions have no client API. */
export async function listToolPermissionHistory(
  status: "pending" | "decided" | "all" = "all",
  options: ListToolPermissionHistoryOptions = {},
): Promise<ToolPermissionHistoryResponse> {
  const request = options.request || authFetch;
  const params = new URLSearchParams({ status });
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  return request<ToolPermissionHistoryResponse>(`/api/ai/tool-permissions/inbox?${params.toString()}`, {
    signal: options.signal,
  });
}
