import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("ai-platform shell does not load external Google font stylesheets", () => {
  const indexHtml = readSource("../../index.html");
  const offlineHtml = readSource("../../public/offline.html");

  assert.doesNotMatch(indexHtml, /fonts\.googleapis\.com/);
  assert.doesNotMatch(indexHtml, /fonts\.gstatic\.com/);
  assert.doesNotMatch(indexHtml, /Source Sans 3/);
  assert.doesNotMatch(offlineHtml, /fonts\.googleapis\.com/);
  assert.doesNotMatch(offlineHtml, /fonts\.gstatic\.com/);
  assert.doesNotMatch(offlineHtml, /Source Sans 3/);
});

test("service worker does not register external Google font cache routes", () => {
  const serviceWorkerSource = readSource("../sw.ts");

  assert.doesNotMatch(serviceWorkerSource, /fonts\.googleapis\.com/);
  assert.doesNotMatch(serviceWorkerSource, /fonts\.gstatic\.com/);
  assert.doesNotMatch(serviceWorkerSource, /FONT_(?:STYLES|FILES)_CACHE/);
});
