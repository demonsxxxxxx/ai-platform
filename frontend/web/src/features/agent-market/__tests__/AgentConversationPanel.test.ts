import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(relativePath: string): string {
  return readFileSync(join(root, "src", relativePath), "utf8");
}

test("Agent chat keeps both history scopes inside one primary sidebar", () => {
  const chat = read("components/layout/AppContent/ChatAppContent.tsx");
  const shell = read("components/layout/AppContent/AppShell.tsx");
  const sidebar = read("components/panels/SidebarParts/SessionListContent.tsx");
  const rail = read("components/panels/SidebarParts/SidebarRail.tsx");
  const menuHost = read("components/layout/AppContent/WorkbenchMenuHost.tsx");
  const userMenu = read("components/layout/UserMenu.tsx");

  // One primary sidebar owns navigation and both history projections; no
  // adjacent Agent panel takes thread width.
  assert.doesNotMatch(shell, /contentSidebar/);
  assert.doesNotMatch(`${chat}\n${shell}`, /<AgentConversationPanel/);
  assert.match(sidebar, /data-history-scope-switch/);
  assert.match(sidebar, /data-workbench-task-history/);
  assert.match(sidebar, /data-workbench-global-history/);

  // Scope selection never reclassifies a record: each projection keeps its own
  // server source and its own selection owner.
  assert.match(chat, /sessionSource=\{agentWorkspaceSessionSource\}/);
  assert.match(chat, /buildSessionHistoryPath\(session\.id, session\.agent_conversation\)/);
  assert.match(chat, /onSelectGlobalHistorySession=\{[\s\S]*handleSelectGlobalHistorySession/);

  assert.match(sidebar, /mt-auto flex items-center justify-between[\s\S]*<UserMenu showLabel \/>[\s\S]*<Settings/);
  assert.match(rail, /<UserMenu \/>[\s\S]*workbench-menu-open/);
  assert.match(sidebar, /workbench-menu-open/);
  assert.match(rail, /bottom: window\.innerHeight - rect\.top \+ 8/);
  assert.match(rail, /left: rect\.right \+ 8/);
  assert.doesNotMatch(rail, /right: window\.innerWidth - rect\.right/);
  assert.match(sidebar, /bottom: window\.innerHeight - rect\.top \+ 8/);
  assert.match(sidebar, /right: window\.innerWidth - rect\.right/);
  assert.match(menuHost, /<DropdownMenuPortal>/);
  assert.match(menuHost, /<DropdownMenuPositioner anchor=\{anchor\}/);
  assert.match(menuHost, /menuPosition\.left \?\? window\.innerWidth/);
  assert.match(menuHost, /window\.innerHeight - menuPosition\.bottom/);
  assert.doesNotMatch(`${rail}\n${sidebar}`, /rect\.top - 220/);
  assert.match(menuHost, /workbench-menu-open/);
  assert.doesNotMatch(menuHost, /data-workbench-header|data-workbench-menu-host/);
  assert.match(userMenu, /<DropdownMenu[\s\S]*placement=\{showLabel \? "topLeft" : "rightBottom"\}/);
  assert.match(userMenu, /<Drawer[\s\S]*placement="bottom"/);
  assert.doesNotMatch(userMenu, /rect\.bottom \+ 8|window\.innerWidth - rect\.right/);
  assert.match(shell, /<WorkbenchMenuHost/);
  assert.doesNotMatch(shell, /<Header|showHeaderUserMenu/);
});

test("the retired adjacent Agent history panel has no production importer", () => {
  const retired = "features/agent-market/AgentConversationPanel.tsx";
  const productionSources = [
    "components/layout/AppContent/ChatAppContent.tsx",
    "components/layout/AppContent/AppShell.tsx",
    "components/panels/SessionSidebar.tsx",
    "components/panels/SidebarParts/SessionListContent.tsx",
  ];
  const importers = productionSources.filter((file) =>
    /AgentConversationPanel|contentSidebar|agentHistoryInMainPanel/.test(read(file)),
  );

  assert.deepEqual(importers, [], `${retired} threading must stay unreferenced`);
  // Implementation is retained on disk until an explicit delete is approved.
  assert.match(read(retired), /data-agent-conversation-panel/);
});
