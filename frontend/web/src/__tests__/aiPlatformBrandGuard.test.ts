import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

const activeFiles = [
  "index.html",
  "public/manifest.json",
  "public/offline.html",
  "public/robots.txt",
  "public/sitemap.xml",
  "src/constants/index.ts",
  "src/i18n/locales/en.json",
  "src/i18n/locales/zh.json",
  "src/i18n/locales/ja.json",
  "src/i18n/locales/ko.json",
  "src/i18n/locales/ru.json",
  "src/sw.ts",
  "src/pwa.ts",
  "src/pwaGuards.ts",
  "src/hooks/useBrowserNotification.ts",
  "src/hooks/useSessionConfig.ts",
  "src/utils/sessionTitleEvents.ts",
  "src/components/common/selectionActionPrompt.ts",
  "src/components/persona/usePersonaPlaza.ts",
  "src/components/profile/ProfileModal.tsx",
  "src/components/chat/ChatInputHelpMenu.tsx",
  "src/components/chat/WelcomePage.tsx",
  "src/components/share/SharedPage.tsx",
  "src/components/auth/AuthPage.tsx",
  "src/components/auth/AuthLayout.tsx",
  "src/components/auth/ForgotPassword.tsx",
  "src/components/auth/ResetPassword.tsx",
  "src/components/pages/ChannelsPage.tsx",
  "src/components/sidebar/RecentChatsDialog.tsx",
  "src/components/panels/SidebarParts/SidebarRail.tsx",
  "src/components/panels/SidebarParts/SessionListContent.tsx",
];

const bannedPatterns = [
  /\bLambChat\b/,
  /lambchat\.com/i,
  /github\.com\/(?:clivia|Yanyutin753)\/LambChat/i,
  /yanyutin753\.github\.io\/LambChat/i,
  /\bClivia\b/,
];

test("active frontend no longer exposes LambChat brand authority", () => {
  const offenders: string[] = [];

  for (const file of activeFiles) {
    const source = readFileSync(join(root, file), "utf8");
    for (const pattern of bannedPatterns) {
      if (pattern.test(source)) offenders.push(`${file} -> ${pattern}`);
    }
  }

  assert.deepEqual(offenders, []);
});

test("ai-platform product constants are the active brand source", () => {
  const constants = readFileSync(join(root, "src/constants/index.ts"), "utf8");
  assert.match(constants, /export const APP_NAME = "AI Platform"/);
  assert.match(
    constants,
    /export const APP_HOME_URL = "http:\/\/10\.56\.0\.211:18001\/"/,
  );
});

test("brand entry surfaces consume the ai-platform home authority", () => {
  const entryFiles = [
    "src/components/auth/AuthPage.tsx",
    "src/components/share/SharedPage.tsx",
    "src/components/panels/SidebarParts/SessionListContent.tsx",
  ];

  const offenders = entryFiles.filter((file) => {
    const source = readFileSync(join(root, file), "utf8");
    return !source.includes("APP_HOME_URL");
  });

  assert.deepEqual(offenders, []);
});
