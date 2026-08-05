import assert from "node:assert/strict";
import test from "node:test";

import type { ModelOption } from "../../../services/api/modelPublic";
import type {
  AgentProfileAdminProjection,
  PublicSkillResponse,
  ToolState,
} from "../../../types";
import {
  agentBuilderBlockReason,
  buildAgentProfileDraftRequest,
  createUnsavedAgentEditor,
  getAgentProfilePublishBlock,
  getAgentProfileSaveBlock,
  hydrateAgentProfileEditor,
  isAgentProfileEditorDirty,
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
  validateAgentProfileEditor,
  type AgentBuilderCurrentCatalog,
} from "../agentBuilderAdapter";

function skill(overrides: Partial<PublicSkillResponse> = {}): PublicSkillResponse {
  return {
    name: "document-review",
    expected_version: "2026.07.28",
    input_modes: [],
    requires_file: false,
    description: "Review an authorized document.",
    tags: [],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 0,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
    ...overrides,
  };
}

const model: ModelOption = {
  id: "model-id",
  value: "platform/model",
  label: "Platform model",
};

function profile(
  overrides: Partial<AgentProfileAdminProjection> = {},
): AgentProfileAdminProjection {
  return {
    agent_id: "agt_document_review",
    revision: 7,
    status: "draft",
    name: "文档审阅助手",
    description: "审阅授权文档。",
    instructions: "仅使用已授权资料。",
    model_id: model.id,
    selected_skill: {
      skill_id: "document-review",
      expected_version: "2026.07.28",
    },
    mcp_tool_ids: ["mcp:knowledge:search"],
    content_hash: "a".repeat(64),
    ...overrides,
  };
}

function catalog(
  overrides: Partial<AgentBuilderCurrentCatalog> = {},
): AgentBuilderCurrentCatalog {
  return {
    skills: [skill()],
    mcpTools: [
      {
        id: "mcp:knowledge:search",
        label: "Knowledge search",
        description: "Search the authorized knowledge base.",
      },
    ],
    models: [model],
    skillsResolved: true,
    mcpToolsResolved: true,
    modelsResolved: true,
    effectivePermissionsKnown: true,
    ...overrides,
  };
}

test("maps only complete authorized Skill and safe MCP identities", () => {
  assert.deepEqual(
    mapAuthorizedBuilderSkills({
      skills: [skill()],
      catalogReadResolved: true,
      effectivePermissionsKnown: false,
    }),
    [],
  );
  const tools: Array<ToolState & { label?: string }> = [
    {
      name: "mcp:knowledge:search",
      label: "Knowledge search",
      description: "Search the authorized knowledge base.",
      category: "mcp",
      server: "private-server-name",
      parameters: [{ name: "secret", type: "string", description: "private", required: true }],
      enabled: true,
    },
  ];
  assert.deepEqual(mapSafeBuilderMcpTools(tools), [
    {
      id: "mcp:knowledge:search",
      label: "Knowledge search",
      description: "Search the authorized knowledge base.",
    },
  ]);
});

test("hydrates every exact server identity without catalog fallback", () => {
  const serverProfile = profile({
    model_id: "removed-model",
    selected_skill: {
      skill_id: "removed-skill",
      expected_version: "sha256:removed",
    },
    mcp_tool_ids: ["mcp:removed"],
  });
  const editor = hydrateAgentProfileEditor(serverProfile);

  assert.equal(editor.agentId, "agt_document_review");
  assert.equal(editor.revision, 7);
  assert.equal(editor.status, "draft");
  assert.equal(editor.modelId, "removed-model");
  assert.deepEqual(editor.selectedSkill, {
    skill_id: "removed-skill",
    expected_version: "sha256:removed",
  });
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:removed"]);
  assert.equal(isAgentProfileEditorDirty(editor), false);

  serverProfile.selected_skill.skill_id = "mutated-after-hydration";
  serverProfile.mcp_tool_ids.push("mutated-after-hydration");
  assert.equal(editor.selectedSkill?.skill_id, "removed-skill");
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:removed"]);
});

