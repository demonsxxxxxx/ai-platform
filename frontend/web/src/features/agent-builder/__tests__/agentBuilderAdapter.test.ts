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
  knowledgeSourceContainsEditorScope,
  validateAgentProfileEditor,
  type AgentBuilderCurrentCatalog,
} from "../agentBuilderAdapter";

test("a pristine unsaved editor does not trigger a discard warning", () => {
  const editor = createUnsavedAgentEditor();
  assert.equal(editor.avatarSeed, "");
  assert.equal(editor.knowledgeEnabled, false);
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
    published_revision: null,
    status: "draft",
    name: "文档审阅助手",
    description: "在授权范围内审阅企业文档。",
    starter_prompts: ["请审阅这份材料"],
    avatar_ref: "builtin:document",
    avatar_seed: "agt-document-review",
    market_tags: ["文档"],
    visibility: "tenant",
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
    instructions: "仅使用已授权资料。",
    skill_set: [{ skill_id: "document-review" }],
    mcp_tool_ids: ["gateway::knowledge.search"],
    knowledge_enabled: false,
    knowledge_source_ids: [],
    retrieval_profile_id: null,
    content_hash: "a".repeat(64),
    created_at: "2026-08-01T00:00:00Z",
    published_at: null,
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
        id: "gateway::knowledge.search",
        label: "Knowledge search",
        description: "Search the authorized knowledge base.",
      },
    ],
    knowledgeSources: [
      {
        id: "ks_finance",
        name: "财务制度",
        description: "已治理的财务制度知识源。",
        authorization_version: 3,
        connection_name: "公司 RAGFlow",
        last_seen_at: "2026-08-30T01:00:00Z",
        available: true,
        source_status: "active",
        connection_status: "active",
        visibility: "enterprise",
        allowed_department_count: 0,
        allowed_department_ids: [],
        allowed_roles: [],
        allowed_user_ids: [],
      },
    ],
    retrievalProfiles: [
      {
        id: "krp_default",
        revision: 1,
        name: "标准检索",
        description: "平台默认确定性检索策略。",
        status: "active",
        content_hash: "b".repeat(64),
      },
    ],
    skillsResolved: true,
    mcpToolsResolved: true,
    knowledgeResolved: true,
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
      name: "gateway::knowledge.search",
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
      id: "gateway::knowledge.search",
      label: "Knowledge search",
      description: "Search the authorized knowledge base.",
    },
  ]);
});

test("hydrates Skill names without catalog fallback", () => {
  const serverProfile = profile({
    skill_set: [{ skill_id: "removed-skill" }],
    mcp_tool_ids: ["gateway::removed"],
  });
  const editor = hydrateAgentProfileEditor(serverProfile);

  assert.equal(editor.agentId, "agt_document_review");
  assert.equal(editor.revision, 7);
  assert.equal(editor.status, "draft");
  assert.deepEqual(editor.selectedSkills, [{
    skill_id: "removed-skill",
  }]);
  assert.deepEqual(editor.selectedMcpToolIds, ["gateway::removed"]);
  assert.equal(isAgentProfileEditorDirty(editor), false);

  serverProfile.skill_set[0].skill_id = "mutated-after-hydration";
  serverProfile.mcp_tool_ids.push("mutated-after-hydration");
  assert.equal(editor.selectedSkills[0]?.skill_id, "removed-skill");
  assert.deepEqual(editor.selectedMcpToolIds, ["gateway::removed"]);
});

