import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { AgentConversationIdentity } from "../../../../types/agentProfile.ts";

register(
  new URL("../../../../features/agent-market/__tests__/frontendAssetLoader.mjs", import.meta.url),
  import.meta.url,
);
await new Promise<void>((resolve) => setImmediate(resolve));

const {
  AgentConversationIdentityBanner,
  areAgentConversationControlsLocked,
  exposeGenericChatControl,
  getChatToolAccess,
  getOrCreateAgentConversationOperationId,
  ensureAgentConversationForFirstSend,
  submitAgentFirstMessageSingleFlight,
  isExactAgentWorkspaceBinding,
  recoverAgentConversationIdentity,
} = await import("../ChatAppContent.tsx");
const { agentProfileApi } = await import("../../../../services/api/agentProfile.ts");
const { sessionApi } = await import("../../../../services/api/session.ts");

const safeIdentity: AgentConversationIdentity = {
  agent_id: "agt_support",
  revision: 7,
  name: "支持助手",
  description: "处理已授权的支持请求。",
  welcome_message: "欢迎使用支持助手。",
  starter_prompts: ["帮我处理支持请求"],
  capability_summary: "在授权范围内处理企业支持请求。",
  recommended_tasks: ["支持请求分流"],
  supported_input_types: ["text"],
  expected_outputs: ["处理建议"],
  permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
  avatar_ref: "builtin:assistant",
  category: "support",
  published_at: "2026-08-04T01:00:00Z",
};

const safeWorkspace = {
  agent_id: safeIdentity.agent_id,
  expected_revision: safeIdentity.revision,
} as const;

test("recovers an exact current Agent Conversation and keeps ordinary sessions generic", async () => {
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetPublished = agentProfileApi.getPublished;
  let detailCalls = 0;
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: safeIdentity.agent_id,
    title: safeIdentity.name,
    purpose: "conversation",
    agent_conversation: sessionId === "session-agent" ? safeIdentity : null,
  });
  agentProfileApi.getPublished = async () => {
    detailCalls += 1;
    return {
      ...safeIdentity,
      expected_revision: safeIdentity.revision,
    };
  };

  try {
    assert.deepEqual(await recoverAgentConversationIdentity("session-agent"), safeIdentity);
    assert.equal(await recoverAgentConversationIdentity("session-generic"), null);
    assert.equal(
      detailCalls,
      0,
      "conversation recovery must not depend on, inherit, or probe the current publication",
    );
  } finally {
    sessionApi.getAuthoritative = originalGetAuthoritative;
    agentProfileApi.getPublished = originalGetPublished;
  }
});

test("keeps immutable revision history while current access remains authorized", async () => {
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetPublished = agentProfileApi.getPublished;
  sessionApi.getAuthoritative = async (sessionId) => {
    if (sessionId === "session-denied") {
      throw Object.assign(new Error("denied"), { status: 403 });
    }
    if (sessionId === "session-missing") {
      throw Object.assign(new Error("missing"), { status: 404 });
    }
    return {
      session_id: sessionId,
      workspace_id: "default",
      agent_id: safeIdentity.agent_id,
      title: safeIdentity.name,
      purpose: "conversation",
      agent_conversation: safeIdentity,
    };
  };
  agentProfileApi.getPublished = async () => ({
    ...safeIdentity,
    expected_revision: safeIdentity.revision + 1,
  });

  try {
    assert.deepEqual(
      await recoverAgentConversationIdentity("session-stale"),
      safeIdentity,
      "a current N+1 publication must not rewrite or hide an owned revision N conversation",
    );
    await assert.rejects(
      recoverAgentConversationIdentity("session-denied"),
      (error: unknown) => (error as { status?: unknown }).status === 403,
    );
    await assert.rejects(
      recoverAgentConversationIdentity("session-missing"),
      (error: unknown) => (error as { status?: unknown }).status === 404,
    );
  } finally {
    sessionApi.getAuthoritative = originalGetAuthoritative;
    agentProfileApi.getPublished = originalGetPublished;
  }
});

test("recovers an owned immutable Agent Conversation after its current profile is withdrawn", async () => {
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetPublished = agentProfileApi.getPublished;
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: safeIdentity.agent_id,
    title: safeIdentity.name,
    purpose: "conversation",
    agent_conversation: safeIdentity,
  });
  agentProfileApi.getPublished = async () => {
    throw Object.assign(new Error("withdrawn"), { status: 404 });
  };

  try {
    assert.deepEqual(
      await recoverAgentConversationIdentity("session-withdrawn"),
      safeIdentity,
      "a withdrawn current profile must not block an owned session pinned to an immutable revision",
    );
  } finally {
    sessionApi.getAuthoritative = originalGetAuthoritative;
    agentProfileApi.getPublished = originalGetPublished;
  }
});

