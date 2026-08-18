import test from "node:test";
import assert from "node:assert/strict";
import {
  createMcpRuntimeContext,
  prepareMcpRuntimeContext,
} from "../mcpRuntime.ts";
import { ApiRequestError } from "../fetch.ts";
import {
  MCP_GATEWAY_JWT_STORAGE_KEY,
  clearMcpGatewayJwt,
  getMcpGatewayJwt,
  setMcpGatewayJwt,
} from "../../../utils/mcpGatewayAuth.ts";

function installStorage(): Map<string, string> {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem(key: string) {
        return values.get(key) ?? null;
      },
      setItem(key: string, value: string) {
        values.set(key, value);
      },
      removeItem(key: string) {
        values.delete(key);
      },
    },
  });
  return values;
}

test("runtime context sends the company JWT only in JWT-Authorization", async () => {
  const originalFetch = globalThis.fetch;
  installStorage();
  setMcpGatewayJwt("company.jwt.value");
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(
      JSON.stringify({
        mcp_context_id: "mcpctx_opaque",
        expires_at: "2026-08-07T12:00:00Z",
        catalog_revision: 12,
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const result = await createMcpRuntimeContext();
    const captured = calls[0];
    assert.ok(captured);
    assert.equal(result.mcp_context_id, "mcpctx_opaque");
    assert.equal(captured.url, "/api/ai/mcp/runtime-contexts");
    const headers = new Headers(captured.init?.headers);
    assert.equal(headers.get("JWT-Authorization"), "Bearer company.jwt.value");
    assert.equal(headers.get("Authorization"), null);
    assert.equal(captured.init?.body, undefined);
    assert.equal(captured.init?.credentials, "include");
    assert.equal(captured.init?.redirect, "error");
  } finally {
    globalThis.fetch = originalFetch;
    clearMcpGatewayJwt();
  }
});

test("runtime context 401 clears only the MCP credential", async () => {
  const originalFetch = globalThis.fetch;
  const values = installStorage();
  values.set("ai_platform_session_present", "cookie-session-marker");
  setMcpGatewayJwt("expired.jwt");
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "mcp_gateway_unauthorized" }), {
      status: 401,
    })) as typeof fetch;

  try {
    await assert.rejects(
      createMcpRuntimeContext(),
      (error: unknown) => error instanceof ApiRequestError && error.status === 401,
    );
    assert.equal(getMcpGatewayJwt(), null);
    assert.equal(values.get("ai_platform_session_present"), "cookie-session-marker");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("unrelated 401 does not clear the MCP credential", async () => {
  const originalFetch = globalThis.fetch;
  installStorage();
  setMcpGatewayJwt("still-valid.jwt");
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "session_expired" }), {
      status: 401,
    })) as typeof fetch;

  try {
    await assert.rejects(
      createMcpRuntimeContext(),
      (error: unknown) => error instanceof ApiRequestError && error.status === 401,
    );
    assert.equal(getMcpGatewayJwt(), "still-valid.jwt");
  } finally {
    globalThis.fetch = originalFetch;
    clearMcpGatewayJwt();
  }
});

test("chat preparation requires JWT context for every selected platform MCP", async () => {
  const originalFetch = globalThis.fetch;
  const values = installStorage();
  let fetchCount = 0;
  globalThis.fetch = (async () => {
    fetchCount += 1;
    return new Response(
      JSON.stringify({
        mcp_context_id: "mcpctx_profile",
        expires_at: "2026-08-07T12:00:00Z",
      }),
    );
  }) as typeof fetch;

  try {
    assert.equal(
      await prepareMcpRuntimeContext({}),
      undefined,
    );
    await assert.rejects(
      prepareMcpRuntimeContext({
        selectedMcpToolIds: ["legacy-static-tool"],
      }),
      (error: unknown) =>
        error instanceof ApiRequestError && error.code === "mcp_jwt_missing",
    );
    assert.equal(fetchCount, 0);
    values.set(MCP_GATEWAY_JWT_STORAGE_KEY, "profile.jwt");
    assert.equal(
      await prepareMcpRuntimeContext({}),
      undefined,
    );
    assert.equal(fetchCount, 0);
    assert.equal(
      await prepareMcpRuntimeContext({ profileSelected: true }),
      "mcpctx_profile",
    );
    assert.equal(fetchCount, 1);
    assert.equal(
      await prepareMcpRuntimeContext({
        selectedMcpToolIds: ["inventory.read"],
      }),
      "mcpctx_profile",
    );
    assert.equal(fetchCount, 2);
  } finally {
    globalThis.fetch = originalFetch;
    clearMcpGatewayJwt();
  }
});
