import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface AdminModelConnection {
  configured: boolean;
  revision: number | null;
  base_url: string;
  key_fingerprint: string;
  updated_at?: string;
}

export interface AdminModelProvider {
  id: string;
  name: string;
  revision: number | null;
  model_count: number;
}

export interface ModelProviderImpact extends AdminModelProvider {
  group_count: number;
  default_group_count: number;
  active_run_count: number;
  cleanable_count: number;
  blocked_models: Array<{
    id: string; label: string; group_count: number; default_group_count: number;
    active_run_count: number; groups: Array<{ name: string }>;
  }>;
}

export interface AdminModelEntry {
  id: string;
  value: string;
  label: string;
  provider: string;
  provider_id?: string;
  provider_name?: string;
  enabled: boolean;
  available: boolean;
  order: number;
  last_seen_revision: number;
  last_seen_at: string;
  auto_compact_window?: number;
  max_output_tokens?: number;
}

export interface AdminModelState {
  connection: AdminModelConnection;
  models: AdminModelEntry[];
  providers?: AdminModelProvider[];
}

interface ModelAdminRequestOptions {
  signal?: AbortSignal;
  providerId?: string;
  missingOnly?: boolean;
}

export const modelAdminApi = {
  createProvider(name: string, options: ModelAdminRequestOptions = {}): Promise<{ id: string; name: string }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-providers`, { method: "POST", signal: options.signal, body: JSON.stringify({ name }) });
  },

  providerImpact(id: string, options: ModelAdminRequestOptions = {}): Promise<ModelProviderImpact> {
    return authFetch(`${API_BASE}/api/ai/admin/model-providers/${encodeURIComponent(id)}/impact${options.missingOnly ? "?missing_only=true" : ""}`, { signal: options.signal, cache: "no-store" });
  },

  retireProvider(id: string, expectedRevision: number | null, options: ModelAdminRequestOptions = {}): Promise<{ id: string; deleted: boolean; model_count?: number; retained_count?: number }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-providers/${encodeURIComponent(id)}/retire`, { method: "POST", signal: options.signal, body: JSON.stringify({ expected_revision: expectedRevision, confirm: true, ...(options.missingOnly ? { missing_only: true } : {}) }) });
  },

  get(options: ModelAdminRequestOptions = {}): Promise<AdminModelState> {
    return authFetch<AdminModelState>(`${API_BASE}/api/ai/admin/${options.providerId ? `model-providers/${encodeURIComponent(options.providerId)}` : "models"}`, {
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
        ...(options.providerId ? { provider_id: options.providerId } : {}),
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
        ...(options.providerId ? { provider_id: options.providerId } : {}),
        models: models.map((model) => ({
          id: model.id,
          value: model.value,
          display_name: model.display_name ?? model.label,
          enabled: model.enabled,
          order: model.order,
          ...(model.auto_compact_window ? { auto_compact_window: model.auto_compact_window } : {}),
          ...(model.max_output_tokens ? { max_output_tokens: model.max_output_tokens } : {}),
        })),
      }),
    });
  },
};
