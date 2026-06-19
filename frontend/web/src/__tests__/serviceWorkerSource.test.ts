import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(resolve(import.meta.dirname, "../sw.ts"), "utf8");

test("service worker uses Workbox precaching with an injected Vite manifest", () => {
  assert.match(source, /precacheAndRoute\(self\.__WB_MANIFEST\)/);
  assert.match(source, /cleanupOutdatedCaches\(\)/);
});

test("service worker keeps dynamic AI Platform backends out of runtime caches", () => {
  assert.match(source, /getPwaRequestKind/);
  assert.doesNotMatch(source, /registerRoute\([^]*\/api/);
  assert.doesNotMatch(source, /registerRoute\([^]*text\/event-stream/);
});

test("service worker uses ai-platform cache and notification branding", () => {
  assert.match(source, /ai-platform-app-shell-v2/);
  assert.match(source, /ai-platform-static-v2/);
  assert.match(source, /AI Platform is offline\./);
  assert.match(source, /payload\.title \|\| "AI Platform"/);
  assert.match(source, /You have a new AI Platform update\./);
  assert.doesNotMatch(source, /LambChat/);
  assert.doesNotMatch(source, /lambchat/);
});
