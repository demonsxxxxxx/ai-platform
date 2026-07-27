import type { ModelOption } from "../../services/api/modelPublic";
import type {
  PublicSkillResponse,
  SelectedAgentProfileRequest,
  SelectedSkillRequest,
  ToolState,
} from "../../types";

export interface AgentBuilderSafeMcpTool {
  id: string;
  label: string;
  description: string;
}

export interface AgentBuilderDraft {
  message: string;
  description?: string;
  /** Server-owned after an administrator saves this local draft. */
  instructions: string;
  model: ModelOption | null;
  selectedSkill: PublicSkillResponse | null;
  selectedMcpToolIds: string[];
  agentId?: string;
  draftRevision?: number;
  selectedAgentProfile?: SelectedAgentProfileRequest | null;
}

export interface AgentBuilderSubmission {
  message: string;
  agentOptions: Record<string, boolean | string | number>;
  selectedSkill: SelectedSkillRequest | null;
  selectedMcpToolIds: string[];
  selectedAgentProfile: SelectedAgentProfileRequest | null;
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

export type AgentBuilderSubmissionBlockCode =
  | "message_required"
  | "file_attachment_unavailable"
  | "catalog_unavailable"
  | "selected_skill_stale"
  | "selected_mcp_tool_unavailable"
  | "selected_model_stale";

export type AgentBuilderSubmissionPreparation =
  | { kind: "ready"; submission: AgentBuilderSubmission }
  | {
      kind: "blocked";
      code: AgentBuilderSubmissionBlockCode;
      sanitizedDraft: AgentBuilderDraft;
    };

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

/** Keep MCP browser presentation limited to the chat catalog's safe identity. */
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

/**
 * Resolve a local draft against the current public catalogs. Stale identities
 * remain visible in the local draft so a refresh can never silently turn a
 * governed submission into a less-specific one. Only an explicit re-selection
 * may replace them.
 */
export function revalidateAgentBuilderDraft(
  draft: AgentBuilderDraft,
  catalog: AgentBuilderCurrentCatalog,
): { sanitizedDraft: AgentBuilderDraft; code: AgentBuilderSubmissionBlockCode | null } {
  let selectedSkill = draft.selectedSkill;
  let selectedMcpToolIds = Array.from(
    new Set(draft.selectedMcpToolIds.filter((toolId) => toolId.trim().length > 0)),
  );

  if (selectedSkill) {
    const requestedSkill = selectedSkill;
    if (!catalog.skillsResolved || !catalog.effectivePermissionsKnown) {
      return {
        sanitizedDraft: { ...draft, selectedMcpToolIds },
        code: "catalog_unavailable",
      };
    }

    const currentSkill = mapAuthorizedBuilderSkills({
      skills: catalog.skills,
      catalogReadResolved: catalog.skillsResolved,
      effectivePermissionsKnown: catalog.effectivePermissionsKnown,
    }).find((skill) => skill.name === requestedSkill.name);
    const selectedSkillMatchesCurrent =
      currentSkill?.expected_version === requestedSkill.expected_version &&
      currentSkill.requires_file === requestedSkill.requires_file;
    if (!selectedSkillMatchesCurrent) {
      return {
        sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
        code: "selected_skill_stale",
      };
    }
  }

  if (selectedMcpToolIds.length > 0) {
    if (!catalog.mcpToolsResolved) {
      return {
        sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
        code: "catalog_unavailable",
      };
    }

    const authorizedToolIds = new Set(catalog.mcpTools.map((tool) => tool.id));
    const unavailableToolIds = selectedMcpToolIds.filter(
      (toolId) => !authorizedToolIds.has(toolId),
    );
    if (unavailableToolIds.length > 0) {
      return {
        sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
        code: "selected_mcp_tool_unavailable",
      };
    }
  }

  if (draft.model) {
    if (!catalog.modelsResolved) {
      return {
        sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
        code: "catalog_unavailable",
      };
    }
    const selectedModelMatchesCurrent = catalog.models.some(
      (model) =>
        model.id === draft.model?.id && model.value === draft.model.value,
    );
    if (!selectedModelMatchesCurrent) {
      return {
        sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
        code: "selected_model_stale",
      };
    }
  }

  if (selectedSkill?.requires_file) {
    return {
      sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
      code: "file_attachment_unavailable",
    };
  }

  return {
    sanitizedDraft: { ...draft, selectedSkill, selectedMcpToolIds },
    code: null,
  };
}

/** Materialize only the fields the existing Chat submission seam accepts. */
export function prepareAgentBuilderSubmission(
  draft: AgentBuilderDraft,
  catalog: AgentBuilderCurrentCatalog,
): AgentBuilderSubmissionPreparation {
  const message = draft.message.trim();
  if (!message) {
    return { kind: "blocked", code: "message_required", sanitizedDraft: draft };
  }

  // A published profile is a complete, server-owned execution selection.
  // Do not inspect unrelated local-builder fields before handing it off: those
  // fields are deliberately omitted from the Chat request by this adapter.
  if (draft.selectedAgentProfile) {
    return {
      kind: "ready",
      submission: {
        message,
        agentOptions: {},
        selectedSkill: null,
        selectedMcpToolIds: [],
        selectedAgentProfile: draft.selectedAgentProfile,
      },
    };
  }

  const revalidated = revalidateAgentBuilderDraft(draft, catalog);
  if (revalidated.code !== null) {
    return {
      kind: "blocked",
      code: revalidated.code,
      sanitizedDraft: revalidated.sanitizedDraft,
    };
  }
  const sanitizedDraft = revalidated.sanitizedDraft;

  return {
    kind: "ready",
    submission: {
      message,
      agentOptions: sanitizedDraft.model ? { model_id: sanitizedDraft.model.id } : {},
      selectedSkill: sanitizedDraft.selectedSkill
        ? {
            skill_id: sanitizedDraft.selectedSkill.name,
            expected_version: sanitizedDraft.selectedSkill.expected_version,
          }
        : null,
      selectedMcpToolIds: sanitizedDraft.selectedMcpToolIds,
      selectedAgentProfile: null,
    },
  };
}
