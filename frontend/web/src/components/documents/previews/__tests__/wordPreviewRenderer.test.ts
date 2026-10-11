import assert from "node:assert/strict";
import test from "node:test";
import {
  createWordPreviewRendererOptions,
  renderDocxPreviewHtml,
} from "../wordPreviewRenderer.ts";

test("DOCX preview preserves page geometry and disables altChunk rendering", () => {
  assert.deepEqual(
    {
      inWrapper: createWordPreviewRendererOptions().inWrapper,
      ignoreWidth: createWordPreviewRendererOptions().ignoreWidth,
      ignoreHeight: createWordPreviewRendererOptions().ignoreHeight,
      renderAltChunks: createWordPreviewRendererOptions().renderAltChunks,
    },
    {
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: false,
      renderAltChunks: false,
    },
  );
});

test("DOCX preview is primary and strips unsafe hyperlink protocols", async () => {
  let href: string | null = "javascript:alert(1)";
  const link = {
    getAttribute: () => href,
    removeAttribute: () => {
      href = null;
    },
  };
  const container = {
    innerHTML: "",
    textContent: "",
    querySelectorAll: () => [link],
  } as unknown as HTMLElement;
  const calls: string[] = [];

  const result = await renderDocxPreviewHtml({
    arrayBuffer: new ArrayBuffer(8),
    container,
    renderAsync: async (_input, output) => {
      calls.push("docx-preview");
      output.innerHTML = "<a href=\"javascript:alert(1)\">Document</a>";
    },
    convertToHtml: async () => {
      calls.push("mammoth");
      return { value: "<p>Fallback</p>" };
    },
  });

  assert.deepEqual(calls, ["docx-preview"]);
  assert.deepEqual(result, { kind: "docx-preview" });
  assert.equal(href, null);
});

test("DOCX preview falls back to Mammoth after a renderer failure", async () => {
  const container = {
    innerHTML: "<section>stale</section>",
    textContent: "stale",
    querySelectorAll: () => [],
  } as unknown as HTMLElement;
  const calls: string[] = [];

  const result = await renderDocxPreviewHtml({
    arrayBuffer: new ArrayBuffer(8),
    container,
    renderAsync: async () => {
      calls.push("docx-preview");
      throw new Error("render failed");
    },
    convertToHtml: async () => {
      calls.push("mammoth");
      return { value: "<p>Fallback</p>" };
    },
    onDocxPreviewError: () => undefined,
  });

  assert.deepEqual(calls, ["docx-preview", "mammoth"]);
  assert.deepEqual(result, { kind: "html", html: "<p>Fallback</p>" });
  assert.equal(container.innerHTML, "");
});

test("DOCX preview retains normal links and removes executable protocols", async () => {
  const hrefs = ["https://example.test/doc", "mailto:reader@example.test", "tel:+12345678", "#chapter", "javascript:alert(1)", "data:text/html,test", "vbscript:msgbox(1)"];
  const links = hrefs.map(href => ({
    href: href as string | null,
    getAttribute() { return this.href; },
    removeAttribute() { this.href = null; },
  }));
  const container = {
    innerHTML: "", textContent: "",
    querySelectorAll: () => links,
  } as unknown as HTMLElement;
  await renderDocxPreviewHtml({
    arrayBuffer: new ArrayBuffer(8), container,
    renderAsync: async (_input, output) => { output.innerHTML = "<p>Links</p>"; },
    convertToHtml: async () => { throw new Error("Unexpected fallback"); },
  });
  assert.deepEqual(links.map(link => link.href), [...hrefs.slice(0, 4), null, null, null]);
});
