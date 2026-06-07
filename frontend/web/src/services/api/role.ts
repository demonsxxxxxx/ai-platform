/**
 * Role API - 角色管理
 */

import type {
  Role,
  RoleCreate,
  RoleListResponse,
  RoleUpdate,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface RoleListParams {
  skip?: number;
  limit?: number;
  q?: string;
}

export function buildRoleListUrl(params: RoleListParams = {}): string {
  const searchParams = new URLSearchParams();
  if (params.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params.limit !== undefined)
    searchParams.set("limit", String(params.limit));
  if (params.q) searchParams.set("q", params.q);
  const query = searchParams.toString();
  return `${API_BASE}/api/roles/${query ? `?${query}` : ""}`;
}

export const roleApi = {
  /**
   * 列出角色
   */
  async list(params: RoleListParams = {}): Promise<RoleListResponse> {
    return authFetch<RoleListResponse>(buildRoleListUrl(params));
  },

  /**
   * 获取单个角色
   */
  async get(roleId: string): Promise<Role> {
    return authFetch<Role>(`${API_BASE}/api/roles/${roleId}`);
  },

  /**
   * 创建角色
   */
  async create(roleData: RoleCreate): Promise<Role> {
    return authFetch<Role>(`${API_BASE}/api/roles/`, {
      method: "POST",
      body: JSON.stringify(roleData),
    });
  },

  /**
   * 更新角色
   */
  async update(roleId: string, roleData: RoleUpdate): Promise<Role> {
    return authFetch<Role>(`${API_BASE}/api/roles/${roleId}`, {
      method: "PUT",
      body: JSON.stringify(roleData),
    });
  },

  /**
   * 删除角色
   */
  async delete(roleId: string): Promise<{ status: string }> {
    return authFetch<{ status: string }>(`${API_BASE}/api/roles/${roleId}`, {
      method: "DELETE",
    });
  },
};
