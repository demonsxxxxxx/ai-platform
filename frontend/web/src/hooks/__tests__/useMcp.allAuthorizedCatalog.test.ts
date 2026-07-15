import assert from "node:assert/strict";
import test from "node:test";

import type { MCPServerResponse, MCPServersResponse } from "../../types";
import {
  collectAllAuthorizedMcpServers,
  resolveMcpServersAfterListFailure,
} from "../useMcp.ts";

function server(name: string): MCPServerResponse {
  return {
    name,
    transport: "sse",
    enabled: true,
    is_system: true,
    can_edit: false,
    allowed_roles: [],
    role_quotas: {},
  };
}

function page(
  servers: MCPServerResponse[],
  total: number,
  skip: number,
  limit: number,
): MCPServersResponse {
  return { servers, total, skip, limit };
}

test("complete authorized MCP catalog retrieves more than 200 servers", async () => {
  const everyServer = Array.from({ length: 201 }, (_, index) =>
    server(`server-${index + 1}`),
  );
  const requests: Array<{ skip: number; limit: number }> = [];

  const result = await collectAllAuthorizedMcpServers(async (params) => {
    requests.push(params);
    return page(
      everyServer.slice(params.skip, params.skip + params.limit),
      everyServer.length,
      params.skip,
      params.limit,
    );
  });

  assert.equal(result.servers.length, 201);
  assert.equal(result.total, 201);
  assert.deepEqual(requests, [
    { skip: 0, limit: 200 },
    { skip: 200, limit: 200 },
  ]);
});

test("complete authorized MCP catalog rejects offset mismatches", async () => {
  await assert.rejects(
    () =>
      collectAllAuthorizedMcpServers(async (params) =>
        page([server("alpha")], 1, params.skip + 1, params.limit),
      ),
    /authorized_mcp_catalog_offset_mismatch/,
  );
});

test("complete authorized MCP catalog rejects a duplicate page with no progress", async () => {
  await assert.rejects(
    () =>
      collectAllAuthorizedMcpServers(async (params) =>
        page([server("alpha")], 2, params.skip, params.limit),
      ),
    /authorized_mcp_catalog_no_progress/,
  );
});

test("complete authorized MCP catalog rejects an incomplete empty page", async () => {
  await assert.rejects(
    () =>
      collectAllAuthorizedMcpServers(async (params) =>
        page([], 1, params.skip, params.limit),
      ),
    /authorized_mcp_catalog_incomplete/,
  );
});

test("complete authorized MCP catalog clears stale entries after a failed read", () => {
  const current = [server("stale")];

  assert.deepEqual(resolveMcpServersAfterListFailure(current, true), []);
  assert.equal(resolveMcpServersAfterListFailure(current, false), current);
});
