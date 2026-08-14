import type { SelectedSkillRequest } from "./skill";

export const AGENT_PROFILE_AVATAR_REFS = ["builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"] as const;

export type AgentProfileAvatarRef = (typeof AGENT_PROFILE_AVATAR_REFS)[number];

export const AGENT_PROFILE_CATEGORIES = ["general", "support", "writing", "research", "operations"] as const;

export type AgentProfileCategory = (typeof AGENT_PROFILE_CATEGORIES)[number];

export const AGENT_PROFILE_CATEGORY_LABELS = {
  general: "通用专家",
  support: "支持服务",
  writing: "内容写作",
  research: "研究分析",
  operations: "运营效率",
} as const satisfies Record<AgentProfileCategory, string>;

/** Optimistic client lock for one published Agent Profile revision. */
export interface SelectedAgentProfileRequest {
  agent_id: string;
  expected_revision: number;
}

/** Safe ordinary-user market card. Execution configuration stays server-owned. */
export interface AgentProfilePublicProjection extends SelectedAgentProfileRequest {
  name: string;
  description: string;
  welcome_message: string;
  starter_prompts: string[];
  capability_summary: string;
  recommended_tasks: string[];
  supported_input_types: Array<"text" | "file">;
  expected_outputs: string[];
  permissions_and_data_access_notice: string;
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed?: string;
  category: AgentProfileCategory;
  published_at: string | null;
}

/** Safe immutable identity recovered from a server-owned Agent Conversation. */
export interface AgentConversationIdentity {
  agent_id: string;
  revision: number;
  name: string;
  description: string;
  welcome_message: string;
  starter_prompts: string[];
  capability_summary: string;
  recommended_tasks: string[];
  supported_input_types: Array<"text" | "file">;
  expected_outputs: string[];
  permissions_and_data_access_notice: string;
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed?: string;
  category: AgentProfileCategory;
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
  const seed = record.avatar_seed.trim();
  if (
    !seed ||
    [...seed].length > 128 ||
    [...seed].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || (codePoint >= 0x7f && codePoint <= 0x9f);
    })
  ) {
    return fallback;
  }
  return seed;
}

function requirePositiveRevision(value: unknown, code: string): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw new Error(code);
  return value as number;
}

function requireStringList(value: unknown, code: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new Error(code);
  }
  return [...value];
}

function projectEnterpriseFields(
  record: Record<string, unknown>,
  code: string,
): Pick<
  AgentProfilePublicProjection,
  | "welcome_message"
  | "starter_prompts"
  | "capability_summary"
  | "recommended_tasks"
  | "supported_input_types"
  | "expected_outputs"
  | "permissions_and_data_access_notice"
  | "published_at"
> {
  const receivedSupportedInputTypes =
    record.supported_input_types === undefined
      ? ["text", "file"]
      : requireStringList(record.supported_input_types, code);
  if (
    receivedSupportedInputTypes.length === 0 ||
    receivedSupportedInputTypes.some((item) => item !== "text" && item !== "file")
  ) {
    throw new Error(code);
  }
  return {
    welcome_message:
      record.welcome_message === undefined
        ? ""
        : requireString(record.welcome_message, code, true),
    starter_prompts:
      record.starter_prompts === undefined
        ? []
        : requireStringList(record.starter_prompts, code),
    capability_summary:
      record.capability_summary === undefined
        ? ""
        : requireString(record.capability_summary, code, true),
    recommended_tasks:
      record.recommended_tasks === undefined
        ? []
        : requireStringList(record.recommended_tasks, code),
    supported_input_types: ["text", "file"],
    expected_outputs:
      record.expected_outputs === undefined
        ? []
        : requireStringList(record.expected_outputs, code),
    permissions_and_data_access_notice:
      record.permissions_and_data_access_notice === undefined
        ? ""
        : requireString(record.permissions_and_data_access_notice, code, true),
    published_at: typeof record.published_at === "string" ? record.published_at : null,
  };
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
    ...projectEnterpriseFields(record, PROFILE_ERROR),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, PROFILE_ERROR),
    avatar_seed: projectAvatarSeed(record, PROFILE_ERROR),
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
    ...projectEnterpriseFields(record, IDENTITY_ERROR),
    avatar_ref: requireOneOf(record.avatar_ref, AGENT_PROFILE_AVATAR_REFS, IDENTITY_ERROR),
    avatar_seed: projectAvatarSeed(record, IDENTITY_ERROR),
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
    purpose:
      record.purpose === "builder_test" ? "builder_test" : "conversation",
    agent_conversation: projectAgentConversationIdentity(record.agent_conversation),
    created_at: typeof record.created_at === "string" ? record.created_at : null,
    updated_at: typeof record.updated_at === "string" ? record.updated_at : null,
  };
}

export interface AgentProfileDraftRequest {
  name: string;
  description: string;
  welcome_message: string;
  starter_prompts: string[];
  capability_summary: string;
  recommended_tasks: string[];
  supported_input_types: Array<"text" | "file">;
  expected_outputs: string[];
  permissions_and_data_access_notice: string;
  instructions: string;
  model_id: string;
  selected_skill: SelectedSkillRequest;
  skill_set: SelectedSkillRequest[];
  mcp_tool_ids: string[];
  avatar_ref: AgentProfileAvatarRef;
  avatar_seed: string;
  avatar_asset_id: string | null;
  category: AgentProfileCategory;
  visibility: "tenant" | "restricted";
  allowed_department_ids: string[];
  allowed_roles: string[];
  allowed_user_ids: string[];
  /** 0 creates a profile; later saves must name the current immutable revision. */
  expected_draft_revision: number;
}

export interface AgentProfileAdminProjection extends Omit<
  AgentProfileDraftRequest,
  "avatar_seed" | "expected_draft_revision" | "skill_set"
> {
  avatar_seed?: string;
  skill_set?: SelectedSkillRequest[];
  agent_id: string;
  revision: number;
  /** Current aggregate publication; absent only while talking to a rolling old API. */
  published_revision?: number | null;
  status: "draft" | "published" | "withdrawn";
  content_hash: string;
  created_at?: string | null;
  published_at?: string | null;
}

export interface AgentProfileMutationResponse {
  agent_profile: AgentProfileAdminProjection;
  audit_id: string;
}
