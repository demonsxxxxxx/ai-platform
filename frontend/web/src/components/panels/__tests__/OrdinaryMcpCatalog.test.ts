import assert from "node:assert/strict";
import test from "node:test";

import type { MCPToolInfo } from "../../../types";
import {
  beginOrdinaryMcpToolDiscovery,
  collectOrdinaryMcpTools,
  ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY,
  publishOrdinaryMcpToolDiscovery,
} from "../OrdinaryMcpCatalog.tsx";

function tool(serverName: string): MCPToolInfo {
  return {
    name: `${serverName}-tool`,
    description: `${serverName} public tool`,
    parameters: [],
  };
}

test("ordinary MCP discovery bounds in-flight requests across a large catalog", async () => {
  const serverNames = Array.from({ length: 24 }, (_, index) => `server-${index}`);
  let inFlight = 0;
  let maxInFlight = 0;

  const result = await collectOrdinaryMcpTools(serverNames, async (serverName) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 1));
    inFlight -= 1;
    return [tool(serverName)];
  });

  assert.equal(maxInFlight, ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY);
  assert.equal(result.unavailable, false);
  assert.equal(Object.keys(result.toolsByServer).length, 24);
});

test("an older ordinary MCP tool generation cannot publish after a newer one", () => {
  const prior = beginOrdinaryMcpToolDiscovery(1, ["old"]);
  const current = beginOrdinaryMcpToolDiscovery(2, ["new"]);
  const published = publishOrdinaryMcpToolDiscovery(current, prior.generation, {
    toolsByServer: { old: [tool("old")] },
    unavailable: false,
  });

  assert.deepEqual(published, current);
});

test("an empty ordinary MCP catalog clears tools, unavailable state, and loading", () => {
  const loading = beginOrdinaryMcpToolDiscovery(4, ["stale"]);
  const empty = beginOrdinaryMcpToolDiscovery(5, []);

  assert.equal(loading.toolsLoading, true);
  assert.deepEqual(empty, {
    generation: 5,
    toolsByServer: {},
    toolsLoading: false,
    toolsUnavailable: false,
  });
});
