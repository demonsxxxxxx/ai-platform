import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { resolveMcpGovernanceState } from "../mcpGovernanceState.ts";

test("MCP directory is ready for empty backed projections while permission proof is pending", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: false,
    canManageMcp: false,
    servers: [],
    total: 0,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.directoryAvailability.state, "disabled");
  assert.equal(state.lifecycleAvailability.state, "admin-only");
  assert.equal(state.credentialsAvailability.state, "admin-only");
  assert.equal(state.requiredPermission, "mcp:read");
});

test("MCP directory marks API permission denials as forbidden", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: false,
    canManageMcp: false,
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
      canManageMcp: false,
      servers: [],
      total: 0,
    }).pageState,
    "loading",
  );
  assert.equal(
    resolveMcpGovernanceState({
      isAuthenticated: false,
      canReadMcp: true,
      canManageMcp: false,
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
      canManageMcp: false,
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
    canManageMcp: true,
    servers: [
      {
        name: "repo-tools",
        transport: "streamable_http",
        url: "https://example.invalid/mcp",
        enabled: true,
        is_system: true,
        can_edit: true,
        allowed_roles: ["developer"],
        role_quotas: {},
        credential_state: "configured",
        created_at: "2026-06-24T00:00:00Z",
        updated_at: "2026-06-24T00:00:00Z",
      },
    ],
    total: 1,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.directoryAvailability.state, "enabled");
  assert.equal(state.lifecycleAvailability.state, "enabled");
  assert.equal(state.credentialsAvailability.state, "enabled");
});

test("MCP directory keeps lifecycle admin-only for ordinary users", () => {
  const state = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    canManageMcp: false,
    servers: [
      {
        name: "repo-tools",
        transport: "streamable_http",
        enabled: true,
        is_system: true,
        can_edit: false,
        allowed_roles: ["user"],
        role_quotas: {},
        credential_state: "configured",
      },
    ],
    total: 1,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.lifecycleAvailability.state, "admin-only");
  assert.equal(state.credentialsAvailability.state, "admin-only");
});

test("MCP directory degrades non-permission failures without opening lifecycle controls", () => {
  const empty = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    canManageMcp: false,
    servers: [],
    total: 0,
  });
  const failed = resolveMcpGovernanceState({
    isAuthenticated: true,
    canReadMcp: true,
    canManageMcp: true,
    servers: [],
    total: 0,
    loadError: "mcp projection unavailable",
  });

  assert.equal(empty.pageState, "ready");
  assert.equal(empty.directoryAvailability.state, "disabled");
  assert.equal(empty.lifecycleAvailability.state, "admin-only");
  assert.equal(failed.pageState, "degraded");
  assert.equal(failed.governedUnavailable, false);
  assert.equal(failed.lifecycleAvailability.state, "unavailable");
  assert.equal(failed.credentialsAvailability.state, "unavailable");
});

test("mcp panel gates lifecycle controls behind the AI-admin capability", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(source, /isAiAdminUser\(user\)/);
  assert.match(source, /canManageMcpLifecycle\(\{/);
  assert.doesNotMatch(source, /CapabilityDistributionAdminCard/);
  assert.doesNotMatch(source, /useCapabilityDistributions/);
  assert.match(source, /canManageMcp && !mcpGovernance\.governedUnavailable/);
  assert.match(source, /data-mcp-admin-controls/);
  assert.match(source, /createServer/);
  assert.match(source, /updateServer/);
  assert.match(source, /deleteServer/);
  assert.match(source, /toggleServer/);
  assert.doesNotMatch(source, /details=\{\[error\]\.filter/);
});

test("mcp editor traps focus and restores the invoking control", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(source, /editorDialogRef/);
  assert.match(source, /editorPreviousFocusRef/);
  assert.match(source, /editorLoadingRef/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /event\.key !== "Tab"/);
  assert.match(source, /event\.shiftKey/);
  assert.match(source, /data-mcp-editor-initial-focus/);
  assert.match(source, /ref=\{editorDialogRef\}/);
  assert.match(source, /editorPreviousFocusRef\.current\?\.focus\(\)/);
});

test("mcp lifecycle mutations keep the editor mounted", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(
    source,
    /const directoryIsLoading =[\s\S]*?authLoading \|\|[\s\S]*?isLoading &&[\s\S]*?servers\.length === 0 &&[\s\S]*?!editorOpen/,
  );
  assert.match(source, /isLoading: directoryIsLoading/);
});

test("mcp admin transient state closes when lifecycle authority disappears", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(
    source,
    /useEffect\(\(\) => \{[\s\S]*?if \(canManageMcpUi\) return;[\s\S]*?setEditorOpen\(false\);[\s\S]*?setEditorServer\(null\);[\s\S]*?setDeleteTarget\(null\);[\s\S]*?\}, \[canManageMcpUi\]\);/,
  );
  assert.match(source, /\}, \[editorOpen, canManageMcpUi\]\);/);
  assert.match(
    source,
    /isOpen=\{canManageMcpUi && Boolean\(deleteTarget\)\}/,
  );
  assert.match(source, /if \(!canManageMcpUi \|\| !deleteTarget\) return;/);
});