test("rejects authoritative Agent identity returned for a different Session", async () => {
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetPublished = agentProfileApi.getPublished;
  let detailCalls = 0;
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: "session-other",
    workspace_id: "default",
    agent_id: safeIdentity.agent_id,
    title: safeIdentity.name,
    purpose: "conversation",
    agent_conversation: sessionId === "session-requested-bound" ? safeIdentity : null,
  });
  agentProfileApi.getPublished = async () => {
    detailCalls += 1;
    throw new Error("must_not_reauthorize_mismatched_session");
  };

  try {
    await assert.rejects(
      recoverAgentConversationIdentity("session-requested-bound"),
      /agent_conversation_identity_mismatch/,
    );
    await assert.rejects(
      recoverAgentConversationIdentity("session-requested-generic"),
      /agent_conversation_identity_mismatch/,
    );
    assert.equal(detailCalls, 0, "mismatched Session identity must fail before publication lookup");
  } finally {
    sessionApi.getAuthoritative = originalGetAuthoritative;
    agentProfileApi.getPublished = originalGetPublished;
  }
});

test("generic Chat tools remain available while Agent loading and bound workspaces expose none", () => {
  assert.deepEqual(
    getChatToolAccess({ agentWorkspace: undefined, phase: "generic", sessionId: "generic" }),
    { enabled: true, sessionId: "generic" },
  );

  for (const phase of ["loading", "bound"] as const) {
    assert.deepEqual(
      getChatToolAccess({ agentWorkspace: safeWorkspace, phase, sessionId: "agent-a" }),
      { enabled: false, sessionId: null },
    );
  }
});

test("an Agent workspace becomes send-ready only after its exact bound Session is recovered", () => {
  const boundState = {
    phase: "bound" as const,
    targetSessionId: "session-agent",
    identity: safeIdentity,
  };

  assert.equal(
    isExactAgentWorkspaceBinding({
      agentWorkspace: safeWorkspace,
      state: { phase: "generic", targetSessionId: null, identity: null },
      sessionId: null,
    }),
    false,
    "a bare revision-bound workspace must keep the composer disabled",
  );
  assert.equal(
    isExactAgentWorkspaceBinding({
      agentWorkspace: safeWorkspace,
      state: boundState,
      sessionId: "session-other",
    }),
    false,
    "a recovered Session may not authorize a different workspace route",
  );
  assert.equal(
    isExactAgentWorkspaceBinding({
      agentWorkspace: safeWorkspace,
      state: boundState,
      sessionId: "session-agent",
    }),
    true,
    "the exact admitted Agent Session enables the canonical composer path",
  );
});

test("persists one Agent Conversation operation identity across a response-loss retry", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
  const createId = () => "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";

  const first = getOrCreateAgentConversationOperationId({
    agentId: safeIdentity.agent_id,
    revision: safeIdentity.revision,
    storage,
    createId,
  });
  const replay = getOrCreateAgentConversationOperationId({
    agentId: safeIdentity.agent_id,
    revision: safeIdentity.revision,
    storage,
    createId: () => "6ed64d27-bbdb-486b-9b2c-1ece2cad1ee1",
  });

  assert.equal(first, replay);
});

test("first-send Agent creation is single-flight and binds before returning", async () => {
  const coordinator = { current: null as Promise<string> | null };
  let createCalls = 0;
  let bindCalls = 0;
  let releaseCreate!: () => void;
  const createGate = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  const createConversation = async () => {
    createCalls += 1;
    await createGate;
    return {
      session_id: "session-agent",
      workspace_id: "default",
      agent_id: safeIdentity.agent_id,
      title: safeIdentity.name,
      purpose: "conversation" as const,
      agent_conversation: safeIdentity,
    };
  };
  const bindConversation = async (sessionId: string) => {
    bindCalls += 1;
    assert.equal(sessionId, "session-agent");
    return true;
  };

  const first = ensureAgentConversationForFirstSend({
    coordinator,
    profile: safeWorkspace,
    createConversation,
    bindConversation,
  });
  const duplicate = ensureAgentConversationForFirstSend({
    coordinator,
    profile: safeWorkspace,
    createConversation,
    bindConversation,
  });
  assert.equal(createCalls, 1);
  releaseCreate();

  assert.deepEqual(await Promise.all([first, duplicate]), [
    "session-agent",
    "session-agent",
  ]);
  assert.equal(createCalls, 1);
  assert.equal(bindCalls, 1);
});

