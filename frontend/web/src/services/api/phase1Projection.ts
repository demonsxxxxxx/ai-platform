import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import { modelPublicApi, type ModelOption } from "./modelPublic";
import type { AgentInfo } from "../../types";

export interface AgentAppProjection {
  app_id: string;
  name: string;
  mode: "chat" | "file" | "chat_file";
  default_skill_id: string;
  allowed_input_types: string[];
  output_types: string[];
  status: "active" | "disabled";
}

export interface AgentAppsProjectionResponse {
  agent_apps: AgentAppProjection[];
}

export interface AdminSkillVersionProjection {
  skill_id: string;
  version: string;
  content_hash?: string | null;
  description?: string | null;
  source?: Record<string, unknown> | null;
  status: string;
  created_at?: string | null;
}

export interface AdminSkillDetailProjection {
  skill_id: string;
  status: string;
  visible_to_user: boolean;
  description?: string | null;
  versions: AdminSkillVersionProjection[];
  dependency_policy?: Record<string, unknown> | null;
  release_policy?: Record<string, unknown> | null;
  recent_snapshots?: Record<string, unknown>[];
}

interface BackendAdminSkillDetailProjection {
  skill?: {
    id?: string | null;
    skill_id?: string | null;
    status?: string | null;
    visible_to_user?: boolean | null;
    description?: string | null;
  };
  dependency_policy?: Record<string, unknown> | null;
  release_policy?: Record<string, unknown> | null;
  versions?: AdminSkillVersionProjection[];
  recent_snapshots?: Record<string, unknown>[];
}

export interface AdminSkillSyncProjection {
  synced: AdminSkillVersionProjection[];
}

export interface SkillGovernanceProjection {
  publicAgents: AgentInfo[];
  agentApps: AgentAppProjection[];
  details: AdminSkillDetailProjection[];
  detailErrors: { skill_id: string; error: string }[];
  agentAppsError?: string | null;
}

export interface AdminToolPolicyProjection {
  tool_id: string;
  server_id: string;
  name: string;
  description: string;
  registry_status: string;
  policy_status: string;
  effective_status: string;
  write_capable: boolean;
  risk_level: string;
  visible_to_user: boolean;
  source: string;
  requires_decision: boolean;
  reason?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface AdminToolPoliciesProjectionResponse {
  contract_version: string;
  tenant_id: string;
  tool_policies: AdminToolPolicyProjection[];
  summary: {
    returned_count: number;
    limit: number;
    include_disabled: boolean;
  };
}

export interface ActiveNotificationProjection {
  id?: string;
  title?: string;
  content?: string;
  level?: string;
  type?: string;
  created_at?: string;
  starts_at?: string;
  ends_at?: string;
}

type ActiveNotificationProjectionResponse =
  | ActiveNotificationProjection[]
  | { notifications?: ActiveNotificationProjection[] };

export interface Phase1ModelCatalogProjection {
  models: ModelOption[];
  count: number;
  enabled_count: number;
  providers: { value: string; protocol: string; prefixes: string[] }[];
  providers_error?: string | null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function deriveGovernedSkillIds(
  agentApps: readonly AgentAppProjection[],
): string[] {
  const seen = new Set<string>();
  const skillIds: string[] = [];
  for (const app of agentApps) {
    const skillId = app.default_skill_id.trim();
    if (!skillId || seen.has(skillId)) continue;
    seen.add(skillId);
    skillIds.push(skillId);
  }
  return skillIds;
}

export function normalizeActiveNotificationProjectionResponse(
  response: ActiveNotificationProjectionResponse,
): ActiveNotificationProjection[] {
  return Array.isArray(response) ? response : (response.notifications ?? []);
}

function normalizeAdminSkillDetail(
  fallbackSkillId: string,
  response: BackendAdminSkillDetailProjection,
): AdminSkillDetailProjection {
  const skill = response.skill ?? {};
  const versions = response.versions ?? [];
  const latestVersion = versions[0];
  return {
    skill_id: skill.skill_id || skill.id || fallbackSkillId,
    status: skill.status || latestVersion?.status || "unknown",
    visible_to_user: Boolean(skill.visible_to_user),
    description: skill.description || latestVersion?.description || null,
    versions,
    dependency_policy: response.dependency_policy ?? null,
    release_policy: response.release_policy ?? null,
    recent_snapshots: response.recent_snapshots ?? [],
  };
}

export async function listModelCatalogWithSources(sources: {
  listAvailable: typeof modelPublicApi.listAvailable;
  listProviders: typeof modelPublicApi.listProviders;
}): Promise<Phase1ModelCatalogProjection> {
  const models = await sources.listAvailable();
  try {
    const providers = await sources.listProviders();
    return { ...models, providers, providers_error: null };
  } catch (error: unknown) {
    return { ...models, providers: [], providers_error: errorMessage(error) };
  }
}

export const phase1ProjectionApi = {
  async listAgentApps(): Promise<AgentAppsProjectionResponse> {
    return authFetch<AgentAppsProjectionResponse>(
      `${API_BASE}/api/ai/agent-apps`,
    );
  },

  async listPublicAgents(): Promise<{
    agents: AgentInfo[];
    count: number;
    default_agent?: string;
  }> {
    return authFetch(`${API_BASE}/api/agents`);
  },

  async listSkillGovernanceProjection(): Promise<SkillGovernanceProjection> {
    const publicAgents = await this.listPublicAgents();
    let agentApps: AgentAppProjection[] = [];
    let agentAppsError: string | null = null;

    try {
      agentApps = (await this.listAgentApps()).agent_apps;
    } catch (error: unknown) {
      agentAppsError = errorMessage(error);
    }

    const skillIds = deriveGovernedSkillIds(agentApps);
    const details = await Promise.allSettled(
      skillIds.map((skillId) => this.getAdminSkill(skillId)),
    );
    return {
      publicAgents: publicAgents.agents,
      agentApps,
      details: details.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      ),
      detailErrors: details.flatMap((result, index) =>
        result.status === "rejected"
          ? [{ skill_id: skillIds[index], error: errorMessage(result.reason) }]
          : [],
      ),
      agentAppsError,
    };
  },

  async syncBuiltinSkills(): Promise<AdminSkillSyncProjection> {
    return authFetch<AdminSkillSyncProjection>(
      `${API_BASE}/api/ai/admin/skills/sync-builtin`,
      { method: "POST" },
    );
  },

  async getAdminSkill(skillId: string): Promise<AdminSkillDetailProjection> {
    const response = await authFetch<BackendAdminSkillDetailProjection>(
      `${API_BASE}/api/ai/admin/skills/${encodeURIComponent(skillId)}`,
    );
    return normalizeAdminSkillDetail(skillId, response);
  },

  async listToolPolicies(): Promise<AdminToolPoliciesProjectionResponse> {
    return authFetch<AdminToolPoliciesProjectionResponse>(
      `${API_BASE}/api/ai/admin/tool-policies?include_disabled=true&limit=200`,
    );
  },

  async listModelCatalog(): Promise<Phase1ModelCatalogProjection> {
    return listModelCatalogWithSources(modelPublicApi);
  },

  async listActiveNotifications(): Promise<ActiveNotificationProjection[]> {
    const response = await authFetch<ActiveNotificationProjectionResponse>(
      `${API_BASE}/api/notifications/active`,
    );
    return normalizeActiveNotificationProjectionResponse(response);
  },
};
