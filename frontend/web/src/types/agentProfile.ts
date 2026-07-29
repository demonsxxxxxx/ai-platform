import type { SelectedSkillRequest } from "./skill";

export const AGENT_PROFILE_AVATAR_REFS = ["builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"] as const;

export type AgentProfileAvatarRef = (typeof AGENT_PROFILE_AVATAR_REFS)[number];

export const AGENT_PROFILE_CATEGORIES = ["general", "support", "writing", "research", "operations"] as const;

export type AgentProfileCategory = (typeof AGENT_PROFILE_CATEGORIES)[number];

/** Optimistic client lock for one published Agent Profile revision. */
export interface SelectedAgentProfileRequest {
  agent_id: string;
  expected_revision: number;
}

/** Safe ordinary-user market card. Execution configuration stays server-owned. */
export interface AgentProfilePublicProjection extends SelectedAgentProfileRequest {
  name: string;
  description: string;
  avatar_ref: AgentProfileAvatarRef;
  category: AgentProfileCategory;
}

/** Safe immutable identity recovered from a server-owned Agent Conversation. */
export interface AgentConversationIdentity {
  agent_id: string;
  revision: number;
  name: string;
  description: string;
  avatar_ref: AgentProfileAvatarRef;
  category: AgentProfileCategory;
}

/** Canonical server projection for either an Agent-bound or ordinary Session. */
export interface AgentConversationSessionProjection {
  session_id: string;
  workspace_id: string;
  agent_id: string;
  title: string;
  agent_conversation: AgentConversationIdentity | null;
  created_at?: string | null;
  updated_at?: string | null;
}

function requireRecord(value: unknown, code: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(code);
  return value as Record<string, unknown>;
}

function requireString(value: unknown, code: string, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) throw new Error(code);
  return value;
}

function requirePositiveRevision(value: unknown, code: string): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw new Error(code);
  return value as number;
}

function requireOneOf<const T extends readonly string[]>(
  value: unknown,
  allowed: T,
  code: string,
): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) throw new Error(code);
  return value as T[number];
}

const PROFILE_ERROR = "invalid_agent_profile_projection";
const IDENTITY_ERROR = "invalid_agent_conversation_projection";
const SESSION_ERROR = "invalid_agent_conversation_session";

/** Rebuild a public card from its allowlisted fields and reject malformed data. */
export function projectAgentProfilePublicProjection(value: unknown): AgentProfilePublicProjection {
  const record = requireRecord(value, PROFILE_ERROR);
  return {
    agent_id: requireString(record.agent_id, PROFILE_ERROR),
    expected_revision: requirePositiveRevision(record.expected_revision, PROFILE_ERROR),
    name: requireString(record.name, PROFILE_ERROR),
    description: requireString(record.description, PROFILE_ERROR, true),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, PROFILE_ERROR),
    category: requireOneOf(record.category, AGENT_PROFILE_CATEGORIES, PROFILE_ERROR),
  };
}

/** Rebuild the optional safe Agent identity without retaining extra server fields. */
export function projectAgentConversationIdentity(value: unknown): AgentConversationIdentity | null {
  if (value === null || value === undefined) return null;
  const record = requireRecord(value, IDENTITY_ERROR);
  return {
    agent_id: requireString(record.agent_id, IDENTITY_ERROR),
    revision: requirePositiveRevision(record.revision, IDENTITY_ERROR),
    name: requireString(record.name, IDENTITY_ERROR),
    description: requireString(record.description, IDENTITY_ERROR, true),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, IDENTITY_ERROR),
    category: requireOneOf(record.category, AGENT_PROFILE_CATEGORIES, IDENTITY_ERROR),
  };
}

/** Rebuild the canonical Session projection and discard every non-public field. */
export function projectAgentConversationSession(value: unknown): AgentConversationSessionProjection {
  const record = requireRecord(value, SESSION_ERROR);
  return {
    session_id: requireString(record.session_id, SESSION_ERROR),
    workspace_id: requireString(record.workspace_id, SESSION_ERROR),
    agent_id: requireString(record.agent_id, SESSION_ERROR),
    title: requireString(record.title, SESSION_ERROR, true),
    agent_conversation: projectAgentConversationIdentity(record.agent_conversation),
    created_at: typeof record.created_at === "string" ? record.created_at : null,
    updated_at: typeof record.updated_at === "string" ? record.updated_at : null,
  };
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
  status: "draft" | "published";
  content_hash: string;
}

export interface AgentProfileMutationResponse {
  agent_profile: AgentProfileAdminProjection;
  audit_id: string;
}
