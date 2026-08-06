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
  AgentWorkspaceWelcome,
  areAgentConversationControlsLocked,
  exposeGenericChatControl,
  getChatToolAccess,
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
  supported_file_types: [],
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

test("renders a productized Agent workspace welcome before creating a conversation", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentWorkspaceWelcome, {
      profile: {
        ...safeIdentity,
        expected_revision: safeIdentity.revision,
      },
      creating: false,
      readOnly: false,
      error: null,
      historyError: null,
      onStart: () => {},
      onStarterPrompt: () => {},
      onOpenDetail: () => {},
    }),
  );

  assert.match(html, /data-agent-workspace-welcome/);
  assert.match(html, /企业已发布/);
  assert.match(html, /欢迎使用支持助手/);
  assert.match(html, /权限与数据访问/);
  assert.match(html, /帮我处理支持请求/);
  assert.match(html, /开始新对话/);
  assert.doesNotMatch(html, /model_id|instructions|mcp_tool_ids|selected_skill/);
});

test("Agent workspace sidebar consumes the server-paginated session source", () => {
  const source = readFileSync(
    new URL("../ChatAppContent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /agentWorkspaceSessionSource\?: SessionSidebarSessionSource/);
  assert.match(source, /sessionSource=\{agentWorkspaceSessionSource\}/);
  assert.match(source, /agentWorkspace && !agentWorkspaceSessionSource[\s\S]*\? \(\) => false/);
  assert.match(source, /onAgentWorkspaceSessionCreated\?\.\(session\.session_id\)/);
  assert.match(source, /composerPlaceholder=\{[\s\S]*agentWorkspace\.name/);
});
