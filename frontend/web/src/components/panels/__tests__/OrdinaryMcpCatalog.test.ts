import assert from "node:assert/strict";
import test from "node:test";

import type { MCPToolInfo } from "../../../types";
import { collectMcpTools, MCP_TOOL_DISCOVERY_CONCURRENCY } from "../../mcp/useMcpToolDiscovery";

function response(name: string, tools: MCPToolInfo[] = [tool(name)]) {
  return { server_name: name, tools, count: tools.length, unavailable_reason: null };
}
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

  const result = await collectMcpTools(serverNames, async (serverName) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 1));
    inFlight -= 1;
    return response(serverName);
  });

  assert.equal(maxInFlight, MCP_TOOL_DISCOVERY_CONCURRENCY);
  assert.equal(result.unavailableServers.size, 0);
  assert.equal(result.toolsByServer.size, 24);
});

test("HTTP-200 discovery failure stays unavailable while other servers retain their tools", async () => {
  const result = await collectMcpTools(["failed", "healthy"], async (name) =>
    name === "failed" ? { ...response(name, []), unavailable_reason: "discovery_failed" } : response(name),
  );
  assert.deepEqual(Array.from(result.unavailableServers), ["failed"]);
  assert.deepEqual(result.toolsByServer.get("healthy"), [tool("healthy")]);
  assert.equal(result.toolsByServer.has("failed"), false);
});

test("malformed, mismatched and duplicate catalogs cannot supply a successful count", async () => {
  for (const invalid of [
    { ...response("bad"), count: 2 },
    { ...response("bad"), server_name: "other" },
    { ...response("bad"), unavailable_reason: undefined },
    { ...response("bad"), tools: null },
    response("bad", [tool("bad"), tool("bad")]),
    { ...response("bad"), tools: [{ ...tool("bad"), description: undefined }] },
  ]) {
    const result = await collectMcpTools(["bad"], async () => invalid as ReturnType<typeof response>);
    assert.equal(result.toolsByServer.has("bad"), false);
    assert.equal(result.unavailableServers.has("bad"), true);
  }
});

test("cancelled ordinary discovery does not dequeue after its active workers finish", async () => {
  const serverNames = Array.from({ length: 20 }, (_, index) => `server-${index}`);
  const started: string[] = [];
  const resolvers: Array<(tools: MCPToolInfo[]) => void> = [];
  let activeGeneration = true;

  const pending = collectMcpTools(
    serverNames,
    async (serverName) => {
      started.push(serverName);
      return response(serverName, await new Promise<MCPToolInfo[]>((resolve) => resolvers.push(resolve)));
    },
    () => activeGeneration,
  );

  assert.equal(started.length, MCP_TOOL_DISCOVERY_CONCURRENCY);
  activeGeneration = false;
  resolvers.forEach((resolve, index) => resolve([tool(`active-${index}`)]));
  await pending;

  assert.equal(started.length, MCP_TOOL_DISCOVERY_CONCURRENCY);
});

test("successful zero tools and an empty server set are not failures", async () => {
  const empty = await collectMcpTools([], async () => { throw new Error("must not discover"); });
  assert.equal(empty.toolsByServer.size, 0);
  assert.equal(empty.unavailableServers.size, 0);
  const zero = await collectMcpTools(["zero"], async (name) => response(name, []));
  assert.deepEqual(zero.toolsByServer.get("zero"), []);
  assert.equal(zero.unavailableServers.size, 0);
});

test("server names cannot collide with object prototype properties", async () => {
  const result = await collectMcpTools(["constructor", "__proto__"], async (name) => response(name));
  assert.equal(result.toolsByServer.size, 2);
  assert.deepEqual(result.toolsByServer.get("constructor"), [tool("constructor")]);
  assert.deepEqual(result.toolsByServer.get("__proto__"), [tool("__proto__")]);
});
