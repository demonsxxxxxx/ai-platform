import test from "node:test";
import assert from "node:assert/strict";
import {
  createMcpRuntimeContext,
  discardMcpRuntimeContext,
  prepareMcpRuntimeContext,
} from "../mcpRuntime.ts";
import { ApiRequestError } from "../fetch.ts";

test("runtime context relies on the backend-owned JWT", async () => {
  const originalFetch = globalThis.fetch;
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
    assert.equal(headers.get("JWT-Authorization"), null);
    assert.equal(headers.get("Authorization"), null);
    assert.equal(captured.init?.body, undefined);
    assert.equal(captured.init?.credentials, "include");
    assert.equal(captured.init?.redirect, "error");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("runtime context propagates a backend credential rejection", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "mcp_gateway_unauthorized" }), {
      status: 401,
    })) as typeof fetch;

  try {
    await assert.rejects(
      createMcpRuntimeContext(),
      (error: unknown) => error instanceof ApiRequestError && error.status === 401,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("runtime context discard is an opaque principal-scoped DELETE", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  try {
    await discardMcpRuntimeContext("mcpctx/a");
    assert.equal(calls.length, 1);
    assert.equal(calls[0]?.url, "/api/ai/mcp/runtime-contexts/mcpctx%2Fa");
    assert.equal(calls[0]?.init?.method, "DELETE");
    assert.equal(calls[0]?.init?.credentials, "include");
    assert.ok(calls[0]?.init?.signal instanceof AbortSignal);
    assert.equal(
      new Headers(calls[0]?.init?.headers).get("JWT-Authorization"),
      null,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("runtime context discard aborts a stalled request after one second", async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  let capturedSignal: AbortSignal | null = null;
  let abortObserved = false;
  let timeoutMs: number | undefined;

  globalThis.setTimeout = ((callback: TimerHandler, timeout?: number) => {
    timeoutMs = timeout;
    queueMicrotask(() => {
      if (typeof callback === "function") callback();
    });
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as unknown as typeof setTimeout;
  globalThis.clearTimeout = (() => {}) as typeof clearTimeout;
  globalThis.fetch = (async (_input, init) =>
    new Promise<Response>((_resolve, reject) => {
      capturedSignal = init?.signal ?? null;
      const abort = () => {
        abortObserved = capturedSignal?.aborted === true;
        reject(new Error("discard aborted"));
      };
      if (capturedSignal?.aborted) abort();
      else capturedSignal?.addEventListener("abort", abort, { once: true });
    })) as typeof fetch;

  try {
    await discardMcpRuntimeContext("mcpctx-stalled");
    assert.equal(timeoutMs, 1_000);
    assert.equal(abortObserved, true);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test("unrelated 401 remains a normal API error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "session_expired" }), {
      status: 401,
    })) as typeof fetch;

  try {
    await assert.rejects(
      createMcpRuntimeContext(),
      (error: unknown) => error instanceof ApiRequestError && error.status === 401,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("chat preparation requests a backend-owned context for every selected platform MCP", async () => {
  const originalFetch = globalThis.fetch;
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
  }
});
