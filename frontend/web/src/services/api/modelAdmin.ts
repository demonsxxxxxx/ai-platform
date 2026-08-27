import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface AdminModelConnection {
  configured: boolean;
  revision: number | null;
  base_url: string;
  key_fingerprint: string;
  updated_at?: string;
}

export interface AdminModelEntry {
  id: string;
  value: string;
  label: string;
  provider: string;
  enabled: boolean;
  available: boolean;
  is_default: boolean;
  order: number;
  last_seen_revision: number;
  last_seen_at: string;
}

export interface AdminModelState {
  connection: AdminModelConnection;
  models: AdminModelEntry[];
}

export const modelAdminApi = {
  get(): Promise<AdminModelState> {
    return authFetch<AdminModelState>(`${API_BASE}/api/ai/admin/models`);
  },

  configure(baseUrl: string, credential?: string): Promise<AdminModelState> {
    return authFetch<AdminModelState>(`${API_BASE}/api/ai/admin/models/connection`, {
      method: "PUT",
      body: JSON.stringify({
        base_url: baseUrl,
        ...(credential ? { credential } : {}),
      }),
    });
  },

  sync(): Promise<AdminModelState> {
    return authFetch<AdminModelState>(`${API_BASE}/api/ai/admin/models/sync`, {
      method: "POST",
    });
  },

  patch(
    modelId: string,
    patch: { display_name?: string; enabled?: boolean; is_default?: boolean },
  ): Promise<AdminModelEntry> {
    return authFetch<AdminModelEntry>(
      `${API_BASE}/api/ai/admin/models/${encodeURIComponent(modelId)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  },
};
