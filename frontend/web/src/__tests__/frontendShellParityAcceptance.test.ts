import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { APP_ROUTE_PATHS } from "../appRouteManifest.ts";

const root = process.cwd();

function readApp(): string {
  let source = readFileSync(join(root, "src/App.tsx"), "utf8");
  for (const [id, path] of Object.entries(APP_ROUTE_PATHS)) {
    source = source.replaceAll(`path={APP_ROUTE_PATHS.${id}}`, `path="${path}"`);
  }
  return source;
}

test("frontend shell parity components are registered", () => {
  const files = [
    "src/components/workbench/WorkbenchShell.tsx",
    "src/components/workbench/WorkbenchRightPanel.tsx",
    "src/components/chat/ComposerChips.tsx",
    "src/components/governance/GovernanceAvailabilityBadge.tsx",
    "src/components/workbench/GovernedRouteWorkbench.tsx",
    "src/components/panels/ModelCatalogPanel.tsx",
    "src/components/share/ShareUnavailableState.tsx",
  ];

  for (const file of files) {
    assert.match(readFileSync(join(root, file), "utf8"), /export /, file);
  }
});

test("app routes expose PRD phase 1B and 1C surfaces", () => {
  const app = readApp();
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );

  for (const route of [
    "/chat",
    "/apps",
    "/skills",
    "/mcp",
    "/files",
  ]) {
    assert.match(app, new RegExp(`path="${route.replace("/", "\\/")}`));
  }

  assert.match(tabs, /apps:\s*LaunchpadPanel/);
  assert.match(tabs, /skills:\s*SkillsHubPanel/);
  assert.doesNotMatch(tabs, /const MarketplacePanel = lazy/);
  assert.doesNotMatch(tabs, /marketplace:\s*SkillsHubPanel/);
  assert.match(tabs, /mcp:\s*MCPPanel/);
  assert.match(tabs, /models:\s*ModelCatalogPanel/);
  assert.doesNotMatch(tabs, /models:\s*ModelPanel/);
  assert.doesNotMatch(tabs, /models:\s*QuarantinedLegacyPanel/);
  for (const legacyPath of [
    "src/components/layout/AppContent/QuarantinedLegacyPanel.tsx",
    "src/components/panels/ModelPanel/ModelPanel.tsx",
  ]) {
    assert.equal(existsSync(join(root, legacyPath)), false, legacyPath);
  }
});

