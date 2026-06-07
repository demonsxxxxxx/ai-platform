import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../McpBlockPreview.tsx", import.meta.url),
  "utf8",
);

test("MCP image previews resolve protected platform URLs before rendering img src", () => {
  assert.match(source, /resolveDocumentPreviewUrl/);
  assert.doesNotMatch(
    source,
    /const\s+src\s*=[\s\S]*?block\.url[\s\S]*?<img[\s\S]*?src=\{src\}/,
    "MCP image blocks must not render block.url directly as an img src",
  );
  assert.doesNotMatch(
    source,
    /openBlockPreview\(\{\s*type:\s*"image",\s*src\s*\}\)/,
    "MCP image preview panels must receive the resolved safe image src",
  );
});

test("MCP file previews open protected platform URLs through preview helpers", () => {
  assert.match(source, /openPreviewUrl/);
  assert.doesNotMatch(
    source,
    /<a[\s\S]*?href=\{preview\.url\}/,
    "MCP file preview panels must not render preview.url as a raw anchor href",
  );
});

test("MCP rich result links do not expose raw protected platform URLs", () => {
  assert.match(source, /openPreviewUrl/);
  assert.doesNotMatch(
    source,
    /<a[\s\S]*?href=\{url\}/,
    "MCP rich result URLs must not render result.url as a raw anchor href",
  );
});
