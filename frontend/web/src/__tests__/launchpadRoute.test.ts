import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const appSource = readFileSync(resolve(import.meta.dirname, "../App.tsx"), "utf8");
const typesSource = readFileSync(
  resolve(import.meta.dirname, "../components/layout/AppContent/types.ts"),
  "utf8",
);
const tabSource = readFileSync(
  resolve(import.meta.dirname, "../components/layout/AppContent/TabContent.tsx"),
  "utf8",
);
const sidebarSource = readFileSync(
  resolve(import.meta.dirname, "../components/panels/SessionSidebar.tsx"),
  "utf8",
);
const sidebarPartsSource = [
  "../components/panels/SidebarParts/SessionListContent.tsx",
  "../components/panels/SidebarParts/SidebarRail.tsx",
]
  .map((path) => readFileSync(resolve(import.meta.dirname, path), "utf8"))
  .join("\n");
const authRedirectSource = readFileSync(
  resolve(
    import.meta.dirname,
    "../components/auth/authRedirectTransition.ts",
  ),
  "utf8",
);
const oauthCallbackSource = readFileSync(
  resolve(import.meta.dirname, "../components/auth/OAuthCallback.tsx"),
  "utf8",
);
const landingSource = readFileSync(
  resolve(import.meta.dirname, "../components/landing/LandingPage.tsx"),
  "utf8",
);

test("launchpad route is protected and mapped to AppContent", () => {
  assert.match(appSource, /path="\/apps"/);
  assert.match(appSource, /<LaunchpadPage \/>/);
  assert.match(appSource, /activeTab="apps"/);
});

test("launchpad tab is registered in layout and sidebar", () => {
  assert.match(typesSource, /\|\s*"apps"/);
  assert.match(tabSource, /apps:\s*LaunchpadPanel/);
  assert.match(`${sidebarSource}\n${sidebarPartsSource}`, /navigate\("\/apps"\)/);
  assert.match(sidebarPartsSource, /nav\.apps/);
});

test("chat workbench is the default authenticated landing destination", () => {
  assert.match(authRedirectSource, /return redirectPath \|\| "\/chat"/);
  assert.match(appSource, /navigate\(redirectPath \?\? "\/chat"/);
  assert.match(oauthCallbackSource, /getRedirectPath\(\) \|\| "\/chat"/);
  assert.match(landingSource, /navigate\("\/apps", \{ replace: true \}\)/);
});

test("root path routes by auth state instead of rendering the marketing landing page", () => {
  assert.doesNotMatch(appSource, /LandingPage/);
  assert.match(appSource, /function RootRedirect\(\)/);
  assert.match(appSource, /<Navigate to="\/chat" replace \/>/);
  assert.match(appSource, /<Navigate to="\/auth\/login" replace \/>/);
  assert.match(appSource, /path="\/"\s+element=\{<RootRedirect \/>}/);
});
