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

const AGENT_PROFILE_ADMIN_SCHEMA_HEADERS = {
  "X-AI-Agent-Profile-Schema": "2",
} as const;

// During a rolling upgrade, an old API/worker still requires a permissive
// physical MIME list. This is transport-only and is not part of the editor or
// current Agent Profile contract.
const ROLLING_LEGACY_FILE_CAPABILITY = [
  "application/*",
  "audio/*",
  "chemical/*",
  "font/*",
  "image/*",
  "message/*",
  "model/*",
  "multipart/*",
  "text/*",
  "video/*",
] as const;

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

  listAdmin(): Promise<{ agent_profiles: AgentProfileAdminProjection[] }> {
    return authFetch(`${API_BASE}/api/ai/admin/agent-profiles`, {
      headers: AGENT_PROFILE_ADMIN_SCHEMA_HEADERS,
    });
  },

  listHistory(agentId: string): Promise<{ agent_profiles: AgentProfileAdminProjection[] }> {
    return authFetch(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/history`,
      { cache: "no-store", headers: AGENT_PROFILE_ADMIN_SCHEMA_HEADERS },
    );
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
        headers: AGENT_PROFILE_ADMIN_SCHEMA_HEADERS,
        body: JSON.stringify({
          ...draft,
          supported_file_types: ROLLING_LEGACY_FILE_CAPABILITY,
        }),
      },
    );
  },

  publish(agentId: string, expectedRevision: number): Promise<AgentProfileMutationResponse> {
    return authFetch(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/publish`,
      {
        method: "POST",
        headers: AGENT_PROFILE_ADMIN_SCHEMA_HEADERS,
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    );
  },

  unpublish(agentId: string, expectedRevision: number): Promise<AgentProfileMutationResponse> {
    return authFetch(
      `${API_BASE}/api/ai/admin/agent-profiles/${encodeURIComponent(agentId)}/unpublish`,
      {
        method: "POST",
        headers: AGENT_PROFILE_ADMIN_SCHEMA_HEADERS,
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    );
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
