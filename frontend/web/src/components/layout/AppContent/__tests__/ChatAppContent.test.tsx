import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

register(
  new URL("../../../../features/agent-market/__tests__/frontendAssetLoader.mjs", import.meta.url),
  import.meta.url,
);
await new Promise<void>((resolve) => setImmediate(resolve));

const {
  AgentConversationIdentityBanner,
  areAgentConversationControlsLocked,
  exposeGenericChatControl,
  recoverAgentConversationIdentity,
} = await import("../ChatAppContent.tsx");
const { agentProfileApi } = await import("../../../../services/api/agentProfile.ts");
const { sessionApi } = await import("../../../../services/api/session.ts");

const safeIdentity = {
  agent_id: "agt_support",
  revision: 7,
  name: "支持助手",
  description: "处理已授权的支持请求。",
  avatar_ref: "builtin:assistant",
  category: "support",
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
    agent_conversation: sessionId === "session-agent" ? safeIdentity : null,
  });
  agentProfileApi.getPublished = async () => {
    detailCalls += 1;
    return {
      agent_id: safeIdentity.agent_id,
      expected_revision: safeIdentity.revision,
      name: safeIdentity.name,
      description: safeIdentity.description,
      avatar_ref: safeIdentity.avatar_ref,
      category: safeIdentity.category,
    };
  };

  try {
    assert.deepEqual(await recoverAgentConversationIdentity("session-agent"), safeIdentity);
    assert.equal(await recoverAgentConversationIdentity("session-generic"), null);
    assert.equal(detailCalls, 1, "generic sessions must not inherit or probe a prior Agent");
  } finally {
    sessionApi.getAuthoritative = originalGetAuthoritative;
    agentProfileApi.getPublished = originalGetPublished;
  }
});

test("fails closed on revision drift and authoritative 403/404 recovery", async () => {
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
      agent_conversation: safeIdentity,
    };
  };
  agentProfileApi.getPublished = async () => ({
    agent_id: safeIdentity.agent_id,
    expected_revision: safeIdentity.revision + 1,
    name: safeIdentity.name,
    description: safeIdentity.description,
    avatar_ref: safeIdentity.avatar_ref,
    category: safeIdentity.category,
  });

  try {
    await assert.rejects(
      recoverAgentConversationIdentity("session-stale"),
      /agent_conversation_revision_mismatch/,
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

test("renders only safe Agent identity and locks conflicting controls", () => {
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
});
