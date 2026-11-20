import assert from "node:assert/strict";
import test from "node:test";

import {
  beginChatMcpCatalogRequest,
  classifyChatMcpCatalog,
  parseChatMcpCatalogResponse,
  publishChatMcpCatalogFailure,
  publishChatMcpCatalogSuccess,
  reconcileChatMcpToolSelection,
} from "../useTools.ts";

function response(overrides: Record<string, unknown> = {}) {
  return {
    tools: [
      {
        tool_id: "gateway::tenant-search",
        label: "tenant-search",
        description: "由平台治理的工具。",
        category: "mcp",
        server: "gateway",
        cached: false,
      },
    ],
    unavailable: [],
    count: 1,
    ...overrides,
  };
}

test("validates ready, empty, and degraded Chat MCP catalogs without retaining private fields", () => {
  const ready = parseChatMcpCatalogResponse(
    response({
      selected_mcp_tool_ids: ["gateway::tenant-search"],
      private_server: "must-not-reach-tools",
    }),
  );
  assert.equal(classifyChatMcpCatalog(ready), "ready");
  assert.deepEqual(ready.tools, [
    {
      name: "gateway::tenant-search",
      label: "tenant-search",
      description: "由平台治理的工具。",
      category: "mcp",
      server: "gateway",
      parameters: [],
      system_disabled: false,
      user_disabled: false,
      enabled: false,
    },
  ]);

  const empty = parseChatMcpCatalogResponse({ tools: [], unavailable: [], count: 0 });
  assert.equal(classifyChatMcpCatalog(empty), "empty");

  const degraded = parseChatMcpCatalogResponse({
    tools: [],
    unavailable: [
      { label: "gateway-a", reason: "discovery_failed" },
      { label: "gateway-b", reason: "private_reason=discarded" },
    ],
    count: 0,
  });
  assert.equal(classifyChatMcpCatalog(degraded), "degraded");
  assert.deepEqual(degraded.unavailable, [
    { label: "gateway-a", reasonKey: "tools.catalog.unavailable.discoveryFailed" },
    { label: "gateway-b", reasonKey: "tools.catalog.unavailable.generic" },
  ]);
});

test("projects optional Gateway upstream source metadata without exposing extra fields", () => {
  const catalog = parseChatMcpCatalogResponse(response({
    tools: [{
      ...response().tools[0],
      upstream_server: "项目一",
      gateway_internal_id: "must-not-reach-tools",
    }],
  }));
  assert.equal(catalog.tools[0].upstream_server, "项目一");
  assert.equal("gateway_internal_id" in catalog.tools[0], false);

  const unknown = parseChatMcpCatalogResponse(response({
    tools: [{ ...response().tools[0], upstream_server: null }],
  }));
  assert.equal(unknown.tools[0].upstream_server, null);
  const malformedSource = parseChatMcpCatalogResponse(response({
    tools: [{ ...response().tools[0], upstream_server: 42 }],
  }));
  assert.equal(malformedSource.tools.length, 1);
  assert.equal(malformedSource.tools[0].upstream_server, null);

  const oversizedSource = parseChatMcpCatalogResponse(response({
    tools: [{ ...response().tools[0], upstream_server: "x".repeat(129) }],
  }));
  assert.equal(oversizedSource.tools[0].upstream_server, null);

  assert.throws(
    () => parseChatMcpCatalogResponse(response({
      tools: [{ ...response().tools[0], tool_id: 42 }],
    })),
    /chat_mcp_catalog_tool_invalid/,
  );
});

test("unavailable services retain only authorized connection labels and fixed reasons", () => {
  const catalog = parseChatMcpCatalogResponse(response({ unavailable: [
    { label: "gateway-a", reason: "authorization_required", message: "private-secret" },
    { label: "gateway-b", reason: "no_tools" },
    { label: "https://private.example/?token=secret", reason: "private-secret" },
    { label: "gateway\n", reason: "private-secret" },
  ] }));
  assert.deepEqual(catalog.unavailable, [
    { label: "gateway-a", reasonKey: "tools.catalog.unavailable.authorizationRequired" },
    { label: "gateway-b", reasonKey: "tools.catalog.unavailable.noTools" },
    { reasonKey: "tools.catalog.unavailable.generic" },
    { reasonKey: "tools.catalog.unavailable.generic" },
  ]);
  assert.doesNotMatch(JSON.stringify(catalog), /private|token|secret/);
});

test("rejects malformed responses instead of treating them as an empty catalog", () => {
  assert.throws(
    () => parseChatMcpCatalogResponse(response({ count: 2 })),
    /chat_mcp_catalog_count_invalid/,
  );
  assert.throws(
    () =>
      parseChatMcpCatalogResponse(
        response({ unavailable: [{ label: "已配置 MCP 服务", reason: 42 }] }),
      ),
    /chat_mcp_catalog_unavailable_invalid/,
  );
});

