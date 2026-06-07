import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("html artifact preview iframe cannot execute scripts or inherit platform origin", () => {
  const previewSource = readFileSync(
    new URL("../HtmlPreview.tsx", import.meta.url),
    "utf8",
  );

  const sandboxMatch = previewSource.match(/sandbox="([^"]*)"/);
  assert.ok(sandboxMatch, "HTML preview iframe should keep a sandbox");
  const sandboxFlags = sandboxMatch[1];

  assert.doesNotMatch(
    sandboxFlags,
    /\ballow-scripts\b/,
    "artifact HTML must not be allowed to execute scripts",
  );
  assert.doesNotMatch(
    sandboxFlags,
    /\ballow-same-origin\b/,
    "artifact HTML must not run under the platform origin",
  );
});

test("html artifact preview keeps source viewing available as the safe fallback", () => {
  const previewSource = readFileSync(
    new URL("../HtmlPreview.tsx", import.meta.url),
    "utf8",
  );

  assert.match(previewSource, /DeferredCodeMirrorViewer/);
  assert.match(previewSource, /language="html"/);
});
