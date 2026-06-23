import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();

test("route-level forbidden state keeps the workbench shell without loading gated panels", () => {
  const tabContent = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );
  const appContent = readFileSync(
    join(root, "src/components/layout/AppContent/index.tsx"),
    "utf8",
  );
  const nonChat = readFileSync(
    join(root, "src/components/layout/AppContent/NonChatAppContent.tsx"),
    "utf8",
  );
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const routeUnavailableBranch =
    tabContent.match(
      /if \(routeUnavailable\) \{[\s\S]*?\n {2}\}\n\n {2}const Panel = panelMap/,
    )?.[0] ?? "";

  assert.match(tabContent, /if \(routeUnavailable\)/);
  assert.match(tabContent, /data-frontend-governance-state=\{routeUnavailable\.state\}/);
  assert.match(tabContent, /<GovernedRouteWorkbench/);
  assert.match(tabContent, /config=\{routeUnavailable\}/);
  assert.doesNotMatch(routeUnavailableBranch, /items-center justify-center/);
  assert.match(appContent, /routeUnavailable=\{routeUnavailable\}/);
  assert.match(nonChat, /routeUnavailable=\{routeUnavailable\}/);
  assert.match(app, /fallbackComponent=\{/);
  assert.match(app, /<WorkbenchForbiddenPage/);
  assert.doesNotMatch(app, /redirectTo="\/chat"/);
});