test("materializes create and update requests with the exact optimistic revision", () => {
  const created = {
    ...createUnsavedAgentEditor(),
    name: " 新智能体 ",
    description: " 统一说明 ",
    starterPrompts: [" 示例问题 "],
    instructions: "Keep trailing space. ",
    selectedSkills: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
    selectedMcpToolIds: ["gateway::knowledge.search"],
    marketTag: " 客户服务\n人力资源 ",
    allowedDepartmentIds: ["药品注册"],
  };
  assert.deepEqual(buildAgentProfileDraftRequest(created), {
    name: "新智能体",
    description: "统一说明",
    starter_prompts: ["示例问题"],
    instructions: "Keep trailing space. ",
    skill_set: [{
      skill_id: "document-review",
    }],
    mcp_tool_ids: ["gateway::knowledge.search"],
    knowledge_enabled: false,
    knowledge_source_ids: [],
    retrieval_profile_id: null,
    avatar_ref: "builtin:agent",
    avatar_seed: "新智能体",
    market_tags: ["客户服务", "人力资源"],
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

test("requires selected MCP references to remain in the resolved catalog", () => {
  const editor = hydrateAgentProfileEditor(profile({
    mcp_tool_ids: ["gateway::previously-authorized"],
  }));
  assert.deepEqual(
    validateAgentProfileEditor(editor, catalog()),
    {
      code: "selected_mcp_tool_unavailable",
      unavailableMcpToolIds: ["gateway::previously-authorized"],
    },
  );
  assert.equal(
    validateAgentProfileEditor(
      editor,
      catalog({ mcpTools: [], mcpToolsResolved: false }),
    )?.code,
    "catalog_unavailable",
  );
  assert.deepEqual(editor.selectedMcpToolIds, ["gateway::previously-authorized"]);
  assert.deepEqual(editor.selectedSkills, [{ skill_id: "document-review" }]);
});

test("persists only the Skill Set name references", () => {
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
  assert.deepEqual(request.skill_set, [
    { skill_id: "document-review" },
    { skill_id: "fact-extraction" },
  ]);
});

test("rejects more than 32 Skills and duplicate Skill names", () => {
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

test("fails closed while the selected Skill catalog is unresolved", () => {
  const editor = hydrateAgentProfileEditor(profile());
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ skillsResolved: false }))?.code,
    "catalog_unavailable",
  );
});

test("supports zero or up to eight governed knowledge sources and retains stale pins", () => {
  const base = hydrateAgentProfileEditor(profile());
  assert.equal(
    validateAgentProfileEditor(base, catalog({ knowledgeResolved: false })),
    null,
    "knowledge-free drafts do not depend on the optional Knowledge catalog",
  );

  const eightSources = Array.from({ length: 8 }, (_, index) => ({
    id: `ks_${index}`,
    name: `知识源 ${index}`,
    description: "",
    authorization_version: 1,
    connection_name: "公司 RAGFlow",
    last_seen_at: null,
    available: true,
    source_status: "active" as const,
    connection_status: "active" as const,
    visibility: "enterprise" as const,
    allowed_department_count: 0,
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
  }));
  const selected = {
    ...base,
    knowledgeEnabled: true,
    knowledgeSourceIds: eightSources.map((source) => source.id),
    retrievalProfileId: "krp_default",
  };
  assert.equal(
    validateAgentProfileEditor(selected, catalog({ knowledgeSources: eightSources })),
    null,
  );
  assert.equal(
    validateAgentProfileEditor(
      { ...selected, knowledgeSourceIds: [...selected.knowledgeSourceIds, "ks_8"] },
      catalog({ knowledgeSources: eightSources }),
    )?.code,
    "knowledge_source_limit_exceeded",
  );

  const stale = {
    ...base,
    knowledgeEnabled: true,
    knowledgeSourceIds: ["ks_removed"],
    retrievalProfileId: "krp_default",
  };
  const issue = validateAgentProfileEditor(stale, catalog());
  assert.equal(issue?.code, "selected_knowledge_source_unavailable");
  assert.deepEqual(issue?.unavailableKnowledgeSourceIds, ["ks_removed"]);
  assert.deepEqual(stale.knowledgeSourceIds, ["ks_removed"]);
});

test("blocks a Knowledge source whose department scope cannot contain the Agent", () => {
  const base = hydrateAgentProfileEditor(
    profile({
      visibility: "restricted",
      allowed_department_ids: ["finance", "legal"],
      knowledge_enabled: true,
      knowledge_source_ids: ["ks_finance"],
      retrieval_profile_id: "krp_default",
    }),
  );
  const restrictedSource = {
    ...catalog().knowledgeSources[0]!,
    visibility: "restricted" as const,
    allowed_department_count: 1,
    allowed_department_ids: ["finance"],
  };

  assert.equal(knowledgeSourceContainsEditorScope(restrictedSource, base), false);
  const issue = validateAgentProfileEditor(
    base,
    catalog({ knowledgeSources: [restrictedSource] }),
  );
  assert.equal(issue?.code, "knowledge_scope_incompatible");
  assert.deepEqual(issue?.unavailableKnowledgeSourceIds, ["ks_finance"]);

  const narrowed = { ...base, allowedDepartmentIds: ["finance"] };
  assert.equal(knowledgeSourceContainsEditorScope(restrictedSource, narrowed), true);
  assert.equal(
    validateAgentProfileEditor(
      narrowed,
      catalog({ knowledgeSources: [restrictedSource] }),
    ),
    null,
  );
});

test("requires a server-listed retrieval profile for a Knowledge binding", () => {
  const editor = {
    ...hydrateAgentProfileEditor(profile()),
    knowledgeEnabled: true,
    knowledgeSourceIds: ["ks_finance"],
    retrievalProfileId: "krp_removed",
  };
  assert.equal(
    validateAgentProfileEditor(editor, catalog())?.code,
    "retrieval_profile_unavailable",
  );
  const request = buildAgentProfileDraftRequest({
    ...editor,
    retrievalProfileId: "krp_default",
  });
  assert.equal(request.knowledge_enabled, true);
  assert.deepEqual(request.knowledge_source_ids, ["ks_finance"]);
});

test("disabled enterprise Knowledge retains configuration without requiring its catalog", () => {
  const editor = hydrateAgentProfileEditor(profile({
    knowledge_enabled: false,
    knowledge_source_ids: ["ks_retained"],
    retrieval_profile_id: "krp_default",
  }));

  assert.equal(editor.knowledgeEnabled, false);
  assert.equal(
    validateAgentProfileEditor(editor, catalog({ knowledgeResolved: false })),
    null,
  );
  assert.equal(buildAgentProfileDraftRequest(editor).knowledge_enabled, false);
  assert.deepEqual(buildAgentProfileDraftRequest(editor).knowledge_source_ids, ["ks_retained"]);
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
