import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("frontend shell parity components are registered", () => {
  const files = [
    "src/components/workbench/WorkbenchShell.tsx",
    "src/components/workbench/WorkbenchRightPanel.tsx",
    "src/components/chat/ComposerChips.tsx",
    "src/components/governance/GovernanceAvailabilityBadge.tsx",
    "src/components/channels/ChannelImportPanel.tsx",
    "src/components/share/ShareUnavailableState.tsx",
  ];

  for (const file of files) {
    assert.match(readFileSync(join(root, file), "utf8"), /export /, file);
  }
});

test("app routes expose PRD phase 1B and 1C surfaces", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );

  for (const route of ["/chat", "/apps", "/skills", "/marketplace", "/mcp"]) {
    assert.match(app, new RegExp(`path="${route.replace("/", "\\/")}`));
  }
  assert.match(app, /path="\/channels\/:channelType\?\/:instanceId\?"/);

  assert.match(tabs, /apps:\s*LaunchpadPanel/);
  assert.match(tabs, /skills:\s*SkillsHubPanel/);
  assert.match(tabs, /marketplace:\s*SkillsHubPanel/);
  assert.match(tabs, /mcp:\s*MCPPanel/);
  assert.match(tabs, /channels:\s*ChannelImportPanel/);
});

test("phase 1C discovery routes are login reachable and fail closed inside pages", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");

  for (const route of [
    "/skills",
    "/marketplace",
    "/mcp",
    "/channels/:channelType?/:instanceId?",
  ]) {
    const routePattern = new RegExp(
      `path="${route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"[\\s\\S]{0,260}<ProtectedRoute>[\\s\\S]{0,180}<`,
    );
    assert.match(
      app,
      routePattern,
      `${route} should render inside the authenticated shell without route-level business permission redirects`,
    );
  }

  for (const [route, page] of [
    ["/users", "UsersPage"],
    ["/roles", "RolesPage"],
    ["/settings", "SettingsPage"],
  ]) {
    const adminRoutePattern = new RegExp(
      `path="${route}"[\\s\\S]{0,520}<ProtectedRoute[\\s\\S]{0,180}permissions=\\{\\[[\\s\\S]{0,260}<${page} \\/>`,
    );
    assert.match(
      app,
      adminRoutePattern,
      `${page} should remain route-gated because it exposes admin-only operations`,
    );
  }
});

test("legacy brand authority is absent from active browser entry", () => {
  const index = readFileSync(join(root, "index.html"), "utf8");
  assert.doesNotMatch(index, /\bLambChat\b|lambchat\.com/i);
  assert.match(index, /AI Platform - Enterprise AI Workbench/);
});
