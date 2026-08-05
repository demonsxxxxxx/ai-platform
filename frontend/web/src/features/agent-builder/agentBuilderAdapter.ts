import type { ModelOption } from "../../services/api/modelPublic";
import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
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
  models: readonly ModelOption[];
  skillsResolved: boolean;
  mcpToolsResolved: boolean;
  modelsResolved: boolean;
  effectivePermissionsKnown: boolean;
}

export interface AgentBuilderEditor {
  agentId: string | null;
  revision: number | null;
  status: AgentProfileAdminProjection["status"] | null;
  name: string;
  description: string;
  instructions: string;
  modelId: string;
  selectedSkill: SelectedSkillRequest | null;
  selectedMcpToolIds: string[];
  materializedProfile: AgentProfileAdminProjection | null;
}

export type AgentBuilderBlockCode =
  | "no_selection"
  | "name_required"
  | "instructions_required"
  | "model_required"
  | "skill_required"
  | "profile_revision_missing"
  | "catalog_unavailable"
  | "selected_model_stale"
  | "selected_skill_stale"
  | "selected_mcp_tool_unavailable"
  | "no_changes"
  | "save_required"
  | "unsaved_changes"
  | "published_revision";

export interface AgentBuilderValidationIssue {
  code: AgentBuilderBlockCode;
  unavailableMcpToolIds?: readonly string[];
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
    status: null,
    name: "",
    description: "",
    instructions: "",
    modelId: "",
    selectedSkill: null,
    selectedMcpToolIds: [],
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
    status: profile.status,
    name: profile.name,
    description: profile.description,
    instructions: profile.instructions,
    modelId: profile.model_id,
    selectedSkill: { ...profile.selected_skill },
    selectedMcpToolIds: [...profile.mcp_tool_ids],
    materializedProfile: {
      ...profile,
      selected_skill: { ...profile.selected_skill },
      mcp_tool_ids: [...profile.mcp_tool_ids],
    },
  };
}

function editorDefinition(editor: AgentBuilderEditor) {
  return {
    name: editor.name.trim(),
    description: editor.description.trim(),
    instructions: editor.instructions,
    model_id: editor.modelId,
    selected_skill: editor.selectedSkill,
    mcp_tool_ids: editor.selectedMcpToolIds,
  };
}

function profileDefinition(profile: AgentProfileAdminProjection) {
  return {
    name: profile.name,
    description: profile.description,
    instructions: profile.instructions,
    model_id: profile.model_id,
    selected_skill: profile.selected_skill,
    mcp_tool_ids: profile.mcp_tool_ids,
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
    editor.instructions.trim() ||
    editor.modelId.trim() ||
    editor.selectedSkill ||
    editor.selectedMcpToolIds.length > 0,
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
  if (!editor.modelId.trim()) return { code: "model_required" };
  if (!editor.selectedSkill) return { code: "skill_required" };

  if (!catalog.modelsResolved) return { code: "catalog_unavailable" };
  if (!catalog.models.some((model) => model.id === editor.modelId)) {
    return { code: "selected_model_stale" };
  }

  if (!catalog.skillsResolved || !catalog.effectivePermissionsKnown) {
    return { code: "catalog_unavailable" };
  }
  const selectedSkillIsCurrent = catalog.skills.some(
    (skill) =>
      skill.enabled &&
      skill.name === editor.selectedSkill?.skill_id &&
      skill.expected_version === editor.selectedSkill.expected_version,
  );
  if (!selectedSkillIsCurrent) return { code: "selected_skill_stale" };

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
  if (!editor.selectedSkill) {
    throw new Error("agent_profile_editor_incomplete");
  }
  return {
    name: editor.name.trim(),
    description: editor.description.trim(),
    instructions: editor.instructions,
    model_id: editor.modelId,
    selected_skill: { ...editor.selectedSkill },
    mcp_tool_ids: [...editor.selectedMcpToolIds],
    expected_draft_revision: editor.agentId ? (editor.revision ?? 0) : 0,
  };
}

/** Project one validation code to stable Chinese recovery copy. */
export function agentBuilderBlockReason(issue: AgentBuilderValidationIssue): string {
  switch (issue.code) {
    case "no_selection":
      return "请先选择或新建一个智能体。";
    case "name_required":
      return "缺少名称，请填写后再保存。";
    case "instructions_required":
      return "缺少系统说明，请填写后再保存。";
    case "model_required":
      return "缺少模型，请选择当前可用模型。";
    case "skill_required":
      return "缺少 Skill，请选择一个已授权版本。";
    case "profile_revision_missing":
      return "当前智能体缺少可用于版本锁定的服务端 revision，请刷新列表。";
    case "catalog_unavailable":
      return "授权目录尚未完整加载，暂不能保存或发布。";
    case "selected_model_stale":
      return "所选模型已不在当前目录中，请重新选择。";
    case "selected_skill_stale":
      return "所选 Skill 或其固定版本已不可用，请重新选择。";
    case "selected_mcp_tool_unavailable":
      return `已选 MCP 工具中有 ${issue.unavailableMcpToolIds?.length ?? 1} 项不可用，请明确移除或重新选择。`;
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
