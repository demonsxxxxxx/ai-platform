import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
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
    "src/components/panels/ModelCatalogPanel.tsx",
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
  assert.match(tabs, /marketplace:\s*MarketplacePanel/);
  assert.match(tabs, /mcp:\s*MCPPanel/);
  assert.match(tabs, /channels:\s*ChannelImportPanel/);
  assert.match(tabs, /models:\s*ModelCatalogPanel/);
  assert.doesNotMatch(tabs, /models:\s*ModelPanel/);
  assert.doesNotMatch(tabs, /channels:\s*ChannelPanel/);
  assert.doesNotMatch(tabs, /models:\s*QuarantinedLegacyPanel/);
  for (const legacyPath of [
    "src/components/layout/AppContent/QuarantinedLegacyPanel.tsx",
    "src/components/panels/ChannelPanel.tsx",
    "src/components/panels/channel/feishu/FeishuPanel.tsx",
    "src/components/panels/ModelPanel/ModelPanel.tsx",
  ]) {
    assert.equal(existsSync(join(root, legacyPath)), false, legacyPath);
  }
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

  const rolesRoutePattern = new RegExp(
    `path="/roles"[\\s\\S]{0,760}<ProtectedRoute[\\s\\S]{0,220}permissions=\\{\\[[\\s\\S]{0,360}fallbackComponent=\\{[\\s\\S]{0,220}<WorkbenchForbiddenPage[\\s\\S]{0,360}<RolesPage \\/>`,
  );
  assert.match(
    app,
    rolesRoutePattern,
    "RolesPage should stay route-gated but render a workbench forbidden state instead of redirecting to chat",
  );

  for (const [route, page] of [
    ["/users", "UsersPage"],
    ["/settings", "SettingsPage"],
    ["/feedback", "FeedbackPage"],
    ["/agents", "AgentsPage"],
    ["/notifications", "NotificationsPage"],
  ]) {
    const phaseTwoRoutePattern = new RegExp(
      `path="${route}"[\\s\\S]{0,260}<ProtectedRoute>[\\s\\S]{0,220}<${page} \\/>[\\s\\S]{0,120}<\\/ProtectedRoute>`,
    );
    const phaseTwoPagePattern = new RegExp(
      `function ${page}\\(\\)[\\s\\S]{0,420}<PhaseTwoWorkbenchPage[\\s\\S]{0,180}activeTab="${route.slice(1)}"`,
    );
    assert.match(
      app,
      phaseTwoRoutePattern,
      `${page} should remain login reachable without route-level business permission redirects`,
    );
    assert.match(
      app,
      phaseTwoPagePattern,
      `${page} should render the shared degraded Phase 2 workbench state instead of loading the legacy panel`,
    );
  }

  assert.doesNotMatch(app, /redirectTo="\/chat"/);
  assert.match(app, /function WorkbenchForbiddenPage/);
  assert.match(app, /function PhaseTwoWorkbenchPage/);
  assert.match(app, /routeUnavailable=\{\{/);
});