test("materializes create and update requests with the exact optimistic revision", () => {
  const created = {
    ...createUnsavedAgentEditor(),
    name: " 新智能体 ",
    description: " 简介 ",
    instructions: "Keep trailing space. ",
    modelId: model.id,
    selectedSkill: {
      skill_id: "document-review",
      expected_version: "2026.07.28",
    },
    selectedMcpToolIds: ["mcp:knowledge:search"],
  };
  assert.deepEqual(buildAgentProfileDraftRequest(created), {
    name: "新智能体",
    description: "简介",
    instructions: "Keep trailing space. ",
    model_id: "model-id",
    selected_skill: {
      skill_id: "document-review",
      expected_version: "2026.07.28",
    },
    mcp_tool_ids: ["mcp:knowledge:search"],
    expected_draft_revision: 0,
  });

  const existing = hydrateAgentProfileEditor(profile({ revision: 11 }));
  assert.equal(buildAgentProfileDraftRequest(existing).expected_draft_revision, 11);
});

test("reports precise missing data and revision reasons", () => {
  const empty = createUnsavedAgentEditor();
  assert.equal(validateAgentProfileEditor(empty, catalog())?.code, "name_required");
  const withoutInstructions = { ...empty, name: "Agent" };
  assert.equal(
    validateAgentProfileEditor(withoutInstructions, catalog())?.code,
    "instructions_required",
  );
  const withoutModel = { ...withoutInstructions, instructions: "System" };
  assert.equal(validateAgentProfileEditor(withoutModel, catalog())?.code, "model_required");
  const withoutSkill = { ...withoutModel, modelId: model.id };
  assert.equal(validateAgentProfileEditor(withoutSkill, catalog())?.code, "skill_required");
  const withoutRevision = {
    ...hydrateAgentProfileEditor(profile()),
    revision: null,
  };
  assert.equal(
    validateAgentProfileEditor(withoutRevision, catalog())?.code,
    "profile_revision_missing",
  );
  assert.match(
    agentBuilderBlockReason({ code: "profile_revision_missing" }),
    /revision.*刷新/,
  );
});

test("preserves and blocks stale model, Skill version, and MCP identities", () => {
  const editor = hydrateAgentProfileEditor(profile());
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ models: [] }))?.code,
    "selected_model_stale",
  );
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ skills: [skill({ expected_version: "new" })] }))?.code,
    "selected_skill_stale",
  );
  const mcpIssue = validateAgentProfileEditor(editor, catalog({ mcpTools: [] }));
  assert.equal(mcpIssue?.code, "selected_mcp_tool_unavailable");
  assert.deepEqual(mcpIssue?.unavailableMcpToolIds, ["mcp:knowledge:search"]);
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:knowledge:search"]);
  assert.equal(editor.selectedSkill?.expected_version, "2026.07.28");
  assert.equal(editor.modelId, "model-id");
});

test("fails closed while selected catalogs are unresolved", () => {
  const editor = hydrateAgentProfileEditor(profile());
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ modelsResolved: false }))?.code,
    "catalog_unavailable",
  );
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ skillsResolved: false }))?.code,
    "catalog_unavailable",
  );
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ mcpToolsResolved: false }))?.code,
    "catalog_unavailable",
  );
});

test("publish requires one clean successfully saved draft", () => {
  const unsaved = {
    ...createUnsavedAgentEditor(),
    name: "Agent",
    instructions: "System",
    modelId: model.id,
    selectedSkill: {
      skill_id: "document-review",
      expected_version: "2026.07.28",
    },
  };
  assert.equal(getAgentProfilePublishBlock(unsaved, catalog())?.code, "save_required");

  const draft = hydrateAgentProfileEditor(profile());
  assert.equal(getAgentProfileSaveBlock(draft, catalog())?.code, "no_changes");
  assert.equal(getAgentProfilePublishBlock(draft, catalog()), null);

  const dirty = { ...draft, instructions: "Changed instructions" };
  assert.equal(isAgentProfileEditorDirty(dirty), true);
  assert.equal(getAgentProfilePublishBlock(dirty, catalog())?.code, "unsaved_changes");
  assert.equal(getAgentProfileSaveBlock(dirty, catalog()), null);

  const published = hydrateAgentProfileEditor(profile({ status: "published" }));
  assert.equal(getAgentProfilePublishBlock(published, catalog())?.code, "published_revision");
});
