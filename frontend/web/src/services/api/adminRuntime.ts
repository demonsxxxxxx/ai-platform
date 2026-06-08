import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export type RuntimeLimitGroup = Record<
  string,
  string | number | boolean | null | undefined
>;

export interface AdminRuntimeCapacityProjection {
  schema_version?: string;
  profile?: string;
  limits?: Record<string, RuntimeLimitGroup | undefined>;
  live_signal_route?: string;
  load_test_gates?: string[];
  production_default_policy?: string;
  warnings?: string[];
}

export interface AdminRuntimeBackpressureProjection {
  reasons?: string[];
  queue?: {
    reason?: string | null;
    worker_capacity?: RuntimeLimitGroup;
    quota?: RuntimeLimitGroup;
    sample?: RuntimeLimitGroup;
  };
  database_pool?: RuntimeLimitGroup;
  model_gateway?: RuntimeLimitGroup;
}

export interface AdminRuntimeGovernanceDomain {
  status?: string;
  implemented?: string[];
  gaps?: string[];
  next_checks?: string[];
}

export interface AdminRuntimeGovernanceProjection {
  schema_version?: string;
  gate?: string;
  status?: string;
  ordinary_user_policy?: string;
  domains?: Record<string, AdminRuntimeGovernanceDomain | undefined>;
  open_gaps?: string[];
  evidence_policy?: string;
}

export interface AdminRuntimeOverview {
  tenant_id?: string;
  capacity?: AdminRuntimeCapacityProjection;
  backpressure?: AdminRuntimeBackpressureProjection;
  governance?: AdminRuntimeGovernanceProjection;
  database_pool?: {
    configured?: RuntimeLimitGroup;
    open?: boolean;
    stats?: RuntimeLimitGroup;
  };
  admission?: RuntimeLimitGroup & {
    top_users?: Array<RuntimeLimitGroup>;
  };
}

type AdminRuntimeRequestFn = <T>(
  url: string,
  options?: RequestInit,
) => Promise<T>;

export interface GetAdminRuntimeOverviewOptions {
  request?: AdminRuntimeRequestFn;
}

export async function getAdminRuntimeOverview(
  options: GetAdminRuntimeOverviewOptions = {},
): Promise<AdminRuntimeOverview> {
  const request = options.request || authFetch;
  return request<AdminRuntimeOverview>(
    `${API_BASE}/api/ai/admin/runtime/overview`,
  );
}

export const adminRuntimeApi = {
  getOverview: getAdminRuntimeOverview,
};
