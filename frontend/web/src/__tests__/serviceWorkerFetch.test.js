import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../sw.ts", import.meta.url), "utf8");

test("service worker keeps a local offline navigation fallback", () => {
  assert.match(source, /const OFFLINE_URL = "\/offline\.html"/);
  assert.match(source, /new NetworkFirst/);
  assert.match(source, /getOfflineFallback/);
});

test("service worker preserves push notification routing", () => {
  assert.match(source, /addEventListener\("push"/);
  assert.match(source, /showNotification/);
  assert.match(source, /addEventListener\("notificationclick"/);
  assert.match(source, /openWindow/);
});
