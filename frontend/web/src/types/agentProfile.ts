export const AGENT_PROFILE_AVATAR_REFS = [
  "builtin:agent",
  "builtin:assistant",
  "builtin:document",
  "builtin:research",
  "builtin:cartoon",
  "builtin:emoji",
  "builtin:pixel",
  "builtin:portrait",
  "builtin:abstract",
  "builtin:planet",
  "builtin:clay",
  "builtin:icon",
] as const;

export type AgentProfileAvatarRef = (typeof AGENT_PROFILE_AVATAR_REFS)[number];

/** Optimistic client lock for one published Agent Profile revision. */
export interface SelectedAgentProfileRequest {
  agent_id: string;
  expected_revision: number;
}

/** Safe ordinary-user market card. Execution configuration stays server-owned. */
export interface AgentProfilePublicProjection extends SelectedAgentProfileRequest {
  name: string;
  description: string;
  starter_prompts: string[];
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed: string;
  market_tags: string[];
  completed_tasks?: number;
  is_favorite: boolean;
  published_at: string | null;
}

/** Safe immutable identity recovered from a server-owned Agent Conversation. */
export interface AgentConversationIdentity {
  agent_id: string;
  revision: number;
  name: string;
  description: string;
  starter_prompts: string[];
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed: string;
  published_at: string | null;
}

/** Canonical server projection for either an Agent-bound or ordinary Session. */
export interface AgentConversationSessionProjection {
  session_id: string;
  workspace_id: string;
  agent_id: string;
  title: string;
  purpose: "conversation" | "builder_test";
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

function projectAvatarSeed(record: Record<string, unknown>, code: string): string {
  const fallback = requireString(record.agent_id, code);
  if (typeof record.avatar_seed !== "string") return fallback;
  if ([...record.avatar_seed].some((character) => (character.codePointAt(0) ?? 0) < 32)) {
    return fallback;
  }
  const seed = record.avatar_seed.trim();
  if (!seed || [...seed].length > 128) {
    return fallback;
  }
  return seed;
}

function requirePositiveRevision(value: unknown, code: string): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw new Error(code);
  return value as number;
}

function requireNonNegativeInteger(value: unknown, code: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new Error(code);
  return value as number;
}

function requireStringList(value: unknown, code: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new Error(code);
  }
  return [...value];
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
    starter_prompts: requireStringList(record.starter_prompts, PROFILE_ERROR),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, PROFILE_ERROR),
    avatar_seed: projectAvatarSeed(record, PROFILE_ERROR),
    market_tags: requireStringList(record.market_tags, PROFILE_ERROR),
    ...(record.completed_tasks === undefined
      ? {}
      : { completed_tasks: requireNonNegativeInteger(record.completed_tasks, PROFILE_ERROR) }),
    is_favorite: record.is_favorite === true,
    published_at: typeof record.published_at === "string" ? record.published_at : null,
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
    starter_prompts: requireStringList(record.starter_prompts, IDENTITY_ERROR),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, IDENTITY_ERROR),
    avatar_seed: projectAvatarSeed(record, IDENTITY_ERROR),
    published_at: typeof record.published_at === "string" ? record.published_at : null,
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
    purpose:
      record.purpose === "builder_test" ? "builder_test" : "conversation",
    agent_conversation: projectAgentConversationIdentity(record.agent_conversation),
    created_at: typeof record.created_at === "string" ? record.created_at : null,
    updated_at: typeof record.updated_at === "string" ? record.updated_at : null,
  };
}

export interface AgentProfileSkillReference {
  skill_id: string;
}

export interface AgentProfileDraftRequest {
  name: string;
  description: string;
  starter_prompts: string[];
  instructions: string;
  skill_set: AgentProfileSkillReference[];
  mcp_tool_ids: string[];
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed: string;
  market_tags: string[];
  visibility: "tenant" | "restricted";
  allowed_department_ids: string[];
  allowed_roles: string[];
  allowed_user_ids: string[];
  /** 0 creates a profile; later saves must name the current immutable revision. */
  expected_draft_revision: number;
}

export interface AgentProfileAdminProjection
  extends Omit<AgentProfileDraftRequest, "expected_draft_revision"> {
  agent_id: string;
  revision: number;
  published_revision: number | null;
  status: "draft" | "published" | "withdrawn";
  content_hash: string;
  created_at: string | null;
  published_at: string | null;
}

/** Rebuild an admin projection from the hard-cut allowlist. */
export function validateAgentProfileAdminProjection(value: unknown): AgentProfileAdminProjection {
  const record = requireRecord(value, "invalid_agent_profile_admin_projection");
  const code = "invalid_agent_profile_admin_projection";
  if (!Array.isArray(record.skill_set)) throw new Error(code);
  const skillSet = record.skill_set.map((skill) => ({
    skill_id: requireString(requireRecord(skill, code).skill_id, code),
  }));
  if (skillSet.length === 0) throw new Error(code);
  const publishedRevision = record.published_revision === null
    ? null
    : requirePositiveRevision(record.published_revision, code);
  return {
    agent_id: requireString(record.agent_id, code),
    revision: requirePositiveRevision(record.revision, code),
    published_revision: publishedRevision,
    status: requireOneOf(record.status, ["draft", "published", "withdrawn"] as const, code),
    name: requireString(record.name, code),
    description: requireString(record.description, code, true),
    starter_prompts: requireStringList(record.starter_prompts, code),
    instructions: requireString(record.instructions, code),
    skill_set: skillSet,
    mcp_tool_ids: requireStringList(record.mcp_tool_ids, code),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, code),
    avatar_seed: projectAvatarSeed(record, code),
    market_tags: requireStringList(record.market_tags, code),
    visibility: requireOneOf(record.visibility, ["tenant", "restricted"] as const, code),
    allowed_department_ids: requireStringList(record.allowed_department_ids, code),
    allowed_roles: requireStringList(record.allowed_roles, code),
    allowed_user_ids: requireStringList(record.allowed_user_ids, code),
    content_hash: requireString(record.content_hash, code),
    created_at: typeof record.created_at === "string" ? record.created_at : null,
    published_at: typeof record.published_at === "string" ? record.published_at : null,
  };
}

export interface AgentProfileMutationResponse {
  agent_profile: AgentProfileAdminProjection;
  audit_id: string;
}
