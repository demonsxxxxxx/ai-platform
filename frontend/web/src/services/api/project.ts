/**
 * Project API - 项目管理
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type { Project } from "../../types";

export const projectApi = {
  /**
   * List all projects for current user
   */
  async list(): Promise<Project[]> {
    return authFetch<Project[]>(`${API_BASE}/api/projects`);
  },
};
