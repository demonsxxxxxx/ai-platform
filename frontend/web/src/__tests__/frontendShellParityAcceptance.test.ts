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
    "src/components/workbench/GovernedRouteWorkbench.tsx",
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
  assert.doesNotMatch(tabs, /const MarketplacePanel = lazy/);
  assert.match(tabs, /marketplace:\s*SkillsHubPanel/);
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
    "/roles",
    "/persona",
    "/files",
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
    ["/settings", "SettingsPage"],
    ["/feedback", "FeedbackPage"],
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

  assert.doesNotMatch(app, /redirectTo="\/chat"/);
  assert.match(app, /function WorkbenchForbiddenPage/);
  assert.doesNotMatch(app, /function PhaseTwoWorkbenchPage/);
  assert.match(app, /routeUnavailable=\{\{/);
});

test("roles route is login reachable and does not load legacy role management APIs", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
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
  assert.match(rolesRoute, /<ProtectedRoute>/);
  assert.doesNotMatch(rolesRoute, /Permission\.ROLE_MANAGE/);
  assert.doesNotMatch(rolesRoute, /fallbackComponent=/);
  assert.doesNotMatch(rolesRoute, /<WorkbenchForbiddenPage/);
  assert.match(authTypes, /ADMIN_STATUS = "admin:status"/);
  assert.match(rolesPanel, /data-role-plaza-shell/);
  assert.match(rolesPanel, /resolveRoleGovernanceState/);
  assert.match(rolesPanel, /roleGovernanceApi\.getOverview/);
  assert.match(rolesPanel, /data-frontend-governance-state=\{roleGovernance\.pageState\}/);
  assert.doesNotMatch(rolesPanel, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(rolesPanel, /roleDirectoryBacked:\s*false/);
  assert.doesNotMatch(rolesPanel, /data-role-plaza-backend-gap/);
  assert.match(rolesPanel, /Permission\.ROLE_READ/);
  assert.match(rolesPanel, /Permission\.ROLE_REQUEST/);
  assert.match(rolesPanel, /Permission\.ROLE_MANAGE/);
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
  assert.match(sidebar, /navigate\("\/marketplace"\)/);
  assert.match(sidebar, /navigate\("\/mcp"\)/);
  assert.match(sidebar, /navigate\("\/apps"\)/);
  assert.doesNotMatch(sidebar, /navigate\("\/persona"\)/);
  assert.doesNotMatch(sidebar, /navigate\("\/files"\)/);
  assert.doesNotMatch(sidebar, /onOpenPersonaPlaza|onOpenFileLibrary/);
  assert.doesNotMatch(sidebar, /hasMoreMenuItems|MobileMoreMenuSheet|DesktopMoreMenu/);
  assert.doesNotMatch(sidebar, /font-serif|icons\/icon\.svg/);
});

