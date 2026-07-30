import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  AgentProfileMutationResponse,
  AgentProfilePublicProjection,
} from "../../types";
import {
  projectAgentConversationSession,
  projectAgentProfilePublicProjection,
  type AgentConversationSessionProjection,
  type AgentProfileCategory,
  type SelectedAgentProfileRequest,
} from "../../types/agentProfile";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface AgentProfileCatalogResponse {
  agent_profiles: AgentProfilePublicProjection[];
}

export interface AgentProfileCatalogQuery {
  query?: string;
  category?: AgentProfileCategory;
}

/** Build the current-principal published catalog URL. */
export function buildAgentProfileCatalogUrl(query: AgentProfileCatalogQuery = {}): string {
  const searchParams = new URLSearchParams();
  const normalizedQuery = query.query?.trim();
  if (normalizedQuery) searchParams.set("query", normalizedQuery);
  if (query.category) searchParams.set("category", query.category);
  const search = searchParams.toString();
  return `${API_BASE}/api/ai/agent-profiles${search ? `?${search}` : ""}`;
}

/** Build the current published detail URL for one opaque Agent id. */
export function buildAgentProfileDetailUrl(agentId: string): string {
  return `${API_BASE}/api/ai/agent-profiles/${encodeURIComponent(agentId)}`;
}

function projectCatalogResponse(value: unknown): AgentProfileCatalogResponse {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error("invalid_agent_profile_catalog");
  const profiles = (value as { agent_profiles?: unknown }).agent_profiles;
  if (!Array.isArray(profiles)) throw new Error("invalid_agent_profile_catalog");
  return { agent_profiles: profiles.map(projectAgentProfilePublicProjection) };
}

function projectConversationListResponse(
  value: unknown,
): AgentConversationSessionProjection[] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  const sessions = (value as { sessions?: unknown }).sessions;
  if (!Array.isArray(sessions)) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  return sessions.map(projectAgentConversationSession);
}

export const agentProfileApi = {
  async listPublished(query: AgentProfileCatalogQuery = {}): Promise<AgentProfileCatalogResponse> {
    const response = await authFetch<unknown>(buildAgentProfileCatalogUrl(query), { cache: "no-store" });
    return projectCatalogResponse(response);
  },

  async getPublished(agentId: string): Promise<AgentProfilePublicProjection> {
    const response = await authFetch<unknown>(buildAgentProfileDetailUrl(agentId), { cache: "no-store" });
    return projectAgentProfilePublicProjection(response);
  },

  /** List only server-authorized conversations with their safe pinned identity. */
  async listConversations(): Promise<AgentConversationSessionProjection[]> {
    const response = await authFetch<unknown>(
      `${API_BASE}/api/ai/chat/sessions`,
      { cache: "no-store" },
    );
    return projectConversationListResponse(response);
  },

  async createConversation(selection: SelectedAgentProfileRequest): Promise<AgentConversationSessionProjection> {
    const selected_agent_profile = {
      agent_id: selection.agent_id,
      expected_revision: selection.expected_revision,
    };
    const response = await authFetch<unknown>(`${API_BASE}/api/ai/agent-conversations`, {
      method: "POST",
      body: JSON.stringify({ selected_agent_profile }),
    });
    return projectAgentConversationSession(response);
  },

  listAdmin(): Promise<{ agent_profiles: AgentProfileAdminProjection[] }> {
    return authFetch(`${API_BASE}/api/ai/admin/agent-profiles`);
  },

  saveDraft(
    draft: AgentProfileDraftRequest,
    agentId?: string,
  ): Promise<AgentProfileMutationResponse> {
    return authFetch(
      agentId
        ? `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}`
        : `${API_BASE}/api/ai/admin/agent-profiles`,
      {
        method: agentId ? "PUT" : "POST",
        body: JSON.stringify(draft),
      },
    );
  },

  publish(agentId: string, expectedRevision: number): Promise<AgentProfileMutationResponse> {
    return authFetch(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/publish`,
      {
        method: "POST",
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    );
  },
};
