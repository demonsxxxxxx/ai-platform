import assert from "node:assert/strict";
import test from "node:test";

import { ApiRequestError } from "../../../services/api/fetch";
import type { ModelOption } from "../../../services/api/modelPublic";
import type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  AgentProfileMutationResponse,
  PublicSkillResponse,
} from "../../../types";
import {
  getAgentProfilePublishBlock,
  type AgentBuilderCurrentCatalog,
} from "../agentBuilderAdapter";
import {
  AgentBuilderController,
  projectAgentBuilderError,
  type AgentBuilderProfileApi,
} from "../agentBuilderController";

const model: ModelOption = {
  id: "model-id",
  value: "platform/model",
  label: "Platform model",
};

function skill(): PublicSkillResponse {
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

function fakeApi(overrides: Partial<AgentBuilderProfileApi> = {}): AgentBuilderProfileApi {
  return {
    listAdmin: async () => ({ agent_profiles: [] }),
    saveDraft: async () => ({ agent_profile: profile(), audit_id: "audit-save" }),
    publish: async () => ({
      agent_profile: profile({ revision: 8, status: "published" }),
      audit_id: "audit-publish",
    }),
    ...overrides,
  };
}

test("initial load exposes loading then hydrates exact server profiles", async () => {
  let resolveList: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: () => new Promise((resolve) => {
      resolveList = resolve;
    }),
  }));

  const pending = controller.loadProfiles();
  assert.equal(controller.state.listPhase, "loading");
  assert.equal(controller.state.activeEditor, null);
  resolveList?.({ agent_profiles: [profile()] });
  const loaded = await pending;

  assert.equal(loaded.listPhase, "ready");
  assert.equal(loaded.profiles.length, 1);
  assert.deepEqual(loaded.activeEditor?.selectedSkills, [profile().selected_skill]);
  assert.deepEqual(loaded.activeEditor?.selectedMcpToolIds, profile().mcp_tool_ids);
  assert.equal(loaded.activeEditor?.revision, 7);
});

test("list load represents empty and safe error states", async () => {
  const empty = new AgentBuilderController(fakeApi());
  await empty.loadProfiles();
  assert.equal(empty.state.listPhase, "ready");
  assert.deepEqual(empty.state.profiles, []);
  assert.equal(empty.state.activeEditor, null);

  const failed = new AgentBuilderController(fakeApi({
    listAdmin: async () => {
      throw new ApiRequestError("raw backend payload", 403, "not_ai_admin");
    },
  }));
  await failed.loadProfiles();
  assert.equal(failed.state.listPhase, "error");
  assert.match(failed.state.listError?.message ?? "", /HTTP 403/);
  assert.match(failed.state.listError?.message ?? "", /not_ai_admin/);
  assert.doesNotMatch(failed.state.listError?.message ?? "", /raw backend payload/);
});

test("refresh reopens the selected server identity at its returned revision", async () => {
  const responses = [
    [profile(), profile({ agent_id: "agt_other", name: "其他助手" })],
    [profile({ revision: 9, status: "published", name: "已更新助手" })],
  ];
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: responses.shift() ?? [] }),
  }));

  await controller.loadProfiles();
  controller.selectProfile("agt_document_review", true);
  await controller.loadProfiles();

  assert.equal(controller.state.activeEditor?.agentId, "agt_document_review");
  assert.equal(controller.state.activeEditor?.revision, 9);
  assert.equal(controller.state.activeEditor?.status, "published");
  assert.equal(controller.state.activeEditor?.name, "已更新助手");
});

test("New Agent creates one unsaved form and existing selection hydrates exactly", async () => {
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: [profile()] }),
  }));
  await controller.loadProfiles();

  controller.createNewAgent();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "本地表单" }));
  controller.selectProfile("agt_document_review", true);
  assert.equal(controller.state.activeEditor?.instructions, "仅使用已授权资料。");
  assert.equal(controller.state.localEditor, null);
  controller.createNewAgent();

  assert.equal(controller.state.localEditor, controller.state.activeEditor);
  assert.equal(controller.state.activeEditor?.name, "");
  assert.equal(controller.state.activeEditor?.agentId, null);
});