test("first-send creation rejects a mismatched pinned identity before binding", async () => {
  let bindCalls = 0;
  await assert.rejects(
    ensureAgentConversationForFirstSend({
      coordinator: { current: null },
      profile: safeWorkspace,
      createConversation: async () => ({
        session_id: "session-other",
        workspace_id: "default",
        agent_id: "agt_other",
        title: "Other",
        purpose: "conversation",
        agent_conversation: { ...safeIdentity, agent_id: "agt_other" },
      }),
      bindConversation: async () => {
        bindCalls += 1;
        return true;
      },
    }),
    /agent_workspace_identity_mismatch/,
  );
  assert.equal(bindCalls, 0);
});

test("a recommendation double-click creates and submits one real first user turn", async () => {
  const coordinator = {
    current: null as {
      submissionKey: string;
      promise: Promise<{ status: "accepted" }>;
    } | null,
  };
  let ensureCalls = 0;
  let submitCalls = 0;
  let releaseSubmit!: () => void;
  const submitGate = new Promise<void>((resolve) => {
    releaseSubmit = resolve;
  });
  const ensureConversation = async () => {
    ensureCalls += 1;
    return "session-agent";
  };
  const submitMessage = async (sessionId: string) => {
    submitCalls += 1;
    assert.equal(sessionId, "session-agent");
    await submitGate;
    return { status: "accepted" as const };
  };

  const first = submitAgentFirstMessageSingleFlight({
    coordinator,
    submissionKey: JSON.stringify({ content: "支持请求分流", fileIds: [] }),
    ensureConversation,
    submitMessage,
  });
  const duplicate = submitAgentFirstMessageSingleFlight({
    coordinator,
    submissionKey: JSON.stringify({ content: "支持请求分流", fileIds: [] }),
    ensureConversation,
    submitMessage,
  });
  await Promise.resolve();
  assert.equal(ensureCalls, 1);
  assert.equal(submitCalls, 1);
  releaseSubmit();
  assert.deepEqual(await Promise.all([first, duplicate]), [
    { status: "accepted" },
    { status: "accepted" },
  ]);
});

test("an accepted first submission releases its flight while reusing the bound conversation", async () => {
  const creationCoordinator = { current: null as Promise<string> | null };
  const submissionCoordinator = {
    current: null as {
      submissionKey: string;
      promise: Promise<{ status: "accepted" }>;
    } | null,
  };
  let createCalls = 0;
  let bindCalls = 0;
  const submittedSessionIds: string[] = [];
  const ensureConversation = () =>
    ensureAgentConversationForFirstSend({
      coordinator: creationCoordinator,
      profile: safeWorkspace,
      createConversation: async () => {
        createCalls += 1;
        return {
          session_id: "session-agent",
          workspace_id: "default",
          agent_id: safeIdentity.agent_id,
          title: safeIdentity.name,
          purpose: "conversation" as const,
          agent_conversation: safeIdentity,
        };
      },
      bindConversation: async () => {
        bindCalls += 1;
        return true;
      },
    });
  const submitMessage = async (sessionId: string) => {
    submittedSessionIds.push(sessionId);
    return { status: "accepted" as const };
  };

  assert.deepEqual(
    await submitAgentFirstMessageSingleFlight({
      coordinator: submissionCoordinator,
      submissionKey: JSON.stringify({ content: "第一问", fileIds: [] }),
      ensureConversation,
      submitMessage,
    }),
    { status: "accepted" },
  );
  assert.deepEqual(
    await submitAgentFirstMessageSingleFlight({
      coordinator: submissionCoordinator,
      submissionKey: JSON.stringify({ content: "第二问", fileIds: [] }),
      ensureConversation,
      submitMessage,
    }),
    { status: "accepted" },
  );

  assert.equal(createCalls, 1);
  assert.equal(bindCalls, 1);
  assert.deepEqual(submittedSessionIds, ["session-agent", "session-agent"]);
});

