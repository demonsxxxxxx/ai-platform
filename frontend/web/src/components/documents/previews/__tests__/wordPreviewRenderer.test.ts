import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { createWordPreviewRendererOptions, renderDocxPreviewHtml } from "../wordPreviewRenderer.ts";
import { createSyntheticWordDocument } from "./wordPreviewFixture.ts";

function installDom() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  for (const key of ["window", "document", "DOMParser", "XMLSerializer", "Node", "Element", "HTMLElement", "Blob", "FileReader"]) {
    Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
  }
  Object.defineProperty(globalThis, "requestAnimationFrame", { value: () => 0, configurable: true });
  return dom;
}

test("DOCX preview preserves geometry/local resources and disables altChunk", () => {
  const options = createWordPreviewRendererOptions();
  assert.equal(options.inWrapper, true);
  assert.equal(options.ignoreWidth, false);
  assert.equal(options.ignoreHeight, false);
  assert.equal(options.renderAltChunks, false);
  assert.equal(options.useBase64URL, true);
});

test("real DOCX renderer keeps first insertion inert, retaining embedded resources and pages", async () => {
  const dom = installDom();
  const { renderAsync } = await import("docx-preview");
  let networkCalls = 0;
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => { networkCalls++; throw new Error("External IO forbidden"); };
  try {
    const result = await renderDocxPreviewHtml({
      arrayBuffer: await createSyntheticWordDocument(),
      renderAsync: async (bytes, container, styleContainer, options) => {
        assert.equal(container.isConnected, false);
        assert.equal(container.parentNode?.nodeType, 11);
        assert.equal(styleContainer, container);
        const result = await renderAsync(bytes, container, styleContainer, options);
        assert.equal(container.isConnected, false);
        return result;
      },
      convertToHtml: async () => { throw new Error("Unexpected fallback"); },
    });
    assert.equal(result.kind, "docx-preview");
    assert.match(result.html, /Final synthetic page/);
    assert.ok((result.html.match(/<section/g) ?? []).length >= 2);
    assert.match(result.html, /data:application\/octet-stream;base64,iVBOR/);
    assert.match(result.html, /@font-face/);
    assert.match(result.html, /SyntheticPreview/);
    assert.match(result.html, /<math/);
    assert.match(result.html, /<mroot/);
    const { prepareWordPreviewDocument } = await import("../wordHtmlPreview.ts");
    const prepared = prepareWordPreviewDocument({ html: result.html });
    assert.match(prepared, /<math/);
    assert.match(prepared, /<mroot/);
    assert.match(prepared, /data:application\/octet-stream;base64,iVBOR/);
    assert.match(result.html, /https:\/\/synthetic.invalid\/pixel/);
    assert.equal(document.body.innerHTML, "");
    assert.equal(networkCalls, 0);
  } finally {
    globalThis.fetch = oldFetch;
    dom.window.close();
  }
});

test("DOCX failure clears inert output and preserves the Mammoth fallback", async () => {
  const dom = installDom();
  const calls: string[] = [];
  const result = await renderDocxPreviewHtml({
    arrayBuffer: new ArrayBuffer(8),
    renderAsync: async (_input, container) => {
      calls.push("docx-preview");
      container.innerHTML = "<section>partial</section>";
      throw new Error("render failed");
    },
    convertToHtml: async () => { calls.push("mammoth"); return { value: "<p>Fallback</p>" }; },
    onDocxPreviewError: () => undefined,
  });
  assert.deepEqual(calls, ["docx-preview", "mammoth"]);
  assert.deepEqual(result, { kind: "html", html: "<p>Fallback</p>" });
  assert.equal(document.body.innerHTML, "");
  dom.window.close();
});

test("browser dark fixture triggers real DOCX failure and readable text fallback", async () => {
  const dom = installDom();
  try {
    const { renderAsync } = await import("docx-preview");
    const { isDocxSafeForMammoth, extractDocxTextFallback, docxTextToHtml } = await import("../wordPreviewUtils.ts");
    const arrayBuffer = await createSyntheticWordDocument({ fallback: true });
    let failed = false;
    const result = await renderDocxPreviewHtml({
      arrayBuffer, renderAsync,
      onDocxPreviewError: () => { failed = true; },
      convertToHtml: async ({ arrayBuffer }) => {
        assert.equal(await isDocxSafeForMammoth(arrayBuffer), false);
        return { value: docxTextToHtml(await extractDocxTextFallback(arrayBuffer)) };
      },
    });
    assert.equal(failed, true);
    assert.deepEqual(result, { kind: "html", html: "<p>Dark synthetic fallback</p>" });
  } finally { dom.window.close(); }
});
