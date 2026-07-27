import assert from "node:assert/strict";
import test from "node:test";

import type { ModelOption } from "../../../services/api/modelPublic";
import type { PublicSkillResponse, ToolState } from "../../../types";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
  prepareAgentBuilderSubmission,
  revalidateAgentBuilderDraft,
  type AgentBuilderCurrentCatalog,
  type AgentBuilderDraft,
} from "../agentBuilderAdapter";

function skill(overrides: Partial<PublicSkillResponse> = {}): PublicSkillResponse {
  return {
    name: "document-review",
    expected_version: "2026.07.27",
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

function draft(overrides: Partial<AgentBuilderDraft> = {}): AgentBuilderDraft {
  return {
    message: "Review the current document",
    instructions: "LOCAL ONLY: do not submit this text",
    model,
    selectedSkill: skill(),
    selectedMcpToolIds: ["mcp:knowledge:search"],
    ...overrides,
  };
}

function catalog(overrides: Partial<AgentBuilderCurrentCatalog> = {}): AgentBuilderCurrentCatalog {
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

test("blocks without clearing a revoked, disabled, or version-mismatched Skill selection", () => {
  for (const currentSkill of [
    undefined,
    skill({ enabled: false }),
    skill({ expected_version: "2026.07.28" }),
  ]) {
    const result = revalidateAgentBuilderDraft(
      draft(),
      catalog({ skills: currentSkill ? [currentSkill] : [] }),
    );
    assert.equal(result.code, "selected_skill_stale");
    assert.equal(result.sanitizedDraft.selectedSkill?.expected_version, "2026.07.27");
  }
});

test("blocks a requires-file change until a real attachment handle exists", () => {
  const result = revalidateAgentBuilderDraft(
    draft(),
    catalog({ skills: [skill({ requires_file: true })] }),
  );
  assert.equal(result.code, "selected_skill_stale");
  assert.equal(result.sanitizedDraft.selectedSkill?.requires_file, false);

  const fileRequiredDraft = draft({ selectedSkill: skill({ requires_file: true }) });
  const blocked = prepareAgentBuilderSubmission(
    fileRequiredDraft,
    catalog({ skills: [skill({ requires_file: true })] }),
  );
  assert.equal(blocked.kind, "blocked");
  if (blocked.kind !== "blocked") return;
  assert.equal(blocked.code, "file_attachment_unavailable");
});

test("blocks without removing MCP identities that disappeared from the current catalog", () => {
  const result = revalidateAgentBuilderDraft(
    draft({ selectedMcpToolIds: ["mcp:knowledge:search", "mcp:revoked"] }),
    catalog(),
  );
  assert.equal(result.code, "selected_mcp_tool_unavailable");
  assert.deepEqual(result.sanitizedDraft.selectedMcpToolIds, [
    "mcp:knowledge:search",
    "mcp:revoked",
  ]);
});

test("blocks a removed model or a current model whose transport value changed", () => {
  for (const models of [
    [],
    [{ ...model, value: "platform/model-v2" }],
  ]) {
    const result = revalidateAgentBuilderDraft(draft(), catalog({ models }));
    assert.equal(result.code, "selected_model_stale");
    assert.deepEqual(result.sanitizedDraft.model, model);
  }
});

test("requires the selected model catalog to resolve before admission", () => {
  const prepared = prepareAgentBuilderSubmission(
    draft(),
    catalog({ modelsResolved: false }),
  );
  assert.equal(prepared.kind, "blocked");
  if (prepared.kind !== "blocked") return;
  assert.equal(prepared.code, "catalog_unavailable");
});

test("does not prepare a selected catalog entry while its current authorization is unknown", () => {
  const prepared = prepareAgentBuilderSubmission(
    draft(),
    catalog({ skillsResolved: false }),
  );
  assert.equal(prepared.kind, "blocked");
  if (prepared.kind !== "blocked") return;
  assert.equal(prepared.code, "catalog_unavailable");
});

test("prepares a published Agent selection without forwarding local builder selectors", () => {
  const prepared = prepareAgentBuilderSubmission(
    draft({
      selectedAgentProfile: {
        agent_id: "profile-doc-review",
        expected_revision: 7,
      },
    }),
    catalog({ modelsResolved: false, skillsResolved: false, mcpToolsResolved: false }),
  );
  assert.equal(prepared.kind, "ready");
  if (prepared.kind !== "ready") return;
  assert.deepEqual(prepared.submission, {
    message: "Review the current document",
    agentOptions: {},
    selectedSkill: null,
    selectedMcpToolIds: [],
    selectedAgentProfile: {
      agent_id: "profile-doc-review",
      expected_revision: 7,
    },
  });
});
