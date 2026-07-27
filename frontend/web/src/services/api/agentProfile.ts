import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  AgentProfileMutationResponse,
  AgentProfilePublicProjection,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface AgentProfileCatalogResponse {
  agent_profiles: AgentProfilePublicProjection[];
}

export const agentProfileApi = {
  listPublished(): Promise<AgentProfileCatalogResponse> {
    return authFetch(`${API_BASE}/api/ai/agent-profiles`);
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
