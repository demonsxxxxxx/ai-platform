import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { resolveAppRoute } from "../appRouteManifest.ts";
import { AuthProvider } from "../hooks/useAuth.tsx";
import { useAgent } from "../hooks/useAgent.ts";
import {
  DEFAULT_CHAT_AGENT_ID,
  sessionApi,
} from "../services/api.ts";

const src = resolve(import.meta.dirname, "..");
const read = (relativePath: string) =>
  readFileSync(resolve(src, relativePath), "utf8");

test("legacy product URLs resolve through the active route manifest to NotFound", () => {
  for (const route of [
    "/persona",
    "/persona/preset-a",
    "/agent-workspace",
    "/agent-workspace?session=legacy",
    "/agents",
    "/agents/legacy",
    "/channels",
    "/channels/feishu/legacy",
    "/profile",
  ]) {
    assert.equal(resolveAppRoute(route), "notFound", route);
  }
});

test("legacy product surfaces are absent from navigation and lazy imports", () => {
  const navigationSources = [
    read("components/layout/UserMenu.tsx"),
    read("components/panels/SidebarParts/SidebarRail.tsx"),
    read("components/panels/SidebarParts/SessionListContent.tsx"),
  ].join("\n");
  const tabContent = read("components/layout/AppContent/TabContent.tsx");

  for (const route of ["/persona", "/agent-workspace", "/agents", "/channels"]) {
    assert.doesNotMatch(navigationSources, new RegExp(route.replace("/", "\\/")), route);
  }
  for (const legacyModule of [
    "PersonaWorkbenchPanel",
    "AgentWorkspacePanel",
    "AgentDirectoryPanel",
    "ChannelImportPanel",
  ]) {
    assert.doesNotMatch(tabContent, new RegExp(legacyModule), legacyModule);
  }
});

test("legacy profile product surface is absent from account navigation and source", () => {
  const accountMenu = read("components/layout/UserMenu.tsx");
  const appShell = read("components/layout/AppContent/AppShell.tsx");

  assert.doesNotMatch(accountMenu, /onShowProfile|navItems|["'`]\/skills["'`]/);
  assert.doesNotMatch(appShell, /ProfileModal|showProfileModal/);
});

let chatHookSnapshot: ReturnType<typeof useAgent> | null = null;

function ChatHookProbe() {
  chatHookSnapshot = useAgent();
  return createElement("div");
}

test("Chat hook exposes no legacy agent-directory state or loader", () => {
  chatHookSnapshot = null;
  renderToString(
    createElement(AuthProvider, null, createElement(ChatHookProbe)),
  );

  assert.ok(chatHookSnapshot);
  for (const legacyField of [
    "agents",
    "currentAgent",
    "agentsLoading",
    "allowedModelIds",
    "selectAgent",
    "switchAgent",
    "refreshAgents",
  ]) {
    assert.equal(legacyField in chatHookSnapshot, false, legacyField);
  }

  const useAgentSource = read("hooks/useAgent.ts");
  assert.doesNotMatch(useAgentSource, /fetchAgents|\/agents/);
});

test("Chat submission uses the fixed general-agent request contract", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    requests.push({ input, init });
    return new Response(
      JSON.stringify({
        session_id: "session-a",
        run_id: "run-a",
        trace_id: "trace-a",
        status: "queued",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await sessionApi.submitChat("hello", undefined, { model: "gpt-5" });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(DEFAULT_CHAT_AGENT_ID, "general-agent");
  assert.equal(
    requests[0]?.input,
    "/api/chat/stream?agent_id=general-agent",
  );
  assert.equal(requests[0]?.init?.method, "POST");
  const body = JSON.parse(String(requests[0]?.init?.body));
  assert.equal(body.message, "hello");
  assert.deepEqual(body.agent_options, { model: "gpt-5" });
  assert.equal("agent_id" in body, false);
  assert.equal(requests.some(({ input }) => String(input).includes("/api/agents")), false);
});

test("Chat removes preference, selector, mention, and agent command surfaces", () => {
  const useAgentSource = read("hooks/useAgent.ts");
  const commands = read("components/chat/chatInputCommands.ts");
  const chatInput = read("components/chat/ChatInput.tsx");

  assert.doesNotMatch(useAgentSource, /defaultAgentId|agent-preference-updated/);
  assert.doesNotMatch(commands, /["']agent["']/);
  assert.doesNotMatch(chatInput, /MentionPopup|useMentionSearch|useMentionState/);
});
