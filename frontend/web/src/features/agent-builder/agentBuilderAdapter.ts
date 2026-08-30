import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  KnowledgeBuilderSource,
  KnowledgeRetrievalProfile,
  PublicSkillResponse,
  SelectedSkillRequest,
  ToolState,
} from "../../types";

export interface AgentBuilderSafeMcpTool {
  id: string;
  label: string;
  description: string;
}

export interface AgentBuilderCurrentCatalog {
  skills: readonly PublicSkillResponse[];
  mcpTools: readonly AgentBuilderSafeMcpTool[];
  knowledgeSources: readonly KnowledgeBuilderSource[];
  retrievalProfiles: readonly KnowledgeRetrievalProfile[];
  skillsResolved: boolean;
  mcpToolsResolved: boolean;
  knowledgeResolved: boolean;
  knowledgeSelectionResolved?: boolean;
  missingKnowledgeSourceIds?: readonly string[];
  effectivePermissionsKnown: boolean;
}

export interface AgentBuilderEditor {
  agentId: string | null;
  revision: number | null;
  publishedRevision: number | null;
  status: AgentProfileAdminProjection["status"] | null;
  name: string;
  description: string;
  welcomeMessage: string;
  starterPrompts: string[];
  capabilitySummary: string;
  recommendedTasks: string[];
  supportedInputTypes: Array<"text" | "file">;
  expectedOutputs: string[];
  permissionsAndDataAccessNotice: string;
  instructions: string;
  selectedSkills: SelectedSkillRequest[];
  selectedMcpToolIds: string[];
  knowledgeSourceIds: string[];
  retrievalProfileId: string | null;
  avatarRef: AgentProfileDraftRequest["avatar_ref"];
  avatarSeed: string;
  avatarAssetId: string | null;
  category: AgentProfileDraftRequest["category"];
  visibility: AgentProfileDraftRequest["visibility"];
  allowedDepartmentIds: string[];
  allowedRoles: string[];
  allowedUserIds: string[];
  materializedProfile: AgentProfileAdminProjection | null;
}

export type AgentBuilderBlockCode =
  | "no_selection"
  | "name_required"
  | "instructions_required"
  | "skill_required"
  | "skill_limit_exceeded"
  | "profile_revision_missing"
  | "catalog_unavailable"
  | "selected_skill_stale"
  | "selected_mcp_tool_unavailable"
  | "knowledge_selection_incomplete"
  | "knowledge_source_limit_exceeded"
  | "selected_knowledge_source_unavailable"
  | "knowledge_scope_incompatible"
  | "retrieval_profile_unavailable"
  | "no_changes"
  | "save_required"
  | "unsaved_changes"
  | "published_revision";

export interface AgentBuilderValidationIssue {
  code: AgentBuilderBlockCode;
  unavailableMcpToolIds?: readonly string[];
  unavailableKnowledgeSourceIds?: readonly string[];
}

function normalizedSet(values: readonly string[], caseInsensitive = false): Set<string> {
  return new Set(
    values
      .map((value) => value.trim())
      .filter(Boolean)
      .map((value) => (caseInsensitive ? value.toLowerCase() : value)),
  );
}

function isSubset(values: Set<string>, container: Set<string>): boolean {
  return [...values].every((value) => container.has(value));
}

/** Mirror the server's source-contains-Agent ACL rule for early Builder feedback. */
export function knowledgeSourceContainsEditorScope(
  source: KnowledgeBuilderSource,
  editor: AgentBuilderEditor,
): boolean {
  if (source.visibility === "enterprise") return true;
  if (editor.visibility === "tenant") return false;

  const sourceDepartments = normalizedSet(source.allowed_department_ids);
  const sourceRoles = normalizedSet(source.allowed_roles, true);
  const sourceUsers = normalizedSet(source.allowed_user_ids);
  const agentDepartments = normalizedSet(editor.allowedDepartmentIds);
  const agentRoles = normalizedSet(editor.allowedRoles, true);
  const agentUsers = normalizedSet(editor.allowedUserIds);

  if (!isSubset(agentUsers, sourceUsers)) return false;
  if (
    sourceDepartments.size > 0 &&
    (agentDepartments.size === 0 || !isSubset(agentDepartments, sourceDepartments))
  ) {
    return false;
  }
  if (
    sourceRoles.size > 0 &&
    (agentRoles.size === 0 || !isSubset(agentRoles, sourceRoles))
  ) {
    return false;
  }
  return sourceDepartments.size > 0 || sourceRoles.size > 0 || sourceUsers.size > 0;
}

