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
      { label: "已配置 MCP 服务", reason: "discovery_failed" },
      { label: "已配置 MCP 服务", reason: "private_reason=discarded" },
    ],
    count: 0,
  });
  assert.equal(classifyChatMcpCatalog(degraded), "degraded");
  assert.deepEqual(degraded.unavailable, [
    "tools.catalog.unavailable.discoveryFailed",
    "tools.catalog.unavailable.generic",
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
  assert.deepEqual(catalog.unavailable, ["tools.catalog.unavailable.discoveryFailed"]);
  assert.deepEqual(reconcileChatMcpToolSelection(["gateway::stale"], catalog.tools, "degraded"), []);
});
