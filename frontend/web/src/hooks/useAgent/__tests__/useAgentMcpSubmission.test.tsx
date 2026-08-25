import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import { installTestDom } from "./testDom.ts";

const dom = installTestDom();

async function loadMcpSubmissionHarness() {
  dom.window.localStorage.clear();
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider } = await import("../../useAuth.tsx");
  const { useAgent } = await import("../../useAgent.ts");
  const { authApi } = await import("../../../services/api/auth.ts");
  const originalGetCurrentUser = authApi.getCurrentUser;
  const originalBootstrapAuthContext = authApi.bootstrapAuthContext;
  let snapshot: UseAgentReturn | null = null;
  const container = dom.document.createElement("div");
  const root = createRoot(container as never);

  authApi.getCurrentUser = async () => ({
    id: "user-a",
    tenant_id: "tenant-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions: [],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
  authApi.bootstrapAuthContext = async () => {};

  function Probe() {
    snapshot = useAgent({
      getDisabledMcpTools: () => ["gateway::inventory.search"],
    });
    return null;
  }

  await React.act(async () => {
    root.render(React.createElement(AuthProvider, null, React.createElement(Probe)));
  });

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot, "useAgent hook should be mounted");
      return snapshot;
    },
    async cleanup() {
      try {
        await React.act(async () => root.unmount());
      } finally {
        authApi.getCurrentUser = originalGetCurrentUser;
        authApi.bootstrapAuthContext = originalBootstrapAuthContext;
        dom.window.localStorage.clear();
      }
    },
  };
}

test("MCP selection submits directly without creating a browser runtime context", async () => {
  const harness = await loadMcpSubmissionHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalFetch = globalThis.fetch;
  const submissions: unknown[][] = [];
  const requests: string[] = [];

  try {
    globalThis.fetch = (async (input) => {
      requests.push(String(input));
      throw new Error("unexpected_fetch");
    }) as typeof fetch;
    sessionApi.submitChat = (async (...args) => {
      submissions.push(args);
      return { status: "needs_confirmation", suggestions: [] };
    }) as typeof sessionApi.submitChat;

    let outcome: unknown;
    await harness.act(async () => {
      outcome = await harness.hook.sendMessage("使用 MCP 工具");
    });

    assert.deepEqual(outcome, { status: "accepted" });
    assert.equal(submissions.length, 1);
    assert.deepEqual(submissions[0]?.[9], ["gateway::inventory.search"]);
    assert.deepEqual(requests, []);
    assert.equal(submissions[0]?.length, 11);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});
