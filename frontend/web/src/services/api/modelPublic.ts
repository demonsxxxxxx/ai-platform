/**
 * Public model projection API.
 *
 * This module is safe for ordinary browser entry points. It only exposes
 * model options, provider names, and the administrator-selected default.
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface ModelProfile {
  auto_compact_window?: number;
  max_output_tokens?: number;
}

export interface ModelOption {
  id: string;
  value: string;
  provider?: string;
  label: string;
  description?: string;
  profile?: ModelProfile;
}

export interface AvailableModelListResponse {
  models: ModelOption[];
  count: number;
  enabled_count: number;
  default_model_id?: string | null;
}

interface ModelPublicRequestOptions {
  signal?: AbortSignal;
}

export const modelPublicApi = {
  async listAvailable(
    options: ModelPublicRequestOptions & { groupId?: string; expertAgentId?: string } = {},
  ): Promise<AvailableModelListResponse> {
    const query = options.groupId ? `?group_id=${encodeURIComponent(options.groupId)}` : "";
    return authFetch<AvailableModelListResponse>(
      `${API_BASE}${options.expertAgentId ? `/api/ai/agent-profiles/${encodeURIComponent(options.expertAgentId)}/models` : "/api/agent/models/available"}${query}`,
      { signal: options.signal, cache: "no-store" },
    );
  },
};
