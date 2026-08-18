import assert from "node:assert/strict";

import { MCP_GATEWAY_JWT_STORAGE_KEY } from "../../../utils/mcpGatewayAuth.ts";

export function installSuccessfulMcpRuntimeContext(
  storage: Storage,
  contextPrefix = "mcpctx-test",
) {
  const originalFetch = globalThis.fetch;
  const contextIds: string[] = [];
  const previousJwt = storage.getItem(MCP_GATEWAY_JWT_STORAGE_KEY);
  storage.setItem(MCP_GATEWAY_JWT_STORAGE_KEY, "company.jwt");
  globalThis.fetch = (async (input) => {
    assert.equal(String(input), "/api/ai/mcp/runtime-contexts");
    const contextId = `${contextPrefix}-${contextIds.length + 1}`;
    contextIds.push(contextId);
    return new Response(
      JSON.stringify({
        mcp_context_id: contextId,
        expires_at: "2099-01-01T00:00:00Z",
      }),
    );
  }) as typeof fetch;

  return {
    contextIds,
    restore() {
      globalThis.fetch = originalFetch;
      if (previousJwt === null) {
        storage.removeItem(MCP_GATEWAY_JWT_STORAGE_KEY);
      } else {
        storage.setItem(MCP_GATEWAY_JWT_STORAGE_KEY, previousJwt);
      }
    },
  };
}
