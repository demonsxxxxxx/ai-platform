import type { SelectedSkillRequest } from "./skill";

/** Optimistic client lock for one published Agent Profile revision. */
export interface SelectedAgentProfileRequest {
  agent_id: string;
  expected_revision: number;
}

/** Safe ordinary-user market card. Execution configuration stays server-owned. */
export interface AgentProfilePublicProjection extends SelectedAgentProfileRequest {
  name: string;
  description: string;
}

export interface AgentProfileDraftRequest {
  name: string;
  description: string;
  instructions: string;
  model_id: string;
  selected_skill: SelectedSkillRequest;
  mcp_tool_ids: string[];
}

export interface AgentProfileAdminProjection extends AgentProfileDraftRequest {
  agent_id: string;
  revision: number;
  status: "draft" | "published";
  content_hash: string;
}

export interface AgentProfileMutationResponse {
  agent_profile: AgentProfileAdminProjection;
  audit_id: string;
}
