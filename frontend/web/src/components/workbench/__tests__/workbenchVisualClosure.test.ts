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

test("workbench right context uses the same canvas as the main workspace", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const rightPanel = read("src/components/workbench/WorkbenchRightPanel.tsx");

  assert.match(surface, /context:[\s\S]*bg-\[var\(--theme-bg\)\]/);
  assert.match(rightPanel, /bg-\[var\(--theme-bg\)\]/);
  assert.match(rightPanel, /workbenchSurface\.secondaryPanel/);
});

test("post-login projection panels share workbench surface tokens", () => {
  const panels = new Map([
    ["AgentDirectoryPanel", read("src/components/panels/AgentDirectoryPanel.tsx")],
    ["ModelCatalogPanel", read("src/components/panels/ModelCatalogPanel.tsx")],
    ["MemoryPanel", read("src/components/panels/MemoryPanel/index.tsx")],
  ]);

  for (const [name, source] of panels) {
    assert.match(source, /data-frontend-governance-state/, name);
    assert.match(source, /bg-\[var\(--theme-bg\)\]/, name);
    assert.match(source, /workbenchSurface\.(?:compactPanel|panel)/, name);
    assert.doesNotMatch(source, /bg-white(?:\/\d+)?/, name);
    assert.doesNotMatch(source, /dark:bg-stone-950(?:\/\d+)?/, name);
    assert.doesNotMatch(source, /text-stone-(?:700|800|900)/, name);
  }
});

test("skills marketplace cards stay dense and enterprise-workbench sized", () => {
  const baseCard = read("src/components/common/SkillBaseCard.tsx");
  const cardCss = read("src/styles/card-base.css");
  const utilities = read("src/styles/utilities.css");

  assert.match(baseCard, /p-3\.5 sm:p-4/);
  assert.match(baseCard, /text-sm font-semibold/);
  assert.doesNotMatch(baseCard, /text-base font-semibold/);
  assert.match(cardCss, /border-radius:\s*0\.5rem/);
  assert.doesNotMatch(cardCss, /translateY\(-4px\)/);
  assert.match(utilities, /minmax\(260px,\s*1fr\)/);
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

  assert.match(hub, /resolveSkillsHubGovernance/);
  assert.match(hub, /data-required-permission=\{hubGovernance\.requiredPermission\}/);
  assert.match(resolver, /requiredPermission: "skill:read" \| "marketplace:read"/);
  assert.match(resolver, /marketplace:read/);
  assert.match(resolver, /skill:read/);
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
  assert.match(welcome, /data-composer-command-dock/);
  assert.match(welcome, /data-composer-selection-summary/);
  assert.match(welcome, /workbench\.commandDock/);
  assert.match(welcome, /workbench\.commandDockHint/);
  assert.doesNotMatch(welcome, /welcome-workbench-cockpit/);
  assert.doesNotMatch(welcome, /WorkbenchQueueList/);
  assert.doesNotMatch(welcome, /workbenchSurface\.cockpit/);
  assert.doesNotMatch(welcome, /sm:grid-cols-3/);
  assert.doesNotMatch(welcome, /workbench\.slashSkillsHint/);
  assert.doesNotMatch(welcome, /workbench\.slashMcpHint/);
  assert.doesNotMatch(welcome, /workbench\.slashContextHint/);
  assert.doesNotMatch(welcome, /welcome-card-shimmer/);
  assert.doesNotMatch(welcome, /rounded-2xl/);
  assert.doesNotMatch(welcomeLayout, /rounded-2xl/);
  assert.match(welcomeLayout, /rounded-lg/);
});
