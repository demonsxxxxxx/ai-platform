import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { APP_ROUTE_PATHS } from "../appRouteManifest.ts";

const root = process.cwd();

function readSource(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

function readApp(): string {
  let app = readSource("src/App.tsx");
  for (const [id, path] of Object.entries(APP_ROUTE_PATHS)) {
    app = app.replaceAll(`path={APP_ROUTE_PATHS.${id}}`, `path="${path}"`);
  }
  return app;
}

test("authenticated sidebar treats skills as admin skill management and removes role plaza plus marketplace shortcuts", () => {
  const sidebarRail = readSource(
    "src/components/panels/SidebarParts/SidebarRail.tsx",
  );
  const navigationState = readSource(
    "src/components/panels/SidebarParts/navigationState.ts",
  );
  const sidebar = [
    sidebarRail,
    navigationState,
    readSource("src/components/panels/SessionSidebar.tsx"),
  ].join("\n");

  assert.match(sidebarRail, /onOpenSkills/);
  assert.match(sidebarRail, /nav\.skillManagement/);
  assert.match(navigationState, /\|\s*"skills"/);
  assert.match(navigationState, /\/skills/);

  assert.doesNotMatch(sidebar, /onOpenMarketplace/);
  assert.doesNotMatch(sidebar, /onOpenRoles/);
  assert.doesNotMatch(sidebar, /navigate\("\/marketplace"\)/);
  assert.doesNotMatch(sidebar, /navigate\("\/roles"\)/);
  assert.doesNotMatch(navigationState, /\|\s*"marketplace"/);
  assert.doesNotMatch(navigationState, /\|\s*"roles"/);
});

test("marketplace route is folded into admin skill management", () => {
  const app = readApp();
  const tabContent = readSource("src/components/layout/AppContent/TabContent.tsx");
  const skillsHub = readSource("src/components/panels/SkillsHubPanel.tsx");
  const state = readSource("src/components/panels/SkillsHubPanel/state.ts");

  assert.match(app, /path="\/marketplace"[\s\S]*?<Navigate to="\/skills" replace \/>/);
  assert.match(tabContent, /skills:\s*SkillsHubPanel/);
  assert.doesNotMatch(tabContent, /marketplace:\s*SkillsHubPanel/);
  assert.doesNotMatch(skillsHub, /location\.pathname === "\/marketplace"/);
  assert.doesNotMatch(state, /marketplace:read/);
  assert.match(state, /skill:admin/);
  assert.match(state, /marketplace:admin/);
});

test("company navigation owns legacy webUI links without iframe embedding", () => {
  const catalog = readSource("src/components/launchpad/catalog.ts");
  const panel = readSource("src/components/launchpad/LaunchpadPanel.tsx");
  const zh = readSource("src/i18n/locales/zh.json");
  const en = readSource("src/i18n/locales/en.json");

  assert.match(catalog, /url:\s*"http:\/\/10\.56\.0\.25:8189\/#\/TaskManagement\/indexSpace\/"/);
  assert.match(panel, /data-company-navigation-shell/);
  assert.match(panel, /openUrl\(tab\.url\)/);
  assert.match(panel, /window\.open/);
  assert.doesNotMatch(catalog, /"icon":/);
  assert.doesNotMatch(catalog, /icon\?:/);
  assert.doesNotMatch(catalog, /systemKey/);
  assert.doesNotMatch(catalog, /VITE_LEGACY_WEBUI_FRAME_URL/);
  assert.doesNotMatch(catalog, /VITE_LEGACY_NONGMP_URL/);
  assert.doesNotMatch(catalog, /buildLegacySystemUrl/);
  assert.doesNotMatch(panel, /data-legacy-webui-frame/);
  assert.doesNotMatch(panel, /<iframe/);
  assert.doesNotMatch(panel, /sandbox=/);
  assert.doesNotMatch(panel, /allow="clipboard-read; clipboard-write"/);
  assert.match(zh, /"companyNavigation"/);
  assert.match(en, /"companyNavigation"/);
});