/**
 * The public Skill projection is usable only when its permission envelope is
 * complete. This mirrors the fail-closed contract used by the Chat composer.
 */
export function mapAuthorizedBuilderSkills({
  skills,
  catalogReadResolved,
  effectivePermissionsKnown,
}: {
  skills: readonly PublicSkillResponse[];
  catalogReadResolved: boolean;
  effectivePermissionsKnown: boolean;
}): PublicSkillResponse[] {
  if (!catalogReadResolved || !effectivePermissionsKnown) return [];
  return skills.filter(
    (skill) =>
      skill.enabled &&
      skill.name.trim().length > 0 &&
      skill.expected_version.trim().length > 0,
  );
}

/** Keep MCP presentation limited to the Chat catalog's safe identity. */
export function mapSafeBuilderMcpTools(
  tools: readonly (ToolState & { label?: string })[],
): AgentBuilderSafeMcpTool[] {
  return tools
    .filter(
      (tool) =>
        tool.category === "mcp" &&
        tool.name.trim().length > 0 &&
        tool.description.trim().length > 0,
    )
    .map((tool) => ({
      id: tool.name,
      label: tool.label?.trim() || tool.name,
      description: tool.description,
    }));
}

/** Create the one explicit unsaved editor form. */
export function createUnsavedAgentEditor(): AgentBuilderEditor {
  return {
    agentId: null,
    revision: null,
    publishedRevision: null,
    status: null,
    name: "",
    description: "",
    welcomeMessage: "",
    starterPrompts: [],
    capabilitySummary: "",
    recommendedTasks: [],
    supportedInputTypes: ["text", "file"],
    expectedOutputs: [],
    permissionsAndDataAccessNotice: "",
    instructions: "",
    selectedSkills: [],
    selectedMcpToolIds: [],
    knowledgeSourceIds: [],
    retrievalProfileId: null,
    avatarRef: "builtin:agent",
    avatarSeed: "",
    avatarAssetId: null,
    category: "general",
    visibility: "tenant",
    allowedDepartmentIds: [],
    allowedRoles: [],
    allowedUserIds: [],
    materializedProfile: null,
  };
}

/** Hydrate every editable field and server identity from one admin projection. */
export function hydrateAgentProfileEditor(
  profile: AgentProfileAdminProjection,
): AgentBuilderEditor {
  return {
    agentId: profile.agent_id,
    revision: profile.revision,
    publishedRevision: profile.published_revision ?? (
      profile.status === "published" ? profile.revision : null
    ),
    status: profile.status,
    name: profile.name,
    description: profile.description,
    welcomeMessage: profile.welcome_message,
    starterPrompts: [...profile.starter_prompts],
    capabilitySummary: profile.capability_summary,
    recommendedTasks: [...profile.recommended_tasks],
    supportedInputTypes: [...profile.supported_input_types],
    expectedOutputs: [...profile.expected_outputs],
    permissionsAndDataAccessNotice: profile.permissions_and_data_access_notice,
    instructions: profile.instructions,
    selectedSkills: (profile.skill_set?.length
      ? profile.skill_set
      : [profile.selected_skill]
    ).map((skill) => ({ ...skill })),
    selectedMcpToolIds: [...profile.mcp_tool_ids],
    knowledgeSourceIds: [...(profile.knowledge_source_ids ?? [])],
    retrievalProfileId: profile.retrieval_profile_id ?? null,
    avatarRef: profile.avatar_ref,
    avatarSeed: profile.avatar_seed?.trim() || profile.agent_id,
    avatarAssetId: profile.avatar_asset_id,
    category: profile.category,
    visibility: profile.visibility,
    allowedDepartmentIds: [...profile.allowed_department_ids],
    allowedRoles: [...profile.allowed_roles],
    allowedUserIds: [...profile.allowed_user_ids],
    materializedProfile: {
      ...profile,
      selected_skill: { ...profile.selected_skill },
      skill_set: (profile.skill_set?.length
        ? profile.skill_set
        : [profile.selected_skill]
      ).map((skill) => ({ ...skill })),
      mcp_tool_ids: [...profile.mcp_tool_ids],
      knowledge_source_ids: [...(profile.knowledge_source_ids ?? [])],
      retrieval_profile_id: profile.retrieval_profile_id ?? null,
      starter_prompts: [...profile.starter_prompts],
      recommended_tasks: [...profile.recommended_tasks],
      supported_input_types: [...profile.supported_input_types],
      expected_outputs: [...profile.expected_outputs],
      allowed_department_ids: [...profile.allowed_department_ids],
      allowed_roles: [...profile.allowed_roles],
      allowed_user_ids: [...profile.allowed_user_ids],
    },
  };
}

