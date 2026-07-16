import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (relativePath: string) =>
  readFileSync(join(root, "src", relativePath), "utf8");

test("account metadata cannot replace the fixed Chinese UI language", () => {
  const auth = read("hooks/useAuth.tsx");

  assert.doesNotMatch(auth, /i18n\.changeLanguage/);
  assert.doesNotMatch(auth, /localStorage\.setItem\("language"/);
});

test("product language controls are absent from authentication, landing, workbench, and shared surfaces", () => {
  for (const relativePath of [
    "components/auth/AuthLayout.tsx",
    "components/auth/AuthPage.tsx",
    "components/auth/ForgotPassword.tsx",
    "components/auth/ResetPassword.tsx",
    "components/landing/components/Navbar.tsx",
    "components/layout/AppContent/Header.tsx",
    "components/share/SharedPage.tsx",
  ]) {
    const source = read(relativePath);
    assert.doesNotMatch(source, /LanguageToggle|changeLanguage|common\.language/);
  }
});

test("transport and notification client copy explicitly use Chinese", () => {
  const fetch = read("services/api/fetch.ts");
  const authApi = read("services/api/auth.ts");
  const notifications = read(
    "components/layout/AppContent/useWebSocketNotifications.tsx",
  );
  const projections = read("components/workbench/WorkbenchProjectionPages.tsx");

  assert.match(fetch, /headers\.set\("Accept-Language", "zh-CN"\)/);
  assert.match(fetch, /finalHeaders\.set\("Accept-Language", "zh-CN"\)/);
  assert.match(authApi, /"Accept-Language": "zh-CN"/);
  assert.match(notifications, /i18n\.getFixedT\("zh"\)/);
  assert.match(projections, /resolveChineseNotificationText/);
  assert.doesNotMatch(projections, /language\.startsWith/);
});

test("active notification surfaces use the Chinese-only projection helper", () => {
  const dialog = read("components/notification/NotificationDialog.tsx");
  const projections = read("components/workbench/WorkbenchProjectionPages.tsx");

  for (const source of [dialog, projections]) {
    assert.match(source, /resolveChineseNotificationText/);
    assert.doesNotMatch(source, /title_i18n\.en|content_i18n\.en/);
  }
});

test("empty projections and toast dismissal use Chinese copy", () => {
  const zh = JSON.parse(read("i18n/locales/zh.json"));
  const empty = zh.workbench.projections.empty;
  const projections = read("components/workbench/WorkbenchProjectionPages.tsx");
  const notifications = read(
    "components/layout/AppContent/useWebSocketNotifications.tsx",
  );

  assert.equal(empty.noRowsTitle, "未返回可展示的数据");
  assert.equal(empty.nextActionTitle, "下一步操作");
  assert.equal(
    empty.nextActionDescription,
    "如需创建或修改记录，请使用受管理员治理的流程。",
  );
  assert.doesNotMatch(
    projections,
    /"Safe projection"|"No rows returned"|"Next action"/,
  );
  assert.match(notifications, /aria-label=\{t\("common\.close"\)\}/);
});

test("common visible preview controls use Chinese translations instead of inline English", () => {
  for (const relativePath of [
    "components/skill/BinaryFilePreview.tsx",
    "components/chat/ChatMessage/items/ToolResultPanel.tsx",
    "components/documents/previews/ExcalidrawPreview.tsx",
    "components/documents/previews/PptPreview.tsx",
  ]) {
    const source = read(relativePath);
    assert.doesNotMatch(source, />Download</);
    assert.doesNotMatch(source, />Fit</);
    assert.doesNotMatch(source, />No content</);
    assert.doesNotMatch(source, /aria-label="Close"/);
  }
});

test("chat help control uses the Chinese help-document translation", () => {
  const helpMenu = read("components/chat/ChatInputHelpMenu.tsx");

  assert.match(helpMenu, /aria-label=\{t\("chat\.helpDocs"\)\}/);
  assert.match(helpMenu, /t\("chat\.helpDocs", "帮助文档"\)/);
  assert.doesNotMatch(helpMenu, /aria-label="Help"/);
  assert.doesNotMatch(helpMenu, /AI Platform documentation/);
});