test("dirty editors require explicit discard before selecting or creating", async () => {
  const other = profile({ agent_id: "agt_other", name: "其他助手" });
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: [profile(), other] }),
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "未保存名称" }));

  controller.selectProfile(other.agent_id);
  assert.equal(controller.state.activeEditor?.agentId, "agt_document_review");
  assert.equal(controller.state.activeEditor?.name, "未保存名称");
  controller.createNewAgent();
  assert.equal(controller.state.activeEditor?.agentId, "agt_document_review");

  controller.selectProfile(other.agent_id, true);
  assert.equal(controller.state.activeEditor?.agentId, other.agent_id);
  controller.updateActiveEditor((editor) => ({ ...editor, description: "未保存简介" }));
  controller.createNewAgent();
  assert.equal(controller.state.activeEditor?.agentId, other.agent_id);
  controller.createNewAgent(true);
  assert.equal(controller.state.activeEditor?.agentId, null);
});

test("successful create materializes server identity and enables publish", async () => {
  const saveCalls: Array<{ draft: AgentProfileDraftRequest; agentId?: string }> = [];
  const saved = profile({ revision: 1, name: "新智能体" });
  const controller = new AgentBuilderController(fakeApi({
    saveDraft: async (draft, agentId) => {
      saveCalls.push({ draft, agentId });
      return { agent_profile: saved, audit_id: "audit-save" };
    },
  }));
  controller.createNewAgent();
  controller.updateActiveEditor((editor) => ({
    ...editor,
    name: "新智能体",
    capabilitySummary: "在授权范围内处理企业任务。",
    recommendedTasks: ["企业任务处理"],
    expectedOutputs: ["处理建议"],
    permissionsAndDataAccessNotice: "仅访问当前用户授权的数据。",
    instructions: "服务端说明",
    modelId: model.id,
    selectedSkills: [{
      skill_id: "document-review",
      expected_version: "2026.07.28",
    }],
    selectedMcpToolIds: ["mcp:knowledge:search"],
  }));

  await controller.saveActiveProfile(catalog());

  assert.equal(saveCalls.length, 1);
  assert.equal(saveCalls[0].agentId, undefined);
  assert.equal(saveCalls[0].draft.expected_draft_revision, 0);
  assert.equal(controller.state.activeEditor?.agentId, "agt_document_review");
  assert.equal(controller.state.activeEditor?.revision, 1);
  assert.equal(controller.state.localEditor, null);
  assert.equal(getAgentProfilePublishBlock(controller.state.activeEditor, catalog()), null);
});

test("edit disables publish, save fences the exact revision, then publish adopts its response", async () => {
  const saveCalls: Array<{ draft: AgentProfileDraftRequest; agentId?: string }> = [];
  const publishCalls: Array<{ agentId: string; revision: number }> = [];
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: [profile()] }),
    saveDraft: async (draft, agentId) => {
      saveCalls.push({ draft, agentId });
      return {
        agent_profile: profile({
          revision: 8,
          status: "draft",
          instructions: draft.instructions,
        }),
        audit_id: "audit-save",
      };
    },
    publish: async (agentId, revision) => {
      publishCalls.push({ agentId, revision });
      return {
        agent_profile: profile({
          revision: 9,
          status: "published",
          instructions: "更新后的说明",
        }),
        audit_id: "audit-publish",
      };
    },
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, instructions: "更新后的说明" }));
  assert.equal(
    getAgentProfilePublishBlock(controller.state.activeEditor, catalog())?.code,
    "unsaved_changes",
  );

  await controller.saveActiveProfile(catalog());
  assert.deepEqual(saveCalls, [{
    agentId: "agt_document_review",
    draft: {
      name: "文档审阅助手",
      description: "审阅授权文档。",
      welcome_message: "欢迎使用企业专家。",
      starter_prompts: ["请审阅这份材料"],
      capability_summary: "在授权范围内审阅企业文档。",
      recommended_tasks: ["文档审阅"],
      supported_input_types: ["text", "file"],
      expected_outputs: ["审阅意见"],
      permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
      instructions: "更新后的说明",
      model_id: "model-id",
      selected_skill: {
        skill_id: "document-review",
        expected_version: "2026.07.28",
      },
      skill_set: [{
        skill_id: "document-review",
        expected_version: "2026.07.28",
      }],
      mcp_tool_ids: ["mcp:knowledge:search"],
      avatar_ref: "builtin:document",
      avatar_seed: "agt_document_review",
      avatar_asset_id: null,
      category: "operations",
      visibility: "tenant",
      allowed_department_ids: [],
      allowed_roles: [],
      allowed_user_ids: [],
      expected_draft_revision: 7,
    },
  }]);
  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(getAgentProfilePublishBlock(controller.state.activeEditor, catalog()), null);

  await controller.publishActiveProfile(catalog());
  assert.deepEqual(publishCalls, [{ agentId: "agt_document_review", revision: 8 }]);
  assert.equal(controller.state.activeEditor?.revision, 9);
  assert.equal(controller.state.activeEditor?.status, "published");
  assert.equal(
    getAgentProfilePublishBlock(controller.state.activeEditor, catalog())?.code,
    "published_revision",
  );
});

