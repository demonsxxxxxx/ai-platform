import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, "../App.tsx"), "utf8");

function extractFunctionBody(name: string): string {
  const start = appSource.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} should exist`);

  const firstBrace = appSource.indexOf("{", start);
  assert.notEqual(firstBrace, -1, `${name} should have a body`);

  let depth = 0;
  for (let index = firstBrace; index < appSource.length; index += 1) {
    const char = appSource[index];
    if (char === "{") depth += 1;
    if (char === "}") depth -= 1;
    if (depth === 0) {
      return appSource.slice(firstBrace + 1, index);
    }
  }

  throw new Error(`${name} body is unterminated`);
}

test("keeps chat page title updates isolated from the chat UI tree", () => {
  const chatPageBody = extractFunctionBody("ChatPage");

  assert.match(appSource, /function ChatPageSEO\(/);
  assert.match(chatPageBody, /<ChatPageSEO \/>/);
  assert.match(chatPageBody, /<AppContent key="chat" activeTab="chat" \/>/);
  assert.doesNotMatch(chatPageBody, /useState<.*sessionName|setSessionName/);
  assert.doesNotMatch(
    chatPageBody,
    /listenSessionTitleUpdated|sessionApi\.get/,
  );
});
