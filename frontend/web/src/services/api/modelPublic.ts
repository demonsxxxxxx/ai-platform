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

const PINNED_MODEL_IDS_STORAGE_KEY = "pinnedModelIds";

function readPinnedModelIds(): string[] {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(PINNED_MODEL_IDS_STORAGE_KEY) || "[]",
    );
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function writePinnedModelIds(ids: string[]): string[] {
  localStorage.setItem(PINNED_MODEL_IDS_STORAGE_KEY, JSON.stringify(ids));
  return ids;
}

export const modelPublicApi = {
  async listAvailable(): Promise<AvailableModelListResponse> {
    return authFetch<AvailableModelListResponse>(
      `${API_BASE}/api/agent/models/available`,
    );
  },

  async listProviders(): Promise<
    { value: string; protocol: string; prefixes: string[] }[]
  > {
    return authFetch(`${API_BASE}/api/agent/models/providers/list`);
  },

  async getPinnedModelIds(): Promise<string[]> {
    return readPinnedModelIds();
  },

  async updatePinnedModelIds(ids: string[]): Promise<string[]> {
    return writePinnedModelIds(ids);
  },
};
