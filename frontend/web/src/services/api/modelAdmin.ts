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
  access_mode?: "all" | "selected";
  allowed_user_ids?: string[];
  last_seen_revision: number;
  last_seen_at: string;
  auto_compact_window?: number;
  max_output_tokens?: number;
}

export interface AdminModelState {
  connection: AdminModelConnection;
  models: AdminModelEntry[];
}

interface ModelAdminRequestOptions {
  signal?: AbortSignal;
}

export const modelAdminApi = {
  get(options: ModelAdminRequestOptions = {}): Promise<AdminModelState> {
    return authFetch<AdminModelState>(`${API_BASE}/api/ai/admin/models`, {
      signal: options.signal,
      cache: "no-store",
    });
  },

  discover(baseUrl: string, credential?: string, options: ModelAdminRequestOptions = {}): Promise<{
    connection: AdminModelConnection;
    base_url: string;
    models: AdminModelEntry[];
  }> {
    return authFetch(`${API_BASE}/api/ai/admin/models/discover`, {
      method: "POST",
      signal: options.signal,
      body: JSON.stringify({
        base_url: baseUrl,
        ...(credential ? { credential } : {}),
      }),
    });
  },

  publish(
    baseUrl: string,
    credential: string | undefined,
    expectedRevision: number | null,
    models: Array<AdminModelEntry & { display_name?: string }>,
    options: ModelAdminRequestOptions = {},
  ): Promise<AdminModelState> {
    return authFetch(`${API_BASE}/api/ai/admin/models/publish`, {
      method: "POST",
      signal: options.signal,
      body: JSON.stringify({
        base_url: baseUrl,
        ...(credential ? { credential } : {}),
        expected_revision: expectedRevision,
        models: models.map((model) => ({
          id: model.id,
          value: model.value,
          display_name: model.display_name ?? model.label,
          enabled: model.enabled,
          is_default: model.is_default,
          order: model.order,
          access_mode: model.access_mode ?? "all",
          allowed_user_ids:
            model.access_mode === "selected" ? model.allowed_user_ids ?? [] : [],
          ...(model.auto_compact_window ? { auto_compact_window: model.auto_compact_window } : {}),
          ...(model.max_output_tokens ? { max_output_tokens: model.max_output_tokens } : {}),
        })),
      }),
    });
  },
};
