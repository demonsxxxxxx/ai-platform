import { authFetch } from "./fetch";

export interface UsageCounts {
  input_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  output_tokens: number;
  input_total: number;
  total_tokens: number;
}
export interface UsageUser extends UsageCounts {
  user_id: string | null;
  work_id: string | null;
  display_name: string | null;
}
export interface UsageModel extends UsageCounts { model: string }
export interface UsageMeta {
  start_date: string;
  end_date: string;
  timezone: "Asia/Shanghai";
  collection_started_at: string | null;
  coverage: {
    status: "reported_final" | "partial" | "unavailable";
    reason: "not_collected" | "report_missing" | "baseline_unknown" | "partial_report" | null;
    scopes: number;
    partial_scopes: number;
  };
}
export interface UsageReport<T> {
  data: T[];
  total: number;
  limit: number | null;
  offset: number;
  meta: UsageMeta;
  /** Whole-window aggregate; absent on older servers and null when no records exist. */
  summary?: UsageCounts | null;
}
export interface UsageDates { start_date: string; end_date: string }
export interface UsageUsersQuery extends UsageDates { q: string; limit: number; offset: number }

export const modelUsageApi = {
  users(query: UsageUsersQuery, signal: AbortSignal): Promise<UsageReport<UsageUser>> {
    const params = new URLSearchParams({ ...query, limit: String(query.limit), offset: String(query.offset) });
    return authFetch(`/api/ai/admin/usage/users?${params}`, { method: "GET", signal });
  },
  models(userId: string, dates: UsageDates, signal: AbortSignal): Promise<UsageReport<UsageModel>> {
    const params = new URLSearchParams({ ...dates });
    return authFetch(`/api/ai/admin/usage/users/${encodeURIComponent(userId)}/models?${params}`, { method: "GET", signal });
  },
};