function editorDefinition(editor: AgentBuilderEditor) {
  return {
    name: editor.name.trim(),
    description: editor.description.trim(),
    welcome_message: editor.welcomeMessage.trim(),
    starter_prompts: editor.starterPrompts.map((item) => item.trim()).filter(Boolean),
    capability_summary: editor.capabilitySummary.trim(),
    recommended_tasks: editor.recommendedTasks.map((item) => item.trim()).filter(Boolean),
    supported_input_types: editor.supportedInputTypes,
    expected_outputs: editor.expectedOutputs.map((item) => item.trim()).filter(Boolean),
    permissions_and_data_access_notice: editor.permissionsAndDataAccessNotice.trim(),
    instructions: editor.instructions,
    selected_skill: editor.selectedSkills[0] ?? null,
    skill_set: editor.selectedSkills,
    mcp_tool_ids: editor.selectedMcpToolIds,
    knowledge_source_ids: editor.knowledgeSourceIds,
    retrieval_profile_id: editor.retrievalProfileId,
    avatar_ref: editor.avatarRef,
    avatar_seed: editor.avatarSeed.trim(),
    avatar_asset_id: editor.avatarAssetId,
    category: editor.category,
    visibility: editor.visibility,
    allowed_department_ids: editor.allowedDepartmentIds,
    allowed_roles: editor.allowedRoles,
    allowed_user_ids: editor.allowedUserIds,
  };
}

function profileDefinition(profile: AgentProfileAdminProjection) {
  return {
    name: profile.name,
    description: profile.description,
    welcome_message: profile.welcome_message,
    starter_prompts: profile.starter_prompts,
    capability_summary: profile.capability_summary,
    recommended_tasks: profile.recommended_tasks,
    supported_input_types: profile.supported_input_types,
    expected_outputs: profile.expected_outputs,
    permissions_and_data_access_notice: profile.permissions_and_data_access_notice,
    instructions: profile.instructions,
    selected_skill: profile.selected_skill,
    skill_set: profile.skill_set?.length ? profile.skill_set : [profile.selected_skill],
    mcp_tool_ids: profile.mcp_tool_ids,
    knowledge_source_ids: profile.knowledge_source_ids ?? [],
    retrieval_profile_id: profile.retrieval_profile_id ?? null,
    avatar_ref: profile.avatar_ref,
    avatar_seed: profile.avatar_seed?.trim() || profile.agent_id,
    avatar_asset_id: profile.avatar_asset_id,
    category: profile.category,
    visibility: profile.visibility,
    allowed_department_ids: profile.allowed_department_ids,
    allowed_roles: profile.allowed_roles,
    allowed_user_ids: profile.allowed_user_ids,
  };
}

/** Return whether the editor differs from its last authoritative response. */
export function isAgentProfileEditorDirty(editor: AgentBuilderEditor): boolean {
  if (!editor.materializedProfile) return true;
  return (
    JSON.stringify(editorDefinition(editor)) !==
    JSON.stringify(profileDefinition(editor.materializedProfile))
  );
}

/** Return whether leaving this editor would discard user-authored input. */
export function hasUnsavedAgentProfileEdits(editor: AgentBuilderEditor): boolean {
  if (editor.agentId) return isAgentProfileEditorDirty(editor);
  return Boolean(
    editor.name.trim() ||
    editor.description.trim() ||
    editor.welcomeMessage.trim() ||
    editor.starterPrompts.length > 0 ||
    editor.capabilitySummary.trim() ||
    editor.recommendedTasks.length > 0 ||
    editor.supportedInputTypes.length !== 2 ||
    editor.supportedInputTypes[0] !== "text" ||
    editor.supportedInputTypes[1] !== "file" ||
    editor.expectedOutputs.length > 0 ||
    editor.permissionsAndDataAccessNotice.trim() ||
    editor.instructions.trim() ||
    editor.selectedSkills.length > 0 ||
    editor.selectedMcpToolIds.length > 0 ||
    editor.knowledgeSourceIds.length > 0 ||
    editor.retrievalProfileId !== null ||
    editor.avatarRef !== "builtin:agent" ||
    editor.avatarSeed !== "" ||
    editor.avatarAssetId !== null ||
    editor.category !== "general" ||
    editor.visibility !== "tenant" ||
    editor.allowedDepartmentIds.length > 0 ||
    editor.allowedRoles.length > 0 ||
    editor.allowedUserIds.length > 0,
  );
}

