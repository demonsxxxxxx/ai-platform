import assert from "node:assert/strict";
import test from "node:test";

import type { MCPServerResponse, MCPServersResponse } from "../../types";
import {
  beginMcpCatalogRequest,
  buildMcpRequestScope,
  collectAllAuthorizedMcpServers,
  publishMcpCatalogSuccess,
  resolveMcpServersAfterListFailure,
  resolveVisibleMcpCatalogState,
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

test("a delayed ordinary catalog result cannot overwrite a newer admin page", () => {
  const ordinaryScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: true,
  });
  const adminScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: false,
    listParams: { skip: 20, limit: 20, q: "newer" },
  });
  const ordinaryRequest = { scope: ordinaryScope, epoch: 1 };
  const adminRequest = { scope: adminScope, epoch: 2 };
  let state = beginMcpCatalogRequest(
    {
      request: { scope: ordinaryScope, epoch: 0 },
      servers: [],
      total: 0,
      isLoading: false,
      error: null,
    },
    ordinaryRequest,
  );
  state = beginMcpCatalogRequest(state, adminRequest);

  state = publishMcpCatalogSuccess(
    state,
    ordinaryRequest,
    page([server("ordinary")], 1, 0, 200),
  );

  assert.equal(state.request.scope, adminScope);
  assert.deepEqual(state.servers, []);
  assert.equal(state.isLoading, true);
});

test("a delayed admin page cannot overwrite a newer ordinary catalog result", () => {
  const adminScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: false,
    listParams: { skip: 0, limit: 20, q: "older" },
  });
  const ordinaryScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: true,
  });
  const adminRequest = { scope: adminScope, epoch: 1 };
  const ordinaryRequest = { scope: ordinaryScope, epoch: 2 };
  let state = beginMcpCatalogRequest(
    {
      request: { scope: adminScope, epoch: 0 },
      servers: [],
      total: 0,
      isLoading: false,
      error: null,
    },
    adminRequest,
  );
  state = beginMcpCatalogRequest(state, ordinaryRequest);

  state = publishMcpCatalogSuccess(
    state,
    adminRequest,
    page([server("admin")], 1, 0, 20),
  );

  assert.equal(state.request.scope, ordinaryScope);
  assert.deepEqual(state.servers, []);
  assert.equal(state.isLoading, true);
});

test("a changed MCP request scope exposes no prior servers before its request starts", () => {
  const adminScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: false,
    listParams: { skip: 0, limit: 20, q: "admin" },
  });
  const ordinaryScope = buildMcpRequestScope({
    enabled: true,
    allAuthorizedCatalog: true,
  });
  const visible = resolveVisibleMcpCatalogState(
    {
      request: { scope: adminScope, epoch: 3 },
      servers: [server("admin-only")],
      total: 1,
      isLoading: false,
      error: null,
    },
    ordinaryScope,
    true,
  );

  assert.deepEqual(visible.servers, []);
  assert.equal(visible.total, 0);
  assert.equal(visible.isLoading, true);
  assert.equal(visible.error, null);
});
