import assert from "node:assert/strict";
import test from "node:test";

import type { MCPToolInfo } from "../../../types";
import {
  beginOrdinaryMcpToolDiscovery,
  buildOrdinaryMcpServerSetKey,
  collectOrdinaryMcpTools,
  ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY,
  publishOrdinaryMcpToolDiscovery,
  resolveVisibleOrdinaryMcpToolDiscovery,
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

test("cancelled ordinary discovery does not dequeue after its active workers finish", async () => {
  const serverNames = Array.from({ length: 20 }, (_, index) => `server-${index}`);
  const started: string[] = [];
  const resolvers: Array<(tools: MCPToolInfo[]) => void> = [];
  let activeGeneration = true;

  const pending = collectOrdinaryMcpTools(
    serverNames,
    async (serverName) => {
      started.push(serverName);
      return new Promise<MCPToolInfo[]>((resolve) => resolvers.push(resolve));
    },
    () => activeGeneration,
  );

  assert.equal(started.length, ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY);
  activeGeneration = false;
  resolvers.forEach((resolve, index) => resolve([tool(`active-${index}`)]));
  await pending;

  assert.equal(started.length, ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY);
});

test("an empty ordinary MCP catalog clears tools, unavailable state, and loading", () => {
  const loading = beginOrdinaryMcpToolDiscovery(4, ["stale"]);
  const empty = beginOrdinaryMcpToolDiscovery(5, []);

  assert.equal(loading.toolsLoading, true);
  assert.deepEqual(empty, {
    generation: 5,
    serverSetKey: buildOrdinaryMcpServerSetKey([]),
    toolsByServer: {},
    toolsLoading: false,
    toolsUnavailable: false,
  });
});

test("a mismatched ordinary server set synchronously masks prior discovery state", () => {
  const prior = {
    ...beginOrdinaryMcpToolDiscovery(8, ["old-server"]),
    toolsByServer: { "old-server": [tool("old-server")] },
    toolsLoading: true,
    toolsUnavailable: true,
  };
  const visible = resolveVisibleOrdinaryMcpToolDiscovery(prior, ["new-server"]);

  assert.deepEqual(visible, {
    generation: 8,
    serverSetKey: buildOrdinaryMcpServerSetKey(["new-server"]),
    toolsByServer: {},
    toolsLoading: false,
    toolsUnavailable: false,
  });
});