test("catalog drift fails closed before save or publish calls", async () => {
  let saves = 0;
  let publishes = 0;
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: [profile()] }),
    saveDraft: async () => {
      saves += 1;
      return { agent_profile: profile(), audit_id: "audit" };
    },
    publish: async () => {
      publishes += 1;
      return { agent_profile: profile(), audit_id: "audit" };
    },
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "Changed" }));
  await controller.saveActiveProfile(catalog({ skills: [] }));
  assert.equal(saves, 0);
  assert.match(
    controller.state.mutation.phase === "error"
      ? controller.state.mutation.error.message
      : "",
    /Skill/,
  );

  await controller.loadProfiles();
  await controller.publishActiveProfile(catalog({ mcpTools: [] }));
  assert.equal(publishes, 0);
});

test("safe save errors expose typed status and code but never raw messages", async () => {
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({ agent_profiles: [profile()] }),
    saveDraft: async () => {
      throw new ApiRequestError(
        "database row and private payload",
        409,
        "agent_profile_revision_stale",
      );
    },
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "Changed" }));
  await controller.saveActiveProfile(catalog());
  const message = controller.state.mutation.phase === "error"
    ? controller.state.mutation.error.message
    : "";
  assert.match(message, /HTTP 409/);
  assert.match(message, /agent_profile_revision_stale/);
  assert.doesNotMatch(message, /database row|private payload/);

  const unknown = projectAgentBuilderError("save", new Error("secret backend copy"));
  assert.doesNotMatch(unknown.message, /secret backend copy/);
  assert.equal(unknown.status, undefined);
  assert.equal(unknown.code, undefined);
});

test("revision integrity errors use action-neutral copy for publish and unpublish", () => {
  for (const action of ["publish", "unpublish"] as const) {
    const projected = projectAgentBuilderError(
      action,
      new ApiRequestError(
        "private integrity detail",
        409,
        "agent_profile_revision_integrity_mismatch",
      ),
    );
    assert.match(projected.message, /当前操作/);
    assert.match(projected.message, /重新保存为新版本/);
    assert.doesNotMatch(projected.message, /阻止发布|阻止下架|private integrity detail/);
  }
});

test("revision conflict recovery discards edits only after an explicit successful reload", async () => {
  let listCalls = 0;
  const latest = profile({ revision: 8, name: "服务端最新版本" });
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => {
      listCalls += 1;
      if (listCalls === 3) {
        throw new ApiRequestError("private reload failure", 503, "service_unavailable");
      }
      return { agent_profiles: [listCalls === 1 ? profile() : latest] };
    },
    saveDraft: async () => {
      throw new ApiRequestError("raw conflict payload", 409, "agent_profile_revision_stale");
    },
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "本地未保存名称" }));
  await controller.saveActiveProfile(catalog());

  await controller.loadProfiles();
  assert.equal(controller.state.profiles[0]?.revision, 8);
  assert.equal(controller.state.activeEditor?.revision, 7);
  assert.equal(controller.state.activeEditor?.name, "本地未保存名称");
  await controller.loadProfiles(true);
  assert.equal(controller.state.listPhase, "error");
  assert.equal(controller.state.activeEditor?.revision, 7);
  assert.equal(controller.state.activeEditor?.name, "本地未保存名称");
  await controller.loadProfiles(true);

  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(controller.state.activeEditor?.name, "服务端最新版本");
  assert.equal(controller.state.mutation.phase, "idle");
});

