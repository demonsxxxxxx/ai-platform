import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface WorkbenchGovernance {
  projection: string;
  tenant_id: string;
  workspace_id: string;
  degraded: boolean;
  audit_required: boolean;
  rollback_available: boolean;
  secret_material_projected: boolean;
}

export interface WorkbenchUserProjection {
  id: string;
  username: string;
  email: string | null;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
  tenant_id: string;
  department_id: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkbenchUserListResponse {
  users: WorkbenchUserProjection[];
  items: WorkbenchUserProjection[];
  total: number;
  skip: number;
  limit: number;
  governance: WorkbenchGovernance;
}

const WORKBENCH_USERS_API = `${API_BASE}/api/users/`;

function withQuery(url: string, params: Record<string, string | number | undefined>) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") searchParams.set(key, String(value));
  }
  const query = searchParams.toString();
  return query ? `${url}?${query}` : url;
}

export const workbenchApi = {
  listUsers(params: { skip?: number; limit?: number; search?: string } = {}) {
    return authFetch<WorkbenchUserListResponse>(
      withQuery(WORKBENCH_USERS_API, params),
    );
  },
};