/** Find exact current catalog identities without replacing stale server pins. */
export function validateAgentProfileEditor(
  editor: AgentBuilderEditor,
  catalog: AgentBuilderCurrentCatalog,
): AgentBuilderValidationIssue | null {
  if (editor.agentId !== null && (!Number.isInteger(editor.revision) || (editor.revision ?? 0) < 1)) {
    return { code: "profile_revision_missing" };
  }
  if (!editor.name.trim()) return { code: "name_required" };
  if (!editor.instructions.trim()) return { code: "instructions_required" };
  if (editor.selectedSkills.length === 0) return { code: "skill_required" };
  if (editor.selectedSkills.length > 32) return { code: "skill_limit_exceeded" };

  if (!catalog.skillsResolved || !catalog.effectivePermissionsKnown) {
    return { code: "catalog_unavailable" };
  }
  const selectedSkillIds = new Set<string>();
  const selectedSkillsAreCurrent = editor.selectedSkills.every((selection) => {
    if (selectedSkillIds.has(selection.skill_id)) return false;
    selectedSkillIds.add(selection.skill_id);
    return catalog.skills.some(
      (skill) =>
        skill.enabled &&
        skill.name === selection.skill_id &&
        skill.expected_version === selection.expected_version,
    );
  });
  if (!selectedSkillsAreCurrent) return { code: "selected_skill_stale" };

  if (editor.selectedMcpToolIds.length > 0) {
    if (!catalog.mcpToolsResolved) return { code: "catalog_unavailable" };
    const currentIds = new Set(catalog.mcpTools.map((tool) => tool.id));
    const unavailableMcpToolIds = editor.selectedMcpToolIds.filter(
      (toolId, index, selectedIds) =>
        !toolId.trim() ||
        !currentIds.has(toolId) ||
        selectedIds.indexOf(toolId) !== index,
    );
    if (unavailableMcpToolIds.length > 0) {
      return { code: "selected_mcp_tool_unavailable", unavailableMcpToolIds };
    }
  }

  const knowledgeEnabled = editor.knowledgeSourceIds.length > 0;
  if (knowledgeEnabled !== Boolean(editor.retrievalProfileId)) {
    return { code: "knowledge_selection_incomplete" };
  }
  if (editor.knowledgeSourceIds.length > 8) {
    return { code: "knowledge_source_limit_exceeded" };
  }
  if (knowledgeEnabled) {
    if (!catalog.knowledgeResolved) return { code: "catalog_unavailable" };
    if (catalog.knowledgeSelectionResolved === false) {
      return { code: "catalog_unavailable" };
    }
    const currentSources = new Map(
      catalog.knowledgeSources.map((source) => [source.id, source]),
    );
    const verifiedMissing = new Set(catalog.missingKnowledgeSourceIds ?? []);
    const unavailableKnowledgeSourceIds = editor.knowledgeSourceIds.filter(
      (sourceId, index, selectedIds) =>
        !sourceId.trim() ||
        currentSources.get(sourceId)?.available === false ||
        verifiedMissing.has(sourceId) ||
        (!currentSources.has(sourceId) && catalog.knowledgeSelectionResolved !== true) ||
        selectedIds.indexOf(sourceId) !== index,
    );
    if (unavailableKnowledgeSourceIds.length > 0) {
      return {
        code: "selected_knowledge_source_unavailable",
        unavailableKnowledgeSourceIds,
      };
    }
    const incompatibleKnowledgeSourceIds = editor.knowledgeSourceIds.filter((sourceId) => {
      const source = currentSources.get(sourceId);
      return source ? !knowledgeSourceContainsEditorScope(source, editor) : false;
    });
    if (incompatibleKnowledgeSourceIds.length > 0) {
      return {
        code: "knowledge_scope_incompatible",
        unavailableKnowledgeSourceIds: incompatibleKnowledgeSourceIds,
      };
    }
    if (
      !catalog.retrievalProfiles.some(
        (profile) => profile.id === editor.retrievalProfileId && profile.status === "active",
      )
    ) {
      return { code: "retrieval_profile_unavailable" };
    }
  }

  return null;
}

/** Return the first precise reason that a save must remain disabled. */
export function getAgentProfileSaveBlock(
  editor: AgentBuilderEditor | null,
  catalog: AgentBuilderCurrentCatalog,
): AgentBuilderValidationIssue | null {
  if (!editor) return { code: "no_selection" };
  const validation = validateAgentProfileEditor(editor, catalog);
  if (validation) return validation;
  return isAgentProfileEditorDirty(editor) ? null : { code: "no_changes" };
}

