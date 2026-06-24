import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(resolve(import.meta.dirname, "../sw.ts"), "utf8");

test("service worker uses Workbox precaching with an injected Vite manifest", () => {
  assert.match(source, /const PRECACHE_MANIFEST = self\.__WB_MANIFEST/);
  assert.match(source, /precacheAndRoute\(PRECACHE_MANIFEST\)/);
  assert.match(source, /cleanupOutdatedCaches\(\)/);
});

test("service worker keeps dynamic LambChat backends out of runtime caches", () => {
  assert.match(source, /getPwaRequestKind/);
  assert.doesNotMatch(source, /registerRoute\([^]*\/api/);
  assert.doesNotMatch(source, /registerRoute\([^]*text\/event-stream/);
});

test("service worker versions and clears runtime caches so stale workbench bundles cannot return", () => {
  assert.match(source, /function getBuildCacheVersion/);
  assert.match(source, /const BUILD_CACHE_VERSION = getBuildCacheVersion\(PRECACHE_MANIFEST\)/);
  assert.match(source, /`ai-platform-app-shell-\$\{BUILD_CACHE_VERSION\}`/);
  assert.match(source, /`ai-platform-static-\$\{BUILD_CACHE_VERSION\}`/);
  assert.match(source, /function isAiPlatformRuntimeCache/);
  assert.match(source, /deleteOutdatedRuntimeCaches/);
  assert.match(source, /self\.addEventListener\("activate"/);
  assert.doesNotMatch(source, /ai-platform-app-shell-v1/);
  assert.doesNotMatch(source, /ai-platform-static-v1/);
});