test("a failed first submission retries on the same bound Agent conversation", async () => {
  const creationCoordinator = { current: null as Promise<string> | null };
  const submissionCoordinator = {
    current: null as {
      submissionKey: string;
      promise: Promise<{ status: "accepted" }>;
    } | null,
  };
  let createCalls = 0;
  let bindCalls = 0;
  let submitCalls = 0;
  const ensureConversation = () =>
    ensureAgentConversationForFirstSend({
      coordinator: creationCoordinator,
      profile: safeWorkspace,
      createConversation: async () => {
        createCalls += 1;
        return {
          session_id: "session-agent",
          workspace_id: "default",
          agent_id: safeIdentity.agent_id,
          title: safeIdentity.name,
          purpose: "conversation" as const,
          agent_conversation: safeIdentity,
        };
      },
      bindConversation: async () => {
        bindCalls += 1;
        return true;
      },
    });
  const submitMessage = async (sessionId: string) => {
    assert.equal(sessionId, "session-agent");
    submitCalls += 1;
    if (submitCalls === 1) throw new Error("transient submit failure");
    return { status: "accepted" as const };
  };

  await assert.rejects(
    submitAgentFirstMessageSingleFlight({
      coordinator: submissionCoordinator,
      submissionKey: JSON.stringify({ content: "retry", fileIds: [] }),
      ensureConversation,
      submitMessage,
    }),
    /transient submit failure/,
  );
  assert.deepEqual(
    await submitAgentFirstMessageSingleFlight({
      coordinator: submissionCoordinator,
      submissionKey: JSON.stringify({ content: "retry", fileIds: [] }),
      ensureConversation,
      submitMessage,
    }),
    { status: "accepted" },
  );
  assert.equal(createCalls, 1);
  assert.equal(bindCalls, 1);
  assert.equal(submitCalls, 2);
});

test("a late first-submission completion cannot clear a newer flight", async () => {
  const coordinator = {
    current: null as {
      submissionKey: string;
      promise: Promise<{ status: "accepted" }>;
    } | null,
  };
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  const oldSubmission = submitAgentFirstMessageSingleFlight({
    coordinator,
    submissionKey: JSON.stringify({ content: "旧问题", fileIds: [] }),
    ensureConversation: async () => "session-agent",
    submitMessage: async () => {
      await oldGate;
      return { status: "accepted" as const };
    },
  });
  const newerFlight = Promise.resolve({ status: "accepted" as const });
  coordinator.current = { submissionKey: "newer", promise: newerFlight };

  releaseOld();
  assert.deepEqual(await oldSubmission, { status: "accepted" });
  assert.equal(coordinator.current?.promise, newerFlight);
});

test("a different composer payload is not accepted by an in-flight recommendation", async () => {
  const coordinator = {
    current: null as {
      submissionKey: string;
      promise: Promise<{ status: "accepted" }>;
    } | null,
  };
  let ensureCalls = 0;
  const submitted: Array<{ sessionId: string; content: string; fileIds: string[] }> = [];
  let releaseRecommendation!: () => void;
  const recommendationGate = new Promise<void>((resolve) => {
    releaseRecommendation = resolve;
  });
  const ensureConversation = async () => {
    ensureCalls += 1;
    return "session-agent";
  };

  const recommendation = submitAgentFirstMessageSingleFlight({
    coordinator,
    submissionKey: JSON.stringify({ content: "支持请求分流", fileIds: [] }),
    ensureConversation,
    submitMessage: async (sessionId) => {
      submitted.push({ sessionId, content: "支持请求分流", fileIds: [] });
      await recommendationGate;
      return { status: "accepted" as const };
    },
  });
  const composerDraft = submitAgentFirstMessageSingleFlight({
    coordinator,
    submissionKey: JSON.stringify({ content: "分析附件", fileIds: ["file-report"] }),
    ensureConversation,
    submitMessage: async (sessionId) => {
      submitted.push({ sessionId, content: "分析附件", fileIds: ["file-report"] });
      return { status: "accepted" as const };
    },
  });

  assert.deepEqual(
    await composerDraft,
    { status: "failed" },
    "an unsent draft must not receive accepted and therefore must not be cleared by ChatInput",
  );
  assert.equal(
    JSON.stringify(submitted),
    JSON.stringify([
      { sessionId: "session-agent", content: "支持请求分流", fileIds: [] },
    ]),
  );
  releaseRecommendation();
  assert.deepEqual(await recommendation, { status: "accepted" });

  assert.deepEqual(
    await submitAgentFirstMessageSingleFlight({
      coordinator,
      submissionKey: JSON.stringify({ content: "分析附件", fileIds: ["file-report"] }),
      ensureConversation,
      submitMessage: async (sessionId) => {
        submitted.push({ sessionId, content: "分析附件", fileIds: ["file-report"] });
        return { status: "accepted" as const };
      },
    }),
    { status: "accepted" },
  );
  assert.equal(ensureCalls, 2);
  assert.deepEqual(submitted, [
    { sessionId: "session-agent", content: "支持请求分流", fileIds: [] },
    { sessionId: "session-agent", content: "分析附件", fileIds: ["file-report"] },
  ]);
});