/** Return the first precise reason that a publish must remain disabled. */
export function getAgentProfilePublishBlock(
  editor: AgentBuilderEditor | null,
  catalog: AgentBuilderCurrentCatalog,
): AgentBuilderValidationIssue | null {
  if (!editor?.agentId || !editor.revision) return { code: "save_required" };
  if (isAgentProfileEditorDirty(editor)) return { code: "unsaved_changes" };
  if (editor.status !== "draft") return { code: "published_revision" };
  return validateAgentProfileEditor(editor, catalog);
}

/** Materialize the exact optimistic-lock request accepted by agentProfileApi. */
export function buildAgentProfileDraftRequest(
  editor: AgentBuilderEditor,
): AgentProfileDraftRequest {
  if (editor.selectedSkills.length === 0) {
    throw new Error("agent_profile_editor_incomplete");
  }
  return {
    name: editor.name.trim(),
    description: editor.description.trim(),
    welcome_message: editor.welcomeMessage.trim(),
    starter_prompts: editor.starterPrompts.map((item) => item.trim()).filter(Boolean),
    capability_summary: editor.capabilitySummary.trim(),
    recommended_tasks: editor.recommendedTasks.map((item) => item.trim()).filter(Boolean),
    supported_input_types: [...editor.supportedInputTypes],
    expected_outputs: editor.expectedOutputs.map((item) => item.trim()).filter(Boolean),
    permissions_and_data_access_notice: editor.permissionsAndDataAccessNotice.trim(),
    instructions: editor.instructions,
    selected_skill: { ...editor.selectedSkills[0] },
    skill_set: editor.selectedSkills.map((skill) => ({ ...skill })),
    mcp_tool_ids: [...editor.selectedMcpToolIds],
    knowledge_source_ids: [...editor.knowledgeSourceIds],
    retrieval_profile_id: editor.retrievalProfileId,
    avatar_ref: editor.avatarRef,
    avatar_seed: editor.avatarSeed.trim() || editor.name.trim(),
    avatar_asset_id: editor.avatarAssetId,
    category: editor.category,
    visibility: editor.visibility,
    allowed_department_ids: [...editor.allowedDepartmentIds],
    allowed_roles: [...editor.allowedRoles],
    allowed_user_ids: [...editor.allowedUserIds],
    expected_draft_revision: editor.agentId ? (editor.revision ?? 0) : 0,
  };
}

/** Project one validation code to stable Chinese recovery copy. */
export function agentBuilderBlockReason(issue: AgentBuilderValidationIssue): string {
  switch (issue.code) {
    case "no_selection":
      return "请先选择或新建一位专家。";
    case "name_required":
      return "缺少名称，请填写后再保存。";
    case "instructions_required":
      return "缺少 Agent.md 初始指令，请填写后再保存。";
    case "skill_required":
      return "缺少 Skill，请至少选择一个已授权版本。";
    case "skill_limit_exceeded":
      return "一位专家最多可选择 32 个 Skill，请移除多余项。";
    case "profile_revision_missing":
      return "当前专家缺少可用于版本锁定的服务端 revision，请刷新目录。";
    case "catalog_unavailable":
      return "授权目录尚未完整加载，暂不能保存或发布。";
    case "selected_skill_stale":
      return "所选 Skill 或其固定版本已不可用，请重新选择。";
    case "selected_mcp_tool_unavailable":
      return `已选 MCP 工具中有 ${issue.unavailableMcpToolIds?.length ?? 1} 项不可用，请明确移除或重新选择。`;
    case "knowledge_selection_incomplete":
      return "知识库配置不完整，请同时选择知识源与检索策略。";
    case "knowledge_source_limit_exceeded":
      return "一位专家最多可选择 8 个知识源，请移除多余项。";
    case "selected_knowledge_source_unavailable":
      return `已选知识源中有 ${issue.unavailableKnowledgeSourceIds?.length ?? 1} 项已停用、无权访问或不再存在，请明确移除或重新选择。`;
    case "knowledge_scope_incompatible":
      return `专家的可见范围超出 ${issue.unavailableKnowledgeSourceIds?.length ?? 1} 项知识源的部门权限，请收窄专家范围或重新选择知识源。`;
    case "retrieval_profile_unavailable":
      return "所选检索策略已不可用，请重新选择。";
    case "no_changes":
      return "当前内容与服务端版本一致，无需再次保存。";
    case "save_required":
      return "请先成功保存草稿，取得服务端 agent_id 与 revision 后再发布。";
    case "unsaved_changes":
      return "当前有未保存的更改，请先保存草稿后再发布。";
    case "published_revision":
      return "当前 revision 已发布；修改配置并保存为新草稿后才能再次发布。";
  }
}
