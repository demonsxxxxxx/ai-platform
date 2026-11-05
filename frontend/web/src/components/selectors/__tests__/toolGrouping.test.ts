import assert from "node:assert/strict";
import test from "node:test";

import type { ToolState } from "../../../types";
import { groupKeyForTool, groupLabelForTool } from "../toolGrouping";

function tool(name: string, server: string, upstream_server?: string | null): ToolState {
  return {
    name,
    description: "Synthetic",
    category: "mcp",
    server,
    ...(upstream_server === undefined ? {} : { upstream_server }),
    parameters: [],
    enabled: false,
  };
}

test("MCP group keys preserve connection identity for same upstream labels", () => {
  const first = tool("gateway-a::search", "gateway-a", "same-project");
  const second = tool("gateway-b::search", "gateway-b", "same-project");
  assert.notEqual(groupKeyForTool(first), groupKeyForTool(second));
  assert.equal(groupLabelForTool(first), "same-project");
  assert.equal(groupLabelForTool(second), "same-project");
});

test("direct and unknown Gateway source groups remain explicit", () => {
  const direct = tool("gateway::search", "gateway");
  const upstreamDirect = tool("gateway::upstream-direct", "gateway", "direct");
  const upstreamUnknown = tool("gateway::upstream-unknown", "gateway", "unknown");
  const unknown = tool("gateway::legacy", "gateway", null);
  assert.equal(groupLabelForTool(direct), "gateway");
  assert.equal(groupLabelForTool(unknown), null);
  assert.notEqual(groupKeyForTool(direct), groupKeyForTool(upstreamDirect));
  assert.notEqual(groupKeyForTool(upstreamUnknown), groupKeyForTool(unknown));
});
