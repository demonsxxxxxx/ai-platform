import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export type ModelGroupType = "business" | "ordinary_default";
export type ModelGroupAvailability =
  | "available"
  | "group_has_no_models"
  | "group_models_unavailable"
  | "group_disabled"
  | "default_not_for_chat";

export interface PublicModelGroupModel {
  id: string;
  value: string;
  label: string;
  enabled: boolean;
}

export interface PublicModelGroup {
  id: string;
  code: string;
  name: string;
  type: ModelGroupType;
  enabled: boolean;
  revision: number;
  is_primary: boolean;
  availability: ModelGroupAvailability;
  default_model_id?: string | null;
  models: PublicModelGroupModel[];
}

export interface AdminModelGroupSummary {
  id: string;
  code: string;
  name: string;
  type: ModelGroupType;
  enabled: boolean;
  revision: number;
  remark?: string | null;
  sort_order: number;
  model_count: number;
  member_count: number;
  members_are_implicit?: boolean;
  default_model_id?: string | null;
}

export interface AdminModelGroupMember {
  user_id: string;
  work_id?: string | null;
  display_name?: string | null;
  auth_source?: string | null;
  is_primary: boolean;
}

export interface AdminModelGroupModel {
  model_id: string;
  model_value: string;
  label: string;
  enabled: boolean;
  display_order: number;
  priority: number;
  weight: number;
}

export interface AdminModelGroupDetails extends AdminModelGroupSummary {
  active_run_count?: number;
  archived_at?: string | null;
  members: AdminModelGroupMember[];
  models: AdminModelGroupModel[];
}

export interface UserModelGroupMembership {
  id: string;
  code: string;
  name: string;
  enabled: boolean;
  revision: number;
  is_primary: boolean;
}

export interface UserModelGroups {
  user_id: string;
  revision: number;
  groups: UserModelGroupMembership[];
  primary_group_id: string | null;
  default_implicit: boolean;
}

export type ModelGroupBatchOperation = "add" | "replace" | "remove";
export interface ModelGroupBatchModelSnapshot {
  model_id: string;
  enabled: boolean;
  display_order: number;
  priority: number;
  weight: number;
  revision: number;
}
export interface ModelGroupBatchResult {
  tenant_id: string;
  operation: ModelGroupBatchOperation;
  preview: boolean;
  changed: boolean;
  groups: Array<{
    group_id: string;
    group_type: ModelGroupType;
    before: { revision: number; member_count: number; models: ModelGroupBatchModelSnapshot[] };
    after: { revision: number; member_count: number; models: ModelGroupBatchModelSnapshot[]; affected_model_ids: string[] };
    changed: boolean;
  }>;
}

interface RequestOptions { signal?: AbortSignal }

function requestOptions(options: RequestOptions, method: string, body?: unknown): RequestInit {
  return {
    method,
    signal: options.signal,
    cache: "no-store",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

export const modelGroupsApi = {
  async listPublic(options: RequestOptions = {}): Promise<{ groups: PublicModelGroup[]; count: number; version: string }> {
    return authFetch(`${API_BASE}/api/ai/models/groups`, requestOptions(options, "GET"));
  },

  async listAdmin(options: RequestOptions = {}): Promise<{ groups: AdminModelGroupSummary[]; version: string }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups`, requestOptions(options, "GET"));
  },

  async details(groupId: string, options: RequestOptions = {}): Promise<AdminModelGroupDetails> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/${encodeURIComponent(groupId)}`, requestOptions(options, "GET"));
  },

  async create(payload: { code: string; name: string; remark?: string; sort_order?: number }, options: RequestOptions = {}): Promise<AdminModelGroupSummary> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups`, requestOptions(options, "POST", payload));
  },

  async update(groupId: string, payload: { name?: string; enabled?: boolean; remark?: string; sort_order?: number; revision: number; confirm_impact?: boolean }, options: RequestOptions = {}): Promise<AdminModelGroupSummary> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/${encodeURIComponent(groupId)}`, requestOptions(options, "PATCH", payload));
  },

  async retire(groupId: string, payload: { revision: number; confirm_impact: boolean; delete: boolean; target_group_id?: string; target_group_revision?: number; confirm_default_fallback?: boolean }, options: RequestOptions = {}): Promise<{ group_id: string; deleted: boolean; archived: boolean; revision?: number }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/${encodeURIComponent(groupId)}/retire`, requestOptions(options, "POST", payload));
  },

  async batchModels(payload: { group_ids: string[]; model_ids: string[]; operation: ModelGroupBatchOperation; expected_group_revisions: Record<string, number>; preview: boolean }, options: RequestOptions = {}): Promise<ModelGroupBatchResult> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/models/batch`, requestOptions(options, "POST", payload));
  },

  async setModels(groupId: string, payload: { revision: number; default_model_id?: string | null; models: Array<{ model_id: string; enabled: boolean; display_order: number; priority?: number; weight?: number }> }, options: RequestOptions = {}): Promise<{ group_id: string; revision: number; default_model_id?: string | null }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/${encodeURIComponent(groupId)}/models`, requestOptions(options, "PUT", payload));
  },

  async setMembers(groupId: string, payload: { revision: number; confirm_default_fallback?: boolean; members: Array<{ user_id?: string; work_id?: string; is_primary: boolean }> }, options: RequestOptions = {}): Promise<{ group_id: string; revision: number }> {
    return authFetch(`${API_BASE}/api/ai/admin/model-groups/${encodeURIComponent(groupId)}/members`, requestOptions(options, "PUT", payload));
  },

  async userMemberships(userId: string, options: RequestOptions = {}): Promise<UserModelGroups> {
    return authFetch(`${API_BASE}/api/ai/admin/users/${encodeURIComponent(userId)}/model-groups`, requestOptions(options, "GET"));
  },

  async setUserMemberships(userId: string, payload: { group_ids: string[]; primary_group_id: string | null; expected_revision: number; confirm_default_fallback: boolean }, options: RequestOptions = {}): Promise<{ user_id: string; revision: number; group_ids: string[]; primary_group_id: string | null }> {
    return authFetch(`${API_BASE}/api/ai/admin/users/${encodeURIComponent(userId)}/model-groups`, requestOptions(options, "PUT", payload));
  },
};
