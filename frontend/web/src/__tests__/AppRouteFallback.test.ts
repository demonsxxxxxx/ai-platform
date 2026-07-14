import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
    "components/layout/UserMenu.tsx",
  ]) {
    const source = read(relativePath);
    assert.match(source, /workbenchAccessPolicy/, relativePath);
    assert.match(source, /canAccessWorkbenchItem/, relativePath);
  }
});

test("profile renders only canonical role labels through existing locale keys", () => {
  const profile = read("components/profile/tabs/ProfileInfoTab.tsx");
  assert.match(profile, /getCanonicalCompanyRoleCode\(user\)/);
  assert.match(profile, /workbench\.governance\.roleLabels\./);
  assert.doesNotMatch(profile, /\{role\}\s*<\/span>/);
});

test("no-preference and fallback language are Chinese while explicit preference stays first", () => {
  const i18n = read("i18n/index.ts");
  assert.match(i18n, /localStorage\.getItem\("language"\)/);
  assert.doesNotMatch(i18n, /navigator\.language/);
  assert.match(i18n, /typeof window === "undefined"[\s\S]{0,80}return "zh"/);
  assert.match(i18n, /No preference defaults to Chinese[\s\S]{0,80}return "zh"/);
  assert.match(i18n, /fallbackLng:\s*"zh"/);
});