test("confirmed destructive reload fences edits and navigation until its response", async () => {
  let resolveReload: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  let listCalls = 0;
  let saveCalls = 0;
  const other = profile({ agent_id: "agt_other", name: "其他助手" });
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: () => {
      listCalls += 1;
      if (listCalls === 1) {
        return Promise.resolve({ agent_profiles: [profile(), other] });
      }
      return new Promise((resolve) => {
        resolveReload = resolve;
      });
    },
    saveDraft: async () => {
      saveCalls += 1;
      return { agent_profile: profile(), audit_id: "audit-save" };
    },
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "确认前更改" }));

  const reload = controller.loadProfiles(true);
  assert.equal(controller.state.destructiveReloadPending, true);
  controller.updateActiveEditor((editor) => ({ ...editor, name: "确认后的更改" }));
  controller.selectProfile(other.agent_id, true);
  controller.createNewAgent(true);
  await controller.saveActiveProfile(catalog());
  assert.equal(controller.state.activeEditor?.name, "确认前更改");
  assert.equal(saveCalls, 0);

  resolveReload?.({
    agent_profiles: [profile({ revision: 8, name: "服务端新版本" }), other],
  });
  await reload;
  assert.equal(controller.state.destructiveReloadPending, false);
  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(controller.state.activeEditor?.name, "服务端新版本");
});

test("late list results cannot replace a newer refresh", async () => {
  let resolveFirst: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  const responses = [
    () => new Promise<{ agent_profiles: AgentProfileAdminProjection[] }>((resolve) => {
      resolveFirst = resolve;
    }),
    async () => ({ agent_profiles: [profile({ revision: 12, name: "最新版本" })] }),
  ];
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: () => responses.shift()?.() ?? Promise.resolve({ agent_profiles: [] }),
  }));
  const first = controller.loadProfiles();
  await controller.loadProfiles();
  resolveFirst?.({ agent_profiles: [profile({ revision: 2, name: "过期版本" })] });
  await first;

  assert.equal(controller.state.activeEditor?.revision, 12);
  assert.equal(controller.state.activeEditor?.name, "最新版本");
});

test("refresh updates the list without discarding edits made while it is pending", async () => {
  let resolveRefresh: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  let requestCount = 0;
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: () => {
      requestCount += 1;
      if (requestCount === 1) {
        return Promise.resolve({ agent_profiles: [profile()] });
      }
      return new Promise((resolve) => {
        resolveRefresh = resolve;
      });
    },
  }));
  await controller.loadProfiles();

  const refresh = controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({
    ...editor,
    instructions: "刷新期间的本地更改",
  }));
  resolveRefresh?.({ agent_profiles: [profile({ revision: 8 })] });
  await refresh;

  assert.equal(controller.state.profiles[0]?.revision, 8);
  assert.equal(controller.state.activeEditor?.revision, 7);
  assert.equal(controller.state.activeEditor?.instructions, "刷新期间的本地更改");
});

test("a pending list response cannot roll back a successful save response", async () => {
  let resolveRefresh: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  let requestCount = 0;
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: () => {
      requestCount += 1;
      if (requestCount === 1) {
        return Promise.resolve({ agent_profiles: [profile()] });
      }
      return new Promise((resolve) => {
        resolveRefresh = resolve;
      });
    },
    saveDraft: async (draft) => ({
      agent_profile: profile({
        revision: 8,
        name: draft.name,
      }),
      audit_id: "audit-save",
    }),
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "已保存的新名称" }));

  const refresh = controller.loadProfiles();
  await controller.saveActiveProfile(catalog());
  resolveRefresh?.({ agent_profiles: [profile({ revision: 7, name: "过期名称" })] });
  await refresh;

  assert.equal(controller.state.listPhase, "ready");
  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(controller.state.activeEditor?.name, "已保存的新名称");
});