test("a stale response cannot overwrite a retry and only the latest catalog authorizes selection", () => {
  const staleCatalog = parseChatMcpCatalogResponse(response());
  const retryCatalog = parseChatMcpCatalogResponse({
    tools: [
      {
        tool_id: "gateway::current-tool",
        label: "当前 MCP 工具",
        description: "当前目录中的工具。",
        category: "mcp",
        server: "gateway",
        cached: false,
      },
    ],
    unavailable: [],
    count: 1,
  });

  let state = beginChatMcpCatalogRequest(1);
  state = beginChatMcpCatalogRequest(2);
  state = publishChatMcpCatalogSuccess(state, 1, staleCatalog);
  assert.equal(state.status, "loading");
  assert.deepEqual(state.tools, []);

  state = publishChatMcpCatalogSuccess(state, 2, retryCatalog);
  assert.equal(state.status, "ready");
  assert.deepEqual(state.tools.map((tool) => tool.name), ["gateway::current-tool"]);
  assert.deepEqual(
    reconcileChatMcpToolSelection(["gateway::current-tool", "gateway::stale-tool"], state.tools, state.status),
    ["gateway::current-tool"],
  );
  assert.deepEqual(
    reconcileChatMcpToolSelection(["gateway::current-tool"], state.tools, "loading"),
    [],
  );

  state = publishChatMcpCatalogFailure(state, 2);
  assert.equal(state.status, "error");
  assert.deepEqual(
    reconcileChatMcpToolSelection(["gateway::current-tool"], state.tools, state.status),
    [],
  );
});


test("MCP operational unavailability remains degraded with localized discovery copy", () => {
  const catalog = parseChatMcpCatalogResponse({
    tools: [],
    count: 0,
    unavailable: [{ label: "gateway", reason: "discovery_failed" }],
  });
  assert.equal(classifyChatMcpCatalog(catalog), "degraded");
  assert.deepEqual(catalog.unavailable, [{ label: "gateway", reasonKey: "tools.catalog.unavailable.discoveryFailed" }]);
  assert.deepEqual(reconcileChatMcpToolSelection(["gateway::stale"], catalog.tools, "degraded"), []);
});

test("default selection is an exact subset of the current discovered catalog", () => {
  assert.deepEqual(parseChatMcpCatalogResponse(response({ default_mcp_tool_ids: ["gateway::tenant-search"] })).defaultMcpToolIds, ["gateway::tenant-search"]);
  for (const defaults of [null, "gateway::tenant-search", ["gateway::unknown"], ["gateway::tenant-search", "gateway::tenant-search"]]) {
    assert.throws(() => parseChatMcpCatalogResponse(response({ default_mcp_tool_ids: defaults })), /chat_mcp_catalog_defaults_invalid/);
  }
});

test("session and identity changes synchronously hide the old catalog before new defaults arrive", async () => {
  const { installSelectTestDom } = await import("../../components/common/__tests__/selectTestDom");
  const dom = installSelectTestDom();
  const { act, createElement, useEffect, useState } = await import("react");
  const { createLobeTestRoot } = await import("../../components/common/__tests__/lobeUiTestRoot");
  const { useTools, hasValidatedChatMcpCatalog } = await import("../useTools");
  const container = document.createElement("div"); document.body.append(container);
  const root = await createLobeTestRoot(container);
  const oldFetch = globalThis.fetch;
  let pending!: (value: Response) => void;
  let current!: ReturnType<typeof useTools>;
  let selected: string[] | undefined;
  let choose!: (value: string[] | undefined) => void;
  const observations: { session: string | null; status: string; ids: string[] | undefined }[] = [];
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  function Probe({ session, identity }: { session: string | null; identity: string }) {
    const catalog = useTools({ sessionId: session, identityScope: identity });
    current = catalog;
    const [selection, setSelection] = useState<string[]>();
    selected = selection; choose = setSelection;
    observations.push({ session, status: current.catalogState.status, ids: current.serverSelectedToolIds });
    useEffect(() => {
      if (selection === undefined && hasValidatedChatMcpCatalog(catalog.catalogState.status)) setSelection(catalog.serverSelectedToolIds);
    }, [selection, catalog.serverSelectedToolIds, catalog.catalogState.status]);
    return null;
  }
  try {
    for (const prior of [[], ["gateway::tenant-search"]]) {
      globalThis.fetch = (async () => json(response({ selected_mcp_tool_ids: prior }))) as typeof fetch;
      await act(async () => { root.render(createElement(Probe, { session: "old-session", identity: "user-a" })); });
      await act(async () => choose(undefined));
      const first = observations.length;
      globalThis.fetch = (async () => new Promise<Response>(done => { pending = done; })) as typeof fetch;
      await act(async () => { choose(undefined); root.render(createElement(Probe, { session: null, identity: "user-a" })); });
      assert.equal(observations[first].status, "loading");
      assert.equal(observations[first].ids, undefined);
      assert.equal(selected, undefined);
      await act(async () => pending(json(response({ default_mcp_tool_ids: ["gateway::tenant-search"] }))));
      assert.deepEqual(selected, ["gateway::tenant-search"]);
      await act(async () => choose([]));
      await act(async () => { void current.refreshTools(); });
      await act(async () => pending(json(response({ default_mcp_tool_ids: ["gateway::tenant-search"] }))));
      assert.deepEqual(selected, [], "an explicit new-chat cancellation survives refresh");
    }
    const oldRefresh = current.refreshTools;
    const first = observations.length;
    await act(async () => root.render(createElement(Probe, { session: null, identity: "user-b" })));
    assert.equal(observations[first].status, "loading");
    let unexpected = false;
    globalThis.fetch = (async () => { unexpected = true; return json(response()); }) as typeof fetch;
    await act(async () => oldRefresh());
    assert.equal(unexpected, false, "retained old identity callbacks cannot request new data");
  } finally { await act(async () => root.unmount()); container.remove(); globalThis.fetch = oldFetch; dom.close(); }
});
