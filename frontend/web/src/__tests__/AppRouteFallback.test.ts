import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { APP_ROUTE_PATHS } from "../appRouteManifest.ts";

const src = resolve(import.meta.dirname, "..");
const read = (relativePath: string) =>
  readFileSync(resolve(src, relativePath), "utf8");

const readApp = () => {
  let app = read("App.tsx");
  for (const [id, path] of Object.entries(APP_ROUTE_PATHS)) {
    app = app.replaceAll(`path={APP_ROUTE_PATHS.${id}}`, `path="${path}"`);
  }
  return app;
};

test("admin routes redirect ordinary users before mounting management pages", () => {
  const app = readApp();
  const routes = ["users", "roles", "settings", "feedback", "models"];
  for (const route of routes) {
    assert.match(
      app,
      new RegExp(
        `path="/${route}"[\\s\\S]{0,220}<ProtectedRoute[\\s\\S]{0,120}requireAdmin[\\s\\S]{0,120}redirectTo="/chat"`,
      ),
      route,
    );
  }
});

test("requireAdmin uses the signed is_admin projection instead of permission inference", () => {
  const protectedRoute = read("components/auth/ProtectedRoute.tsx");
  assert.match(protectedRoute, /canAccessWorkbenchPath/);
  assert.match(protectedRoute, /const \{[\s\S]*\buser\b[\s\S]*\} =\s*useAuth\(\)/);
  assert.match(protectedRoute, /if \(requireAdmin && user\?\.is_admin !== true\)/);
  assert.doesNotMatch(protectedRoute, /adminPermissions/);
});

test("all workbench navigation renderers reuse the central access policy", () => {
  for (const relativePath of [
    "components/panels/SessionSidebar.tsx",
    "components/panels/SidebarParts/SidebarRail.tsx",
    "components/panels/SidebarParts/SessionListContent.tsx",
  ]) {
    const source = read(relativePath);
    assert.match(source, /workbenchAccessPolicy/, relativePath);
    assert.match(source, /canAccessWorkbenchItem/, relativePath);
  }
});

test("account menu has no duplicate workbench navigation or profile entry", () => {
  const userMenu = read("components/layout/UserMenu.tsx");

  assert.match(userMenu, /data-user-menu-identity/);
  assert.match(userMenu, /auth\.logout/);
  assert.doesNotMatch(userMenu, /useNavigate|navItems|onShowProfile/);
  assert.doesNotMatch(userMenu, /["'`]\/(?:chat|skills|mcp)["'`]/);
});

test("legacy profile source is absent from the authenticated workbench", () => {
  assert.equal(
    existsSync(resolve(src, "components/profile/ProfileModal.tsx")),
    false,
  );
  assert.doesNotMatch(read("components/layout/AppContent/AppShell.tsx"), /ProfileModal/);
  assert.doesNotMatch(read("components/layout/AppContent/index.tsx"), /showProfileModal/);
});

test("no-preference and fallback language are Chinese while explicit preference stays first", () => {
  const i18n = read("i18n/index.ts");
  assert.match(i18n, /localStorage\.getItem\("language"\)/);
  assert.doesNotMatch(i18n, /navigator\.language/);
  assert.match(i18n, /typeof window === "undefined"[\s\S]{0,80}return "zh"/);
  assert.match(i18n, /No preference defaults to Chinese[\s\S]{0,80}return "zh"/);
  assert.match(i18n, /fallbackLng:\s*"zh"/);
});
