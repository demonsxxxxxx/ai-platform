import assert from "node:assert/strict";
import test from "node:test";

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
  hasUnsavedAgentProfileEdits,
  hydrateAgentProfileEditor,
  isAgentProfileEditorDirty,
  listPublishedAgentProfileVersions,
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
  validateAgentProfileEditor,
  type AgentBuilderCurrentCatalog,
} from "../agentBuilderAdapter";

test("a pristine unsaved editor does not trigger a discard warning", () => {
  const editor = createUnsavedAgentEditor();
  assert.equal(editor.avatarSeed, "");
  assert.equal(hasUnsavedAgentProfileEdits(editor), false);
  assert.equal(hasUnsavedAgentProfileEdits({ ...editor, marketTag: "客户服务" }), true);
});

test("counts published snapshots as release versions and ignores draft saves", () => {
  const versions = listPublishedAgentProfileVersions([
    profile({ revision: 4, status: "draft", created_at: "2026-08-01T00:00:00Z" }),
    profile({ revision: 5, status: "published", published_at: "2026-08-02T00:00:00Z" }),
    profile({ revision: 6, status: "draft", created_at: "2026-08-03T00:00:00Z" }),
    profile({ revision: 7, status: "draft", published_at: "2026-08-04T00:00:00Z" }),
  ]);

  assert.deepEqual(
    versions.map(({ profile: publishedProfile, version }) => [publishedProfile.revision, version]),
    [[5, 1], [7, 2]],
  );
});
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

function profile(
  overrides: Partial<AgentProfileAdminProjection> = {},
): AgentProfileAdminProjection {
  return {
    agent_id: "agt_document_review",
    revision: 7,
    status: "draft",
    name: "文档审阅助手",
    description: "审阅授权文档。",
    welcome_message: "欢迎使用企业专家。",
    starter_prompts: ["请审阅这份材料"],
    capability_summary: "在授权范围内审阅企业文档。",
    recommended_tasks: ["文档审阅"],
    supported_input_types: ["text", "file"],
    expected_outputs: ["审阅意见"],
    permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
    avatar_ref: "builtin:document",
    avatar_asset_id: null,
    category: "operations",
    visibility: "tenant",
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
    instructions: "仅使用已授权资料。",
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
    skillsResolved: true,
    mcpToolsResolved: true,
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
  assert.deepEqual(editor.selectedSkills, [{
    skill_id: "removed-skill",
    expected_version: "sha256:removed",
  }]);
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:removed"]);
  assert.equal(isAgentProfileEditorDirty(editor), false);

  serverProfile.selected_skill.skill_id = "mutated-after-hydration";
  serverProfile.mcp_tool_ids.push("mutated-after-hydration");
  assert.equal(editor.selectedSkills[0]?.skill_id, "removed-skill");
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:removed"]);
});

test("materializes create and update requests with the exact optimistic revision", () => {
  const created = {
    ...createUnsavedAgentEditor(),
    name: " 新智能体 ",
    description: " 简介 ",
    welcomeMessage: " 欢迎使用 ",
    starterPrompts: [" 示例问题 "],
    capabilitySummary: " 企业能力 ",
    recommendedTasks: [" 推荐任务 "],
    expectedOutputs: [" 审阅意见 "],
    permissionsAndDataAccessNotice: " 仅访问授权数据 ",
    instructions: "Keep trailing space. ",
    selectedSkills: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
    selectedMcpToolIds: ["mcp:knowledge:search"],
    marketTag: " 客户服务 ",
    allowedDepartmentIds: ["药品注册"],
  };
  assert.deepEqual(buildAgentProfileDraftRequest(created), {
    name: "新智能体",
    description: "简介",
    welcome_message: "欢迎使用",
    starter_prompts: ["示例问题"],
    capability_summary: "企业能力",
    recommended_tasks: ["推荐任务"],
    supported_input_types: ["text", "file"],
    expected_outputs: ["审阅意见"],
    permissions_and_data_access_notice: "仅访问授权数据",
    instructions: "Keep trailing space. ",
    selected_skill: {
      skill_id: "document-review",
      expected_version: "2026.07.28",
    },
    skill_set: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
    mcp_tool_ids: ["mcp:knowledge:search"],
    avatar_ref: "builtin:agent",
    avatar_seed: "新智能体",
    avatar_asset_id: null,
    category: "general",
    market_tag: "客户服务",
    visibility: "tenant",
    allowed_department_ids: ["药品注册"],
    allowed_roles: [],
    allowed_user_ids: [],
    expected_draft_revision: 0,
  });

  const existing = hydrateAgentProfileEditor(profile({ revision: 11 }));
  assert.equal(buildAgentProfileDraftRequest(existing).expected_draft_revision, 11);
});