test("busy save rejects refresh, selection, New Agent, and edits until its response arrives", async () => {
  let resolveSave: ((value: AgentProfileMutationResponse) => void) | undefined;
  let listCalls = 0;
  const other = profile({ agent_id: "agt_other", name: "其他助手" });
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => {
      listCalls += 1;
      return { agent_profiles: [profile(), other] };
    },
    saveDraft: () => new Promise((resolve) => {
      resolveSave = resolve;
    }),
  }));
  await controller.loadProfiles();
  controller.updateActiveEditor((editor) => ({ ...editor, name: "待保存名称" }));

  const saving = controller.saveActiveProfile(catalog());
  assert.equal(controller.state.mutation.phase, "saving");
  controller.updateActiveEditor((editor) => ({ ...editor, name: "不应采用的名称" }));
  controller.selectProfile(other.agent_id);
  controller.createNewAgent();
  await controller.loadProfiles();

  assert.equal(listCalls, 1);
  assert.equal(controller.state.activeEditor?.agentId, "agt_document_review");
  assert.equal(controller.state.activeEditor?.name, "待保存名称");
  resolveSave?.({
    agent_profile: profile({ revision: 8, name: "待保存名称" }),
    audit_id: "audit-save",
  });
  await saving;

  assert.equal(controller.state.mutation.phase, "success");
  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(controller.state.activeEditor?.name, "待保存名称");
});

test("busy publish rejects a later refresh and adopts the published response", async () => {
  let resolvePublish: ((value: AgentProfileMutationResponse) => void) | undefined;
  let listCalls = 0;
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => {
      listCalls += 1;
      return { agent_profiles: [profile()] };
    },
    publish: () => new Promise((resolve) => {
      resolvePublish = resolve;
    }),
  }));
  await controller.loadProfiles();

  const publishing = controller.publishActiveProfile(catalog());
  assert.equal(controller.state.mutation.phase, "publishing");
  await controller.loadProfiles();
  assert.equal(listCalls, 1);
  resolvePublish?.({
    agent_profile: profile({ revision: 8, status: "published" }),
    audit_id: "audit-publish",
  });
  await publishing;

  assert.equal(controller.state.mutation.phase, "success");
  assert.equal(controller.state.activeEditor?.revision, 8);
  assert.equal(controller.state.activeEditor?.status, "published");
});

test("unpublish fences the exact published revision and adopts immutable withdrawn history", async () => {
  const calls: Array<{ agentId: string; revision: number }> = [];
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({
      agent_profiles: [profile({
        revision: 10,
        status: "draft",
        published_revision: 9,
      })],
    }),
    unpublish: async (agentId, revision) => {
      calls.push({ agentId, revision });
      return {
        agent_profile: profile({
          revision: 11,
          status: "withdrawn",
          published_revision: null,
        }),
        audit_id: "audit-unpublish",
      };
    },
  }));
  await controller.loadProfiles();

  await controller.unpublishActiveProfile();

  assert.deepEqual(calls, [{ agentId: "agt_document_review", revision: 9 }]);
  assert.equal(controller.state.activeEditor?.revision, 11);
  assert.equal(controller.state.activeEditor?.status, "withdrawn");
  assert.deepEqual(controller.state.mutation, {
    phase: "success",
    action: "unpublish",
    revision: 11,
  });
});

test("real Builder test creates one controlled test submission for the exact published revision", async () => {
  const calls: Array<{
    agentId: string;
    revision: number;
    message: string;
    submissionId: string;
  }> = [];
  const trialRun = {
    session_id: "ses_test_7ea9303330f540ea8a332f3c6e7b21c4",
    run_id: "run-test",
    status: "queued" as const,
    submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
    purpose: "builder_test" as const,
  };
  const controller = new AgentBuilderController(fakeApi({
    listAdmin: async () => ({
      agent_profiles: [profile({ revision: 9, status: "published" })],
    }),
    runTest: async (agentId, revision, message, submissionId) => {
      calls.push({ agentId, revision, message, submissionId });
      return { ...trialRun, submission_id: submissionId };
    },
  }));
  await controller.loadProfiles();

  await controller.runActiveProfileTest("  Review this request  ");

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.agentId, "agt_document_review");
  assert.equal(calls[0]?.revision, 9);
  assert.equal(calls[0]?.message, "Review this request");
  assert.match(
    calls[0]?.submissionId ?? "",
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  assert.equal(controller.state.mutation.phase, "success");
  if (controller.state.mutation.phase === "success") {
    assert.equal(controller.state.mutation.action, "test");
    assert.deepEqual(controller.state.mutation.trialRun, {
      ...trialRun,
      submission_id: calls[0]?.submissionId,
    });
  }
});
