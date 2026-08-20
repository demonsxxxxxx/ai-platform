import assert from "node:assert/strict";

export function installSuccessfulMcpRuntimeContext(
  contextPrefix = "mcpctx-test",
) {
  const originalFetch = globalThis.fetch;
  const contextIds: string[] = [];
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
    },
  };
}