test("fails closed when stable Agent Conversation operation storage is unavailable", () => {
  const operationId = getOrCreateAgentConversationOperationId({
    agentId: safeIdentity.agent_id,
    revision: safeIdentity.revision,
    storage: null,
    createId: () => "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
  });

  assert.equal(operationId, null);
});

test("fails closed when Agent Conversation operation storage cannot be read or verified", () => {
  const createId = () => "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  const attempts = [
    {
      getItem: () => {
        throw new Error("storage_get_denied");
      },
      setItem: () => {},
    },
    {
      getItem: () => null,
      setItem: () => {
        throw new Error("storage_set_denied");
      },
    },
    {
      getItem: () => null,
      setItem: () => {},
    },
  ];

  for (const storage of attempts) {
    let result: string | null | "threw" = "threw";
    try {
      result = getOrCreateAgentConversationOperationId({
        agentId: safeIdentity.agent_id,
        revision: safeIdentity.revision,
        storage,
        createId,
      });
    } catch {
      // The product seam must convert browser storage failures into a fail-closed result.
    }
    assert.equal(result, null);
  }
});

test("renders only safe Agent identity and locks MCP catalog controls", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentConversationIdentityBanner, { identity: safeIdentity }),
  );
  assert.match(html, /支持助手/);
  assert.match(html, /处理已授权的支持请求/);
  assert.match(html, /支持服务/);
  assert.match(html, /data-agent-conversation-profile/);
  assert.doesNotMatch(html, /content_hash|model_id|skill_id|mcp_tool_ids|PRIVATE/);
  assert.equal(areAgentConversationControlsLocked("loading"), true);
  assert.equal(areAgentConversationControlsLocked("bound"), true);
  assert.equal(areAgentConversationControlsLocked("blocked"), true);
  assert.equal(areAgentConversationControlsLocked("generic"), false);
  const mcpControl = () => "generic-mcp-control";
  assert.equal(exposeGenericChatControl("loading", mcpControl), undefined);
  assert.equal(exposeGenericChatControl("bound", mcpControl), undefined);
  assert.equal(exposeGenericChatControl("blocked", mcpControl), undefined);
  assert.equal(exposeGenericChatControl("generic", mcpControl), mcpControl);
  const retryMcpCatalog = () => "retry-mcp-catalog";
  assert.equal(exposeGenericChatControl("bound", retryMcpCatalog), undefined);
});

test("projects the Agent welcome and recommendations only in the empty Chat UI", () => {
  const chatViewSource = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");
  const appContentSource = readFileSync(
    new URL("../ChatAppContent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(chatViewSource, /messages\.length === 0[\s\S]*agentEmptyProfile/);
  assert.match(chatViewSource, /data-agent-chat-opening/);
  assert.match(chatViewSource, /agentEmptyProfile\.welcome_message/);
  assert.match(chatViewSource, /data-agent-starter-prompts/);
  assert.match(chatViewSource, /onClick=\{\(\) => void onSendMessage\(prompt\)\}/);
  assert.doesNotMatch(appContentSource, /data-agent-workspace-welcome/);
  assert.doesNotMatch(appContentSource, /data-agent-workspace-start/);
});

test("Agent workspace sidebar consumes the server-paginated session source", () => {
  const source = readFileSync(
    new URL("../ChatAppContent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /agentWorkspaceSessionSource\?: SessionSidebarSessionSource/);
  assert.match(source, /sessionSource=\{agentWorkspaceSessionSource\}/);
  assert.match(source, /agentWorkspace && !agentWorkspaceSessionSource[\s\S]*\? \(\) => false/);
  assert.match(source, /onAgentWorkspaceSessionCreated\?\.\(createdSessionId\)/);
  assert.match(source, /composerPlaceholder=\{[\s\S]*agentWorkspace\.name/);
});

test("legacy generic Agent sessions redirect to the canonical dedicated route", () => {
  const source = readFileSync(
    new URL("../ChatAppContent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /if \(!agentWorkspace && identity\)/);
  assert.match(source, /buildAgentMarketWorkspacePath\([\s\S]*identity\.agent_id/);
  assert.match(source, /navigate\([\s\S]*\{ replace: true \}/);
});
