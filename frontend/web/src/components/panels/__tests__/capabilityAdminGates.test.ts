import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  canManageMcpLifecycle,
  canManageSharedMarketplace,
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

test("shared marketplace and mcp management stay ai-admin only", () => {
  assert.equal(
    canManageSharedMarketplace({
      isOwner: true,
      hasMarketplaceAdminPermission: true,
      isAiAdmin: false,
    }),
    false,
  );
  assert.equal(
    canManageSharedMarketplace({
      isOwner: false,
      hasMarketplaceAdminPermission: false,
      isAiAdmin: true,
    }),
    true,
  );
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

test("skills and mcp panels wire unified capability distribution admin sections behind ai-admin checks", () => {
  const skillsPanelSource = readFileSync(
    join(import.meta.dirname, "..", "SkillsPanel", "index.tsx"),
    "utf8",
  );
  const mcpPanelSource = readFileSync(
    join(import.meta.dirname, "..", "MCPPanel.tsx"),
    "utf8",
  );

  assert.match(skillsPanelSource, /CapabilityDistributionAdminCard/);
  assert.match(skillsPanelSource, /isAiAdminUser\(user\)/);
  assert.match(skillsPanelSource, /renderAdminSection=\{renderDistributionAdmin\}/);
  assert.match(mcpPanelSource, /CapabilityDistributionAdminCard/);
  assert.match(mcpPanelSource, /isAiAdminUser\(user\)/);
});
