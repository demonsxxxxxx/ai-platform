/**
 * Public model projection API.
 *
 * This module is safe for ordinary browser entry points. It only exposes
 * model options, provider names, and per-user pinned model preferences.
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface ModelProfile {
  max_input_tokens?: number;
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
    options: ModelPublicRequestOptions = {},
  ): Promise<AvailableModelListResponse> {
    return authFetch<AvailableModelListResponse>(
      `${API_BASE}/api/agent/models/available`,
      { signal: options.signal },
    );
  },

  async getPinnedModelIds(
    options: ModelPublicRequestOptions = {},
  ): Promise<string[]> {
    const user = await authFetch<{
      metadata?: { pinned_model_ids?: string[] };
    }>(`${API_BASE}/api/auth/profile`, { signal: options.signal });
    return user.metadata?.pinned_model_ids ?? [];
  },

  async updatePinnedModelIds(
    ids: string[],
    options: ModelPublicRequestOptions = {},
  ): Promise<string[]> {
    const user = await authFetch<{
      metadata?: { pinned_model_ids?: string[] };
    }>(`${API_BASE}/api/auth/profile/metadata`, {
      method: "PUT",
      body: JSON.stringify({ metadata: { pinned_model_ids: ids } }),
      signal: options.signal,
    });
    return user.metadata?.pinned_model_ids ?? [];
  },
};
