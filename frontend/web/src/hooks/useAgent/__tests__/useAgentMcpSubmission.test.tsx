import assert from "node:assert/strict";
import test from "node:test";

import { MCP_GATEWAY_JWT_STORAGE_KEY } from "../../../utils/mcpGatewayAuth.ts";
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
      getDisabledMcpTools: () => ["inventory.search"],
    });
    return null;
  }

  try {
    await React.act(async () => {
      root.render(
        React.createElement(AuthProvider, null, React.createElement(Probe)),
      );
    });
  } catch (error) {
    authApi.getCurrentUser = originalGetCurrentUser;
    authApi.bootstrapAuthContext = originalBootstrapAuthContext;
    throw error;
  }

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot, "useAgent hook should be mounted");
      return snapshot;
    },
    async dispatchAuthIncarnation(incarnation: string) {
      const { BROWSER_AUTH_INCARNATION_EVENT } = await import(
        "../../browserAuthCoordinator.ts"
      );
      await React.act(async () => {
        dom.window.dispatchEvent(
          new CustomEvent(BROWSER_AUTH_INCARNATION_EVENT, {
            detail: { incarnation },
          }) as unknown as { type: string; [key: string]: unknown },
        );
        await Promise.resolve();
      });
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

test("auth changes during MCP context creation prevent chat submission", async () => {
  const harness = await loadMcpSubmissionHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalFetch = globalThis.fetch;
  let resolveContext!: (response: Response) => void;
  const contextResponse = new Promise<Response>((resolve) => {
    resolveContext = resolve;
  });
  let markContextStarted!: () => void;
  const contextStarted = new Promise<void>((resolve) => {
    markContextStarted = resolve;
  });
  let submissions = 0;
  let pendingSubmission: Promise<unknown> | null = null;
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  try {
    dom.window.localStorage.setItem(MCP_GATEWAY_JWT_STORAGE_KEY, "company.jwt");
    globalThis.fetch = (async (input, init) => {
      requests.push({ url: String(input), init });
      markContextStarted();
      return contextResponse;
    }) as typeof fetch;
    sessionApi.submitChat = (async () => {
      submissions += 1;
      return { status: "needs_confirmation", suggestions: [] };
    }) as typeof sessionApi.submitChat;

    await harness.act(async () => {
      pendingSubmission = harness.hook.sendMessage("需要 MCP 的请求");
      await contextStarted;
    });
    await harness.dispatchAuthIncarnation("replacement-incarnation");
    resolveContext(
      new Response(
        JSON.stringify({
          mcp_context_id: "mcpctx-stale-owner",
          expires_at: "2099-01-01T00:00:00Z",
        }),
      ),
    );

    let outcome: unknown;
    await harness.act(async () => {
      outcome = await pendingSubmission;
    });

    assert.deepEqual(outcome, { status: "failed" });
    assert.equal(submissions, 0);
    const mcpRequests = requests.filter((request) =>
      request.url.startsWith("/api/ai/mcp/runtime-contexts"),
    );
    assert.equal(mcpRequests.length, 2);
    assert.equal(mcpRequests[0]?.init?.method, "POST");
    assert.equal(
      new Headers(mcpRequests[0]?.init?.headers).get("JWT-Authorization"),
      "Bearer company.jwt",
    );
    assert.equal(
      mcpRequests[1]?.url,
      "/api/ai/mcp/runtime-contexts/mcpctx-stale-owner",
    );
    assert.equal(mcpRequests[1]?.init?.method, "DELETE");
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("expired MCP context does not clear the gateway JWT", async () => {
  const harness = await loadMcpSubmissionHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalFetch = globalThis.fetch;
  let submissions = 0;
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  try {
    dom.window.localStorage.setItem(MCP_GATEWAY_JWT_STORAGE_KEY, "company.jwt");
    globalThis.fetch = (async (input, init) => {
      requests.push({ url: String(input), init });
      return new Response(JSON.stringify({ detail: "mcp_context_expired" }), {
        status: 409,
      });
    }) as typeof fetch;
    sessionApi.submitChat = (async () => {
      submissions += 1;
      return { status: "needs_confirmation", suggestions: [] };
    }) as typeof sessionApi.submitChat;

    let outcome: unknown;
    await harness.act(async () => {
      outcome = await harness.hook.sendMessage("使用过期 MCP context");
    });

    assert.deepEqual(outcome, { status: "failed" });
    assert.equal(submissions, 0);
    assert.equal(
      dom.window.localStorage.getItem(MCP_GATEWAY_JWT_STORAGE_KEY),
      "company.jwt",
    );
    const mcpRequests = requests.filter(
      (request) => request.url === "/api/ai/mcp/runtime-contexts",
    );
    assert.equal(mcpRequests.length, 1);
    assert.equal(mcpRequests[0]?.init?.method, "POST");
    assert.equal(
      new Headers(mcpRequests[0]?.init?.headers).get("JWT-Authorization"),
      "Bearer company.jwt",
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});
