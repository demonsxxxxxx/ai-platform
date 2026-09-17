/**
 * Role API - 角色管理
 */

import type { RoleListResponse } from "../../types";
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
};
