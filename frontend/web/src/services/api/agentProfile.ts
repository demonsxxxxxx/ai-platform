import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  AgentProfileMutationResponse,
  AgentProfilePublicProjection,
} from "../../types";
import {
  projectAgentConversationSession,
  projectAgentProfilePublicProjection,
  validateAgentProfileAdminProjection,
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

export interface AgentConversationPage {
  sessions: AgentConversationSessionProjection[];
  next_cursor: string | null;
}

export interface AgentConversationListOptions {
  cursor?: string;
  limit?: number;
}

export interface AgentProfileTrialRunResponse {
  session_id: string;
  run_id: string;
  status: "queued" | "accepted_pending_enqueue";
  submission_id: string;
  purpose: "builder_test";
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

function projectConversationListResponse(value: unknown): AgentConversationPage {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  const record = value as { sessions?: unknown; next_cursor?: unknown };
  const sessions = record.sessions;
  if (!Array.isArray(sessions)) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  if (
    record.next_cursor !== undefined &&
    record.next_cursor !== null &&
    typeof record.next_cursor !== "string"
  ) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  return {
    sessions: sessions.map(projectAgentConversationSession),
    next_cursor: typeof record.next_cursor === "string" ? record.next_cursor : null,
  };
}

function projectAdminListResponse(value: unknown): { agent_profiles: AgentProfileAdminProjection[] } {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_agent_profile_admin_catalog");
  }
  const profiles = (value as { agent_profiles?: unknown }).agent_profiles;
  if (!Array.isArray(profiles)) throw new Error("invalid_agent_profile_admin_catalog");
  return { agent_profiles: profiles.map(validateAgentProfileAdminProjection) };
}

function projectMutationResponse(value: unknown): AgentProfileMutationResponse {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_agent_profile_mutation_response");
  }
  const record = value as { agent_profile?: unknown; audit_id?: unknown };
  if (typeof record.audit_id !== "string" || !record.audit_id) {
    throw new Error("invalid_agent_profile_mutation_response");
  }
  return {
    agent_profile: validateAgentProfileAdminProjection(record.agent_profile),
    audit_id: record.audit_id,
  };
}

export function buildAgentConversationListUrl(
  selection: SelectedAgentProfileRequest,
  options: AgentConversationListOptions = {},
): string {
  const searchParams = new URLSearchParams({
    agent_id: selection.agent_id,
    revision: String(selection.expected_revision),
    limit: String(options.limit ?? 20),
  });
  if (options.cursor) searchParams.set("cursor", options.cursor);
  return `${API_BASE}/api/ai/chat/sessions?${searchParams.toString()}`;
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

  /** List one server-authorized Agent/revision history page. */
  async listConversations(
    selection: SelectedAgentProfileRequest,
    options: AgentConversationListOptions = {},
  ): Promise<AgentConversationPage> {
    const response = await authFetch<unknown>(
      buildAgentConversationListUrl(selection, options),
      { cache: "no-store" },
    );
    return projectConversationListResponse(response);
  },

  async createConversation(
    selection: SelectedAgentProfileRequest,
    operationId: string,
  ): Promise<AgentConversationSessionProjection> {
    const selected_agent_profile = {
      agent_id: selection.agent_id,
      expected_revision: selection.expected_revision,
    };
    const response = await authFetch<unknown>(`${API_BASE}/api/ai/agent-conversations`, {
      method: "POST",
      body: JSON.stringify({ selected_agent_profile, operation_id: operationId }),
    });
    return projectAgentConversationSession(response);
  },

  async listAdmin(): Promise<{ agent_profiles: AgentProfileAdminProjection[] }> {
    const response = await authFetch<unknown>(`${API_BASE}/api/ai/admin/agent-profiles`);
    return projectAdminListResponse(response);
  },

  async listHistory(agentId: string): Promise<{ agent_profiles: AgentProfileAdminProjection[] }> {
    const response = await authFetch<unknown>(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/history`,
      { cache: "no-store" },
    );
    return projectAdminListResponse(response);
  },

  async saveDraft(
    draft: AgentProfileDraftRequest,
    agentId?: string,
  ): Promise<AgentProfileMutationResponse> {
    const response = await authFetch<unknown>(
      agentId
        ? `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}`
        : `${API_BASE}/api/ai/admin/agent-profiles`,
      {
        method: agentId ? "PUT" : "POST",
        body: JSON.stringify(draft),
      },
    );
    return projectMutationResponse(response);
  },

  async publish(agentId: string, expectedRevision: number): Promise<AgentProfileMutationResponse> {
    const response = await authFetch<unknown>(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/publish`,
      {
        method: "POST",
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    );
    return projectMutationResponse(response);
  },

  async unpublish(agentId: string, expectedRevision: number): Promise<AgentProfileMutationResponse> {
    const response = await authFetch<unknown>(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/unpublish`,
      {
        method: "POST",
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    );
    return projectMutationResponse(response);
  },

  runTest(
    agentId: string,
    expectedRevision: number,
    message: string,
    submissionId: string,
  ): Promise<AgentProfileTrialRunResponse> {
    return authFetch(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/test-runs`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          message,
          submission_id: submissionId,
        }),
      },
    );
  },
};