test("reports precise missing data and revision reasons", () => {
  const empty = createUnsavedAgentEditor();
  assert.equal(validateAgentProfileEditor(empty, catalog())?.code, "name_required");
  const withoutInstructions = {
    ...empty,
    name: "Agent",
    capabilitySummary: "Enterprise capability",
    recommendedTasks: ["Review"],
    expectedOutputs: ["Decision"],
    permissionsAndDataAccessNotice: "Authorized data only",
  };
  assert.equal(
    validateAgentProfileEditor(withoutInstructions, catalog())?.code,
    "instructions_required",
  );
  const withoutSkill = { ...withoutInstructions, instructions: "System" };
  assert.equal(validateAgentProfileEditor(withoutSkill, catalog())?.code, "skill_required");
  const coreOnly = {
    ...withoutSkill,
    selectedSkills: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
  };
  assert.equal(
    validateAgentProfileEditor(coreOnly, catalog()),
    null,
    "name, Agent.md, and one Skill are sufficient to save",
  );
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

test("preserves and blocks stale Skill version and MCP identities", () => {
  const editor = hydrateAgentProfileEditor(profile());
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ skills: [skill({ expected_version: "new" })] }))?.code,
    "selected_skill_stale",
  );
  const mcpIssue = validateAgentProfileEditor(editor, catalog({ mcpTools: [] }));
  assert.equal(mcpIssue?.code, "selected_mcp_tool_unavailable");
  assert.deepEqual(mcpIssue?.unavailableMcpToolIds, ["mcp:knowledge:search"]);
  assert.deepEqual(editor.selectedMcpToolIds, ["mcp:knowledge:search"]);
  assert.equal(editor.selectedSkills[0]?.expected_version, "2026.07.28");
});

test("persists an exact multi-Skill set while keeping the primary compatibility shadow", () => {
  const secondSkill = skill({ name: "fact-extraction", expected_version: "sha256:facts" });
  const editor = {
    ...hydrateAgentProfileEditor(profile()),
    selectedSkills: [
      { skill_id: "document-review", expected_version: "2026.07.28" },
      { skill_id: "fact-extraction", expected_version: "sha256:facts" },
    ],
  };

  assert.equal(
    validateAgentProfileEditor(editor, catalog({ skills: [skill(), secondSkill] })),
    null,
  );
  const request = buildAgentProfileDraftRequest(editor);
  assert.deepEqual(request.selected_skill, request.skill_set[0]);
  assert.deepEqual(request.skill_set, editor.selectedSkills);
});

test("rejects more than 32 Skills and duplicate Skill identities across versions", () => {
  const base = hydrateAgentProfileEditor(profile());
  const tooMany = {
    ...base,
    selectedSkills: Array.from({ length: 33 }, (_, index) => ({
      skill_id: `skill-${index}`,
      expected_version: `version-${index}`,
    })),
  };
  assert.equal(validateAgentProfileEditor(tooMany, catalog())?.code, "skill_limit_exceeded");

  const duplicateIdentity = {
    ...base,
    selectedSkills: [
      { skill_id: "document-review", expected_version: "2026.07.28" },
      { skill_id: "document-review", expected_version: "2026.08.01" },
    ],
  };
  assert.equal(
    validateAgentProfileEditor(duplicateIdentity, catalog())?.code,
    "selected_skill_stale",
  );
});

test("fails closed while selected catalogs are unresolved", () => {
  const editor = hydrateAgentProfileEditor(profile());
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
    selectedSkills: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
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
