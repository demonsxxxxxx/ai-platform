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
    assert.doesNotMatch(source, /LanguageToggle|LanguageToggle|changeLanguage|common\.language/);
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
  assert.match(projections, /return value\.zh \|\| value\.en/);
  assert.doesNotMatch(projections, /language\.startsWith/);
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
