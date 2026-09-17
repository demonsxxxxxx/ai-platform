import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";

test("html preview removes external resources and pins the inert srcdoc origin", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    DOMParser: dom.window.DOMParser,
    Node: dom.window.Node,
    Element: dom.window.Element,
    HTMLElement: dom.window.HTMLElement,
  });
  const { prepareHtmlPreviewContent } = await import("../htmlPreviewContent.ts");
  const prepared = prepareHtmlPreviewContent(`
    <html><head>
      <base href="https://platform.example/">
      <meta http-equiv="refresh" content="0;url=https://evil.invalid/refresh">
      <link rel="stylesheet" href="https://evil.invalid/style.css">
      <style>.tracked{background:url(https://evil.invalid/css.png)}</style>
    </head><body>
      <script>window.top.location='https://evil.invalid/script'</script>
      <a href="https://evil.invalid/link">external</a>
      <img src="https://evil.invalid/tracker.png" srcset="https://evil.invalid/2x.png 2x">
      <img src="data:image/png;base64,iVBORw0KGgo=" alt="embedded">
      <div style="background:url(https://evil.invalid/inline.png)">styled</div>
    </body></html>
  `);

  assert.match(prepared, /<base href="about:srcdoc" \/>/);
  assert.match(prepared, /Content-Security-Policy/i);
  assert.match(prepared, /default-src 'none'/);
  assert.match(prepared, /data:image\/png;base64,iVBORw0KGgo=/);
  assert.doesNotMatch(prepared, /evil\.invalid|platform\.example/);
  assert.doesNotMatch(prepared, /<script|http-equiv="refresh"|srcset=/i);
});

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
