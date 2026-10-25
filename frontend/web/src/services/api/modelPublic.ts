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
    options: ModelPublicRequestOptions = {},
  ): Promise<AvailableModelListResponse> {
    return authFetch<AvailableModelListResponse>(
      `${API_BASE}/api/agent/models/available`,
      { signal: options.signal, cache: "no-store" },
    );
  },
};
