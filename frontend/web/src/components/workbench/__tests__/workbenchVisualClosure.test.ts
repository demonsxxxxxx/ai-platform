import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("workbench shell exposes dense chat regions", () => {
  const shell = read("src/components/workbench/WorkbenchShell.tsx");
  const surface = read("src/components/workbench/workbenchSurface.ts");

  assert.match(shell, /data-workbench-region="thread"/);
  assert.match(shell, /data-workbench-region="composer"/);
  assert.match(shell, /data-workbench-region="context"/);
  assert.match(surface, /workspace:/);
  assert.match(surface, /thread:/);
  assert.match(surface, /composer:/);
  assert.match(surface, /context:/);
  assert.match(surface, /commandSurface:/);
  assert.match(surface, /unavailable:/);
});

test("authenticated workbench source avoids marketing and nested-card patterns", () => {
  const text = [
    read("src/components/workbench/WorkbenchShell.tsx"),
    read("src/components/workbench/WorkbenchRightPanel.tsx"),
    read("src/components/chat/WelcomePage.tsx"),
    read("src/styles/chat.css"),
  ].join("\n");
  const tabContent = read("src/components/layout/AppContent/TabContent.tsx");

  assert.doesNotMatch(text, /hero-card|gradient-orb|nested-card/);
  assert.doesNotMatch(text, /rounded-3xl/);
  assert.match(text, /rounded-lg/);
  assert.doesNotMatch(tabContent, /max-w-4xl|sm:max-w-5xl|lg:max-w-6xl/);
  assert.match(tabContent, /data-authenticated-workbench-page/);
});

