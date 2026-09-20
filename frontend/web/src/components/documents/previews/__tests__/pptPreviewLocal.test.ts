import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";

const source = readFileSync(new URL("../PptPreview.tsx", import.meta.url), "utf8");

test("PPTX preview uses the visual renderer inside an inert iframe", () => {
  assert.match(source, /pptxToHtml/);
  assert.match(source, /preparePptxSlideDocument/);
  assert.match(source, /sandbox=""/);
  assert.match(source, /referrerPolicy="no-referrer"/);
  assert.match(source, /extractPptxSlides/);
  assert.match(source, /onDownload=\{onDownload\}/);
});

test("PPTX slide documents remove active and network-capable markup", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "https://preview.invalid/",
  });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    Node: dom.window.Node,
    Element: dom.window.Element,
    HTMLElement: dom.window.HTMLElement,
  });

  const { preparePptxSlideDocument } = await import("../pptHtmlPreview.ts");
  const prepared = preparePptxSlideDocument(`
    <script>window.top.location='https://evil.invalid'</script>
    <a href="javascript:alert(1)" style="background:url(https://evil.invalid/x)">link</a>
    <img src="https://evil.invalid/tracker.png" onerror="alert(1)">
    <img src="data:image/png;base64,AAAA">
  `);

  assert.match(prepared, /Content-Security-Policy/);
  assert.match(prepared, /default-src 'none'/);
  assert.doesNotMatch(prepared, /<script/i);
  assert.doesNotMatch(prepared, /javascript:/i);
  assert.doesNotMatch(prepared, /evil\.invalid/i);
  assert.doesNotMatch(prepared, /onerror/i);
  assert.match(prepared, /data:image\/png;base64,AAAA/);
});
