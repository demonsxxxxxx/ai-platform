import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), "utf8");

test("AppShell and Chat keep one scroll owner for each transcript state", () => {
  const shell = read("src/components/layout/AppContent/AppShell.tsx");
  const chat = read("src/components/layout/AppContent/ChatAppContent.tsx");
  const chatView = read("src/components/layout/AppContent/ChatView.tsx");
  const skills = read("src/components/panels/SkillsHubPanel.tsx");
  const list = read("src/components/panels/SkillsPanel/SkillsList.tsx");

  assert.match(
    shell,
    /relative z-0 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden/,
  );
  assert.match(chat, /min-h-0 flex-1 overflow-hidden[^"]*flex flex-col/);
  assert.doesNotMatch(chat, /min-h-0 flex-1 overflow-y-auto[^"]*flex flex-col/);
  assert.match(
    chatView,
    /messages\.length > 0 \? "overflow-hidden" : ""/,
  );
  assert.match(chatView, /overflow-y-auto[^"]*px-4 py-3 sm:px-5/);
  assert.match(chatView, /data-agent-chat-opening/);
  assert.match(chatView, /<Virtuoso/);
  assert.equal((skills.match(/overflow-y-auto/g) ?? []).length, 1);
  assert.match(skills, /data-primary-page-scroller/);
  assert.doesNotMatch(list, /workbenchSurface\.catalog\.content/);
});

test("skills, market, detail, workspace, and builder share responsive outer gutters", () => {
  const skills = read("src/components/panels/SkillsHubPanel.tsx");
  const market = read("src/features/agent-market/AgentMarketRoute.tsx");
  const workspace = read("src/features/agent-market/AgentWorkspaceRoute.tsx");
  const builder = read("src/features/agent-builder/AgentBuilderWorkbench.tsx");

  assert.match(skills, /px-4[^"]*sm:px-6/);
  assert.match(market, /max-w-\[86rem\][^\n"]*px-4[^\n"]*sm:px-6/);
  assert.match(market, /max-w-4xl[^\n"]*px-4[^\n"]*sm:px-6/);
  assert.match(workspace, /px-4[^\n"]*sm:px-6/);
  assert.match(builder, /px-4[^\n"]*sm:px-6/);
  assert.match(builder, /max-w-6xl[^\n"]*px-4[^\n"]*sm:px-6/);
  assert.match(builder, /lg:grid-cols-\[20rem_minmax\(0,1fr\)\]/);
});

test("all protected routes keep a bounded shell entry", () => {
  const manifest = read("src/appRouteManifest.ts");
  const app = read("src/App.tsx");
  for (const route of [
    "chat",
    "agentBuilder",
    "agentMarket",
    "agentMarketDetail",
    "agentMarketWorkspace",
    "apps",
    "skills",
    "mcp",
    "users",
    "roles",
    "settings",
    "feedback",
    "models",
    "notifications",
    "memory",
  ]) {
    assert.match(manifest, new RegExp(`${route}:`), `${route} must remain in the manifest`);
  }
  assert.match(app, /<ProtectedRoute>/);
  assert.doesNotMatch(app, /overflow-x-auto[^\n]*<ProtectedRoute>/);
});
