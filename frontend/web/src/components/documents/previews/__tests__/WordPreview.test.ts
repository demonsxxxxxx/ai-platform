import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const source = readFileSync(new URL("../WordPreview.tsx", import.meta.url), "utf8");
test("Word preview only mounts static sandboxed output, not renderer-owned nodes", () => {
  assert.match(source, /srcDoc=\{previewDocument\}/);
  assert.match(source, /sandbox=""/);
  assert.match(source, /referrerPolicy="no-referrer"/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|contentRef|document\.head\.appendChild/);
});
test("Word preview preserves DOCX image dimensions against global CSS", () => {
  assert.match(source, /\.docx-preview-content section\.docx img\s*{[^}]*max-width: none;/s);
});

test("Word fallback restores the previous viewer-owner reset boundary", () => {
  assert.match(source, /key=\{loading \|\| renderedWithDocxPreview \? "docx" : "fallback"\}/);
});
