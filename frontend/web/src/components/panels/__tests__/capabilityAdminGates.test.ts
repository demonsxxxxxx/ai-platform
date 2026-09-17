import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  canManageMcpLifecycle,
  isAiAdminRoleUser,
  isAiAdminUser,
} from "../capabilityAdmin.ts";

test("capability admin helpers prefer backend admin projection and keep role alias fallback", () => {
  assert.equal(
    isAiAdminUser({ roles: ["user"], is_admin: true }),
    true,
  );
  assert.equal(isAiAdminRoleUser(["developer"]), true);
  assert.equal(isAiAdminRoleUser(["platform_admin"]), true);
  assert.equal(isAiAdminRoleUser(["auditor"]), false);
});

test("mcp management stays ai-admin only", () => {
  assert.equal(
    canManageMcpLifecycle({
      hasExplicitMcpPermission: true,
      isAiAdmin: false,
    }),
    false,
  );
  assert.equal(
    canManageMcpLifecycle({
      hasExplicitMcpPermission: false,
      isAiAdmin: true,
    }),
    true,
  );
});

test("mcp panel gates shared admin actions on ai-admin projection", () => {
  const mcpPanelSource = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(mcpPanelSource, /isAiAdminUser\(user\)/);
  assert.match(mcpPanelSource, /canManageMcpLifecycle\(\{/);
  assert.match(mcpPanelSource, /canManageMcp && !mcpGovernance\.governedUnavailable/);
  assert.match(mcpPanelSource, /data-mcp-admin-controls/);
  assert.doesNotMatch(mcpPanelSource, /CapabilityDistributionAdminCard/);
  assert.doesNotMatch(mcpPanelSource, /useCapabilityDistributions/);
});