test("post-login navigation keeps governed MCP entry discoverable without stale local permissions", () => {
  const userMenu = readFileSync(
    join(root, "src/components/layout/UserMenu.tsx"),
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

  assert.match(userMenu, /path:\s*"\/mcp"[\s\S]{0,120}show:\s*true/);
  assert.doesNotMatch(userMenu, /Permission\.MCP_READ/);
  assert.match(chatAppContent, /useTools\(\{ enabled: true \}\)/);
  assert.doesNotMatch(chatAppContent, /const canReadMcpTools = hasPermission\(Permission\.MCP_READ\);/);
  assert.match(chatInput, /toolsAvailable/);
  assert.match(chatInput, /skillsAvailable/);
  assert.doesNotMatch(chatInput, /totalToolsCount > 0/);
  assert.doesNotMatch(chatInput, /totalSkillsCount > 0/);
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
  assert.match(surface, /context:[\s\S]*bg-\[var\(--theme-bg\)\]/);
  assert.match(surface, /panel:[\s\S]*bg-\[var\(--theme-bg-card\)\]/);
  assert.match(
    surface,
    /secondaryPanel:[\s\S]*bg-\[var\(--theme-bg-card\)\]/,
  );
  assert.match(surface, /secondaryPanel:/);
  assert.match(rightPanel, /workbenchSurface\.secondaryPanel/);
  assert.match(theme, /--theme-bg:\s*#f3f5f8;/);
  assert.match(theme, /--theme-bg-sidebar:\s*#f3f5f8;/);
  assert.match(theme, /--theme-bg-card:\s*#ffffff;/);
  assert.match(theme, /--theme-bg:\s*#f3f5f8;[\s\S]{0,80}--theme-bg-sidebar:\s*#f3f5f8;/);
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

test("authenticated workbench adopts one dark-rail enterprise shell", () => {
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
  assert.match(theme, /--theme-sidebar-rail:\s*#111827;/);
  assert.match(theme, /--theme-sidebar-panel:\s*#111827;/);
  assert.match(theme, /--theme-sidebar-panel-muted:\s*#1f2937;/);
  assert.match(theme, /--theme-bg:\s*#f3f5f8;/);
  assert.match(sidebar, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(sidebarList, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(sidebarList, /data-workbench-sidebar-panel/);
  assert.match(sidebarList, /text-slate-100/);
  assert.match(sidebarRail, /bg-\[var\(--theme-sidebar-rail\)\]/);
  assert.match(sidebarRail, /text-slate-200/);
  assert.match(projectItem, /hover:bg-\[var\(--theme-sidebar-panel-muted\)\]/);
  assert.match(sessionItem, /hover:bg-\[var\(--theme-sidebar-panel-muted\)\]/);
  assert.match(projectItem, /text-slate-300/);
  assert.match(sessionItem, /text-slate-300/);
  assert.doesNotMatch(sidebarList, /rounded-\[10px\]/);
  assert.doesNotMatch(projectItem, /rounded-\[10px\]/);
  assert.doesNotMatch(sessionItem, /rounded-\[10px\]/);
  assert.doesNotMatch(projectItem, /text-stone-600|text-stone-700|bg-stone-100/);
  assert.doesNotMatch(sessionItem, /text-stone-600|text-stone-700|bg-stone-100/);
  assert.doesNotMatch(sidebarRail, /style=\{\{[\s\S]{0,160}backgroundColor/);
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

test("persona and files routes are governed workbench pages instead of legacy shortcuts", () => {
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
  const personaWorkbench = readFileSync(
    join(root, "src/components/persona/PersonaWorkbenchPanel.tsx"),
    "utf8",
  );
  const filesWorkbench = readFileSync(
    join(root, "src/components/fileLibrary/RevealedFilesWorkbenchPanel.tsx"),
    "utf8",
  );
  const personaSelector = readFileSync(
    join(root, "src/components/persona/PersonaPresetSelector.tsx"),
    "utf8",
  );
  const zhLocale = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

  assert.match(app, /path="\/persona"[\s\S]{0,260}<ProtectedRoute>[\s\S]{0,220}<PersonaPage \/>[\s\S]{0,120}<\/ProtectedRoute>/);
  assert.match(app, /path="\/files"[\s\S]{0,260}<ProtectedRoute>[\s\S]{0,220}<FilesPage \/>[\s\S]{0,120}<\/ProtectedRoute>/);
  assert.match(app, /function PersonaPage\(\)[\s\S]{0,260}<AppContent key="persona" activeTab="persona" \/>/);
  assert.match(app, /function FilesPage\(\)[\s\S]{0,260}<AppContent key="files" activeTab="files" \/>/);
  assert.doesNotMatch(app, /path="\/persona"[\s\S]{0,220}<Navigate to="\/marketplace" replace \/>/);
  assert.doesNotMatch(app, /path="\/files"[\s\S]{0,220}<Navigate to="\/chat" replace \/>/);
  assert.doesNotMatch(activeGraph, /navigate\("\/persona"\)/);
  assert.doesNotMatch(activeGraph, /navigate\("\/files"\)/);
  assert.doesNotMatch(activeGraph, /PersonaPlazaPanel|persona:\s*PersonaPlazaPanel/);
  assert.match(tabs, /const PersonaWorkbenchPanel = lazy/);
  assert.match(tabs, /const RevealedFilesWorkbenchPanel = lazy/);
  assert.match(tabs, /persona:\s*PersonaWorkbenchPanel/);
  assert.match(tabs, /files:\s*RevealedFilesWorkbenchPanel/);
  assert.doesNotMatch(activeGraph, /MobileMoreMenuSheet|DesktopMoreMenu/);
  assert.match(personaWorkbench, /data-persona-workbench-shell/);
  assert.match(personaWorkbench, /data-frontend-governance-state=\{governanceState\}/);
  assert.match(personaWorkbench, /resolveFrontendGovernanceState/);
  assert.match(personaWorkbench, /WorkbenchStateSurface/);
  assert.match(personaWorkbench, /PersonaPresetCard/);
  assert.match(personaWorkbench, /PersonaEditorModal/);
  assert.doesNotMatch(personaWorkbench, /角色广场|rounded-2xl|rounded-3xl|shadow-xl|shadow-2xl/);
  assert.match(filesWorkbench, /data-files-workbench-shell/);
  assert.match(filesWorkbench, /data-frontend-governance-state/);
  assert.match(filesWorkbench, /RevealedFilesPanel/);
  assert.match(filesWorkbench, /WorkbenchStateSurface/);
  assert.doesNotMatch(filesWorkbench, /rounded-2xl|rounded-3xl|shadow-xl|shadow-2xl/);
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
  const skillBaseCard = readFileSync(
    join(root, "src/components/common/SkillBaseCard.tsx"),
    "utf8",
  );
  const marketplaceCard = readFileSync(
    join(root, "src/components/panels/MarketplacePanel/SkillCard.tsx"),
    "utf8",
  );
  const utilities = readFileSync(join(root, "src/styles/utilities.css"), "utf8");

  assert.match(skillsHub, /bg-\[var\(--theme-bg\)\]/);
  assert.match(marketplace, /bg-\[var\(--theme-bg\)\]/);
  assert.match(marketplace, /data-marketplace-catalog-shell/);
  assert.match(marketplace, /data-frontend-governance-state/);
  assert.match(marketplace, /effectiveGovernedUnavailable/);
  assert.match(groupAvailability, /flex flex-col[\s\S]*sm:flex-row/);
  assert.match(marketplaceCard, /versionLabel/);
  assert.match(marketplaceCard, /max-w-28 truncate/);
  assert.match(skillBaseCard, /p-3\.5 sm:p-4/);
  assert.doesNotMatch(skillBaseCard, /text-base font-semibold/);
  assert.match(utilities, /minmax\(260px,\s*1fr\)/);
  assert.doesNotMatch(skillsHub, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /bg-slate-50/);
  assert.doesNotMatch(marketplace, /border-slate-200 bg-white/);
});

test("profile modal shares the authenticated workbench visual language", () => {
  const profileFiles = [
    "src/components/profile/ProfileModal.tsx",
    "src/components/profile/tabs/ProfileInfoTab.tsx",
    "src/components/profile/tabs/ProfileNotificationTab.tsx",
    "src/components/profile/tabs/ProfilePreferencesTab.tsx",
    "src/components/profile/tabs/ProfileToolsTab.tsx",
    "src/components/profile/tabs/ProfileModelsTab.tsx",
    "src/components/profile/tabs/ProfileTermsTab.tsx",
  ];

  const sources = profileFiles.map((file) => [
    file,
    readFileSync(join(root, file), "utf8"),
  ] as const);
  const profileModal =
    sources.find(([file]) => file.endsWith("ProfileModal.tsx"))?.[1] ?? "";

  assert.match(profileModal, /data-profile-workbench-modal/);
  assert.match(profileModal, /bg-\[var\(--theme-bg-card\)\]/);
  assert.match(profileModal, /bg-\[var\(--theme-bg-sidebar\)\]/);

  for (const [file, source] of sources) {
    assert.doesNotMatch(source, /font-serif/, file);
    assert.doesNotMatch(source, /from-amber|to-amber|from-orange|to-orange/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-amber-/, file);
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|decoration)-orange-/, file);
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, file);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, file);
  }
});

test("profile secondary tabs and MCP selectors use workbench control tokens", () => {
  const controlFiles = [
    "src/components/profile/UserAgentPreferencePanel.tsx",
    "src/components/profile/tabs/ProfilePasswordTab.tsx",
    "src/components/profile/tabs/ProfileEnvVarsTab.tsx",
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
  assert.match(modelCatalog, /bg-\[var\(--theme-bg\)\]/);
  assert.ok(JSON.parse(zhLocale).models);
  assert.ok(JSON.parse(enLocale).models);
  assert.doesNotMatch(modelCatalog, /modelApi|agentConfigApi|roleApi/);
  assert.doesNotMatch(modelCatalog, /listProviders/);
  assert.doesNotMatch(modelCatalog, /providers\/list/);
  assert.doesNotMatch(modelCatalog, /glass-card|glass-card-subtle|enterprise-field-control/);
  assert.doesNotMatch(modelCatalog, /Legacy surface quarantined/);
});

test("workbench pages render concrete projection panels instead of thin placeholders", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
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
  assert.match(tabs, /const AgentDirectoryPanel = lazy/);
  assert.match(tabs, /agents:\s*AgentDirectoryPanel/);
  assert.doesNotMatch(app, /agents:[\s\S]{0,420}titleKey:\s*"workbench\.phaseTwo\.agents\.title"/);
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
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
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
      new RegExp(
        `path="${route}"[\\s\\S]{0,260}<ProtectedRoute>[\\s\\S]{0,220}<${page} \\/>[\\s\\S]{0,120}<\\/ProtectedRoute>`,
      ),
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
  assert.match(projectionPages, /governance\.secret_material_projected/);
  assert.match(projectionPages, /state === "degraded"/);
  assert.match(projectionPages, /state === "forbidden"/);
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

test("agents route uses a public read-only directory instead of legacy config admin APIs", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const directory = readFileSync(
    join(root, "src/components/panels/AgentDirectoryPanel.tsx"),
    "utf8",
  );

  assert.match(
    app,
    /path="\/agents"[\s\S]{0,260}<ProtectedRoute>[\s\S]{0,220}<AgentsPage \/>[\s\S]{0,120}<\/ProtectedRoute>/,
  );
  assert.match(app, /function AgentsPage\(\)[\s\S]{0,260}<AppContent key="agents" activeTab="agents" \/>/);
  assert.match(tabs, /agents:\s*AgentDirectoryPanel/);
  assert.match(directory, /data-agent-directory-shell/);
  assert.match(directory, /agentApi\.list\(\)/);
  assert.match(directory, /WorkbenchStateSurface/);
  assert.match(directory, /data-frontend-governance-state/);
  assert.doesNotMatch(directory, /agentConfigApi|roleApi|\/api\/agent\/config|Permission\.AGENT_ADMIN/);
});

test("channels route renders a governed workbench instead of a thin unavailable placeholder", () => {
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const channels = readFileSync(
    join(root, "src/components/channels/ChannelImportPanel.tsx"),
    "utf8",
  );

  assert.match(tabs, /channels:\s*ChannelImportPanel/);
  assert.match(channels, /data-channel-workbench-shell/);
  assert.match(channels, /channelApi\.listCatalog/);
  assert.match(channels, /data-channel-catalog-list/);
  assert.match(channels, /data-channel-admin-governance/);
  assert.match(channels, /PanelHeader/);
  assert.match(channels, /GovernanceAvailabilityBadge/);
  assert.match(channels, /channelImport\.capabilities\.publicSources\.title/);
  assert.match(channels, /channelImport\.catalogReady\.title/);
  assert.doesNotMatch(channels, /const backedSources/);
  assert.doesNotMatch(channels, /backedSources\.length === 0/);
  assert.doesNotMatch(channels, /supportedChannelTypes/);
  assert.doesNotMatch(channels, /channelImport\.backendGap/);
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

  assert.match(mcpPanel, /resolveMcpGovernanceState/);
  assert.match(mcpPanel, /data-mcp-directory-shell/);
  assert.match(mcpPanel, /data-frontend-governance-state=\{mcpGovernance\.pageState\}/);
  assert.match(mcpPanel, /data-required-permission=\{mcpGovernance\.requiredPermission\}/);
  assert.match(mcpPanel, /data-auth-projection-has-permission/);
  assert.match(mcpPanel, /WorkbenchStateSurface/);
  assert.match(mcpPanel, /data-fail-closed-surface="mcp-lifecycle"/);
  assert.match(mcpPanel, /data-fail-closed-surface="mcp-credentials"/);
  assert.match(mcpState, /requiredPermission: "mcp:read"/);
  assert.match(mcpState, /resolveFrontendGovernanceState/);
  assert.match(mcpState, /isPermissionError\(loadError\)/);
  assert.match(mcpState, /featureEnabled:\s*true/);
  assert.match(mcpState, /adminOnly:\s*true/);
  assert.doesNotMatch(mcpPanel, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(mcpPanel, /hasPermission:\s*canReadMcp/);
  assert.doesNotMatch(mcpPanel, /createServer|updateServer|deleteServer|toggleServer|promoteServer|demoteServer/);
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
  for (const source of [skillApi, marketplaceApi]) {
    assert.doesNotMatch(source, /\/api\/ai\/admin/);
    assert.doesNotMatch(source, /\/api\/admin/);
    assert.doesNotMatch(source, /\/admin\/skills|\/admin\/marketplace/);
    assert.doesNotMatch(source, /lambchat/i);
  }
});

test("channels client uses only PR177 governed channel contracts", () => {
  const channelApi = readFileSync(
    join(root, "src/services/api/channel.ts"),
    "utf8",
  );
  const channelAdminHook = readFileSync(
    join(root, "src/hooks/useChannelAdminOperations.ts"),
    "utf8",
  );
  const channelPanel = readFileSync(
    join(root, "src/components/channels/ChannelImportPanel.tsx"),
    "utf8",
  );
  const channelTypes = readFileSync(join(root, "src/types/channel.ts"), "utf8");
  const authTypes = readFileSync(join(root, "src/types/auth.ts"), "utf8");

  assert.match(channelApi, /\/api\/channels\/catalog/);
  assert.match(channelApi, /\/api\/admin\/channels/);
  assert.match(channelApi, /listCatalog/);
  assert.match(channelApi, /channelAdminApi/);
  assert.match(channelApi, /testAdminChannel/);
  assert.match(channelAdminHook, /enabled/);
  assert.match(channelAdminHook, /missing_permission:channel:admin/);
  assert.match(channelPanel, /useChannelAdminOperations\(\{\s*enabled:\s*canAdminChannels/);
  assert.match(channelTypes, /PublicChannelResponse/);
  assert.match(channelTypes, /ChannelAdminOperationResponse/);
  assert.match(authTypes, /CHANNEL_ADMIN = "channel:admin"/);
  for (const legacyEndpoint of [
    "/api/channels/types",
    "/api/channels/${channelType}",
    "/api/channels/${channelType}/${instanceId}",
    "/api/channels/${channelType}/${instanceId}/status",
    "/api/channels/${channelType}/${instanceId}/test",
  ]) {
    assert.ok(!channelApi.includes(legacyEndpoint), legacyEndpoint);
  }
});

test("skills hub lets PR177 public catalogs prove permissions before fail-closed", () => {
  const skillsHub = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  const resolver = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel/state.ts"),
    "utf8",
  );
  const useAuth = readFileSync(join(root, "src/hooks/useAuth.tsx"), "utf8");

  assert.match(skillsHub, /resolveSkillsHubGovernance/);
  assert.match(skillsHub, /canReadSkills = hasAnyPermission\(\[Permission\.SKILL_READ\]\)/);
  assert.match(skillsHub, /canReadMarketplace = hasAnyPermission\(\[Permission\.MARKETPLACE_READ\]\)/);
  assert.match(skillsHub, /const governanceState = hubGovernance\.pageState/);
  assert.match(skillsHub, /data-required-permission=\{hubGovernance\.requiredPermission\}/);
  assert.match(skillsHub, /catalogPermissionDeniedByTab/);
  assert.match(skillsHub, /catalogPermissionDenied: catalogPermissionDeniedByTab\[requestedTab\]/);
  assert.match(skillsHub, /governedUnavailable=\{hubGovernance\.governedUnavailable\}/);
  assert.match(skillsHub, /onPermissionDeniedChange=\{handleCatalogPermissionDeniedChange\}/);
  assert.match(skillsHub, /data-auth-projection-has-permission=\{hubGovernance\.authProjectionHasPermission\}/);
  assert.match(resolver, /catalogPermissionDenied\?: boolean/);
  assert.match(resolver, /hasWorkspace\?: boolean/);
  assert.match(resolver, /pageState: FrontendGovernanceState/);
  assert.match(resolver, /authProjectionHasPermission/);
  assert.match(resolver, /const governedUnavailable = Boolean\(catalogPermissionDenied\)/);
  assert.match(resolver, /!hasWorkspace\s*\?\s*"no-workspace"/);
  assert.match(resolver, /governedUnavailable\s*\?\s*"forbidden"/);
  assert.match(resolver, /projectionError\s*\?\s*"degraded"/);
  assert.match(resolver, /requiredPermission: "skill:read" \| "marketplace:read"/);
  assert.match(resolver, /requestedTab === "marketplace"/);
  assert.match(resolver, /requestedTab === "marketplace" \? canReadMarketplace : canReadSkills/);
  assert.match(resolver, /hasPermission: !governedUnavailable/);
  assert.doesNotMatch(resolver, /hasPermission: canReadMarketplace/);
  assert.doesNotMatch(resolver, /hasPermission: canReadSkills/);
  assert.doesNotMatch(skillsHub, /resolveFrontendGovernanceState/);
  assert.doesNotMatch(skillsHub, /hasPermission:\s*true/);
  assert.doesNotMatch(skillsHub, /hasCatalogPermissionGap/);
  assert.doesNotMatch(skillsHub, /activeTabHasPermission/);
  assert.match(useAuth, /hasEffectivePermission\(permissions, permission\)/);
  assert.match(useAuth, /hasAnyEffectivePermission\(permissions, perms\)/);
  assert.match(useAuth, /hasAllEffectivePermissions\(permissions, perms\)/);
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
