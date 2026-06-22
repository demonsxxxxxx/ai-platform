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

test("authenticated sidebar uses governed workbench entries instead of old plaza shortcuts", () => {
  const sidebar = [
    readFileSync(join(root, "src/components/panels/SessionSidebar.tsx"), "utf8"),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SessionListContent.tsx"),
      "utf8",
    ),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
      "utf8",
    ),
  ].join("\n");

  assert.match(sidebar, /navigate\("\/marketplace"\)/);
  assert.match(sidebar, /navigate\("\/mcp"\)/);
  assert.match(sidebar, /navigate\("\/apps"\)/);
  assert.doesNotMatch(sidebar, /navigate\("\/persona"\)/);
  assert.doesNotMatch(sidebar, /navigate\("\/files"\)/);
  assert.doesNotMatch(sidebar, /onOpenPersonaPlaza|onOpenFileLibrary/);
  assert.doesNotMatch(sidebar, /hasMoreMenuItems|MobileMoreMenuSheet|DesktopMoreMenu/);
  assert.doesNotMatch(sidebar, /font-serif|icons\/icon\.svg/);
});

test("authenticated chat workspace keeps one enterprise surface instead of split white canvas", () => {
  const surface = readFileSync(
    join(root, "src/components/workbench/workbenchSurface.ts"),
    "utf8",
  );
  const rightPanel = readFileSync(
    join(root, "src/components/workbench/WorkbenchRightPanel.tsx"),
    "utf8",
  );
  const theme = readFileSync(join(root, "src/styles/base.css"), "utf8");

  assert.match(surface, /root:\s*clsx\(\s*"flex min-h-0 flex-1 bg-slate-100/);
  assert.match(surface, /thread:[\s\S]*bg-slate-100/);
  assert.match(surface, /composer:[\s\S]*bg-slate-100/);
  assert.match(surface, /context:[\s\S]*bg-slate-100/);
  assert.match(surface, /secondaryPanel:/);
  assert.match(rightPanel, /workbenchSurface\.secondaryPanel/);
  assert.match(theme, /--theme-bg:\s*#f3f5f8;/);
  assert.match(theme, /--theme-bg-sidebar:\s*#f3f5f8;/);
  assert.doesNotMatch(surface, /thread:[\s\S]{0,180}bg-white/);
});

test("authenticated shell chrome avoids legacy playful branding accents", () => {
  const chrome = [
    readFileSync(
      join(root, "src/components/layout/AppContent/Header.tsx"),
      "utf8",
    ),
    readFileSync(join(root, "src/components/layout/UserMenu.tsx"), "utf8"),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SessionListContent.tsx"),
      "utf8",
    ),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
      "utf8",
    ),
  ].join("\n");

  assert.doesNotMatch(chrome, /font-serif|from-amber-400|to-orange-500/);
  assert.doesNotMatch(chrome, /icons\/icon\.svg/);
  assert.match(chrome, /bg-teal-700/);
});

test("legacy brand authority is absent from active browser entry", () => {
  const index = readFileSync(join(root, "index.html"), "utf8");
  assert.doesNotMatch(index, /\bLambChat\b|lambchat\.com/i);
  assert.match(index, /AI Platform - Enterprise AI Workbench/);
});
