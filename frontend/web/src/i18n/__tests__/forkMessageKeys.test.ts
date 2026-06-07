import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const frontendSrc = resolve(currentDir, "../..");

const localeFiles = ["en", "zh", "ja", "ko", "ru"].map((locale) =>
  resolve(frontendSrc, "i18n", "locales", `${locale}.json`),
);

function readJson(path: string) {
  return JSON.parse(readFileSync(path, "utf8"));
}

test("message fork strings are available in every locale", () => {
  for (const localeFile of localeFiles) {
    const locale = readJson(localeFile);
    assert.equal(typeof locale.chat.message.fork, "string");
    assert.equal(typeof locale.chat.message.forkSuccess, "string");
    assert.equal(typeof locale.chat.message.forkFailed, "string");
  }
});

test("fork message components use i18n keys instead of inline English text", () => {
  const files = [
    resolve(
      frontendSrc,
      "components",
      "chat",
      "ChatMessage",
      "UserMessageBubble.tsx",
    ),
    resolve(frontendSrc, "components", "chat", "ChatMessage", "index.tsx"),
    resolve(frontendSrc, "components", "layout", "AppContent", "ChatView.tsx"),
  ];

  for (const file of files) {
    const source = readFileSync(file, "utf8");
    assert.equal(source.includes('title="Fork"'), false);
    assert.equal(source.includes('"Forked to new session"'), false);
    assert.equal(source.includes('"Fork failed"'), false);
  }
});