test("platform admin projections can read the roles route without broadening role writes", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const authTypes = readFileSync(join(root, "src/types/auth.ts"), "utf8");
  const rolesPanel = readFileSync(
    join(root, "src/components/panels/RolesPanel.tsx"),
    "utf8",
  );

  const rolesRoute = app.match(/path="\/roles"[\s\S]{0,900}<RolesPage \/>/)?.[0] ?? "";
  assert.match(rolesRoute, /Permission\.ROLE_MANAGE/);
  assert.match(rolesRoute, /Permission\.SETTINGS_MANAGE/);
  assert.match(rolesRoute, /Permission\.AGENT_ADMIN/);
  assert.match(rolesRoute, /Permission\.ADMIN_STATUS/);
  assert.match(authTypes, /ADMIN_STATUS = "admin:status"/);
  assert.match(rolesPanel, /const canManage = hasPermission\(Permission\.ROLE_MANAGE\);/);
  assert.doesNotMatch(
    rolesPanel,
    /const canManage = hasAnyPermission\(\[[\s\S]*Permission\.AGENT_ADMIN/,
  );
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

  assert.match(sidebar, /navigate\("\/skills"\)/);
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
  const chatView = readFileSync(
    join(root, "src/components/layout/AppContent/ChatView.tsx"),
    "utf8",
  );
  const chatAppContent = readFileSync(
    join(root, "src/components/layout/AppContent/ChatAppContent.tsx"),
    "utf8",
  );
  const runPlayback = readFileSync(
    join(root, "src/components/layout/AppContent/RunPlaybackPanel.tsx"),
    "utf8",
  );
  const rightPanel = readFileSync(
    join(root, "src/components/workbench/WorkbenchRightPanel.tsx"),
    "utf8",
  );
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const mcpPanel = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  const theme = readFileSync(join(root, "src/styles/base.css"), "utf8");
  const authTheme = readFileSync(join(root, "src/styles/auth.css"), "utf8");

  assert.match(surface, /root:[\s\S]*bg-\[var\(--theme-bg\)\]/);
  assert.match(surface, /thread:[\s\S]*bg-\[var\(--theme-bg\)\]/);
  assert.match(surface, /composer:[\s\S]*bg-\[var\(--theme-bg\)\]/);
  assert.match(surface, /context:[\s\S]*bg-\[var\(--theme-bg-sidebar\)\]/);
  assert.match(surface, /panel:[\s\S]*bg-\[var\(--theme-bg-card\)\]/);
  assert.match(
    surface,
    /secondaryPanel:[\s\S]*bg-\[var\(--theme-bg-sidebar\)\]/,
  );
  assert.match(surface, /secondaryPanel:/);
  assert.match(rightPanel, /workbenchSurface\.secondaryPanel/);
  assert.match(theme, /--theme-bg:\s*#e8edf3;/);
  assert.match(theme, /--theme-bg-sidebar:\s*#e8edf3;/);
  assert.match(theme, /--theme-bg-card:\s*#ffffff;/);
  assert.match(authTheme, /html,\s*body\s*\{\s*background:\s*var\(--theme-bg\);/);
  assert.doesNotMatch(authTheme, /html,\s*body\s*\{\s*background:\s*#ffffff;/);
  assert.doesNotMatch(surface, /thread:[\s\S]{0,180}bg-white/);
  assert.doesNotMatch(surface, /context:[\s\S]{0,180}bg-white/);
  for (const [name, source] of [
    ["ChatView", chatView],
    ["ChatAppContent", chatAppContent],
    ["RunPlaybackPanel", runPlayback],
    ["SkillsHubPanel", skillsHub],
    ["MCPPanel", mcpPanel],
  ] as const) {
    assert.doesNotMatch(source, /bg-white(?:\/\d+)?/, name);
    assert.doesNotMatch(source, /bg-stone-50(?!0)(?:\/\d+)?/, name);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, name);
  }
});

test("authenticated shell chrome avoids legacy playful branding accents", () => {
  const chrome = [
    readFileSync(
      join(root, "src/components/layout/AppContent/Header.tsx"),
      "utf8",
    ),
    readFileSync(join(root, "src/components/common/PanelHeader.tsx"), "utf8"),
    readFileSync(join(root, "src/components/layout/UserMenu.tsx"), "utf8"),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SessionListContent.tsx"),
      "utf8",
    ),
    readFileSync(
      join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
      "utf8",
    ),
    readFileSync(join(root, "src/components/panels/SearchDialog.tsx"), "utf8"),
  ].join("\n");

  assert.doesNotMatch(chrome, /font-serif|from-amber-400|to-orange-500/);
  assert.doesNotMatch(chrome, /icons\/icon\.svg/);
  assert.doesNotMatch(chrome, /shadow-xl|shadow-2xl/);
  assert.doesNotMatch(chrome, /bg-white(?:\/\d+)?/);
  assert.doesNotMatch(chrome, /rounded-xl|rounded-2xl|rounded-3xl/);
  assert.doesNotMatch(chrome, /bg-black\/30/);
  assert.match(chrome, /data-workbench-header/);
  assert.match(chrome, /bg-\[var\(--theme-bg\)\]/);
  assert.match(chrome, /bg-teal-700/);
});

test("legacy persona plaza and more-menu pages are removed from the authenticated app graph", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const sidebarParts = readFileSync(
    join(root, "src/components/panels/SidebarParts/index.ts"),
    "utf8",
  );
  const welcome = readFileSync(
    join(root, "src/components/chat/WelcomePage.tsx"),
    "utf8",
  );
  const inputSelectors = readFileSync(
    join(root, "src/components/chat/ChatInputSelectors.tsx"),
    "utf8",
  );
  const activeGraph = [app, tabs, sidebarParts, welcome, inputSelectors].join("\n");
  const personaSelector = readFileSync(
    join(root, "src/components/persona/PersonaPresetSelector.tsx"),
    "utf8",
  );
  const zhLocale = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

  assert.match(app, /path="\/persona"[\s\S]{0,180}<Navigate to="\/marketplace" replace \/>/);
  assert.match(app, /path="\/files"[\s\S]{0,180}<Navigate to="\/chat" replace \/>/);
  assert.doesNotMatch(activeGraph, /navigate\("\/persona"\)/);
  assert.doesNotMatch(activeGraph, /navigate\("\/files"\)/);
  assert.doesNotMatch(activeGraph, /PersonaPlazaPanel|persona:\s*PersonaPlazaPanel/);
  assert.doesNotMatch(tabs, /files:\s*RevealedFilesPage/);
  assert.doesNotMatch(activeGraph, /MobileMoreMenuSheet|DesktopMoreMenu/);
  assert.doesNotMatch(personaSelector, /角色广场/);
  assert.match(personaSelector, /bg-slate-950\/35/);
  assert.match(personaSelector, /rounded-t-lg/);
  assert.match(personaSelector, /sm:rounded-lg/);
  assert.match(personaSelector, /shadow-\[0_8px_24px_rgba\(18,38,63,0\.12\)\]/);
  assert.doesNotMatch(personaSelector, /shadow-xl|shadow-2xl/);
  assert.doesNotMatch(personaSelector, /rounded-2xl|rounded-3xl/);
  assert.doesNotMatch(personaSelector, /bg-black\/30/);
  assert.doesNotMatch(zhLocale, /角色广场/);
});

test("authenticated marketplace pages share the workbench surface tokens", () => {
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const marketplace = readFileSync(
    join(root, "src/components/panels/MarketplacePanel.tsx"),
    "utf8",
  );
  const groupAvailability = readFileSync(
    join(root, "src/components/governance/GroupAvailabilityToggleRow.tsx"),
    "utf8",
  );
  const marketplaceCard = readFileSync(
    join(root, "src/components/panels/MarketplacePanel/SkillCard.tsx"),
    "utf8",
  );

  assert.match(skillsHub, /bg-\[var\(--theme-bg\)\]/);
  assert.match(marketplace, /bg-\[var\(--theme-bg\)\]/);
  assert.match(marketplace, /data-marketplace-catalog-shell/);
  assert.match(marketplace, /data-frontend-governance-state/);
  assert.match(marketplace, /effectiveGovernedUnavailable/);
  assert.match(groupAvailability, /flex flex-col[\s\S]*sm:flex-row/);
  assert.match(marketplaceCard, /versionLabel/);
  assert.match(marketplaceCard, /max-w-28 truncate/);
  assert.doesNotMatch(skillsHub, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /border-slate-200 bg-white/);
});

test("model catalog route is a governed public-projection workbench page", () => {
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const modelCatalog = readFileSync(
    join(root, "src/components/panels/ModelCatalogPanel.tsx"),
    "utf8",
  );
  const zhLocale = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");
  const enLocale = readFileSync(join(root, "src/i18n/locales/en.json"), "utf8");

  assert.match(tabs, /models:\s*ModelCatalogPanel/);
  assert.match(modelCatalog, /data-model-catalog-shell/);
  assert.match(modelCatalog, /modelPublicApi\.listAvailable/);
  assert.match(modelCatalog, /modelPublicApi\.listProviders/);
  assert.match(modelCatalog, /deriveProviderProjections/);
  assert.match(modelCatalog, /providersResult\.status === "fulfilled"/);
  assert.match(modelCatalog, /providerProjectionDegraded/);
  assert.match(modelCatalog, /WorkbenchStateSurface/);
  assert.match(modelCatalog, /data-model-admin-governance/);
  assert.match(modelCatalog, /bg-\[var\(--theme-bg\)\]/);
  assert.ok(JSON.parse(zhLocale).models);
  assert.ok(JSON.parse(enLocale).models);
  assert.doesNotMatch(modelCatalog, /modelApi|agentConfigApi|roleApi/);
  assert.doesNotMatch(modelCatalog, /glass-card|glass-card-subtle|glass-input/);
  assert.doesNotMatch(modelCatalog, /Legacy surface quarantined/);
});

test("production pwa updates auto-activate so old authenticated bundles cannot persist", () => {
  const pwa = readFileSync(join(root, "src/pwa.ts"), "utf8");

  assert.match(pwa, /activateWaitingAiPlatformPwaUpdate\(registration\)/);
  assert.match(pwa, /registration\.addEventListener\("updatefound"/);
  assert.match(pwa, /navigator\.serviceWorker\.addEventListener\("controllerchange"/);
});

test("legacy brand authority is absent from active browser entry", () => {
  const index = readFileSync(join(root, "index.html"), "utf8");
  assert.doesNotMatch(index, /\bLambChat\b|lambchat\.com/i);
  assert.match(index, /AI Platform - Enterprise AI Workbench/);
});