test("post-login routes do not fall back to public landing or split backgrounds", () => {
  const tabContent = read("src/components/layout/AppContent/TabContent.tsx");
  const header = read("src/components/layout/AppContent/Header.tsx");
  const launchpad = read("src/components/launchpad/LaunchpadPanel.tsx");
  const sidebar = read(
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  );

  assert.match(
    tabContent,
    /className="flex-1 overflow-hidden bg-\[var\(--theme-workbench-canvas\)\]"/,
  );
  assert.doesNotMatch(tabContent, /className="flex-1 overflow-hidden bg-\[var\(--theme-bg\)\]"/);
  assert.match(launchpad, /className=\{workbenchSurface\.page\}/);
  assert.match(header, /bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.doesNotMatch(header, /bg-\[var\(--theme-bg\)\]/);
  assert.doesNotMatch(launchpad, /bg-\[var\(--theme-bg\)\]/);
  assert.doesNotMatch(sidebar, /href=\{APP_HOME_URL\}/);
  assert.match(sidebar, /onClick=\{onNewSession\}/);
});

test("expanded sidebar uses one dark enterprise navigation system", () => {
  const sessionSidebar = read("src/components/panels/SessionSidebar.tsx");
  const sessionList = read(
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  );
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");
  const baseCss = read("src/styles/base.css");

  assert.match(sessionList, /data-workbench-sidebar-panel/);
  assert.match(sessionList, /data-workbench-primary-nav/);
  assert.match(sessionList, /data-workbench-nav-group="tasks"/);
  assert.match(sessionList, /data-workbench-nav-group="governance"/);
  assert.match(sessionList, /data-workbench-session-region/);
  assert.match(sessionList, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(sessionSidebar, /bg-\[var\(--theme-sidebar-panel\)\]/);
  assert.match(rail, /bg-\[var\(--theme-sidebar-rail\)\]/);
  assert.match(baseCss, /--theme-sidebar-panel:\s*#111827/);
  assert.match(baseCss, /--theme-sidebar-rail:\s*#111827/);
  assert.doesNotMatch(sessionList, /bg-\[var\(--theme-bg-card\)\]/);
});

test("post-login shell removes legacy LambChat runtime identifiers", () => {
  const auth = read("src/hooks/useAuth.tsx");
  const sources = [
    auth,
    read("src/components/panels/SessionSidebar.tsx"),
    read("src/components/panels/SidebarParts/SessionListContent.tsx"),
    read("src/components/panels/SidebarParts/SidebarRail.tsx"),
    read("src/components/layout/AppContent/TabContent.tsx"),
    read("src/components/layout/AppContent/Header.tsx"),
  ].join("\n");

  assert.match(auth, /SIDEBAR_COLLAPSED_STORAGE_KEY = "ai-platform-sidebar-collapsed"/);
  assert.doesNotMatch(sources, /lamb/i);
});

test("post-login workbench defaults to expanded application navigation", () => {
  const appContent = read("src/components/layout/AppContent/index.tsx");
  const sessionSidebar = read("src/components/panels/SessionSidebar.tsx");

  assert.match(appContent, /saved !== null \? saved === "true" : false/);
  assert.doesNotMatch(appContent, /saved !== null \? saved === "true" : true/);
  assert.doesNotMatch(sessionSidebar, /useState\(true\)/);
  assert.match(sessionSidebar, /useState\(false\)/);
});

test("light workbench tokens avoid returning to a white chat canvas", () => {
  const baseCss = read("src/styles/base.css");
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const welcome = read("src/components/chat/WelcomePage.tsx");

  assert.match(baseCss, /--theme-bg:\s*#e9eef5;/);
  assert.match(baseCss, /--theme-workbench-canvas:\s*#e9eef5;/);
  assert.match(baseCss, /--theme-workbench-panel:\s*#f6f8fb;/);
  assert.doesNotMatch(baseCss, /--theme-workbench-canvas:\s*#f(?:3f5f8|fffff);/);
  assert.doesNotMatch(baseCss, /--theme-workbench-panel:\s*#f(?:8fafc|fffff);/);
  assert.match(surface, /root:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(surface, /thread:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(welcome, /data-chat-start-surface/);
  assert.doesNotMatch(welcome, /max-w-4xl flex-col gap-3 py-4/);
});

test("workbench right context uses the same canvas as the main workspace", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const rightPanel = read("src/components/workbench/WorkbenchRightPanel.tsx");
  const chatInput = read("src/components/chat/ChatInput.tsx");

  assert.match(surface, /context:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(rightPanel, /bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(rightPanel, /workbenchSurface\.secondaryPanel/);
  assert.match(chatInput, /backgroundColor: "var\(--theme-workbench-canvas\)"/);
  assert.doesNotMatch(chatInput, /backgroundColor: "var\(--theme-bg\)"/);
});

test("post-login projection panels share workbench surface tokens", () => {
  const panels = new Map([
    ["MCPPanel", read("src/components/panels/MCPPanel.tsx")],
    ["RolesPanel", read("src/components/panels/RolesPanel.tsx")],
    [
      "ChannelImportPanel",
      read("src/components/channels/ChannelImportPanel.tsx"),
    ],
    [
      "PersonaWorkbenchPanel",
      read("src/components/persona/PersonaWorkbenchPanel.tsx"),
    ],
    [
      "RevealedFilesWorkbenchPanel",
      read("src/components/fileLibrary/RevealedFilesWorkbenchPanel.tsx"),
    ],
    ["AgentDirectoryPanel", read("src/components/panels/AgentDirectoryPanel.tsx")],
    ["ModelCatalogPanel", read("src/components/panels/ModelCatalogPanel.tsx")],
    ["MemoryPanel", read("src/components/panels/MemoryPanel/index.tsx")],
    [
      "WorkbenchProjectionPages",
      read("src/components/workbench/WorkbenchProjectionPages.tsx"),
    ],
  ]);

  for (const [name, source] of panels) {
    assert.match(source, /data-frontend-governance-state/, name);
    assert.match(source, /workbenchSurface\.(?:page|statePage|compactPanel|panel)/, name);
    assert.match(source, /workbenchSurface\.(?:compactPanel|panel|catalog)/, name);
    assert.doesNotMatch(source, /bg-\[var\(--theme-bg\)\]/, name);
    assert.doesNotMatch(source, /className="[^"]*dark:bg-stone-950(?:\/\d+)?[^"]*"/, name);
    assert.doesNotMatch(source, /text-stone-(?:700|800|900)/, name);
  }
});

test("persona files and memory pages stay on the enterprise workbench visual system", () => {
  const personaWorkbench = read(
    "src/components/persona/PersonaWorkbenchPanel.tsx",
  );
  const personaCard = read("src/components/persona/PersonaPresetCard.tsx");
  const personaSelector = read("src/components/persona/PersonaPresetSelector.tsx");
  const personaPreview = read("src/components/persona/PersonaPreviewSidebar.tsx");
  const cardUtils = read("src/components/common/cardUtils.ts");
  const cardCss = read("src/styles/card-base.css");
  const personaCss = read("src/styles/persona.css");
  const baseCss = read("src/styles/base.css");
  const filesWorkbench = read(
    "src/components/fileLibrary/RevealedFilesWorkbenchPanel.tsx",
  );
  const fileToolbar = read("src/components/fileLibrary/components/Toolbar.tsx");
  const fileGridCard = read("src/components/fileLibrary/components/GridCard.tsx");
  const fileListCard = read("src/components/fileLibrary/components/ListCard.tsx");
  const fileEmptyState = read(
    "src/components/fileLibrary/components/EmptyState.tsx",
  );
  const memoryPanel = read("src/components/panels/MemoryPanel/index.tsx");

  for (const [name, source] of new Map([
    ["PersonaPresetCard", personaCard],
    ["PersonaPresetSelector", personaSelector],
    ["PersonaPreviewSidebar", personaPreview],
  ])) {
    assert.doesNotMatch(
      source,
      /nameToGradient|linear-gradient|scb__banner|pps-card__banner/,
      name,
    );
    assert.match(
      source,
      /enterprise-subtle-panel|scb group|pps-card group/,
      name,
    );
  }

  assert.doesNotMatch(cardUtils, /GRADIENT_PALETTES|nameToGradient/);
  assert.doesNotMatch(cardCss, /mp-card|scb__banner|border-shimmer/);
  assert.doesNotMatch(
    personaCss,
    /pps-card__banner|pps-card__status-badge|translateY\(-3px\)/,
  );
  assert.match(baseCss, /--theme-border-strong:/);
  assert.match(fileToolbar, /var\(--theme-workbench-canvas\)/);
  assert.doesNotMatch(fileToolbar, /var\(--theme-bg\)"/);
  assert.doesNotMatch(
    fileGridCard,
    /text-stone-(?:700|800|900)|dark:bg-stone-900/,
  );
  assert.doesNotMatch(
    fileListCard,
    /text-stone-(?:700|800|900)|dark:bg-stone-900/,
  );
  assert.match(fileEmptyState, /enterprise-empty-state/);
  assert.match(memoryPanel, /className=\{workbenchSurface\.page\}/);
  assert.match(memoryPanel, /className=\{workbenchSurface\.statePage\}/);
  assert.match(personaWorkbench, /data-persona-degraded-workbench-grid/);
  assert.match(personaWorkbench, /data-persona-degraded-main/);
  assert.match(personaWorkbench, /data-persona-degraded-contract/);
  assert.match(filesWorkbench, /data-files-degraded-workbench-grid/);
  assert.match(filesWorkbench, /data-files-degraded-main/);
  assert.match(filesWorkbench, /data-files-degraded-contract/);
  for (const [name, source] of new Map([
    ["PersonaWorkbenchPanel", personaWorkbench],
    ["RevealedFilesWorkbenchPanel", filesWorkbench],
  ])) {
    assert.match(source, /xl:grid-cols-\[minmax\(0,1fr\)_18rem\]/, name);
    assert.match(source, /WorkbenchStateSurface/, name);
  }
});

test("workbench surface exports shared page containers for governed routes", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");

  assert.match(surface, /page:/);
  assert.match(surface, /statePage:/);
  assert.match(surface, /sectionPanel:/);
  assert.match(surface, /page:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(surface, /statePage:[\s\S]*bg-\[var\(--theme-workbench-canvas\)\]/);
  assert.match(surface, /panel:[\s\S]*bg-\[var\(--theme-workbench-panel\)\]/);
  assert.match(surface, /compactPanel:[\s\S]*bg-\[var\(--theme-workbench-panel\)\]/);
  assert.match(surface, /sectionPanel:[\s\S]*bg-\[var\(--theme-workbench-panel\)\]/);
});

test("safe projection pages render a full workbench instead of thin lists", () => {
  const projectionPages = read("src/components/workbench/WorkbenchProjectionPages.tsx");
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.match(projectionPages, /data-projection-workbench-grid/);
  assert.match(projectionPages, /data-projection-task-panel/);
  assert.match(projectionPages, /data-projection-summary-panel/);
  assert.match(projectionPages, /data-projection-insight-panel/);
  assert.match(projectionPages, /data-projection-list-panel/);
  assert.match(projectionPages, /data-projection-empty-state/);
  assert.match(projectionPages, /ProjectionEmptyItem/);
  assert.match(projectionPages, /ProjectionMetric/);
  assert.match(projectionPages, /ProjectionInsightPanel/);
  assert.match(projectionPages, /ProjectionListPanel/);
  assert.match(projectionPages, /xl:grid-cols-\[minmax\(0,1fr\)_18rem\]/);
  assert.match(projectionPages, /workbench\.projections\.currentTask/);
  assert.match(projectionPages, /workbench\.projections\.governance\.summaryTitle/);
  assert.match(projectionPages, /workbench\.projections\.users\.directoryTitle/);
  assert.match(projectionPages, /workbench\.projections\.settings\.secretChip/);
  assert.match(projectionPages, /workbench\.projections\.feedback\.queueTitle/);
  assert.match(projectionPages, /workbench\.projections\.notifications\.streamTitle/);
  assert.equal(zh.workbench.projections.currentTask, "当前任务");
  assert.equal(en.workbench.projections.currentTask, "Current task");
  assert.equal(zh.workbench.projections.governance.summaryTitle, "读写治理摘要");
  assert.equal(en.workbench.projections.governance.summaryTitle, "Read/write governance");
  assert.ok(zh.workbench.projections.users.roleLabels.admin);
  assert.ok(en.workbench.projections.settings.categories.security);
  assert.ok(zh.workbench.projections.feedback.status.open);
  assert.ok(en.workbench.projections.notifications.readState.unread);
  assert.doesNotMatch(projectionPages, /bg-\[var\(--theme-bg\)\]/);
  assert.doesNotMatch(projectionPages, /text-stone-(?:700|800|900)/);
  assert.doesNotMatch(projectionPages, /<div className="mt-3">\{children\}<\/div>/);
});

test("notification projection metrics use the same visible rows as the stream", () => {
  const projectionPages = read("src/components/workbench/WorkbenchProjectionPages.tsx");

  assert.match(projectionPages, /const visibleNotifications = dedupeNotifications\(combined\);/);
  assert.match(projectionPages, /const unreadCount = visibleNotifications\.filter/);
  assert.match(projectionPages, /const activeCount = visibleNotifications\.filter/);
  assert.match(projectionPages, /value: visibleNotifications\.length/);
  assert.match(projectionPages, /\{visibleNotifications\.length === 0 \?/);
  assert.match(projectionPages, /visibleNotifications\.map\(\(item\) =>/);
  assert.doesNotMatch(projectionPages, /const unreadCount = combined\.filter/);
  assert.doesNotMatch(projectionPages, /const activeCount = combined\.filter/);
  assert.doesNotMatch(projectionPages, /value: combined\.length/);
  assert.doesNotMatch(projectionPages, /dedupeNotifications\(combined\)\.map/);
});

test("skills marketplace cards stay dense and enterprise-workbench sized", () => {
  const baseCard = read("src/components/common/SkillBaseCard.tsx");
  const cardCss = read("src/styles/card-base.css");

  assert.match(baseCard, /p-3\.5 sm:p-4/);
  assert.match(baseCard, /text-sm font-semibold/);
  assert.match(baseCard, /bg-\[var\(--theme-workbench-panel\)\]/);
  assert.doesNotMatch(baseCard, /text-base font-semibold/);
  assert.match(cardCss, /border-radius:\s*0\.5rem/);
  assert.match(cardCss, /background:\s*var\(--theme-workbench-panel\);/);
  assert.doesNotMatch(cardCss, /translateY\(-4px\)/);
});

test("role plaza state is resolver-driven instead of hard-coded ready", () => {
  const roles = read("src/components/panels/RolesPanel.tsx");
  const resolver = read("src/components/panels/roleGovernanceState.ts");

  assert.match(roles, /resolveRoleGovernanceState/);
  assert.match(roles, /roleGovernanceApi\.getOverview/);
  assert.match(roles, /data-frontend-governance-state=\{roleGovernance\.pageState\}/);
  assert.doesNotMatch(roles, /data-frontend-governance-state="ready"/);
  assert.doesNotMatch(roles, /roleDirectoryBacked:\s*false/);
  assert.match(resolver, /overview/);
  assert.match(resolver, /featureEnabled:\s*roleDirectoryBacked/);
  assert.match(resolver, /isPermissionError\(loadError\)/);
  assert.match(resolver, /adminOnly: !canManageRoles/);
});

test("skills hub routes use the public contract resolver", () => {
  const hub = read("src/components/panels/SkillsHubPanel.tsx");
  const resolver = read("src/components/panels/SkillsHubPanel/state.ts");
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.match(hub, /resolveSkillsHubGovernance/);
  assert.match(hub, /data-required-permission=\{hubGovernance\.requiredPermission\}/);
  assert.match(hub, /data-effective-projection-has-permission=\{hubGovernance\.effectiveProjectionHasPermission\}/);
  assert.match(hub, /data-effective-permissions-source=\{hubGovernance\.effectivePermissionsSource\}/);
  assert.match(hub, /statusCopyNamespace/);
  assert.match(hub, /"skillsHub\.skills"/);
  assert.match(hub, /"skillsHub\.marketplace"/);
  assert.match(hub, /\$\{statusCopyNamespace\}\.\$\{statusCopyKey\}\.title/);
  assert.match(hub, /\$\{statusCopyNamespace\}\.\$\{statusCopyKey\}\.description/);
  assert.match(hub, /data-skills-catalog-status-strip/);
  assert.match(hub, /statusIndicatorClass/);
  assert.match(hub, /bg-amber-500/);
  assert.match(hub, /bg-rose-500/);
  assert.match(hub, /bg-\[var\(--theme-primary\)\]/);
  assert.doesNotMatch(hub, /bg-emerald-500/);
  assert.doesNotMatch(hub, /rounded-full bg-emerald-500/);
  assert.doesNotMatch(hub, /<section className=\{`\$\{workbenchSurface\.compactPanel\} p-3`\}>/);
  assert.doesNotMatch(hub, /PanelHeader/);
  assert.match(hub, /data-skills-catalog-status/);
  assert.doesNotMatch(hub, /data-skills-catalog-nav/);
  assert.equal(zh.skillsHub.skills.ready.title, "Skills 目录可用");
  assert.equal(zh.skillsHub.marketplace.ready.title, "技能商店可用");
  assert.notEqual(
    zh.skillsHub.skills.ready.description,
    zh.skillsHub.marketplace.ready.description,
  );
  assert.equal(en.skillsHub.skills.ready.title, "Skills catalog is available");
  assert.equal(en.skillsHub.marketplace.ready.title, "Marketplace is available");
  assert.notEqual(
    en.skillsHub.skills.ready.description,
    en.skillsHub.marketplace.ready.description,
  );
  assert.match(resolver, /requiredPermission: "skill:read" \| "marketplace:read"/);
  assert.match(resolver, /effectivePermissions\?: string\[\]/);
  assert.match(resolver, /effectiveProjectionHasPermission/);
  assert.match(resolver, /effectivePermissionsSource/);
  assert.match(resolver, /authProjectionHasPermission && !effectivePermissions/);
  assert.match(resolver, /effectivePermissionsKnown\?: boolean/);
  assert.match(resolver, /marketplace:read/);
  assert.match(resolver, /skill:read/);
});

test("skills marketplace hub uses one workbench canvas instead of split page backgrounds", () => {
  const hub = read("src/components/panels/SkillsHubPanel.tsx");
  const skillsPanel = read("src/components/panels/SkillsPanel/index.tsx");
  const skillsList = read("src/components/panels/SkillsPanel/SkillsList.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");
  const skillCss = read("src/styles/skill.css");

  for (const [name, source] of new Map([
    ["SkillsHubPanel", hub],
    ["SkillsPanel", skillsPanel],
    ["MarketplacePanel", marketplace],
  ])) {
    assert.match(
      source,
      /bg-\[var\(--theme-workbench-canvas\)\]|className=\{workbenchSurface\.page\}/,
      name,
    );
    assert.doesNotMatch(
      source,
      /className="[^"]*bg-\[var\(--theme-bg\)\][^"]*"/,
      name,
    );
  }

  assert.match(skillsList, /data-skills-catalog-toolbar/);
  assert.match(marketplace, /data-marketplace-catalog-toolbar/);
  assert.match(skillsList, /skill-catalog-toolbar/);
  assert.match(marketplace, /skill-catalog-toolbar/);
  assert.match(skillsList, /skill-catalog-toolbar__row/);
  assert.match(marketplace, /skill-catalog-toolbar__row/);
  assert.match(skillsList, /skill-catalog-toolbar__search/);
  assert.match(marketplace, /skill-catalog-toolbar__search/);
  assert.match(skillsList, /skill-catalog-toolbar__actions/);
  assert.match(marketplace, /skill-catalog-toolbar__actions/);
  assert.match(skillsList, /workbenchSurface\.catalog\.toolbarShell/);
  assert.match(marketplace, /workbenchSurface\.catalog\.toolbarShell/);
  assert.match(skillsList, /workbenchSurface\.catalog\.content/);
  assert.match(marketplace, /workbenchSurface\.catalog\.content/);
  assert.match(skillsList, /workbenchSurface\.catalog\.cardGrid/);
  assert.match(marketplace, /workbenchSurface\.catalog\.cardGrid/);
  assert.match(skillsList, /workbenchSurface\.catalog\.emptyState/);
  assert.match(marketplace, /workbenchSurface\.catalog\.emptyState/);
  assert.doesNotMatch(skillsList, /auto-grid-cols/);
  assert.doesNotMatch(marketplace, /auto-grid-cols/);
  assert.doesNotMatch(skillsList, /text-stone-(?:400|500|600|700|800|900)/);
  assert.doesNotMatch(marketplace, /text-stone-(?:400|500|600|700|800|900)/);
  assert.doesNotMatch(marketplace, /text-slate-(?:400|500|600|700|800|900)/);
  assert.doesNotMatch(hub, /data-skills-catalog-sidebar/);
  assert.doesNotMatch(hub, /<aside/);
  assert.doesNotMatch(hub, /showTabSwitcher/);
  assert.doesNotMatch(hub, /data-skills-catalog-nav/);
  assert.doesNotMatch(hub, /actions=\{/);
  assert.match(hub, /data-skills-catalog-status/);
  assert.match(skillCss, /--skill-grid-bg:\s*var\(--theme-workbench-canvas\);/);
  assert.match(
    skillCss,
    /\.skill-content-area\s*{\s*background:\s*var\(--theme-workbench-canvas\);/,
  );
  assert.match(
    skillCss,
    /\.skill-panel-header\s*{[\s\S]*background:\s*var\(--theme-workbench-canvas\);/,
  );
  assert.match(skillCss, /\.skill-catalog-toolbar__row/);
  assert.match(skillCss, /\.skill-catalog-toolbar__search/);
  assert.match(skillCss, /\.skill-catalog-toolbar__actions/);
  assert.match(skillCss, /\.skill-catalog-toolbar\s*{[\s\S]*border-bottom:\s*0;/);
  assert.doesNotMatch(
    skillCss,
    /\.skill-content-area\s*{\s*background:\s*var\(--theme-bg\);/,
  );
});

test("reachable catalog pages delegate page backgrounds to workbench surface tokens", () => {
  const sources = new Map([
    ["SkillsHubPanel", read("src/components/panels/SkillsHubPanel.tsx")],
    ["SkillsPanel", read("src/components/panels/SkillsPanel/index.tsx")],
    ["MarketplacePanel", read("src/components/panels/MarketplacePanel.tsx")],
    ["AgentDirectoryPanel", read("src/components/panels/AgentDirectoryPanel.tsx")],
    ["ModelCatalogPanel", read("src/components/panels/ModelCatalogPanel.tsx")],
    [
      "ChannelImportPanel",
      read("src/components/channels/ChannelImportPanel.tsx"),
    ],
  ]);

  for (const [name, source] of sources) {
    assert.match(source, /className=\{workbenchSurface\.page\}/, name);
    assert.doesNotMatch(
      source,
      /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/,
      name,
    );
    assert.doesNotMatch(
      source,
      /className="[^"]*dark:bg-stone-950(?:\/\d+)?[^"]*"/,
      name,
    );
  }
});

test("launchpad and unavailable route workbenches use shared surface tokens", () => {
  const launchpad = read("src/components/launchpad/LaunchpadPanel.tsx");
  const governedRoute = read("src/components/workbench/GovernedRouteWorkbench.tsx");
  const stateSurface = read("src/components/workbench/WorkbenchStateSurface.tsx");

  for (const [name, source] of new Map([
    ["LaunchpadPanel", launchpad],
    ["GovernedRouteWorkbench", governedRoute],
  ])) {
    assert.match(source, /className=\{workbenchSurface\.page\}/, name);
    assert.doesNotMatch(
      source,
      /className="[^"]*bg-\[var\(--theme-workbench-canvas\)\][^"]*"/,
      name,
    );
    assert.doesNotMatch(
      source,
      /className="[^"]*dark:bg-stone-950(?:\/\d+)?[^"]*"/,
      name,
    );
  }

  assert.doesNotMatch(stateSurface, /dark:bg-stone-950\/70/);
  assert.match(stateSurface, /workbenchSurface\.statusTile/);
});

test("launchpad and public directory pages use one workbench catalog layout", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const launchpad = read("src/components/launchpad/LaunchpadPanel.tsx");
  const mcp = read("src/components/panels/MCPPanel.tsx");
  const agents = read("src/components/panels/AgentDirectoryPanel.tsx");
  const models = read("src/components/panels/ModelCatalogPanel.tsx");

  assert.match(surface, /catalog:/);
  assert.match(surface, /summaryGrid:/);
  assert.match(surface, /summaryGridFour:/);
  assert.match(surface, /2xl:grid-cols-4/);
  assert.match(surface, /summaryCard:/);
  assert.match(surface, /content:/);
  assert.match(surface, /cardGrid:/);
  assert.match(surface, /entryCard:/);
  assert.match(surface, /metricTile:/);
  assert.match(surface, /iconBox:/);

  for (const [name, source] of new Map([
    ["LaunchpadPanel", launchpad],
    ["MCPPanel", mcp],
    ["AgentDirectoryPanel", agents],
    ["ModelCatalogPanel", models],
  ])) {
    assert.match(source, /className=\{workbenchSurface\.page\}/, name);
    assert.match(source, /workbenchSurface\.catalog\.summaryGrid/, name);
    assert.match(source, /workbenchSurface\.catalog\.summaryCard/, name);
    assert.match(source, /workbenchSurface\.catalog\.cardGrid/, name);
    assert.match(source, /workbenchSurface\.catalog\.entryCard/, name);
    assert.doesNotMatch(source, /text-stone-(?:400|500|600|700|800|900)/, name);
    assert.doesNotMatch(source, /text-slate-(?:400|500|600|700|800|900)/, name);
    assert.doesNotMatch(source, /bg-slate-(?:100|200|900)/, name);
    assert.doesNotMatch(source, /dark:bg-stone-(?:800|900|950)/, name);
  }

  for (const [name, source] of new Map([
    ["MCPPanel", mcp],
    ["AgentDirectoryPanel", agents],
    ["ModelCatalogPanel", models],
  ])) {
    assert.match(source, /workbenchSurface\.catalog\.content/, name);
    assert.match(source, /workbenchSurface\.catalog\.metricTile/, name);
    assert.match(
      source,
      /workbenchSurface\.catalog\.(?:iconBox|compactIconBox)/,
      name,
    );
  }

  assert.match(launchpad, /PanelHeader/);
  assert.match(launchpad, /data-launchpad-directory-shell/);
  assert.doesNotMatch(launchpad, /border-b border-slate-200/);
  assert.match(mcp, /workbenchSurface\.catalog\.summaryGridFour/);
  assert.doesNotMatch(mcp, /workbenchSurface\.catalog\.summaryGrid[^\w]/);
});

test("composer and command surfaces use stable dimensions", () => {
  const css = read("src/styles/chat.css");
  assert.match(css, /\.chat-input-container/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /max-height:\s*min\(52dvh,\s*420px\)/);
  assert.match(css, /\.composer-command-surface/);
  assert.match(css, /overflow:\s*hidden/);
});

test("empty chat keeps the command dock compact and composer-first", () => {
  const welcome = read("src/components/chat/WelcomePage.tsx");
  const welcomeLayout = read("src/components/chat/welcomeLayout.ts");

  assert.match(welcome, /welcome-chat-start/);
  assert.match(welcome, /data-chat-start-surface/);
  assert.match(welcome, /data-chat-start-header/);
  assert.match(welcome, /data-chat-quick-actions/);
  assert.doesNotMatch(welcome, /data-composer-command-dock/);
  assert.doesNotMatch(welcome, /data-composer-selection-summary/);
  assert.doesNotMatch(welcome, /workbench\.commandDock/);
  assert.doesNotMatch(welcome, /workbench\.commandDockHint/);
  assert.doesNotMatch(welcome, /welcome-workbench-cockpit/);
  assert.doesNotMatch(welcome, /WorkbenchQueueList/);
  assert.doesNotMatch(welcome, /workbenchSurface\.cockpit/);
  assert.doesNotMatch(welcome, /flex-1 flex-col justify-center/);
  assert.doesNotMatch(welcome, /sm:grid-cols-3/);
  assert.doesNotMatch(welcome, /workbench\.slashSkillsHint/);
  assert.doesNotMatch(welcome, /workbench\.slashMcpHint/);
  assert.doesNotMatch(welcome, /workbench\.slashContextHint/);
  assert.doesNotMatch(welcome, /welcome-card-shimmer/);
  assert.doesNotMatch(welcome, /rounded-2xl/);
  assert.doesNotMatch(welcomeLayout, /rounded-2xl/);
  assert.match(welcomeLayout, /rounded-lg/);
});

test("expanded app sidebar keeps governed catalogs as first-level smoke targets", () => {
  const sidebar = read(
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  );
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");

  assert.match(sidebar, /data-workbench-nav-item=\{key\}/);
  assert.match(sidebar, /key: "skills"[\s\S]*navigate\("\/skills"\)/);
  assert.match(sidebar, /key: "marketplace"[\s\S]*navigate\("\/marketplace"\)/);
  assert.match(sidebar, /key: "roles"[\s\S]*navigate\("\/roles"\)/);
  assert.doesNotMatch(sidebar, /data-workbench-nav-item="admin-skills"/);
  assert.doesNotMatch(sidebar, /data-workbench-nav-item="admin-roles"/);
  assert.match(rail, /data-workbench-rail-item="skills"/);
  assert.match(rail, /data-workbench-rail-item="marketplace"/);
  assert.match(rail, /data-workbench-rail-item="roles"/);
});
