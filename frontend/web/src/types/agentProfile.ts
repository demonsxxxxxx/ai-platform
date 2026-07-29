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
  /** 0 creates a profile; later saves must name the current immutable revision. */
  expected_draft_revision: number;
}

export interface AgentProfileAdminProjection extends Omit<AgentProfileDraftRequest, "expected_draft_revision"> {
  agent_id: string;
  revision: number;
  status: "draft" | "published" | "withdrawn";
  content_hash: string;
}

export interface AgentProfileMutationResponse {
  agent_profile: AgentProfileAdminProjection;
  audit_id: string;
}
