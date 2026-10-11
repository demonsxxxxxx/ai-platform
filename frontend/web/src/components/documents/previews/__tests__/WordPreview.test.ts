import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../WordPreview.tsx", import.meta.url), "utf8");

function allowedAttributesBlock(): string {
  const match = source.match(/ALLOWED_ATTR:\s*\[([\s\S]*?)\n\s*\],/);
  assert.ok(match, "Word preview should define an allowed attribute list");
  return match[1];
}

test("Word preview preserves DOCX table merge attributes", () => {
  const attributes = allowedAttributesBlock();
  assert.match(attributes, /"colspan"/);
  assert.match(attributes, /"rowspan"/);
});

test("Word preview preserves DOCX image dimensions against global CSS", () => {
  assert.match(
    source,
    /\.docx-preview-content section\.docx img\s*{[^}]*max-width: none;/s,
  );
});

test("Word owns measured direct DOM content rather than fixed-size iframe output", () => {
  assert.match(source, /measureDocxPreview\(container\)/);
  assert.match(source, /naturalWidth=\{docxSize.width\}/);
  assert.match(source, /naturalHeight=\{docxSize.height\}/);
  assert.match(source, /contentRef=\{contentRef\}/);
  assert.doesNotMatch(source, /<iframe|srcDoc=|prepareWordPreviewDocument/);
});
