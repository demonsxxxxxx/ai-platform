import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(relativePath: string): string {
  return readFileSync(join(root, "src", relativePath), "utf8");
}

test("Agent chat workspace has a separate history panel and keeps one session source", () => {
  const panel = read("features/agent-market/AgentConversationPanel.tsx");
  const chat = read("components/layout/AppContent/ChatAppContent.tsx");
  const shell = read("components/layout/AppContent/AppShell.tsx");
  const sidebar = read("components/panels/SidebarParts/SessionListContent.tsx");
  const rail = read("components/panels/SidebarParts/SidebarRail.tsx");
  const header = read("components/layout/AppContent/Header.tsx");
  const userMenu = read("components/layout/UserMenu.tsx");

  assert.match(panel, /data-agent-conversation-panel/);
  assert.match(panel, /新建会话/);
  assert.doesNotMatch(panel, /搜索历史会话|历史会话<\/div>/);
  assert.match(panel, /<\/header>\s*<div className="px-4 pb-3 pt-2">\s*<button/);
  assert.doesNotMatch(sidebar, /data-agent-workspace-identity/);
  assert.match(sidebar, /mt-auto flex items-center justify-between[\s\S]*<UserMenu showLabel \/>[\s\S]*<Settings/);
  assert.match(rail, /<UserMenu \/>[\s\S]*workbench-menu-open/);
  assert.match(sidebar, /workbench-menu-open/);
  assert.match(header, /data-workbench-menu-host/);
  assert.doesNotMatch(header, /data-workbench-header/);
  assert.match(header, /workbench-menu-open/);
  assert.match(header, /showUserMenu \|\| mobileMenuOpen/);
  assert.match(header, /showUserMenu \? "border-l border-\[var\(--theme-border\)\] pl-2 sm:pl-3"/);
  assert.match(header, /showUserMenu \? <UserMenu \/> : null/);
  assert.match(userMenu, /showLabel \? rect\.top - \(menuRef\.current\?\.offsetHeight \|\| 120\) - 8/);
  assert.match(panel, /source\.sessions/);
  assert.match(panel, /source\.loadMore/);
  assert.match(panel, /source\.removeSession/);
  assert.match(panel, /<SessionItem/);
  assert.match(chat, /contentSidebar=\{[\s\S]*?<AgentConversationPanel/);
  assert.match(chat, /source=\{agentWorkspaceSessionSource\}/);
  assert.match(shell, /showHeaderUserMenu = false/);
  assert.doesNotMatch(chat, /showHeaderUserMenu=/);
  assert.match(shell, /contentSidebar\?: ReactNode/);
  assert.match(shell, /\{contentSidebar\}/);
});

test("Agent chat keeps new conversation and history in the navigation below the three-column breakpoint", () => {
  const sessionSidebar = read("components/panels/SidebarParts/SessionListContent.tsx");
  const panel = read("features/agent-market/AgentConversationPanel.tsx");

  assert.match(sessionSidebar, /agentHistoryInMainPanel \? "space-y-1 xl:hidden"/);
  assert.match(sessionSidebar, /agentWorkspace && agentHistoryInMainPanel \? "xl:hidden"/);
  assert.match(panel, /xl:flex[\s\S]*w-60/);
  assert.match(panel, /收起历史会话/);
  assert.match(panel, /展开历史会话/);
});
