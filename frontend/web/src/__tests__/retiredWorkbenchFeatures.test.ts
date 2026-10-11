import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), "utf8");

function productionFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === "__tests__") return [];
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return productionFiles(path);
    return /\.[jt]sx?$/.test(entry.name) && !/\.(test|spec)\./.test(entry.name) ? [path] : [];
  });
}

test("unimplemented feedback and announcements have no production clients or entrypoints", () => {
  for (const path of [
    "src/components/notification/NotificationDialog.tsx",
    "src/components/notification/notificationChineseCopy.ts",
    "src/components/notification/__tests__/notificationChineseCopy.test.ts",
    "src/services/api/notificationPublic.ts",
    "src/services/api/notification.ts",
    "src/services/api/feedback.ts",
    "src/types/notification.ts",
    "src/types/feedback.ts",
  ]) {
    assert.equal(existsSync(join(root, path)), false, path);
  }

  const sources = productionFiles(join(root, "src"));
  for (const path of sources) {
    const source = readFileSync(path, "utf8");
    assert.doesNotMatch(source, /\/api\/(?:feedback|notifications)(?:\/|["'`])/, path);
    assert.doesNotMatch(source, /notificationPublicApi|NotificationDialog|resolveChineseNotificationText/, path);
    assert.doesNotMatch(source, /Workbench(?:Feedback|Notifications)ProjectionPanel|WorkbenchFeedback(?:Stats|Item|ListResponse)|WorkbenchNotification/, path);
    assert.doesNotMatch(source, /workbench-(?:feedback|notifications)-projection|listFeedback|listActiveNotifications|listAdminNotifications/, path);
    assert.doesNotMatch(source, /nav(?:Desc)?\.(?:feedback|notifications)|workbench\.projections\.(?:feedback|notifications)/, path);
  }

  // The remaining route names are compatibility destinations, not navigation items.
  for (const path of [
    "src/components/layout/AppContent/WorkbenchMenuHost.tsx",
    "src/components/layout/UserMenu.tsx",
    "src/components/panels/SidebarParts/SidebarRail.tsx",
    "src/components/panels/SidebarParts/SessionListContent.tsx",
    "src/components/chat/ChatInputShortcuts.tsx",
    "src/components/chat/AgentOptionButton.tsx",
  ]) {
    assert.doesNotMatch(read(path), /feedback|notifications|notifDialog|activeNotifCount/i, path);
  }
  assert.doesNotMatch(read("src/hooks/useAgent.ts"), /feedback/i);
  assert.doesNotMatch(read("src/types/message.ts"), /feedbackId|feedback\?:/);
});

test("retained deep-links keep the existing App guards and static unavailable panel", () => {
  const app = read("src/App.tsx");
  assert.match(app, /path=\{APP_ROUTE_PATHS\.feedback\}[\s\S]*?<ProtectedRoute requireAdmin redirectTo=\{APP_ROUTE_PATHS\.agentMarket\}>\s*<FeedbackPage \/>/);
  assert.match(app, /path=\{APP_ROUTE_PATHS\.notifications\}[\s\S]*?<ProtectedRoute>\s*<NotificationsPage \/>/);
  const tab = read("src/components/layout/AppContent/TabContent.tsx");
  assert.match(tab, /activeTab === "feedback" \|\| activeTab === "notifications"/);
  assert.match(tab, /<WorkbenchUnavailablePanel feature=\{activeTab\} \/>/);
  const unavailable = read("src/components/workbench/WorkbenchUnavailablePanel.tsx");
  assert.match(unavailable, /data-workbench-unavailable=\{feature\}/);
  assert.doesNotMatch(unavailable, /authFetch|fetch\(|useEffect|setInterval|onClick|ProjectionMetric|stats|degraded|retry/i);
});

test("task completion notifications and user diagnostics retain their owners", () => {
  const chat = read("src/components/layout/AppContent/ChatAppContent.tsx");
  assert.match(chat, /useWebSocketNotifications\(\{/);
  const taskNotifications = read("src/components/layout/AppContent/useWebSocketNotifications.tsx");
  assert.match(taskNotifications, /onTaskComplete:/);
  assert.match(taskNotifications, /notify\(notificationCopy\.title/);
  const users = read("src/components/workbench/WorkbenchProjectionPages.tsx");
  assert.match(users, /adminUsersApi\.list/);
  assert.match(users, /adminUsersApi\.diagnostics/);
  assert.match(users, /AdminUserDiagnosticsDrawer/);
});

test("browser smoke fixtures no longer conceal announcement API calls", () => {
  for (const path of [
    "scripts/company-rbac-browser-smoke.mjs",
    "scripts/route-layout-browser-smoke.mjs",
  ]) {
    assert.doesNotMatch(read(path), /pathname\.includes\(["']notification["']\)/);
  }
});
