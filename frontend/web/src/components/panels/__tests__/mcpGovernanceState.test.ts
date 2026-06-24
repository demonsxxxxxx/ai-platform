import test from "node:test";
import assert from "node:assert/strict";

import { resolveMcpGovernanceState } from "../mcpGovernanceState.ts";

test("MCP directory is ready for empty backed projections while permission proof is pending", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: false,
    servers: [],
    total: 0,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.directoryAvailability.state, "disabled");
  assert.equal(state.lifecycleAvailability.state, "admin-only");
  assert.equal(state.requiredPermission, "mcp:read");
});

test("MCP directory marks API permission denials as forbidden", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: false,
    servers: [],
    total: 0,
    loadError: "missing_permission:mcp:read",
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.governedUnavailable, true);
  assert.equal(state.directoryAvailability.state, "unavailable");
});

test("MCP directory exposes loading logged-out and no-workspace states", () => {
  assert.equal(
    resolveMcpGovernanceState({
      isAuthenticated: true,
      isLoading: true,
      canReadMcp: true,
      servers: [],
      total: 0,
    }).pageState,
    "loading",
  );
  assert.equal(
    resolveMcpGovernanceState({
      isAuthenticated: false,
      canReadMcp: true,
      servers: [],
      total: 0,
    }).pageState,
    "logged-out",
  );
  assert.equal(
    resolveMcpGovernanceState({
      isAuthenticated: true,
      hasWorkspace: false,
      canReadMcp: true,
      servers: [],
      total: 0,
    }).pageState,
    "no-workspace",
  );
});

test("MCP directory is ready when backend returns visible servers", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    servers: [
        {
          name: "repo-tools",
          transport: "streamable_http",
          url: "https://example.invalid/mcp",
          enabled: true,
          is_system: true,
          can_edit: false,
        allowed_roles: ["developer"],
        role_quotas: {},
        created_at: "2026-06-24T00:00:00Z",
        updated_at: "2026-06-24T00:00:00Z",
      },
    ],
    total: 1,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.directoryAvailability.state, "enabled");
  assert.equal(state.lifecycleAvailability.state, "admin-only");
});

test("MCP directory degrades empty and non-permission failures without opening lifecycle controls", () => {
  const empty = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    servers: [],
    total: 0,
  });
  const failed = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    servers: [],
    total: 0,
    loadError: "mcp projection unavailable",
  });

  assert.equal(empty.pageState, "ready");
  assert.equal(empty.directoryAvailability.state, "disabled");
  assert.equal(empty.lifecycleAvailability.state, "admin-only");
  assert.equal(failed.pageState, "degraded");
  assert.equal(failed.governedUnavailable, false);
});