test("phase 1C primary workbench routes are login reachable and fail closed inside pages", () => {
  const app = readApp();

  for (const route of [
    "/skills",
    "/mcp",
    "/files",
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
    ["/notifications", "NotificationsPage"],
  ]) {
    const projectionRoutePattern = new RegExp(
      `path="${route}"[\\s\\S]{0,260}<ProtectedRoute>[\\s\\S]{0,220}<${page} \\/>[\\s\\S]{0,120}<\\/ProtectedRoute>`,
    );
    const projectionPagePattern = new RegExp(
      `function ${page}\\(\\)[\\s\\S]{0,260}<AppContent key="${route.slice(1)}" activeTab="${route.slice(1)}" \\/>`,
    );
    assert.match(
      app,
      projectionRoutePattern,
      `${page} should remain login reachable without route-level business permission redirects`,
    );
    assert.match(
      app,
      projectionPagePattern,
      `${page} should render the safe workbench projection panel instead of loading the legacy panel`,
    );
  }

  assert.match(app, /function WorkbenchForbiddenPage/);
  assert.doesNotMatch(app, /function PhaseTwoWorkbenchPage/);
  assert.match(app, /routeUnavailable=\{\{/);
});

test("marketplace route remains a protected compatibility redirect to admin skill management", () => {
  const app = readApp();

  assert.match(
    app,
    /path="\/marketplace"[\s\S]{0,260}<ProtectedRoute>[\s\S]{0,120}<Navigate to="\/skills" replace \/>[\s\S]{0,120}<\/ProtectedRoute>/,
  );
  assert.doesNotMatch(app, /function MarketplacePage/);
});

test("roles route remains direct-addressable without loading legacy role management APIs", () => {
  const app = readApp();
  const authTypes = readFileSync(join(root, "src/types/auth.ts"), "utf8");
  const rolesPanel = readFileSync(
    join(root, "src/components/panels/RolesPanel.tsx"),
    "utf8",
  );
  const roleGovernanceApi = readFileSync(
    join(root, "src/services/api/roleGovernance.ts"),
    "utf8",
  );
  const roleGovernanceTypes = readFileSync(
    join(root, "src/types/roleGovernance.ts"),
    "utf8",
  );

  const rolesRoute =
    app.match(
      /path="\/roles"[\s\S]*?<RolesPage \/>[\s\S]*?<\/ProtectedRoute>/,
    )?.[0] ?? "";
  assert.match(rolesRoute, /<ProtectedRoute requireAdmin redirectTo="\/chat">/);
  assert.doesNotMatch(rolesRoute, /Permission\.ROLE_MANAGE/);
  assert.doesNotMatch(rolesRoute, /fallbackComponent=/);
  assert.doesNotMatch(rolesRoute, /<WorkbenchForbiddenPage/);
  assert.match(authTypes, /ADMIN_STATUS = "admin:status"/);
  assert.match(rolesPanel, /data-role-plaza-shell/);
  assert.match(rolesPanel, /resolveRoleGovernanceState/);
  assert.match(rolesPanel, /roleGovernanceApi\.getOverview/);
  assert.match(rolesPanel, /buildFrontendGovernanceSmokeAttributes\(roleGovernance\.pageState\)/);
  assert.doesNotMatch(rolesPanel, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(rolesPanel, /roleDirectoryBacked:\s*false/);
  assert.doesNotMatch(rolesPanel, /data-role-plaza-backend-gap/);
  assert.match(rolesPanel, /Permission\.ROLE_READ/);
  assert.match(rolesPanel, /Permission\.ROLE_REQUEST/);
  assert.match(rolesPanel, /Permission\.ROLE_MANAGE/);
  assert.match(rolesPanel, /roles\.plaza\.degraded\.detail/);
  assert.doesNotMatch(rolesPanel, /details=\{\[loadError\]/);
  assert.doesNotMatch(rolesPanel, /setLoadError\(\s*err instanceof Error \? err\.message/);
  assert.doesNotMatch(rolesPanel, /roleApi|authApi|getPermissions\(|RoleFormModal/);
  assert.doesNotMatch(rolesPanel, /\/api\/roles/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/overview/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/requests/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/approvals/);
  assert.match(roleGovernanceApi, /\/api\/role-governance\/audit/);
  assert.match(roleGovernanceTypes, /RoleGovernanceOverviewResponse/);
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
  assert.doesNotMatch(sidebar, /navigate\("\/marketplace"\)/);
  assert.match(sidebar, /navigate\("\/mcp"\)/);
  assert.match(sidebar, /navigate\("\/apps"\)/);
  for (const route of ["/models"]) {
    assert.match(sidebar, new RegExp(`navigate\\("${route}"\\)`), route);
  }
  assert.doesNotMatch(sidebar, /navigate\("\/roles"\)/);
  for (const route of ["/files"]) {
    assert.match(sidebar, new RegExp(`navigate\\("${route}"\\)`), route);
  }
  for (const handler of [
    "onOpenModels",
    "onOpenFiles",
  ]) {
    assert.match(sidebar, new RegExp(handler), handler);
  }
  assert.doesNotMatch(sidebar, /onOpenRoles|onOpenMarketplace/);
  assert.doesNotMatch(sidebar, /Permission\.ROLE_READ|Permission\.AGENT_ADMIN|Permission\.MODEL_READ|Permission\.CHANNEL_READ/);
  assert.doesNotMatch(sidebar, /onOpenPersonaPlaza|onOpenFileLibrary/);
  assert.doesNotMatch(sidebar, /hasMoreMenuItems|MobileMoreMenuSheet|DesktopMoreMenu/);
  assert.match(sidebar, /useProjectSessionList\("all"/);
  assert.doesNotMatch(sidebar, /ProjectItem|showProjectSection|sidebar\.projects/);
  assert.doesNotMatch(sidebar, /FolderPlus|sidebar\.newProject|onOpenNewProjectModal|NewProjectModal/);
  assert.doesNotMatch(sidebar, /font-serif|icons\/icon\.svg/);
});

test("post-login navigation keeps MCP in the authoritative sidebar instead of the account menu", () => {
  const userMenu = readFileSync(
    join(root, "src/components/layout/UserMenu.tsx"),
    "utf8",
  );
  const sidebar = readFileSync(
    join(root, "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );
  const chatAppContent = readFileSync(
    join(root, "src/components/layout/AppContent/ChatAppContent.tsx"),
    "utf8",
  );
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(sidebar, /onOpenMcp=\{\(\) => navigate\("\/mcp"\)\}/);
  assert.match(userMenu, /data-user-menu-identity/);
  assert.match(userMenu, /auth\.logout/);
  assert.doesNotMatch(
    userMenu,
    /useNavigate|navItems|data-workbench-user-menu-item|["'`]\/(?:chat|skills|mcp)["'`]/,
  );
  assert.match(userMenu, /overflow-y-auto/);
  assert.match(userMenu, /w-60/);
  assert.match(chatAppContent, /useTools\(\{ enabled: true \}\)/);
  assert.doesNotMatch(chatAppContent, /const canReadMcpTools = hasPermission\(Permission\.MCP_READ\);/);
  assert.match(chatInput, /toolsAvailable/);
  assert.match(chatInput, /skillsAvailable/);
  assert.doesNotMatch(chatInput, /totalToolsCount > 0/);
  assert.doesNotMatch(chatInput, /totalSkillsCount > 0/);
});

test("authenticated chat workspace keeps one warm-neutral LibreChat canvas instead of split backgrounds", () => {
  const surface = readFileSync(
    join(root, "src/components/workbench/workbenchSurface.ts"),
    "utf8",
  );
  const libreSurface = readFileSync(
    join(root, "src/librechat-ui/surface.ts"),
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
  const libreSidePanel = readFileSync(
    join(root, "src/librechat-ui/SidePanel.tsx"),
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

  assert.match(surface, /root:\s*libreChatSurface\.root/);
  assert.match(surface, /thread:\s*libreChatSurface\.thread/);
  assert.match(surface, /composer:\s*libreChatSurface\.composer/);
  assert.match(surface, /context:\s*libreChatSurface\.context/);
  assert.match(surface, /panel:\s*libreChatSurface\.panel/);
  assert.match(surface, /secondaryPanel:\s*libreChatSurface\.panel/);
  assert.match(
    libreSurface,
    /root:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/,
  );
  assert.match(
    libreSurface,
    /thread:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/,
  );
  assert.match(
    libreSurface,
    /composer:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/,
  );
  assert.match(
    libreSurface,
    /context:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/,
  );
  assert.match(
    libreSurface,
    /panel:[\s\S]*bg-\[var\(--theme-workbench-panel\)\]/,
  );
  assert.match(surface, /secondaryPanel:/);
  assert.match(rightPanel, /LibreChatSidePanel/);
  assert.match(libreSidePanel, /workbenchSurface\.secondaryPanel/);
  assert.match(theme, /--theme-bg:\s*#ffffff;/);
  assert.match(theme, /--theme-bg-sidebar:\s*#f7f7f8;/);
  assert.match(theme, /--theme-workbench-panel:\s*#ffffff;/);
  assert.match(theme, /--theme-bg-card:\s*#ffffff;/);
  assert.match(theme, /--theme-workbench-canvas:\s*#ffffff;/);
  assert.doesNotMatch(theme, /--theme-workbench-canvas:\s*#e5e8ed;/);
  assert.doesNotMatch(theme, /--theme-workbench-panel:\s*#f3f4f6;/);
  assert.match(libreSurface, /bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(chatView, /bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(authTheme, /html,\s*body\s*\{\s*background:\s*var\(--theme-bg\);/);
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

test("authenticated workbench adopts one LibreChat light application shell", () => {
  const packageJson = JSON.parse(
    readFileSync(join(root, "package.json"), "utf8"),
  ) as { name?: string };
  const main = readFileSync(join(root, "src/main.tsx"), "utf8");
  const sidebar = readFileSync(
    join(root, "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );
  const sidebarList = readFileSync(
    join(root, "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );
  const sidebarRail = readFileSync(
    join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );
  const projectItem = readFileSync(
    join(root, "src/components/sidebar/ProjectItem.tsx"),
    "utf8",
  );
  const sessionItem = readFileSync(
    join(root, "src/components/sidebar/SessionItem.tsx"),
    "utf8",
  );
  const theme = readFileSync(join(root, "src/styles/base.css"), "utf8");
  const components = readFileSync(
    join(root, "src/styles/components.css"),
    "utf8",
  );
  const enterpriseSelect = readFileSync(
    join(root, "src/components/common/EnterpriseSelect.tsx"),
    "utf8",
  );
  const settingsHook = readFileSync(join(root, "src/hooks/useSettings.ts"), "utf8");

  assert.equal(packageJson.name, "ai-platform-frontend");
  assert.doesNotMatch(main, /styles\/glass\.css/);
  assert.match(components, /enterprise-field-control/);
  assert.match(components, /enterprise-select-dropdown/);
  assert.doesNotMatch(components, /glass-input|glass-select|--glass-/);
  assert.match(enterpriseSelect, /function EnterpriseSelect/);
  assert.doesNotMatch(enterpriseSelect, /GlassSelect|glass-/);
  assert.match(settingsHook, /ai-platform-settings-\$\{date\}\.json/);
  assert.doesNotMatch(settingsHook, /lamb-agent-settings/);
  assert.match(theme, /--theme-sidebar-rail:\s*#f7f7f8;/);
  assert.match(theme, /--theme-sidebar-panel:\s*#f7f7f8;/);
  assert.match(theme, /--theme-sidebar-panel-muted:\s*#ececec;/);
  assert.match(theme, /--theme-bg:\s*#ffffff;/);
  assert.match(theme, /--theme-workbench-canvas:\s*#ffffff;/);
  assert.match(sidebar, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(sidebarList, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(sidebarList, /data-workbench-sidebar-panel/);
  assert.match(sidebarRail, /bg-\[var\(--theme-sidebar-rail\)\]/);
  assert.match(projectItem, /hover:bg-\[var\(--theme-sidebar-panel-muted\)\]/);
  assert.match(sessionItem, /hover:bg-\[var\(--theme-sidebar-panel-muted\)\]/);
  assert.doesNotMatch(sidebarList, /text-slate-100|text-white|border-slate-800/);
  assert.doesNotMatch(sidebarRail, /text-slate-200|text-white|rgba\(255,255,255,0\.1\)/);
  assert.doesNotMatch(projectItem, /text-slate-300/);
  assert.doesNotMatch(sessionItem, /text-slate-300/);
  assert.doesNotMatch(sidebarList, /rounded-\[10px\]/);
  assert.doesNotMatch(projectItem, /rounded-\[10px\]/);
  assert.doesNotMatch(sessionItem, /rounded-\[10px\]/);
  assert.doesNotMatch(sidebarRail, /style=\{\{[\s\S]{0,160}backgroundColor/);
});

test("skills and marketplace use a catalog-first workbench layout", () => {
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const skillsList = readFileSync(
    join(root, "src/components/panels/SkillsPanel/SkillsList.tsx"),
    "utf8",
  );
  const marketplace = readFileSync(
    join(root, "src/components/panels/MarketplacePanel.tsx"),
    "utf8",
  );

  assert.match(skillsHub, /data-skills-catalog-workbench/);
  assert.match(skillsHub, /data-skills-catalog-status/);
  assert.match(skillsHub, /data-skills-catalog-main/);
  assert.match(skillsHub, /className=\{workbenchSurface\.page\}/);
  assert.doesNotMatch(skillsHub, /data-skills-catalog-nav/);
  assert.doesNotMatch(skillsHub, /data-skills-catalog-sidebar/);
  assert.doesNotMatch(skillsHub, /<aside/);
  assert.doesNotMatch(skillsHub, /showTabSwitcher/);
  assert.doesNotMatch(skillsHub, /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/);
  assert.doesNotMatch(skillsHub, /composerEntry/);
  assert.match(skillsList, /data-skills-catalog-toolbar/);
  assert.match(skillsList, /data-skills-catalog-grid/);
  assert.match(marketplace, /data-marketplace-catalog-toolbar/);
  assert.match(marketplace, /data-marketplace-catalog-grid/);
  for (const [name, source] of [
    ["SkillsHubPanel", skillsHub],
    ["SkillsList", skillsList],
    ["MarketplacePanel", marketplace],
  ] as const) {
    assert.doesNotMatch(source, /bg-white(?:\/\d+)?/, name);
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, name);
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
    readFileSync(
      join(root, "src/components/layout/AppContent/useWebSocketNotifications.tsx"),
      "utf8",
    ),
  ].join("\n");

  assert.doesNotMatch(chrome, /font-serif|from-amber-400|to-orange-500/);
  assert.doesNotMatch(chrome, /icons\/icon\.svg/);
  assert.doesNotMatch(chrome, /shadow-xl|shadow-2xl/);
  assert.doesNotMatch(chrome, /bg-white(?:\/\d+)?/);
  assert.doesNotMatch(chrome, /bg-stone-50(?!0)|bg-stone-100/);
  assert.doesNotMatch(chrome, /rounded-xl|rounded-2xl|rounded-3xl/);
  assert.doesNotMatch(chrome, /bg-black\/30/);
  assert.match(chrome, /data-workbench-header/);
  assert.match(chrome, /bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.doesNotMatch(chrome, /bg-\[var\(--theme-bg\)\]/);
  assert.match(chrome, /bg-teal-700/);
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
  const skillBaseCard = readFileSync(
    join(root, "src/components/common/SkillBaseCard.tsx"),
    "utf8",
  );
  const marketplaceCard = readFileSync(
    join(root, "src/components/panels/MarketplacePanel/SkillCard.tsx"),
    "utf8",
  );
  const skillsList = readFileSync(
    join(root, "src/components/panels/SkillsPanel/SkillsList.tsx"),
    "utf8",
  );

  assert.match(skillsHub, /className=\{workbenchSurface\.page\}/);
  assert.match(marketplace, /className=\{workbenchSurface\.page\}/);
  assert.doesNotMatch(skillsHub, /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/);
  assert.doesNotMatch(marketplace, /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/);
  assert.match(marketplace, /data-marketplace-catalog-shell/);
  assert.match(marketplace, /data-frontend-governance-state|buildFrontendGovernanceSmokeAttributes/);
  assert.match(marketplace, /effectiveGovernedUnavailable/);
  assert.match(groupAvailability, /flex flex-col[\s\S]*sm:flex-row/);
  assert.match(marketplaceCard, /versionLabel/);
  assert.match(marketplaceCard, /max-w-28 truncate/);
  assert.match(skillBaseCard, /p-3\.5 sm:p-4/);
  assert.match(skillBaseCard, /bg-\[var\(--theme-workbench-panel\)\]/);
  assert.doesNotMatch(skillBaseCard, /text-base font-semibold/);
  assert.match(skillsList, /workbenchSurface\.catalog\.cardGrid/);
  assert.match(marketplace, /workbenchSurface\.catalog\.cardGrid/);
  assert.match(skillsList, /workbenchSurface\.catalog\.emptyState/);
  assert.match(marketplace, /workbenchSurface\.catalog\.emptyState/);
  assert.doesNotMatch(skillsList, /auto-grid-cols/);
  assert.doesNotMatch(marketplace, /auto-grid-cols/);
  assert.doesNotMatch(skillsHub, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /border-slate-200 bg-white/);
});

test("legacy profile modal and all product tabs are absent from the frontend bundle", () => {
  const profileFiles = [
    "src/components/profile/ProfileModal.tsx",
    "src/components/profile/tabs/ProfileInfoTab.tsx",
    "src/components/profile/tabs/ProfileNotificationTab.tsx",
    "src/components/profile/tabs/ProfilePreferencesTab.tsx",
    "src/components/profile/tabs/ProfileToolsTab.tsx",
    "src/components/profile/tabs/ProfileModelsTab.tsx",
    "src/components/profile/tabs/ProfileTermsTab.tsx",
    "src/components/profile/tabs/ProfilePasswordTab.tsx",
    "src/components/profile/tabs/ProfileEnvVarsTab.tsx",
  ];

  for (const file of profileFiles) {
    assert.equal(existsSync(join(root, file)), false, file);
  }
});

test("MCP selectors use workbench control tokens", () => {
  const controlFiles = [
    "src/components/mcp/RoleSelector.tsx",
    "src/components/mcp/EnvKeysSelector.tsx",
  ];

  const sources = controlFiles.map((file) => [
    file,
    readFileSync(join(root, file), "utf8"),
  ] as const);

  for (const [file, source] of sources) {
    assert.match(
      source,
      /enterprise-form-input|enterprise-subtle-panel|panel-card|btn-primary|btn-icon|enterprise-select-dropdown|theme-bg-card|theme-bg-sidebar/,
      `${file} should depend on the shared enterprise workbench vocabulary`,
    );
    assert.doesNotMatch(source, /font-serif/, file);
    assert.doesNotMatch(source, /from-amber|to-amber|from-orange|to-orange/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-amber-/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-orange-/, file);
    assert.doesNotMatch(source, /rounded-xl|rounded-2xl|rounded-3xl/, file);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl|\bshadow-lg\b/, file);
    assert.doesNotMatch(source, /\bbg-white(?:\/\d+)?\b/, file);
    assert.doesNotMatch(source, /\bbg-stone-50(?!0)(?:\/\d+)?\b/, file);
  }
});

test("authenticated overlay surfaces share the workbench visual language", () => {
  const overlayFiles = [
    "src/components/notification/NotificationDialog.tsx",
    "src/components/share/ShareDialog.tsx",
    "src/components/sidebar/RecentChatsDialog.tsx",
    "src/components/sidebar/ProjectMenu.tsx",
  ];

  const sources = overlayFiles.map((file) => [
    file,
    readFileSync(join(root, file), "utf8"),
  ] as const);

  for (const [file, source] of sources) {
    assert.match(source, /bg-\[var\(--theme-bg-card\)\]|var\(--theme-bg-card\)/, file);
    assert.match(source, /bg-\[var\(--theme-bg-sidebar\)\]|var\(--theme-bg-sidebar\)/, file);
    assert.match(source, /rounded-t-lg|rounded-lg/, file);
    assert.match(source, /shadow-\[0_8px_24px_rgba\(18,38,63,0\.12\)\]/, file);
    assert.doesNotMatch(source, /font-serif/, file);
    assert.doesNotMatch(source, /icons\/icon\.svg/, file);
    assert.doesNotMatch(source, /bg-black\/50|bg-black\/30/, file);
    assert.doesNotMatch(source, /bg-white(?:\/\d+)?/, file);
    assert.doesNotMatch(source, /dark:bg-stone-800|dark:bg-stone-900/, file);
    assert.doesNotMatch(source, /rounded-xl|rounded-2xl|rounded-3xl/, file);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, file);
    assert.doesNotMatch(source, /from-amber|to-amber|from-orange|to-orange/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-amber-/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-orange-/, file);
  }
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
  assert.match(modelCatalog, /deriveProviderProjections/);
  assert.match(modelCatalog, /WorkbenchStateSurface/);
  assert.match(modelCatalog, /data-model-admin-governance/);
  assert.match(modelCatalog, /className=\{workbenchSurface\.page\}/);
  assert.doesNotMatch(modelCatalog, /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/);
  assert.ok(JSON.parse(zhLocale).models);
  assert.ok(JSON.parse(enLocale).models);
  for (const source of [
    modelCatalog,
    JSON.stringify(JSON.parse(zhLocale).models),
    JSON.stringify(JSON.parse(enLocale).models),
  ]) {
    assert.doesNotMatch(source, /管理投影补齐|等待后端补齐|admin projections are backed|backend coverage/);
  }
  assert.doesNotMatch(modelCatalog, /modelApi|agentConfigApi|roleApi/);
  assert.doesNotMatch(modelCatalog, /listProviders/);
  assert.doesNotMatch(modelCatalog, /providers\/list/);
  assert.doesNotMatch(modelCatalog, /glass-card|glass-card-subtle|enterprise-field-control/);
  assert.doesNotMatch(modelCatalog, /Legacy surface quarantined/);
});

test("workbench pages render concrete projection panels instead of thin placeholders", () => {
  const app = readApp();
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const governedRouteWorkbench = readFileSync(
    join(root, "src/components/workbench/GovernedRouteWorkbench.tsx"),
    "utf8",
  );

  assert.doesNotMatch(app, /const phaseTwoWorkbenchConfigs/);
  assert.doesNotMatch(app, /function PhaseTwoWorkbenchPage/);
  for (const tab of ["users", "settings", "feedback", "notifications"]) {
    assert.match(
      app,
      new RegExp(`function ${tab[0].toUpperCase()}${tab.slice(1)}Page\\(\\)[\\s\\S]{0,260}<AppContent key="${tab}" activeTab="${tab}" \\/>`),
    );
  }
  assert.match(tabs, /users:\s*WorkbenchUsersProjectionPanel/);
  assert.match(tabs, /settings:\s*WorkbenchSettingsProjectionPanel/);
  assert.match(tabs, /feedback:\s*WorkbenchFeedbackProjectionPanel/);
  assert.match(tabs, /notifications:\s*WorkbenchNotificationsProjectionPanel/);
  assert.match(tabs, /import \{ GovernedRouteWorkbench \}/);
  assert.match(tabs, /<GovernedRouteWorkbench/);
  assert.doesNotMatch(
    tabs,
    /if \(routeUnavailable\)[\s\S]{0,360}items-center justify-center/,
  );
  assert.match(governedRouteWorkbench, /data-governed-route-workbench/);
  assert.match(governedRouteWorkbench, /data-governed-route-summary/);
  assert.match(governedRouteWorkbench, /data-governed-route-contract/);
  assert.match(governedRouteWorkbench, /data-governed-route-detail/);
  assert.doesNotMatch(
    governedRouteWorkbench,
    /data-governed-route-contract[\s\S]{0,220}bg-\[var\(--theme-bg-sidebar\)\]/,
  );
  assert.match(
    governedRouteWorkbench,
    /data-governed-route-contract[\s\S]{0,220}xl:border-l/,
  );
  assert.match(governedRouteWorkbench, /PanelHeader/);
  assert.match(governedRouteWorkbench, /GovernanceAvailabilityBadge/);
  assert.match(governedRouteWorkbench, /data-governed-route-capability/);
  assert.match(governedRouteWorkbench, /stateLabel/);
  assert.match(governedRouteWorkbench, /surfaceLabel/);
  assert.doesNotMatch(governedRouteWorkbench, /WorkbenchStateSurface/);
  assert.doesNotMatch(governedRouteWorkbench, /data-governed-route-gap/);
  assert.doesNotMatch(
    app,
    /公司用户、系统设置、反馈、通知和 Agent 管理仍等待对应服务能力开放/,
  );
});

test("workbench projection pages consume safe backend contracts instead of phase-two placeholders", () => {
  const app = readApp();
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const projectionPages = readFileSync(
    join(root, "src/components/workbench/WorkbenchProjectionPages.tsx"),
    "utf8",
  );
  const workbenchApi = readFileSync(
    join(root, "src/services/api/workbench.ts"),
    "utf8",
  );
  const authTypes = readFileSync(join(root, "src/types/auth.ts"), "utf8");

  for (const [route, page] of [
    ["/users", "UsersPage"],
    ["/settings", "SettingsPage"],
    ["/feedback", "FeedbackPage"],
    ["/notifications", "NotificationsPage"],
  ]) {
    assert.match(
      app,
      new RegExp(`path="${route}"[\\s\\S]{0,360}<${page} \\/>`),
    );
    assert.match(
      app,
      new RegExp(
        `function ${page}\\(\\)[\\s\\S]{0,260}<AppContent key="${route.slice(1)}" activeTab="${route.slice(1)}" \\/>`,
      ),
    );
    assert.doesNotMatch(
      app,
      new RegExp(
        `function ${page}\\(\\)[\\s\\S]{0,420}<PhaseTwoWorkbenchPage`,
      ),
    );
  }

  assert.match(tabs, /const WorkbenchUsersProjectionPanel = lazy/);
  assert.match(tabs, /users:\s*WorkbenchUsersProjectionPanel/);
  assert.match(tabs, /settings:\s*WorkbenchSettingsProjectionPanel/);
  assert.match(tabs, /feedback:\s*WorkbenchFeedbackProjectionPanel/);
  assert.match(tabs, /notifications:\s*WorkbenchNotificationsProjectionPanel/);

  assert.match(projectionPages, /data-workbench-projection-page/);
  assert.match(projectionPages, /resolveFrontendGovernanceState/);
  assert.match(projectionPages, /WorkbenchStateSurface/);
  assert.match(projectionPages, /GovernanceAvailabilityBadge/);
  assert.match(projectionPages, /workbenchSurface/);
  assert.match(projectionPages, /data-projection-workbench-grid/);
  assert.match(projectionPages, /data-projection-summary-panel/);
  assert.match(projectionPages, /data-projection-insight-panel/);
  assert.match(projectionPages, /data-projection-list-panel/);
  assert.match(projectionPages, /ProjectionMetric/);
  assert.match(projectionPages, /governance\.secret_material_projected/);
  assert.match(projectionPages, /secretMaterialProjected/);
  assert.match(projectionPages, /degraded:\s*Boolean\(\s*governance\?\.degraded\s*\)/);
  assert.doesNotMatch(
    projectionPages,
    /governance\?\.degraded\s*\|\|\s*governance\?\.secret_material_projected/,
  );
  assert.match(projectionPages, /state === "degraded"/);
  assert.match(projectionPages, /state === "forbidden"/);
  assert.doesNotMatch(projectionPages, /<div className="mt-3">\{children\}<\/div>/);
  assert.doesNotMatch(projectionPages, /roleApi|settingsApi|NotificationPanel|UsersPanel/);

  for (const endpoint of [
    "/api/users/",
    "/api/settings/",
    "/api/feedback/",
    "/api/notifications/active",
    "/api/notifications/admin",
  ]) {
    assert.ok(workbenchApi.includes(endpoint), endpoint);
  }
  assert.doesNotMatch(workbenchApi, /\/api\/ai\/admin|\/api\/admin\/settings/);

  assert.match(authTypes, /USER_ADMIN = "user:admin"/);
  assert.match(authTypes, /ROLE_READ = "role:read"/);
  assert.match(authTypes, /ROLE_REQUEST = "role:request"/);
  assert.match(authTypes, /SETTINGS_READ = "settings:read"/);
  assert.match(authTypes, /SETTINGS_ADMIN = "settings:admin"/);
  assert.match(authTypes, /NOTIFICATION_READ = "notification:read"/);
  assert.match(authTypes, /NOTIFICATION_ADMIN = "notification:admin"/);
});

test("safe projection locale copy no longer reports backed workbench pages as unopened backend gaps", () => {
  const zh = JSON.parse(readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8"));
  const en = JSON.parse(readFileSync(join(root, "src/i18n/locales/en.json"), "utf8"));

  for (const [locale, workbench] of [
    ["zh", zh.workbench.phaseTwo],
    ["en", en.workbench.phaseTwo],
  ] as const) {
    for (const page of ["users", "settings", "feedback", "notifications"]) {
      const copy = JSON.stringify(workbench[page]);
      assert.doesNotMatch(
        copy,
        /尚未开放|未开放|等待后端|后端还没有|backend has not|not enabled yet|waiting for backend|Next contract|Projection gap|Admin projection gap/i,
        `${locale}.${page} should describe the safe read projection and governed writes, not a missing backend contract`,
      );
    }
  }
});

test("launchpad navigation is overflow safe on narrow authenticated viewports", () => {
  const launchpad = readFileSync(
    join(root, "src/components/launchpad/LaunchpadPanel.tsx"),
    "utf8",
  );

  assert.match(launchpad, /data-launchpad-tab-strip/);
  assert.match(launchpad, /overflow-x-auto/);
  assert.match(launchpad, /shrink-0/);
  assert.match(launchpad, /min-w-\[/);
  assert.doesNotMatch(launchpad, /whitespace-nowrap[\s\S]{0,120}overflow-visible/);
});

test("frontend governance state machine exposes every authenticated page state", () => {
  const stateMachine = readFileSync(
    join(root, "src/components/governance/frontendGovernanceState.ts"),
    "utf8",
  );
  const stateSurface = readFileSync(
    join(root, "src/components/workbench/WorkbenchStateSurface.tsx"),
    "utf8",
  );

  for (const state of [
    "logged-out",
    "loading",
    "no-workspace",
    "forbidden",
    "degraded",
    "ready",
  ]) {
    assert.match(stateMachine, new RegExp(`"${state}"`));
    assert.match(stateSurface, new RegExp(`workbench\\.states\\.${state}`));
  }
  assert.match(stateMachine, /!isAuthenticated[\s\S]{0,120}"logged-out"/);
  assert.match(stateMachine, /isLoading[\s\S]{0,120}"loading"/);
  assert.match(stateMachine, /!hasWorkspace[\s\S]{0,120}"no-workspace"/);
  assert.match(stateMachine, /!hasPermission[\s\S]{0,120}"forbidden"/);
});

test("mcp workbench route exposes the same frontend governance state machine as skills and roles", () => {
  const mcpPanel = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  const mcpState = readFileSync(
    join(root, "src/components/panels/mcpGovernanceState.ts"),
    "utf8",
  );
  const ordinaryMcp = readFileSync(
    join(root, "src/components/panels/OrdinaryMcpCatalog.tsx"),
    "utf8",
  );

  assert.match(mcpPanel, /resolveMcpGovernanceState/);
  assert.match(mcpPanel, /data-mcp-directory-shell/);
  assert.match(mcpPanel, /buildFrontendGovernanceSmokeAttributes\(mcpGovernance\.pageState\)/);
  assert.match(mcpPanel, /data-required-permission=\{mcpGovernance\.requiredPermission\}/);
  assert.match(mcpPanel, /data-auth-projection-has-permission/);
  assert.match(mcpPanel, /WorkbenchStateSurface/);
  assert.match(mcpPanel, /data-fail-closed-surface="mcp-lifecycle"/);
  assert.match(mcpPanel, /data-fail-closed-surface="mcp-credentials"/);
  assert.match(mcpState, /requiredPermission: "mcp:read"/);
  assert.match(mcpState, /resolveFrontendGovernanceState/);
  assert.match(mcpState, /isPermissionError\(loadError\)/);
  assert.match(mcpState, /featureEnabled:\s*true/);
  assert.match(mcpState, /adminOnly:\s*!canManageMcp/);
  assert.match(mcpState, /enabled:\s*canManageMcp/);
  assert.doesNotMatch(mcpPanel, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(mcpPanel, /hasPermission:\s*canReadMcp/);
  assert.match(mcpPanel, /canManageMcp && !mcpGovernance\.governedUnavailable/);
  assert.match(mcpPanel, /data-mcp-admin-controls/);
  assert.match(mcpPanel, /if \(!isAiAdmin\)/);
  assert.match(mcpPanel, /createServer|updateServer|deleteServer|toggleServer/);
  assert.doesNotMatch(mcpPanel, /promoteServer|demoteServer/);
  assert.match(ordinaryMcp, /data-ordinary-mcp-catalog/);
  assert.match(ordinaryMcp, /mcp\.available\.empty/);
  assert.doesNotMatch(
    ordinaryMcp,
    /allowed_roles|role_quotas|credential|transport|server\.enabled|can_edit/,
  );
});

test("skills and marketplace clients use only PR177 public contracts", () => {
  const skillApi = readFileSync(join(root, "src/services/api/skill.ts"), "utf8");
  const marketplaceApi = readFileSync(
    join(root, "src/services/api/marketplace.ts"),
    "utf8",
  );

  assert.match(skillApi, /const SKILLS_API = `\$\{API_BASE\}\/api\/skills`/);
  assert.match(marketplaceApi, /const MARKETPLACE_API = `\$\{API_BASE\}\/api\/marketplace`/);
  assert.match(skillApi, /\/batch\/toggle/);
  assert.match(skillApi, /\/batch\/delete/);
  assert.match(marketplaceApi, /\/install/);
  assert.match(marketplaceApi, /\/update/);
  assert.match(skillApi, /\/api\/ai\/admin\/skills/);
  for (const source of [skillApi, marketplaceApi]) {
    assert.doesNotMatch(source, /\/api\/admin/);
    assert.doesNotMatch(source, /lambchat/i);
  }
  assert.doesNotMatch(marketplaceApi, /\/admin\/skills|\/admin\/marketplace/);
  assert.doesNotMatch(marketplaceApi, /\/api\/ai\/admin/);
});

test("skills hub keeps management admin-only and serves a bounded ordinary catalog", () => {
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const resolver = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel/state.ts"),
    "utf8",
  );
  const useAuth = readFileSync(join(root, "src/hooks/useAuth.tsx"), "utf8");
  const ordinarySkills = readFileSync(
    join(root, "src/components/panels/AvailableSkillsPanel.tsx"),
    "utf8",
  );

  assert.match(skillsHub, /resolveSkillsHubGovernance/);
  assert.match(skillsHub, /isAiAdminUser\(user\)/);
  assert.match(skillsHub, /return <AvailableSkillsPanel \/>;/);
  assert.doesNotMatch(skillsHub, /useSettingsContext/);
  assert.doesNotMatch(skillsHub, /settingsError/);
  assert.doesNotMatch(skillsHub, /settingsStateDegraded/);
  assert.match(skillsHub, /canReadSkills = hasAnyPermission\(\[Permission\.SKILL_ADMIN\]\)/);
  assert.match(skillsHub, /canReadMarketplace = hasAnyPermission\(\[Permission\.MARKETPLACE_ADMIN\]\)/);
  assert.match(skillsHub, /const governanceState = hubGovernance\.pageState/);
  assert.match(skillsHub, /data-required-permission=\{hubGovernance\.requiredPermission\}/);
  assert.match(skillsHub, /catalogPermissionDeniedByTab/);
  assert.match(skillsHub, /catalogProjectionErrorByTab/);
  assert.match(skillsHub, /catalogPermissionDenied: catalogPermissionDeniedByTab\[requestedTab\]/);
  assert.match(skillsHub, /projectionError: catalogProjectionErrorByTab\[requestedTab\]/);
  assert.match(skillsHub, /governedUnavailable=\{hubGovernance\.governedUnavailable\}/);
  assert.match(skillsHub, /onCatalogStateChange=\{handleCatalogStateChange\}/);
  assert.match(skillsHub, /data-auth-projection-has-permission=\{hubGovernance\.authProjectionHasPermission\}/);
  assert.match(resolver, /catalogPermissionDenied\?: boolean/);
  assert.match(resolver, /hasWorkspace\?: boolean/);
  assert.match(resolver, /pageState: FrontendGovernanceState/);
  assert.match(resolver, /authProjectionHasPermission/);
  assert.match(resolver, /const governedUnavailable = Boolean\(/);
  assert.match(resolver, /!hasAdminPermission && \(effectivePermissionsKnown \|\| catalogReadResolved\)/);
  assert.match(resolver, /!hasWorkspace\s*\?\s*"no-workspace"/);
  assert.match(resolver, /governedUnavailable\s*\?\s*"forbidden"/);
  assert.match(resolver, /catalogReadPending\?: boolean/);
  assert.match(resolver, /catalogReadPending = false/);
  assert.match(resolver, /const probingPermission =/);
  assert.match(resolver, /!catalogReadPending/);
  assert.match(resolver, /projectionError \|\| probingPermission\s*\?\s*"degraded"/);
  assert.match(resolver, /requiredPermission: "skill:admin" \| "marketplace:admin"/);
  assert.match(resolver, /requestedTab === "marketplace"/);
  assert.match(resolver, /requestedTab === "marketplace"[\s\S]*\? canReadMarketplace[\s\S]*: canReadSkills \|\| canReadMarketplace/);
  assert.match(resolver, /hasPermission: hasAdminPermission && !governedUnavailable/);
  assert.doesNotMatch(resolver, /hasPermission: canReadMarketplace/);
  assert.doesNotMatch(resolver, /hasPermission: canReadSkills/);
  assert.doesNotMatch(skillsHub, /resolveFrontendGovernanceState/);
  assert.doesNotMatch(skillsHub, /hasPermission:\s*true/);
  assert.doesNotMatch(skillsHub, /hasCatalogPermissionGap/);
  assert.doesNotMatch(skillsHub, /activeTabHasPermission/);
  assert.match(useAuth, /hasEffectivePermission\(permissions, permission\)/);
  assert.match(useAuth, /hasAnyEffectivePermission\(permissions, perms\)/);
  assert.match(useAuth, /hasAllEffectivePermissions\(permissions, perms\)/);
  assert.match(ordinarySkills, /data-ordinary-skills-catalog/);
  assert.match(ordinarySkills, /skills\.available\.title/);
  assert.match(ordinarySkills, /skills\.available\.fileTypes/);
  assert.doesNotMatch(
    ordinarySkills,
    /expected_version|file_count|skill\.content|skill\.files|is_published|marketplace_is_active/,
  );
});

test("company baseline permissions include backed role plaza and marketplace read contracts", () => {
  const authRoute = readFileSync(join(root, "../../app/routes/auth.py"), "utf8");
  const marketplaceApi = readFileSync(
    join(root, "src/services/api/marketplace.ts"),
    "utf8",
  );
  const useMarketplace = readFileSync(
    join(root, "src/hooks/useMarketplace.ts"),
    "utf8",
  );
  const marketplaceTest = readFileSync(
    join(root, "src/services/api/__tests__/marketplace.test.ts"),
    "utf8",
  );
  const zhLocale = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");
  const enLocale = readFileSync(join(root, "src/i18n/locales/en.json"), "utf8");

  assert.match(authRoute, /"marketplace:read"/);
  assert.match(authRoute, /"role:read"/);
  assert.match(marketplaceApi, /catalog_read_resolved/);
  assert.match(useMarketplace, /setCatalogReadResolved\(data\.catalog_read_resolved\)/);
  assert.match(marketplaceTest, /catalog_read_resolved:\s*true/);
  assert.doesNotMatch(zhLocale, /市场直接写入暂未开放/);
  assert.doesNotMatch(enLocale, /Direct marketplace writes are not available yet/);
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
